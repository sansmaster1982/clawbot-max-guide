# clawbot-max-guide

Каркас инструкции из статьи [AI пишет в MAX вместо меня. Гайд: Bot API + User API + MCP](https://habr.com/) (ссылка появится после публикации).

Это **не production-код**, это упрощённые рабочие примеры, на которых можно собрать собственный AI-секретарь в MAX. Без OpenRouter-ключей, MAX-токенов, личных user_id. Использование на свой страх и риск.

## Структура документации

| Файл | Когда читать |
|------|--------------|
| **[INSTRUCTIONS.md](INSTRUCTIONS.md)** | Хочу собрать своё, пошагово, со всеми граблями и правовой обвязкой |
| [LEGAL.md](LEGAL.md) | Хочу понять что можно, что нельзя, и почему |
| [examples/](examples/) | Готовые упрощённые скрипты, копировать-править-запускать |
| README (этот файл) | Обзор и quick start |

## Что строим

```
                  Я → Bot API → @my-max-bot                ┐
                                                            ├── два входа
                  Я → @my-telegram-bot (Telegram)          ┘
                                   ↓
                        LLM-агент (один воркер)
                                   ↓
                       intent classifier (перехват команд → MCP)
                                   ↓
                                  MCP-сервер (7 тулов)
                                                  ↓
                                  MAX User API (secondary SIM)
                                                  ↓
                          persistent listener на TLS-сокете
                                                  ↓
                          LLM → ответ собеседнику
                                                  ↓
                          копия → @my-max-bot → я
```

Результат: я говорю боту в MAX «забронируй столик у +79..., завтра 19:00, на двоих», он отправляет, ведёт переписку до подтверждения, копию каждой реплики дублирует в мой служебный чат.

## Что внутри

| Файл | Что показывает | Соответствие частям статьи |
|------|----------------|----------------------------|
| `examples/bot_api_polling.py`     | Long polling через `platform-api.max.ru`, авторизация без `Bearer` | Часть 1 |
| `examples/user_api_send.py`       | Отправка через User API с обязательным `cid` (грабля №1) | Части 3-5 |
| `examples/user_api_listener.py`   | Listener c `interactive: True`, парсинг op=128 (грабля №2) | Части 6-7 |
| `examples/intent_classifier.py`   | Intercept команд перед агентом | Часть 10 |
| `examples/mcp_server_stub.py`     | Каркас MCP-сервера на 7 тулов | Часть 9 |
| `examples/notify_owner.py`        | Дублирование переписки хозяину через Bot API | Часть 11 |
| `examples/allowlist.example.json` | Структура allowlist для авто-ответа/форвардинга/блока | Часть 11 |

## Quick start

**Минимальные требования.** Python 3.11+, `httpx`, `msgpack`. Секонд-симка для User API (не основной номер). Для Bot API — верифицированное юрлицо РФ или резидент-ИП ([правила MAX, август 2025](https://habr.com/ru/articles/951326/)).

**1. Авторизуйте секонд-аккаунт по SMS.** Здесь не показано, готовый flow есть в [vkmax](https://github.com/nsdkinx/vkmax) (`auth.py`) или [PyMax](https://github.com/MaxApiTeam/PyMax). После прохождения SMS-логина положите токен:

```bash
mkdir -p ~/.config/clawbot-max
echo "<your-max-user-token>" > ~/.config/clawbot-max/token.txt
chmod 600 ~/.config/clawbot-max/token.txt
```

**2. Установите зависимости:**

```bash
pip install -r examples/requirements.txt
```

**3. Запустите listener:**

```bash
export MAX_SELF_USER_ID=<your-secondary-account-user-id>
python examples/user_api_listener.py
```

Listener держит постоянный TLS-сокет, ловит входящие через push-event op=128, логирует в stdout. LLM-вызов в `call_your_llm()` — заглушка, замените на свой провайдер.

**4. Скопируйте `allowlist.example.json` в `allowlist.json`** (он в gitignore) и впишите туда user_id'ы, которым бот авто-отвечает с контекстом.

**5. Если хотите Bot API для дублирования** (опционально, требует юрлица) — `examples/notify_owner.py` показывает шаблон.

**6. Если хотите подключить как MCP-сервер к Claude Desktop/Cursor/etc** — `examples/mcp_server_stub.py` показывает структуру семи тулов. Доводите до рабочего MCP в зависимости от вашего стека.

## Жирная красная линия

Перед использованием прочитайте [LEGAL.md](LEGAL.md). Коротко:

- Только секонд-симка, только собственные контакты, только бытовая автоматизация для одного физлица
- Коммерческая рассылка, спам, автообзвон, автоматизация от лица других людей — категорически нет. Это уже 152-ФЗ, 38-ФЗ, ст. 159 УК
- Для бизнес-сценариев — через юрлицо и официальный Bot API

## Полные реализации протокола MAX

- [nsdkinx/vkmax](https://github.com/nsdkinx/vkmax) — активный, документация опкодов в `/docs/opcodes.md`
- [rast-games/pyromax](https://github.com/rast-games/pyromax) — aiogram-like, alpha
- [MaxApiTeam/PyMax](https://github.com/MaxApiTeam/PyMax) — заархивирован Feb 2026, но рабочий
- [Sharkow1743/MaxAPI](https://github.com/Sharkow1743/MaxAPI) — ещё одна Python-реализация
- [koval01/gist](https://gist.github.com/koval01/b0baae9a9a0ee4f0d65c0d5377d9b243) — анти-бот эвристики MAX

## Официальные SDK Bot API

- [max-messenger/max-botapi-python](https://github.com/max-messenger/max-botapi-python)
- [max-messenger/max-bot-api-client-ts](https://github.com/max-messenger/max-bot-api-client-ts)
- [max-messenger/max-bot-api-client-go](https://github.com/max-messenger/max-bot-api-client-go)

## Лицензия

MIT, см. [LICENSE](LICENSE).
