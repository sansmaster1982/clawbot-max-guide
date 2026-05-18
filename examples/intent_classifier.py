"""Intent classifier перед LLM-агентом.

Идея из части 10 статьи: LLM-агенты периодически галлюцинируют команды.
Когда хозяин пишет «забронируй столик у +79...», агент может выдумать
несуществующий флаг или ответить вместо выполнения.

Лёгкий классификатор намерений перед агентом ловит типовые команды
и зовёт MCP-tool напрямую, минуя агента. Только chat-режим попадает
к агенту.

Это пример. В production используйте более точный классификатор
(BERT/RoBERTa fine-tune или GPT-4o-mini в JSON-mode).
"""
import json
import os
import re
from dataclasses import dataclass
from typing import Literal

import httpx

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = os.environ.get("INTENT_MODEL", "openai/gpt-4o-mini")


IntentType = Literal[
    "chat",          # обычный разговор, отдать агенту
    "send",          # «напиши X текст Y»
    "engage",        # секретарская задача (бронь/встреча/follow-up)
    "block",         # «заблокируй X» или «заблокируй последнего»
    "unblock",       # «разблокируй X»
    "need_info",     # классификатор не уверен или не хватает данных
]


@dataclass
class Intent:
    type: IntentType
    target: str | int | None = None
    text: str | None = None
    phone: str | None = None
    task_type: str | None = None
    details: str | None = None
    question: str | None = None


SYSTEM_PROMPT = (
    "Ты парсер команд от хозяина AI-секретаря. Он пишет на русском в чат-окно. "
    "Верни СТРОГО JSON без markdown.\n\n"
    "Варианты intent:\n"
    "1. 'chat' — обычный разговор. JSON: {\"intent\":\"chat\"}\n"
    "2. 'send' — отправить кому-то текст. "
    "   JSON: {\"intent\":\"send\",\"target\":<user_id>|<+79...>,\"text\":\"...\"}\n"
    "3. 'engage' — секретарская задача (бронь/встреча/follow-up). "
    "   Поля: phone (+7...), type (booking/meeting/follow-up/info), details. "
    "   JSON: {\"intent\":\"engage\",\"phone\":\"+79...\",\"type\":\"booking\",\"details\":\"...\"}\n"
    "4. 'block' — заблокировать. "
    "   JSON: {\"intent\":\"block\",\"target\":<user_id>|\"last\"}\n"
    "5. 'unblock' — разблокировать. "
    "   JSON: {\"intent\":\"unblock\",\"target\":<user_id>}\n"
    "6. 'need_info' — не хватает данных или классификатор не уверен. "
    "   JSON: {\"intent\":\"need_info\",\"question\":\"...\"}\n\n"
    "Никаких объяснений, только JSON."
)


def classify(text: str) -> Intent:
    """Прогон через LLM. Без LLM-fallback по regex — простой и стабильный."""
    if not OPENROUTER_KEY:
        return Intent(type="chat")
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 500,
                    "response_format": {"type": "json_object"},
                },
            )
            r.raise_for_status()
            data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        # Достаём JSON даже если модель обернула в markdown
        match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", content, re.DOTALL)
        if not match:
            return Intent(type="chat")
        parsed = json.loads(match.group(0))
        return Intent(
            type=parsed.get("intent", "chat"),
            target=parsed.get("target"),
            text=parsed.get("text"),
            phone=parsed.get("phone"),
            task_type=parsed.get("type"),
            details=parsed.get("details"),
            question=parsed.get("question"),
        )
    except Exception as e:
        print(f"[intent] classify failed: {e!r}")
        return Intent(type="chat")


def route(intent: Intent) -> str:
    """Демо-маршрутизация. В реальном listener'е здесь вызовы MCP-tools."""
    if intent.type == "chat":
        return "(passed to LLM agent)"
    if intent.type == "send":
        return f"MCP: max_send(target={intent.target}, text={intent.text!r})"
    if intent.type == "engage":
        return (
            f"MCP: max_engage(phone={intent.phone}, "
            f"type={intent.task_type}, details={intent.details!r})"
        )
    if intent.type == "block":
        return f"MCP: max_block(target={intent.target})"
    if intent.type == "unblock":
        return f"MCP: max_disallow(target={intent.target})"
    if intent.type == "need_info":
        return f"REPLY to owner: {intent.question!r}"
    return f"(unknown intent: {intent.type})"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python intent_classifier.py '<owner-message>'")
        sys.exit(1)
    msg = " ".join(sys.argv[1:])
    intent = classify(msg)
    print(f"Intent: {intent}")
    print(f"Action: {route(intent)}")
