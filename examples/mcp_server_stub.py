"""Каркас MCP-сервера на 7 тулов поверх MAX User API.

Используется MCP Python SDK (`pip install mcp`). Сервер регистрируется
в любом MCP-совместимом агенте (Claude Desktop, Cursor, OpenClaw,
Continue, Cline) одной строкой в конфиге:

  Claude Desktop config (~/.config/claude/claude_desktop_config.json):
    "mcpServers": {
      "max-user": {
        "command": "python",
        "args": ["/path/to/mcp_server_stub.py"]
      }
    }

Тулы вызывают функции из user_api_send.py и user_api_listener.py
(импортируйте по факту вашей структуры проекта).

Это упрощённая структура. Production-версия включает асинхронность,
проверку прав, валидацию входов, error-handling по типу tool-результата.
"""
import json
import os
from pathlib import Path
from typing import Any

# pip install mcp
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


ALLOWLIST_PATH = Path.home() / ".config" / "clawbot-max" / "allowlist.json"


server = Server("max-user")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="max_send",
            description=(
                "Отправить текстовое сообщение в MAX по user_id или телефону. "
                "Использует секонд-аккаунт хозяина."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": ["string", "integer"],
                        "description": "user_id (int) или телефон '+79...'",
                    },
                    "text": {"type": "string"},
                },
                "required": ["target", "text"],
            },
        ),
        Tool(
            name="max_engage",
            description=(
                "Запустить секретарскую задачу: резолвить телефон в user_id, "
                "добавить в allowlist с контекстом, отправить opening-сообщение."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "+79..."},
                    "task_type": {
                        "type": "string",
                        "enum": ["booking", "meeting", "follow-up", "info", "scheduling", "general"],
                    },
                    "details": {"type": "string", "description": "Контекст задачи для LLM"},
                },
                "required": ["phone", "task_type", "details"],
            },
        ),
        Tool(
            name="max_history",
            description="Получить N последних сообщений из чата с указанным user_id.",
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
            description="Резолвить телефон в user_id и профиль контакта.",
            inputSchema={
                "type": "object",
                "properties": {
                    "phone": {"type": "string"},
                },
                "required": ["phone"],
            },
        ),
        Tool(
            name="max_allow",
            description="Добавить user_id в allowlist для авто-ответа, опционально с контекстом.",
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
                "properties": {
                    "user_id": {"type": "integer"},
                },
                "required": ["user_id"],
            },
        ),
        Tool(
            name="max_block",
            description="Жёсткий блок: listener молча игнорит сообщения от user_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer"},
                },
                "required": ["user_id"],
            },
        ),
    ]


# Минимальные реализации. Реальная подключка к user_api_send.py / listener
# зависит от вашего деплоя (отдельный воркер, очередь, локальный socket-call).


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


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "max_send":
        target = arguments["target"]
        text = arguments["text"]
        # TODO: вызвать user_api_send.send_message_to_user
        return [TextContent(type="text", text=f"stub: max_send target={target} text={text!r}")]

    if name == "max_engage":
        phone = arguments["phone"]
        task_type = arguments["task_type"]
        details = arguments["details"]
        # TODO: resolve phone, update allowlist with context, send opening
        return [TextContent(
            type="text",
            text=f"stub: engage phone={phone} type={task_type} details={details!r}",
        )]

    if name == "max_history":
        user_id = arguments["user_id"]
        count = arguments.get("count", 10)
        # TODO: opcode 49 CHAT_HISTORY
        return [TextContent(type="text", text=f"stub: history user_id={user_id} count={count}")]

    if name == "max_contact":
        phone = arguments["phone"]
        # TODO: opcode 46 CONTACT_INFO_BY_PHONE
        return [TextContent(type="text", text=f"stub: contact phone={phone}")]

    if name == "max_allow":
        user_id = int(arguments["user_id"])
        task_type = arguments.get("task_type")
        details = arguments.get("details")
        data = _load_allowlist()
        ids = set(data.get("autoreply", []))
        ids.add(user_id)
        data["autoreply"] = sorted(ids)
        if task_type or details:
            ctxs = data.get("contexts") or {}
            ctxs[str(user_id)] = {"type": task_type or "general", "details": details or ""}
            data["contexts"] = ctxs
        _save_allowlist(data)
        return [TextContent(type="text", text=f"allowed user_id={user_id}")]

    if name == "max_disallow":
        user_id = int(arguments["user_id"])
        data = _load_allowlist()
        ids = set(data.get("autoreply", []))
        ids.discard(user_id)
        data["autoreply"] = sorted(ids)
        _save_allowlist(data)
        return [TextContent(type="text", text=f"disallowed user_id={user_id}")]

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
        return [TextContent(type="text", text=f"blocked user_id={user_id}")]

    return [TextContent(type="text", text=f"unknown tool: {name}")]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
