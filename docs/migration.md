# Extracting the bot applications

**Document version:** 0.1 · **Last updated:** 2026-10-08 · **Status:** 🔴 UNDER REVIEW

The owner selected a separate repository on 2026-10-08 to decouple bot releases
from server releases. The initial source is kajenn commit
`800b593` on `codex/whatsapp-bot`, including the Telegram fixes from `7c4de86`.
This repository starts its own history and distribution version at `0.1.0`.
The implementation source remains available in the original repository history.

Install this package alongside kajenn and update imports and configured class paths:

| Source checkout import | Standalone import |
|---|---|
| `kajenn.applications.TelegramBotApplication` | `kajenn_bot_application.TelegramBotApplication` |
| `kajenn.applications.WhatsAppBotApplication` | `kajenn_bot_application.WhatsAppBotApplication` |
| `kajenn.applications.bot.BotBaseApplication` | `kajenn_bot_application.bot.BotBaseApplication` |
| `kajenn.applications.telegram.TelegramBotInstanceGrammar` | `kajenn_bot_application.telegram.TelegramBotInstanceGrammar` |
| `kajenn.applications.whatsapp.WhatsAppBotInstanceGrammar` | `kajenn_bot_application.whatsapp.WhatsAppBotInstanceGrammar` |

There are no compatibility exports inside kajenn: those would invert the dependency.

Application codes, bot codes, persistence operation names, task names, record
schemas and webhook paths are unchanged. Keep existing application codes when
reusing a registry. User-defined bot classes keep their own import paths; move
those packages only if you also migrate the persisted `bot_class` references.
The example package paths remain `examples.telegram_bot` and `examples.whatsapp_bot`.

The server lifecycle annotation change from the source branch is not part of this
package. Async startup and shutdown already work with the released kajenn server.
