# kajenn-bot-application

**Version:** 0.1 · **Last updated:** 2026-10-08 · **Status:** 🔴 DA REVISIONARE

Telegram and WhatsApp bot applications for [kajenn](https://github.com/kajenn-org/kajenn),
with their own package, tests and release cycle. Based on genropy history and genro-modules.

`BotBaseApplication` provides bot registration, conversations, optional administrator
approval, announcements and reminders. `TelegramBotApplication` and
`WhatsAppBotApplication` provide each platform's webhook and outbound API.

Multiple bot instances can share a class while keeping separate credentials,
configuration and datasets. A central deployment receives webhooks; a local
deployment can send directly using the same application with send-only configuration.
Persistence is delegated to one application-level route. Encrypted filesystem
providers are included as examples.

## Install

From a checkout, with Python 3.11 or newer:

```bash
python -m pip install .
```

The distribution is `kajenn-bot-application`; the import package is
`kajenn_bot_application`:

```python
from kajenn_bot_application import TelegramBotApplication, WhatsAppBotApplication
```

The package depends on `kajenn>=0.3.0`. The server does not depend on this package.
Installation does not require a kajenn source checkout or changes to the server.

## Documentation and examples

- [Shared architecture and capabilities](docs/guides/bots.md)
- [Telegram configuration, persistence and APIs](docs/guides/telegram.md)
- [WhatsApp configuration, templates and delivery receipts](docs/guides/whatsapp.md)
- [Package boundary, development and releases](docs/development.md)
- [Extraction and import migration](docs/migration.md)

Run the recipes from the repository root after configuring the secrets described
in each guide:

```bash
kajenn serve examples/telegram_bot/config.py
kajenn serve examples/whatsapp_bot/config.py
```

Each example also has `local_config.py` for sending without a webhook. Platform
limits remain distinct: Telegram private chats require prior user interaction;
WhatsApp notifications outside the service window require approved templates.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,docs]"
git config core.hooksPath hooks
pytest -q
ruff check src tests examples tools
python tools/check_boundary.py
sphinx-build -W -b html docs docs/_build/html
python -m build
```

Tests use real kajenn routing, tasks and encrypted storage; only provider HTTP
calls are mocked. No Telegram or Meta credentials are needed to run the suite.

Licensed under Apache-2.0. Copyright Softwell S.r.l.
