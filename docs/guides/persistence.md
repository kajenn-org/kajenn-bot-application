# Persistence

**Document version:** 0.2 · **Last updated:** 2026-10-08 · **Status:** 🔴 UNDER REVIEW

Every bot application delegates durable state to one `persistence_route`, such as
`registry/bots`. It is an internal route on another mounted `RoutedApplication`,
not an HTTP URL. All bot instances on that application share this provider.

The route receives `operation`, `application` (the bot application's code) and
`record` (the operation payload, or `None`). Namespace records by application,
bot code and record ID where appropriate. Two mounts may use the same provider
while retaining separate application namespaces.

## Choose a backend

The examples use encrypted JSON through kajenn storage and return 404 to HTTP
requests. They are useful for a single receiving process. Their thread lock
does not coordinate multiple processes.

Your hosting application can implement the same route with its database. Keep
credential encryption, transaction behavior and retention in that application.
Changing the backend does not require changing bot classes or command handlers.
The contract does not itself provide distributed conversation execution.

## Required operations

| Feature | Operations | Required guarantee |
|---|---|---|
| Registrations | `list`, `save` | Durable save; preserve credentials and importable class references |
| Webhook deduplication | `get_receipt`, `save_receipt`, `prune_receipts` | Fixed expiry per accepted event; exclude expired receipts |
| Conversations and admission | `list_conversations`, `get_conversation`, `save_conversation` | Atomic compare-and-save of the supplied revision |
| Telegram polls | `get_poll`, `save_poll` | Durable snapshot scoped by application, bot and poll |
| WhatsApp service windows | `get_window`, `advance_window` | Store the newest timestamp; never regress on late events |
| WhatsApp delivery receipts | `get_message`, `save_message` | Atomic merge of status events and matching recipient aliases |

Reminders and queued announcements use `kajenn.tasks` storage and do not add
registry operations. A Telegram sender doing only registration and direct sends
needs `list` and `save`. WhatsApp outbound sends also record message acceptance;
free-form sending reads the persisted service window.

Use the exact payload and result schemas in the
[Telegram persistence contract](telegram.md#persistence-route-contract) and
[WhatsApp persistence contract](whatsapp.md#persistence-and-recovery).
Those pages specify additional operations and provider-specific fields.

## Conversation revisions

Creation supplies revision zero. For every `save_conversation`, atomically:

1. Read the current revision, treating a missing record as revision zero.
2. Reject a mismatch with the supplied revision.
3. Save the record with the incremented revision.
4. Return the saved record, including that revision.

A read followed by an unconditional write is insufficient. The example locks
these steps within one process; a database provider should use a conditional
update or transaction. Treat a revision conflict as a failed operation that
needs a fresh application decision, not a silent retry of stale context.

## Retention and recovery

Webhook receipts are separate from completed task history. Telegram retains them
for 48 hours after acceptance; WhatsApp retains them for eight days. Duplicate
deliveries do not extend expiry. Tasks are stored before receipts; if the receipt
write fails, preserve the staged task so the provider retry can reuse it.

Conversation, admission, poll, window and message records need an explicit
retention policy in your provider. Removing admission records removes remembered
access. Removing message references can break correlation for outstanding replies.
Back up registry data together with the appropriate key and application namespace.

Provider sends and persistence writes are separate. Even a transactional database
cannot make an external message send and a registry write one atomic operation.
Inspect uncertain outcomes before resending; see [operations](operations.md).
