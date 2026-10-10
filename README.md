# kajenn-bot-application

**Document version:** 0.4 · **Last updated:** 2026-10-09 · **Status:** 🔴 UNDER REVIEW

[![Tests](https://github.com/kajenn-org/kajenn-bot-application/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/kajenn-org/kajenn-bot-application/actions/workflows/tests.yml)
[![Documentation](https://readthedocs.org/projects/kajenn-bot-application/badge/?version=latest)](https://kajenn-bot-application.readthedocs.io/en/latest/)
[![Coverage](https://codecov.io/gh/kajenn-org/kajenn-bot-application/branch/main/graph/badge.svg)](https://app.codecov.io/gh/kajenn-org/kajenn-bot-application)
[![PyPI](https://img.shields.io/pypi/v/kajenn-bot-application)](https://pypi.org/project/kajenn-bot-application/)
[![Python](https://img.shields.io/badge/python-3.11%E2%80%933.14-blue)](https://github.com/kajenn-org/kajenn-bot-application/blob/main/pyproject.toml)
[![License](https://img.shields.io/github/license/kajenn-org/kajenn-bot-application)](LICENSE)

**Run Telegram and WhatsApp bots as kajenn applications. Receive centrally, send
from local services, and keep each bot's configuration and conversations separate.**

Use it for developer notifications, review conversations, administrator-approved
access, announcements and scheduled follow-ups. Bot behavior lives in ordinary
`RoutingClass` methods; configuration uses class-owned grammars.

[Read the documentation](https://kajenn-bot-application.readthedocs.io/en/latest/)
· [Getting started](https://kajenn-bot-application.readthedocs.io/en/latest/getting-started.html)
· [API reference](https://kajenn-bot-application.readthedocs.io/en/latest/api.html)

## What it provides

| Capability | Telegram | WhatsApp Business |
|---|---|---|
| Receive events | Secret-checked webhook | Signed webhook and verification handshake |
| Send from a local service | Same bot token; known reachable chat | Same business credentials; template or known service window |
| Multiple bot instances | Independent BotFather tokens and configuration | Independent business numbers and configuration |
| Conversations | Multiple participants, correlated replies and buttons | Multiple participants, correlated replies and buttons |
| Optional admission | First/all administrator approval; edits settled requests | First/all approval; follow-up notices and optional templates |
| Outbound content | Text, files, media, typing and native polls | Text, files, media, buttons and approved templates |
| Background work | Queued announcements and persistent reminders | Queued announcements and persistent reminders |
| Administration | REST/OpenAPI and MCP with server admin authorization | REST/OpenAPI and MCP with server admin authorization |
| Delivery information | Send API result and task outcome | API acceptance plus webhook receipts |

`BotBaseApplication` owns shared registration, conversation and task behavior.
`TelegramBotApplication` and `WhatsAppBotApplication` preserve each provider's
capabilities and constraints. Persistence is delegated to one route per application;
encrypted filesystem providers are included in the examples.

## Personal Telegram accounts

`TelegramAccountApplication` is a separate application in this same package.
It connects a personal account through MTProto and exposes permitted history,
search, sending, channel/group creation and membership administration through
REST/MCP. Sessions are encrypted locally; grants default to deny. Login stays
local and policy management is restricted to an administrator.

See the [personal account guide](docs/guides/telegram-account.md). Personal account
access and REST/MCP bot administration are included in the **0.2.0b1 beta**.

## Personal WhatsApp account prototype

The source-tree `examples/whatsapp_account` application exposes 76 authenticated
REST/MCP tools for contacts, synchronized messages, replies, media, chat state,
groups, channels, communities, polls, events, profile, privacy, policy and audit.
It includes scheduled texts with optional approval, an event journal, multiple
account mounts, optional local voice transcription and in-process event subscriptions without polling. It uses a pinned, locally patched Tryx build and
is **not included in the published wheel**. Device and directory storage use
private filesystem permissions, without encryption at rest.

See the [prototype guide](docs/guides/whatsapp-account-prototype.rst) for setup,
command contracts, permission rules and history coverage limits.

## Install

Python **3.11–3.14** is tested. `kajenn>=0.4.1` is installed as a dependency.
Bot versions and releases are independent from the server.

Install the beta in a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install "kajenn-bot-application==0.2.0b1"
```

The beta requires an explicit version or pip's `--pre` option; an unqualified
installation continues to select the stable release.

To run the included examples or contribute, clone the repository and install it:

```bash
git clone https://github.com/kajenn-org/kajenn-bot-application.git
cd kajenn-bot-application
python -m pip install .
```

On Windows, activate the environment with `.venv\Scripts\Activate.ps1`.
The distribution is named `kajenn-bot-application`; Python imports use
`kajenn_bot_application`. No kajenn source checkout is needed.

```python
from kajenn_bot_application import TelegramBotApplication, WhatsAppBotApplication
```

## Run your first bot

The [getting started guide](https://kajenn-bot-application.readthedocs.io/en/latest/getting-started.html)
walks through credentials, persistent storage, the public webhook and the first
`/hello` reply. From the repository root, after configuring that environment:

```bash
kajenn serve examples/telegram_bot/config.py --port 8000
# Or run the WhatsApp example:
kajenn serve examples/whatsapp_bot/config.py --port 8000
```

Both examples provide `/hello` and `/echo`. Each has a `local_config.py` recipe
that sends directly without replacing the central webhook. Within an initialized
application, a local job can notify a user:

```python
telegram = server.applications["telegram"]
await telegram.send_message("alpha", mario_chat_id, "You have a new PR")
```

The bot must already be registered, and Mario must have started a private chat
with it. The local service needs the bot token, not only the chat ID. Replies go
to the central webhook. WhatsApp notifications use approved templates outside
the known customer service window.

## Learn and deploy

| Task | Guide |
|---|---|
| Install, configure and get the first reply | [Getting started](https://kajenn-bot-application.readthedocs.io/en/latest/getting-started.html) |
| Understand classes, instances and application mounts | [Architecture](docs/guides/bots.md) |
| Write commands and instance configuration | [Writing a bot](docs/guides/writing-bots.md) |
| Coordinate concurrent requests and approvals | [Conversation recipes](docs/guides/conversations.md) |
| Configure Telegram or WhatsApp features | [Telegram](docs/guides/telegram.md) · [WhatsApp](docs/guides/whatsapp.md) |
| Run, recover and diagnose a deployment | [Operations](docs/guides/operations.md) |
| Replace filesystem storage with application persistence | [Persistence](docs/guides/persistence.md) |
| Move imports from the original kajenn branch | [Migration](docs/migration.md) |

## Scope and delivery guarantees

The bot applications use official bot/business APIs. Personal-account access is
a separate application surface with its own permissions and history limitations.
Admission does not provision application users or grant kajenn router permissions.

The filesystem examples support **one receiving process per registry**. Webhook
acknowledgement means work has been staged, not that a reply was delivered.
Provider sends and persistence writes are separate operations; delivery is not
exactly once. See the operations guide before replaying failed or uncertain work.

## Contributing

```bash
python -m pip install -e ".[dev,docs]"
git config core.hooksPath hooks
pytest -q
ruff check src tests examples tools
python tools/check_boundary.py
sphinx-build -W -b html docs docs/_build/html
```

CI installs the built wheel, runs the integration suite on Python 3.11–3.14,
checks the minimum kajenn version, uploads coverage to Codecov and builds the
documentation with warnings treated as errors. Provider HTTP is mocked; routing,
tasks and encrypted persistence use the real server services.

Changes go through pull requests to `develop`. See
[development and releases](docs/development.md) for the package boundary and checks.

Apache-2.0 · Copyright Softwell S.r.l. · Based on genropy history and genro-modules.
