"""Bot API mirror: дублировать переписку секонд-аккаунта в служебный чат.

Идея: listener после каждого сгенерированного ответа отправляет копию
через Bot API в чат с твоим управляющим ботом, чтобы ты видел диалог
не открывая чат с собеседником.

Требует:
  - Bot API токен (юрлицо РФ или резидент-ИП, регистрация на business.max.ru)
  - user_id хозяина в MAX

Если у тебя нет Bot API доступа, эту часть можно заменить на:
  - SMTP-уведомление через msmtp
  - запись в локальный лог-файл
  - HTTP POST в твой Telegram-бот
"""
import os

import httpx

BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN", "")
OWNER_USER_ID = int(os.environ.get("MAX_OWNER_USER_ID", "0"))
BASE_URL = "https://platform-api.max.ru"


def notify_owner(text: str) -> bool:
    """Best-effort forward to the owner's MAX chat.

    Не блокирует основной поток listener'а. Если Bot API лежит или
    юзер ещё не написал нашему боту первым, возвращает False —
    logика listener'а продолжает работать.
    """
    if not BOT_TOKEN or not OWNER_USER_ID:
        return False
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(
                f"{BASE_URL}/messages",
                params={"user_id": OWNER_USER_ID},
                headers={"Authorization": BOT_TOKEN},  # без Bearer
                json={"text": text},
            )
            if r.status_code >= 400:
                print(f"[notify] HTTP {r.status_code}: {r.text[:200]}")
                return False
            return True
    except httpx.HTTPError as e:
        print(f"[notify] failed: {e!r}")
        return False


def format_mirror(sender_id: int, incoming_text: str, reply_text: str) -> str:
    """Формат для дублирования. Адаптируйте под себя."""
    return (
        f"От {sender_id}: {incoming_text}\n"
        f"Ассистент: {reply_text}"
    )


def format_unknown_sender(sender_id: int, incoming_text: str) -> str:
    """Когда пишет незнакомец (не в allowlist), бот не отвечает,
    но хозяину уведомляет — пусть решает сам."""
    return (
        f"Незнакомец {sender_id} пишет секонд-аккаунту:\n"
        f"«{incoming_text}»\n"
        f"Авто-ответ выключен. Команды:\n"
        f"  !reply <текст>           — ответить от лица секретаря\n"
        f"  !block {sender_id}       — заблокировать молча\n"
        f"  !allow {sender_id}       — разрешить авто-ответ"
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        ok = notify_owner(sys.argv[1])
        print("OK" if ok else "FAILED")
    else:
        print("Usage: python notify_owner.py '<text>'")
