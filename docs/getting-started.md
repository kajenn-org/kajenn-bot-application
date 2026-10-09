# Getting started

**Document version:** 0.2 · **Last updated:** 2026-10-08 · **Status:** 🔴 UNDER REVIEW

This guide takes a fresh checkout to a working bot. Choose Telegram for a short
first run, or WhatsApp when you already have a Meta business app and test number.
Both examples mount a bot application and an encrypted filesystem registry.

## Install the package

```bash
git clone https://github.com/kajenn-org/kajenn-bot-application.git
cd kajenn-bot-application
python -m venv .venv
source .venv/bin/activate
python -m pip install .
```

These are POSIX shell commands. On Windows, use `.venv\Scripts\Activate.ps1`
and the equivalent environment-variable syntax in PowerShell.
Installation brings in `kajenn>=0.4.1`; no sibling server checkout is required.
The examples are part of the source checkout, not the installed wheel.

## Prepare durable storage

Set `GENRO_STORAGE_KEY` to a Fernet key from your secret store. To generate a new
key once for a disposable development installation:

```bash
export GENRO_STORAGE_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

Keep that same key for every restart using the same registry. Save it securely
before closing the shell; generating a different key will not decrypt existing
files. The example registry encrypts registrations and conversation records.
The task spool has a separate storage policy; see [operations](guides/operations.md).

Use a separate storage directory for each deployment. Do not run a central
receiver and a local sender against the same filesystem registry.

## Telegram: first reply

1. Create a bot with Telegram's [BotFather](https://core.telegram.org/bots/tutorial#obtain-your-bot-token).
2. Arrange a public HTTPS endpoint forwarding `/telegram` and its subpaths to the
   local server. The application does not create a tunnel or terminate TLS.
3. Supply the token and the **application mount URL**, without `/alpha`:

   ```bash
   export KAJENN_TELEGRAM_ALPHA_TOKEN='your-bot-token'
   export KAJENN_TELEGRAM_WEBHOOK_URL='https://bots.example.com/telegram'
   kajenn serve examples/telegram_bot/config.py --port 8000
   ```

4. Open the bot in Telegram and send `/hello`. The reply is `Hello [alpha]`.
   `/echo Your message` returns `Your message`.

Startup restores saved registrations, then registers `alpha` from the environment
if it is absent. Registration verifies the token and installs a webhook at
`https://bots.example.com/telegram/alpha` with its own secret.
No polling process is needed.

To add a second instance of the same class, set `KAJENN_TELEGRAM_BETA_TOKEN` to a
**different bot's token** and restart. Its `/hello` response contains `[beta]`.
The dataset is an example label; your bot can use its configuration to choose a
real application dataset. See [writing a bot](guides/writing-bots.md).

## WhatsApp: first reply

First provision a Meta app, business account, phone number and access token.
The package uses your existing credentials; it does not perform Embedded Signup
or provision these resources. Choose a supported Graph API version explicitly.

| Environment variable | Purpose |
|---|---|
| `GENRO_STORAGE_KEY` | Stable encryption key for the example registry |
| `KAJENN_WHATSAPP_API_VERSION` | Explicit Graph version, for example `v25.0` |
| `KAJENN_WHATSAPP_TOKEN` | Access token authorized for the business number |
| `KAJENN_WHATSAPP_PHONE_NUMBER_ID` | Meta's phone-number ID, not the displayed telephone number |
| `KAJENN_WHATSAPP_BUSINESS_ACCOUNT_ID` | Meta's business-account ID |
| `KAJENN_WHATSAPP_WEBHOOK_URL` | Public HTTPS mount, such as `https://bots.example.com/whatsapp` |
| `KAJENN_WHATSAPP_APP_SECRET` | Meta app secret used to verify webhook signatures |
| `KAJENN_WHATSAPP_VERIFY_TOKEN` | A separate value you choose for webhook verification |

Set these through your environment or secret manager, then run:

```bash
kajenn serve examples/whatsapp_bot/config.py --port 8000
```

In Meta's app dashboard, configure the callback URL and the matching verify token,
and subscribe the relevant business account to the `messages` webhook field.
The callback is the mount root, **without `/team`**. Registration creates the
local `team` instance but does not create that remote subscription.

Send `/hello` or `/echo Your message` from an opted-in test recipient. The inbound
message opens the tracked customer service window, allowing the reply. For a
business-initiated notification outside that window, use an approved template.
The [WhatsApp guide](guides/whatsapp.md) covers permissions, templates and receipts.

## Send from a local service

Run the matching `local_config.py` recipe in a separate deployment and storage
namespace. Supply the same bot credentials and a stable local encryption key:

```bash
kajenn serve examples/telegram_bot/local_config.py --port 8001
# Or:
kajenn serve examples/whatsapp_bot/local_config.py --port 8001
```

These recipes register the sending application but send nothing automatically.
From a job or handler with access to the initialized server:

```python
telegram = server.applications["telegram"]
await telegram.send_message("alpha", recipient_chat_id, "You have a new PR")
```

The local service needs credentials and the destination ID. Telegram recipients
must already be reachable by that bot. Incoming replies still arrive centrally;
the sender never changes the webhook. A separate WhatsApp registry does not know
the central receiver's service windows: use `send_template`, or expose a trusted
window view through application persistence.

## Next steps

- [Write commands and define per-instance settings](guides/writing-bots.md).
- [Start a conversation with multiple participants](guides/conversations.md).
- [Choose and implement a persistence provider](guides/persistence.md).
- [Deploy, inspect failures and recover safely](guides/operations.md).
