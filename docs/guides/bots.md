# Shared bot applications

**Version:** 0.1 · **Last updated:** 2026-10-08 · **Status:** 🔴 DA REVISIONARE

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
