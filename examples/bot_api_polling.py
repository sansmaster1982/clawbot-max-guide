"""Bot API: long polling + send through platform-api.max.ru.

Эту часть гайда можно повторить только при наличии верифицированного
юрлица РФ или резидент-ИП (правила MAX с августа 2025).

Регистрация бота: https://business.max.ru
Документация Bot API: https://dev.max.ru/docs-api

Авторизация: header 'Authorization: <token>' БЕЗ Bearer.
Старый формат '?access_token=' возвращает 401.
"""
import asyncio
import os

import httpx

BASE_URL = "https://platform-api.max.ru"
BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN", "<your bot token>")
OWNER_USER_ID = int(os.environ.get("MAX_OWNER_USER_ID", "0"))


async def get_updates(client: httpx.AsyncClient, marker: int | None) -> dict:
    """Long-poll for updates. Returns parsed JSON with 'updates' and 'marker'."""
    params = {"timeout": 30}
    if marker is not None:
        params["marker"] = marker
    r = await client.get(
        f"{BASE_URL}/updates",
        params=params,
        headers={"Authorization": BOT_TOKEN},
    )
    r.raise_for_status()
    return r.json()


async def send_message(client: httpx.AsyncClient, user_id: int, text: str) -> dict:
    """Send a text message to a user that previously messaged the bot.

    Bot API DOES NOT allow writing to users who haven't started a conversation
    with the bot. For initiating contact, use User API (see user_api_send.py).
    """
    r = await client.post(
        f"{BASE_URL}/messages",
        params={"user_id": user_id},
        headers={"Authorization": BOT_TOKEN},
        json={"text": text},
    )
    r.raise_for_status()
    return r.json()


async def handle_update(client: httpx.AsyncClient, update: dict) -> None:
    """Minimal echo handler — replace with your routing logic."""
    if update.get("update_type") != "message_created":
        return
    msg = update.get("message", {})
    sender_id = msg.get("sender", {}).get("user_id")
    text = msg.get("body", {}).get("text", "")
    if not text or not sender_id:
        return
    print(f"[bot] message from {sender_id}: {text!r}")
    await send_message(client, sender_id, f"echo: {text}")


async def main() -> None:
    if BOT_TOKEN.startswith("<"):
        raise SystemExit("Set MAX_BOT_TOKEN environment variable")
    marker: int | None = None
    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            try:
                result = await get_updates(client, marker)
            except httpx.HTTPError as e:
                print(f"[bot] getUpdates failed: {e!r}, retrying in 5s")
                await asyncio.sleep(5)
                continue
            new_marker = result.get("marker")
            if new_marker is not None:
                marker = new_marker
            for update in result.get("updates", []):
                try:
                    await handle_update(client, update)
                except Exception as e:
                    print(f"[bot] handler failed: {e!r}")


if __name__ == "__main__":
    asyncio.run(main())
