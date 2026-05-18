"""User API listener: persistent TLS-сокет с interactive=True для push-событий.

Принимает op=128 NEW_MESSAGE, парсит sender + text по сырым байтам
(стандартный msgpack.unpackb валится на четверти MAX-пакетов из-за
кастомного дельта-кодирования и control-байтов в строковых полях).

Перед запуском один раз вызвать Op 97 (LOGOUT_OTHER_SESSIONS), иначе
push-события могут уходить мобильному клиенту, а не сюда. Op 97
разлогинит ВСЕ другие сессии этого аккаунта, ВКЛЮЧАЯ мобильный.

Реальная LLM-обвязка тут не показана — её надо подключить отдельно
(см. часть 8 статьи: TL;DR — берите chat-only модель и max_tokens=2000+).
"""
import random
import re
import socket
import ssl
import struct
import time

import msgpack

HOST = "api.oneme.ru"
PORT = 443
PROTO_VER = 10

OP_LOGIN = 19
OP_LOGOUT_OTHERS = 97
OP_MSG_SEND = 64
OP_KEEPALIVE = 1
OP_NEW_MESSAGE = 128
OP_CHAT_NOTIFY = 129
OP_READ_RECEIPT = 130
OP_PRESENCE = 132

CID_MIN = 1_750_000_000_000
CID_MAX = 2_000_000_000_000


def connect() -> socket.socket:
    ctx = ssl.create_default_context()
    sock = ctx.wrap_socket(socket.socket(socket.AF_INET), server_hostname=HOST)
    sock.connect((HOST, PORT))
    return sock


def send_packet(sock: socket.socket, opcode: int, payload: dict, seq: int = 1) -> None:
    body = msgpack.packb(payload, use_bin_type=False)
    header = struct.pack(">BBHHI", PROTO_VER, 0, seq, opcode, len(body))
    sock.sendall(header + body)


def recv_packet(sock: socket.socket) -> tuple[int, int, int, bytes]:
    """Returns (cmd, opcode, seq, body_bytes)."""
    header = b""
    while len(header) < 10:
        chunk = sock.recv(10 - len(header))
        if not chunk:
            raise ConnectionError("socket closed")
        header += chunk
    _ver, cmd, seq, opcode, body_len = struct.unpack(">BBHHI", header)
    body = b""
    while len(body) < body_len:
        chunk = sock.recv(body_len - len(body))
        if not chunk:
            raise ConnectionError("socket closed mid-body")
        body += chunk
    return cmd, opcode, seq, body


def login_interactive(sock: socket.socket, token: str) -> None:
    """Op 19 LOGIN with interactive=True — subscribes to push events.

    Без interactive=True сервер не пушит ничего на этот сокет — listener
    будет вечно висеть в recv_packet и тишина.
    """
    send_packet(sock, OP_LOGIN, {
        "token": token,
        "interactive": True,
        "chatsCount": 40,
        "chatsSync": 0,
        "contactsSync": 0,
        "presenceSync": 0,
        "draftsSync": 0,
    })


def logout_other_sessions(sock: socket.socket) -> None:
    """Op 97 — единственный путь гарантировать что push-события приходят
    именно сюда. Разлогинивает все другие сессии аккаунта, в т.ч. мобильный.
    Параметр sessionIds сервер игнорирует.
    """
    send_packet(sock, OP_LOGOUT_OTHERS, {"sessionIds": []})


def parse_op128(raw: bytes) -> tuple[int | None, str]:
    """Extract (sender_user_id, text) from a NEW_MESSAGE push payload.

    Стандартный msgpack.unpackb валится — у MAX дельта-кодирование
    строк и control-байты в текстовых полях. Поэтому regex по сырым
    байтам. Флаг re.DOTALL обязателен: точка не матчит \\n без него,
    а int32-поля содержат любые байты включая 0x0a.
    """
    sender = None
    sm = re.search(rb"\xa6sender\xd2(.{4})", raw, re.DOTALL)
    if sm:
        sender = int.from_bytes(sm.group(1), "big", signed=True)

    text = ""
    tm = re.search(rb"\xa4text", raw)
    if tm:
        i = tm.end()
        if i < len(raw):
            b = raw[i]
            # fixstr (length encoded in marker byte)
            if 0xa0 <= b <= 0xbf:
                ln = b & 0x1f
                text = raw[i+1:i+1+ln].decode("utf-8", errors="replace")
            # str8
            elif b == 0xd9 and i+1 < len(raw):
                ln = raw[i+1]
                text = raw[i+2:i+2+ln].decode("utf-8", errors="replace")
            # str16
            elif b == 0xda and i+2 < len(raw):
                ln = int.from_bytes(raw[i+1:i+3], "big")
                text = raw[i+3:i+3+ln].decode("utf-8", errors="replace")

    # Cleanup: MAX delta-encoding sometimes leaks bytes from next field
    # into the text. Drop control bytes (< 0x20 except \n\t) and stop at
    # 2+ noise bytes in a row.
    cleaned = []
    noise_run = 0
    for ch in text:
        if ord(ch) < 0x20 and ch not in "\n\t":
            noise_run += 1
            if noise_run >= 2:
                break
            continue
        if ch == "�":
            noise_run += 1
            if noise_run >= 2:
                break
            continue
        noise_run = 0
        cleaned.append(ch)
    return sender, "".join(cleaned).strip()


def reply_to_user(sock: socket.socket, user_id: int, text: str) -> None:
    """Op 64 MSG_SEND with mandatory cid (see user_api_send.py for details)."""
    send_packet(sock, OP_MSG_SEND, {
        "userId": user_id,
        "message": {
            "text": text,
            "cid": random.randint(CID_MIN, CID_MAX),
            "elements": [],
            "attaches": [],
        },
        "notify": True,
    })


def call_your_llm(sender_id: int, text: str) -> str:
    """STUB. Replace with your LLM provider call.

    Recommendations:
    - chat-only model (no hidden reasoning field) keeps max_tokens small
    - if reasoning model: max_tokens 2000+, иначе content вернётся null
    - system prompt должен запрещать выдумывать персональные данные хозяина
    """
    return f"(stub reply to {sender_id}: «{text}»)"


def load_token() -> str:
    import pathlib
    p = pathlib.Path.home() / ".config" / "clawbot-max" / "token.txt"
    if not p.exists():
        raise SystemExit(f"Token not found at {p}")
    return p.read_text(encoding="utf-8").strip()


SELF_USER_ID = int(__import__("os").environ.get("MAX_SELF_USER_ID", "0"))


def run_session(token: str) -> None:
    """One persistent session. Returns when socket dies (caller reconnects)."""
    sock = connect()
    sock.settimeout(180)
    try:
        login_interactive(sock, token)
        # Drain login response
        cmd, op, _, _ = recv_packet(sock)
        if cmd != 1:
            raise RuntimeError("LOGIN failed")
        print("[listener] logged in, subscribed to push events")

        # One-time: discard other sessions so push lands here. ВАЖНО: убьёт
        # мобильный клиент тоже, ему потребуется новый SMS-логин.
        logout_other_sessions(sock)
        # Drain response
        for _ in range(20):
            c2, o2, _, _ = recv_packet(sock)
            if o2 == OP_LOGOUT_OTHERS:
                break

        while True:
            try:
                cmd, op, _, raw = recv_packet(sock)
            except socket.timeout:
                # Periodic keepalive (not strictly required but defensive)
                continue

            if op == OP_KEEPALIVE and len(raw) == 0:
                continue
            if op in (OP_CHAT_NOTIFY, OP_READ_RECEIPT, OP_PRESENCE):
                continue

            if op == OP_NEW_MESSAGE:
                sender, text = parse_op128(raw)
                if not text or sender == SELF_USER_ID:
                    continue
                print(f"[listener] from sender={sender}: {text!r}")
                reply = call_your_llm(sender, text)
                if reply:
                    reply_to_user(sock, sender, reply)
                continue

            print(f"[listener] unknown op={op} cmd={cmd} size={len(raw)}")
    finally:
        sock.close()


def main() -> None:
    token = load_token()
    backoff = 2
    while True:
        try:
            run_session(token)
        except KeyboardInterrupt:
            print("[listener] stopped by user")
            return
        except Exception as e:
            print(f"[listener] session died: {e!r}; reconnecting in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    main()
