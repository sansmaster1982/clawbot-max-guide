"""User API: отправка сообщения через TLS-сокет на api.oneme.ru:443.

Это упрощённый пример. Полный flow (SMS-login, обработка 2FA, парсинг
ответов сервера) есть в:
  - https://github.com/nsdkinx/vkmax (документация опкодов в /docs)
  - https://github.com/MaxApiTeam/PyMax (заархивирован, но рабочий)
  - https://github.com/rast-games/pyromax (alpha, aiogram-like)

Здесь показано только то, на чём легко споткнуться: обязательное поле
`cid` внутри `message`. Без него сервер дедупит и тихо проглатывает.

Запуск только после прохождения SMS-логина через указанные библиотеки
и сохранения токена в файл.
"""
import random
import socket
import ssl
import struct
import time

import msgpack

HOST = "api.oneme.ru"
PORT = 443
PROTO_VER = 10

OP_LOGIN = 19
OP_MSG_SEND = 64

# CID диапазон: unix-timestamp в миллисекундах примерно с 2025 до 2033.
# Сервер ожидает что клиент использует свой локальный таймстамп
# как уникальный идентификатор сообщения для dedup.
CID_MIN = 1_750_000_000_000
CID_MAX = 2_000_000_000_000


def connect() -> socket.socket:
    ctx = ssl.create_default_context()
    sock = ctx.wrap_socket(socket.socket(socket.AF_INET), server_hostname=HOST)
    sock.connect((HOST, PORT))
    return sock


def send_packet(sock: socket.socket, opcode: int, payload: dict, seq: int = 1) -> None:
    """Custom 10-byte frame + msgpack body.

    Frame layout:
      [0]    PROTO_VER (1 byte)
      [1]    cmd-byte (0 for outgoing)
      [2:4]  sequence number (2 bytes, big-endian)
      [4:6]  opcode (2 bytes, big-endian)
      [6:10] body length (4 bytes, big-endian)
    """
    body = msgpack.packb(payload, use_bin_type=False)
    header = struct.pack(">BBHHI", PROTO_VER, 0, seq, opcode, len(body))
    sock.sendall(header + body)


def login(sock: socket.socket, token: str) -> None:
    """Op 19 LOGIN. Use interactive=False for send-only sessions."""
    send_packet(sock, OP_LOGIN, {
        "token": token,
        "interactive": False,
        "chatsCount": 0,
        "chatsSync": 0,
        "contactsSync": 0,
        "presenceSync": 0,
        "draftsSync": 0,
    })


def send_message_to_user(sock: socket.socket, user_id: int, text: str) -> None:
    """Op 64 MSG_SEND. CID inside message is MANDATORY.

    Without `cid` the server deduplicates all sends as one message.
    Returns cmd=1 (success) but never actually writes new messages.
    Looks like shadow-ban; isn't.
    """
    cid = random.randint(CID_MIN, CID_MAX)
    # Equivalent and explicit: cid = int(time.time() * 1000)
    send_packet(sock, OP_MSG_SEND, {
        "userId": user_id,
        "message": {
            "text": text,
            "cid": cid,
            "elements": [],
            "attaches": [],
        },
        "notify": True,
    })
    print(f"[user-api] sent message cid={cid} to user_id={user_id}")


def load_token() -> str:
    """Token must be obtained through SMS-login flow (not shown here)."""
    import pathlib
    token_path = pathlib.Path.home() / ".config" / "clawbot-max" / "token.txt"
    if not token_path.exists():
        raise SystemExit(
            f"Token not found at {token_path}. Run SMS-login flow first "
            "(see vkmax or PyMax for working implementation)."
        )
    return token_path.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python user_api_send.py <user_id> <text>")
        sys.exit(1)
    target_user_id = int(sys.argv[1])
    text = " ".join(sys.argv[2:])
    token = load_token()
    sock = connect()
    try:
        login(sock, token)
        send_message_to_user(sock, target_user_id, text)
        time.sleep(2)  # let server process the send
    finally:
        sock.close()
