# Choose an integration

**Document version:** 0.1 · **Last updated:** 2026-10-10 · **Status:** UNDER REVIEW

The repository contains four integrations. Choose by the identity that performs
the operation, not by whether the caller uses REST, MCP or Python.

| Integration | Identity and connection | Availability | Start here |
|---|---|---|---|
| `TelegramBotApplication` | BotFather bot token; HTTPS webhook for receiving | Exported by the package | [Telegram bot](guides/telegram.md) |
| `WhatsAppBotApplication` | Business number and Meta Cloud API credentials; HTTPS webhook | Exported by the package | [WhatsApp Business bot](guides/whatsapp.md) |
| `TelegramAccountApplication` | Personal Telegram session through MTProto; local enrollment | Base in 0.2.0b1; 37-tool expansion in this source branch | [Telegram account](guides/telegram-account.md) |
| `examples.whatsapp_account.application.WhatsAppAccountApplication` | Personal WhatsApp linked device through the patched native Tryx library | Source-tree prototype, 76 tools; absent from the wheel | [WhatsApp account](guides/whatsapp-account-prototype.rst) |

`BotBaseApplication` supplies shared bot behavior. Personal account applications
have their own lifecycle, permissions and storage; they do not inherit bot
registration or bot conversations. A personal account session is not a BotFather
token or a WhatsApp Business credential.

## Configuration and operation

| Concern | Telegram bot | WhatsApp Business bot | Telegram account | WhatsApp account prototype |
|---|---|---|---|---|
| Multiple identities | Register independent bot instances | Register independent business numbers | Separate application mounts, sessions and keys | Separate mounts and paired directories |
| Receive | Bot webhook | Verified business webhook | Connected MTProto client | Connected linked device |
| Local sending | Send-only application with the same bot token | Send-only application with business credentials | Call the application owning the session | Call the application owning the linked device |
| REST operations | `/<mount>/_admin/<operation>` | `/<mount>/_admin/<operation>` | `/<mount>/_account/<operation>`; policy/revocation under `_admin` | `/<mount>/_account/<operation>` |
| MCP endpoint | `/<mount>/_mcp` | `/<mount>/_mcp` | `/<mount>/_mcp` | `/<mount>/_mcp` |
| Remote access control | Server `admin` avatar role | Server `admin` avatar role | Avatar roles plus operation/chat policy | Read/write/manage/admin avatar roles plus operation/chat policy |
| Persistence | One persistence route per application | One persistence route per application | Encrypted session and journal files | Private SQLite files, unencrypted at rest |
| History | No general group-history API | No general personal-chat archive | Bounded provider history reads | Locally observed/synchronized subset; history requests are not a completeness guarantee |

Bot examples use encrypted filesystem registries, but a custom persistence route
chooses its own backend and security. Task spool storage is a separate concern.
See [persistence](guides/persistence.md) and [deployment](guides/operations.md).
A single account session or device directory has one active owner; clients call
that owner instead of opening concurrent copies of its state.

## Approval and scheduling differ

- **Bot admission** controls whether someone joins a bot's workflow. It is not an
  approval requirement for every outbound message. See [conversations](guides/conversations.md).
- **Telegram account text requests** require an administrator's decision and send
  immediately on approval. An approval-only policy omits direct sending grants.
  Native Telegram scheduling is separate and can deliver while the app is stopped.
- **WhatsApp account scheduling** is a local persistent text queue. Approval is an
  optional argument, enabled by default; it is not a mandatory organization-wide
  rule. Dispatch requires the app to run and rechecks operation/chat grants.

For all four, distinguish API acceptance from confirmed recipient delivery.
After an uncertain mutation, inspect the outcome before repeating it. Provider
rights and availability still apply even when local policy allows an operation.

## Documentation and release versions

These pages describe the source revision used by their documentation build.
A branch push does not publish a new PyPI version or guarantee a new public
Read the Docs build. Use the version selector and build revision when comparing
online documentation with an installation. The release tag `v0.2.0b1` contains
the initial personal Telegram commands, not the expanded branch's full set.
The personal WhatsApp prototype requires its source checkout and patched native
dependency; installing the package wheel alone cannot start it.

The [application API](api.rst) documents exported classes. The
[WhatsApp account tool reference](guides/whatsapp-account-tools.rst) documents all
76 prototype routes without requiring its native dependency during a docs build.
