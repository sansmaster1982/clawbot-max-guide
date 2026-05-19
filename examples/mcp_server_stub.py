"""MCP-сервер поверх MAX User API на семь тулов.

6 из 7 тулов реализованы рабоче. `max_history` оставлен как stub,
потому что opcode 49 (CHAT_HISTORY) парсится нестандартно и требует
больше работы по дельта-кодированию строк (см. _parse_history_raw
в чужих реверс-проектах).

Перед использованием:
  1. Пройти SMS-логин через vkmax или PyMax (см. INSTRUCTIONS.md, Шаг 3)
  2. Положить токен в ~/.config/clawbot-max/token.txt с chmod 600
  3. pip install mcp httpx msgpack

Подключение к Claude Desktop / Cursor / OpenClaw / Continue / Cline
описано в INSTRUCTIONS.md, Шаг 9.

Каждый MCP-call открывает свежий сокет к api.oneme.ru:443, делает
INIT + LOGIN (interactive=False), выполняет операцию, закрывает.
Не оптимально по latency, но проще для stateless MCP-режима.
Для production-уровня держите persistent socket в отдельном listener-процессе
и принимайте команды через Unix socket / named pipe.
"""
import json
import random
import re
import socket as _socket
import ssl
import struct
import uuid
from pathlib import Path
from typing import Any

import msgpack

# pip install mcp
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


# ----- константы протокола MAX -----

HOST = "api.oneme.ru"
PORT = 443
PROTO_VER = 10
APP_VERSION = "26.11.0"  # сверьте с актуальным max_cli если будет API-брэйкинг

OP_INIT = 6
OP_LOGIN = 19
OP_CONTACT_INFO_BY_PHONE = 46
OP_MSG_SEND = 64

CID_MIN = 1_750_000_000_000
CID_MAX = 2_000_000_000_000

# Global packet counter. MAX correlates request/response через seq, поэтому
# каждый исходящий пакет должен иметь уникальный возрастающий seq.
_seq = 0


# ----- пути на диске -----

CONFIG_DIR = Path.home() / ".config" / "clawbot-max"
TOKEN_PATH = CONFIG_DIR / "token.txt"
DEVICE_ID_PATH = CONFIG_DIR / "device_id.txt"
ALLOWLIST_PATH = CONFIG_DIR / "allowlist.json"


# ----- транспорт -----

def _connect() -> _socket.socket:
    global _seq
    _seq = 0
    ctx = ssl.create_default_context()
    raw = _socket.create_connection((HOST, PORT), timeout=30)
    sock = ctx.wrap_socket(raw, server_hostname=HOST)
    sock.settimeout(30)
    return sock


def _send_packet(sock: _socket.socket, opcode: int, payload: dict) -> None:
    global _seq
    body = msgpack.packb(payload, use_bin_type=True)
    header = struct.pack(">BBHHI", PROTO_VER, 0, _seq, opcode, len(body))
    _seq += 1
    sock.sendall(header + body)


def _recv_packet(sock: _socket.socket) -> tuple[int, int, int, bytes]:
    header = b""
    while len(header) < 10:
        chunk = sock.recv(10 - len(header))
        if not chunk:
            raise ConnectionError("socket closed during header")
        header += chunk
    _ver, cmd, seq, opcode, length_raw = struct.unpack(">BBHHI", header)
    # Верхний байт length-поля это флаги (compression/encoding), не длина.
    # Без маски при ненулевом флаге читали бы мегабайты несуществующих данных.
    body_len = length_raw & 0x00FFFFFF
    body = b""
    while len(body) < body_len:
        chunk = sock.recv(body_len - len(body))
        if not chunk:
            raise ConnectionError("socket closed mid-body")
        body += chunk
    return cmd, opcode, seq, body


def _wait_for_op(sock: _socket.socket, target_op: int, max_drain: int = 20) -> tuple[int, bytes]:
    """Прочитать пакеты пока не придёт целевой opcode.

    Между нашим запросом и ответом сервер может прислать keepalive (op=1)
    или служебные push-события — их нужно дропнуть.
    """
    for _ in range(max_drain):
        cmd, op, _, raw = _recv_packet(sock)
        if op == target_op:
            return cmd, raw
    raise TimeoutError(f"target op={target_op} not received within {max_drain} packets")


# ----- сессия -----

def _load_token() -> str:
    if not TOKEN_PATH.exists():
        raise RuntimeError(
            f"Token not found at {TOKEN_PATH}. Run SMS-login flow first "
            "(see vkmax/auth.py or PyMax)."
        )
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def _load_or_create_device_id() -> str:
    if DEVICE_ID_PATH.exists():
        return DEVICE_ID_PATH.read_text(encoding="utf-8").strip()
    device_id = str(uuid.uuid4())
    DEVICE_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEVICE_ID_PATH.write_text(device_id, encoding="utf-8")
    DEVICE_ID_PATH.chmod(0o600)
    return device_id


def _open_session() -> _socket.socket:
    """Открыть сокет, выполнить INIT + LOGIN (interactive=False).

    Возвращает готовый сокет для следующих операций.
    Caller должен закрыть сокет в finally.
    """
    token = _load_token()
    device_id = _load_or_create_device_id()
    sock = _connect()
    try:
        # INIT
        _send_packet(sock, OP_INIT, {
            "userAgent": {
                "deviceType": "ANDROID",
                "locale": "ru",
                "appVersion": APP_VERSION,
            },
            "deviceId": device_id,
        })
        cmd, _ = _wait_for_op(sock, OP_INIT)
        if cmd != 1:
            raise RuntimeError(f"INIT failed cmd={cmd}")
        # LOGIN (interactive=False — sessions для send-only без push)
        _send_packet(sock, OP_LOGIN, {
            "token": token,
            "interactive": False,
            "chatsCount": 40,
            "chatsSync": 0,
            "contactsSync": 0,
            "presenceSync": 0,
            "draftsSync": 0,
        })
        cmd, _ = _wait_for_op(sock, OP_LOGIN)
        if cmd != 1:
            raise RuntimeError(f"LOGIN failed cmd={cmd}. Возможно токен протух — пройдите SMS-flow заново.")
        return sock
    except Exception:
        sock.close()
        raise


# ----- операции протокола -----

def _send_message(user_id: int, text: str) -> dict[str, Any]:
    """Op 64 MSG_SEND с обязательным CID."""
    sock = _open_session()
    try:
        cid = random.randint(CID_MIN, CID_MAX)
        _send_packet(sock, OP_MSG_SEND, {
            "userId": user_id,
            "message": {
                "text": text,
                "cid": cid,
                "elements": [],
                "attaches": [],
            },
            "notify": True,
        })
        cmd, raw = _wait_for_op(sock, OP_MSG_SEND)
        if cmd != 1:
            return {"ok": False, "error": f"server cmd={cmd}", "cid": cid}
        return {"ok": True, "cid": cid, "user_id": user_id}
    finally:
        sock.close()


def _resolve_phone(phone: str) -> dict[str, Any]:
    """Op 46 CONTACT_INFO_BY_PHONE. Регекс по сырым байтам для user_id."""
    sock = _open_session()
    try:
        _send_packet(sock, OP_CONTACT_INFO_BY_PHONE, {"phone": phone})
        cmd, raw = _wait_for_op(sock, OP_CONTACT_INFO_BY_PHONE)
        if cmd != 1:
            return {"ok": False, "error": f"server cmd={cmd}"}
        m = re.search(rb"\xa2id\xd2(.{4})", raw, re.DOTALL)
        if not m:
            return {
                "ok": False,
                "error": "user_id not in response (возможно phone не зарегистрирован в MAX)",
            }
        uid = int.from_bytes(m.group(1), "big", signed=True)
        return {"ok": True, "user_id": uid, "phone": phone}
    finally:
        sock.close()


# ----- allowlist -----

def _load_allowlist() -> dict[str, Any]:
    if ALLOWLIST_PATH.exists():
        return json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    return {"autoreply": [], "contexts": {}, "blocked": []}


def _save_allowlist(data: dict[str, Any]) -> None:
    ALLOWLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    ALLOWLIST_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _add_to_autoreply(user_id: int, task_type: str | None = None, details: str | None = None) -> dict[str, Any]:
    data = _load_allowlist()
    ids = set(data.get("autoreply", []))
    ids.add(user_id)
    data["autoreply"] = sorted(ids)
    if task_type or details:
        ctxs = data.get("contexts") or {}
        ctxs[str(user_id)] = {"type": task_type or "general", "details": details or ""}
        data["contexts"] = ctxs
    _save_allowlist(data)
    return data


# ----- MCP-сервер -----

server: Server = Server("max-user")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="max_send",
            description="Отправить текст в MAX по user_id или по телефону (+79...) от лица секонд-аккаунта.",
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": ["string", "integer"],
                        "description": "user_id (int) или телефон '+79...' (string)",
                    },
                    "text": {"type": "string"},
                },
                "required": ["target", "text"],
            },
        ),
        Tool(
            name="max_engage",
            description="Запустить секретарскую задачу: резолв телефона в user_id, добавление в allowlist с контекстом, отправка opening-сообщения.",
            inputSchema={
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "+79..."},
                    "task_type": {
                        "type": "string",
                        "enum": ["booking", "meeting", "follow-up", "info", "scheduling", "general"],
                    },
                    "details": {"type": "string", "description": "Контекст задачи для LLM"},
                    "opening": {"type": "string", "description": "Первое сообщение собеседнику. Если не указано, генерится из details."},
                },
                "required": ["phone", "task_type", "details"],
            },
        ),
        Tool(
            name="max_history",
            description="Получить N последних сообщений из чата. STUB, требует доработки парсинга op=49.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer"},
                    "count": {"type": "integer", "default": 10},
                },
                "required": ["user_id"],
            },
        ),
        Tool(
            name="max_contact",
            description="Резолвить телефон (+79...) в user_id MAX.",
            inputSchema={
                "type": "object",
                "properties": {"phone": {"type": "string"}},
                "required": ["phone"],
            },
        ),
        Tool(
            name="max_allow",
            description="Добавить user_id в allowlist для авто-ответа, опционально с контекстом задачи.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer"},
                    "task_type": {"type": "string"},
                    "details": {"type": "string"},
                },
                "required": ["user_id"],
            },
        ),
        Tool(
            name="max_disallow",
            description="Убрать user_id из allowlist (бот больше не отвечает авто).",
            inputSchema={
                "type": "object",
                "properties": {"user_id": {"type": "integer"}},
                "required": ["user_id"],
            },
        ),
        Tool(
            name="max_block",
            description="Жёсткий блок: listener молча игнорит сообщения от user_id.",
            inputSchema={
                "type": "object",
                "properties": {"user_id": {"type": "integer"}},
                "required": ["user_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "max_send":
            target = arguments["target"]
            text = arguments["text"]
            if isinstance(target, str) and target.startswith("+"):
                resolved = _resolve_phone(target)
                if not resolved.get("ok"):
                    return [TextContent(type="text", text=json.dumps(resolved, ensure_ascii=False))]
                user_id = resolved["user_id"]
            else:
                user_id = int(target)
            result = _send_message(user_id, text)
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

        if name == "max_contact":
            result = _resolve_phone(arguments["phone"])
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

        if name == "max_engage":
            phone = arguments["phone"]
            task_type = arguments["task_type"]
            details = arguments["details"]
            opening = arguments.get("opening") or f"Здравствуйте! {details}"
            resolved = _resolve_phone(phone)
            if not resolved.get("ok"):
                return [TextContent(type="text", text=json.dumps(resolved, ensure_ascii=False))]
            user_id = resolved["user_id"]
            _add_to_autoreply(user_id, task_type=task_type, details=details)
            send_result = _send_message(user_id, opening)
            return [TextContent(type="text", text=json.dumps({
                "ok": send_result.get("ok", False),
                "user_id": user_id,
                "task_type": task_type,
                "opening_sent": opening,
                "send_result": send_result,
            }, ensure_ascii=False))]

        if name == "max_history":
            user_id = arguments["user_id"]
            count = arguments.get("count", 10)
            return [TextContent(type="text", text=json.dumps({
                "ok": False,
                "stub": True,
                "note": "max_history requires op=49 CHAT_HISTORY parsing. См. _parse_history_raw в чужих реверс-проектах (vkmax, PyMax).",
                "user_id": user_id,
                "count": count,
            }, ensure_ascii=False))]

        if name == "max_allow":
            user_id = int(arguments["user_id"])
            _add_to_autoreply(
                user_id,
                task_type=arguments.get("task_type"),
                details=arguments.get("details"),
            )
            return [TextContent(type="text", text=json.dumps({
                "ok": True,
                "user_id": user_id,
                "action": "added to autoreply",
            }, ensure_ascii=False))]

        if name == "max_disallow":
            user_id = int(arguments["user_id"])
            data = _load_allowlist()
            ids = set(data.get("autoreply", []))
            ids.discard(user_id)
            data["autoreply"] = sorted(ids)
            _save_allowlist(data)
            return [TextContent(type="text", text=json.dumps({
                "ok": True,
                "user_id": user_id,
                "action": "removed from autoreply",
            }, ensure_ascii=False))]

        if name == "max_block":
            user_id = int(arguments["user_id"])
            data = _load_allowlist()
            blocked = set(data.get("blocked", []))
            blocked.add(user_id)
            data["blocked"] = sorted(blocked)
            ids = set(data.get("autoreply", []))
            ids.discard(user_id)
            data["autoreply"] = sorted(ids)
            _save_allowlist(data)
            return [TextContent(type="text", text=json.dumps({
                "ok": True,
                "user_id": user_id,
                "action": "blocked",
            }, ensure_ascii=False))]

        return [TextContent(type="text", text=json.dumps({
            "ok": False,
            "error": f"unknown tool: {name}",
        }, ensure_ascii=False))]

    except Exception as e:
        return [TextContent(type="text", text=json.dumps({
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
