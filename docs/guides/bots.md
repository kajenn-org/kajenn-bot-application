# Architecture

**Document version:** 0.1 · **Last updated:** 2026-10-08 · **Status:** 🔴 UNDER REVIEW

`BotBaseApplication` extends `RoutedApplication`. Concrete applications supply
credential checks, webhook decoding, recipient validation, activation and sending.
The base manages configured bot instances, the application-wide persistence route,
conversation state, admission decisions, announcements and scheduled reminders.

Bot classes remain importable `RoutingClass` classes with their own grammar.
`BotInstanceGrammar` supplies optional access settings. A provider validates its own
user IDs; a common bot must not assume that identifiers are integers or phone numbers.
Provider authentication does not grant application roles, even after admission.

Conversation engines share revision checks and participant isolation. Provider
services render buttons and settlement notices. Telegram edits existing requests;
other transports can send a follow-up. Native features such as Telegram polls
remain on their concrete application.

Task staging records durable expiring receipts separately from the task spool.
The provider selects event keys and retention. Existing Telegram task names,
registration dictionaries, callback values and conversation revisions are preserved.
One receiving process owns each registry. Provider sends and persistence writes
are separate operations, so no exactly-once delivery guarantee is implied.


## Provider capabilities

| Operation | Telegram | WhatsApp |
|---|---|---|
| Configured instances and persistence route | Shared base | Shared base |
| Command text and concurrent conversations | Supported | Supported |
| Optional administrator approval | Edits request messages | Sends settlement notices |
| Text and media | Bot API | Cloud API; service window applies |
| Initial business notification | Known reachable chat | Explicit approved template |
| Announcements and reminders | Shared task services | Shared tasks with text/template choice |
| Native polls | Telegram extension | Not implemented |
| Delivery status | Send API result | Accepted plus webhook receipts |

The base delegates sending semantics to each concrete application. It does not
claim equal provider capabilities. Provider-native response dictionaries and
identifier formats remain available at their public APIs. See [WhatsApp bots](whatsapp.md)
for its template, service-window and receipt contracts.


## Three configuration levels

| Level | Owns | Example |
|---|---|---|
| Application mount | Transport, persistence route, receiving/sending mode and throttling | `telegram` at `/telegram` |
| Bot class | Command methods and configuration grammar | `TeamBot` with `settings` and `access` |
| Bot instance | Code, credentials, metadata and grammar values | `engineering` with its own token and dataset |

The same class can be registered several times. A central receiver and a local
sender can also register the same provider credentials in separate deployments.
Deployment mode belongs to the application configuration, not the bot record.

## Event flow

1. The provider posts to the application webhook.
2. The application verifies the secret/signature and identifies a registered bot.
3. It persists a task and a fixed-expiry deduplication receipt, then acknowledges.
4. The task checks admission and resolves the bot's command or conversation route.
5. The provider adapter sends the result; the task records its outcome.

Outgoing Python calls do not pass through the webhook. A local service with the
credentials can call the provider directly; responses from users still arrive
at the central receiver. Storage does not automatically synchronize between those
deployments.

## Package boundary

`kajenn-bot-application` depends on `kajenn>=0.3.0`. The server provides routing,
configuration, task scheduling and storage services. The bot package owns provider
integration and bot conversation behavior. Its release number is independent of
the server's; kajenn does not import or install it.

Start with [getting started](../getting-started.md), then
[write a bot](writing-bots.md) and select an [application persistence provider](persistence.md).
