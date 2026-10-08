# Deployment and troubleshooting

**Document version:** 0.2 · **Last updated:** 2026-10-08 · **Status:** 🔴 UNDER REVIEW

## Deployment model

Run one receiving process per registry when using the filesystem examples.
Expose only the configured bot mount through your HTTPS reverse proxy and keep
the task manager running for inbound dispatch, queued announcements and reminders.
Preserve the provider's path, body and signature headers through the proxy.

| Deployment | Public ingress | Credentials | Persistent state |
|---|---|---|---|
| Telegram receiver | `/<mount>/<bot code>` | Bot token and generated webhook secret | Registrations, receipts, conversations, polls and tasks |
| WhatsApp receiver | `/<mount>` | Number access token, app secret and verify token | Registrations, windows, message receipts, conversations and tasks |
| Local sender | None | Credentials for the same bot/number | Separate local registry or a trusted application-backed view |

The application's `webhook_url` selects receiving mode. Omitting it selects
sending only; inbound HTTP then returns 404. Empty or malformed URLs are errors.
Disabling receiving locally does not remove the central provider webhook.
Simple direct sends do not need the task manager; scheduled or queued work does.

## Startup and persistence

Mount both the bot application and its persistence application. The examples
restore the bot application first, then register optional environment-supplied
bots in the registry application's startup hook. For custom providers, ensure
the persistence route is usable when restoration begins.

Keep application codes and bot-class import paths stable across restarts.
The examples only register environment credentials for missing bot codes;
changing an environment token does not overwrite an existing registration.
Plan credential rotation explicitly in the owning application/provider.

`GENRO_STORAGE_KEY` protects the example registry. Keep the same key with its data
and include both in your recovery plan. Never put real tokens in committed recipes.
Registry encryption does not imply encrypted task payloads: task JSON can contain
message bodies and destinations. Apply the host's access and retention controls
to both stores.

## Understand acknowledgement and delivery

A webhook 200 means an accepted event has been staged with its deduplication
receipt, or an irrelevant event was deliberately ignored. It does not mean the
handler ran or the reply reached the recipient. Handlers and outbound replies
run later through kajenn tasks.

Inspect task descriptors and results from trusted server code:

```python
failed = server.tasks.spool.list_by_status("failed")
descriptor = server.tasks.spool.get(task_id)
result = server.tasks.spool.read_result(task_id)
```

Spool operations are synchronous; use `server.run_sync` when calling them from
latency-sensitive asynchronous application code. Check batch recipient outcomes,
not just whether the announcement task completed.

WhatsApp `accepted` means Graph returned a message ID. Read `get_message` for
later `sent`, `delivered`, `read` or `failed` receipts received centrally. A local
sender's separate registry does not automatically receive those central updates.
Telegram exposes the send API result, not a comparable delivery-receipt stream.

With the kajenn 0.3.0 filesystem task store, reading a schedule while another
thread writes it can observe incomplete JSON and raise a decoding error. A failed
status read is inconclusive: inspect again after execution settles instead of
replaying the message. This affects task-store status reads, independently of
registry encryption and conversation revision checks.

## Retries and uncertain outcomes

Explicit rate limiting and eligible connection-establishment failures are retried
with bounded backoff. Provider rejections are terminal. Read/write interruptions,
server errors and malformed successful responses can leave delivery uncertain;
those are not blindly retried. An interrupted reminder records `uncertain` when
next executed rather than sending a duplicate automatically.

`retry_attempts`, `retry_delay` and `send_interval` are application grammar settings.
Cooldowns are local to one process. Independent local senders do not share a global
rate limiter. For queued batches, read each recipient's status before deciding
what to resend.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Webhook returns 404 | Receiving mode, mount path and bot code. Telegram uses a bot suffix; WhatsApp uses only the mount root. |
| Telegram webhook returns 403 | The `X-Telegram-Bot-Api-Secret-Token` header must match the persisted registration secret. |
| WhatsApp verification or POST returns 403 | Verify-token equality for GET; raw-body HMAC with the Meta app secret for POST. Check proxy body transformations. |
| WhatsApp accepts a webhook but nothing runs | Confirm registered business-account/phone-number IDs and the `messages` subscription; unknown mappings are ignored. |
| Webhook succeeds but no reply arrives | Inspect task failures, admission state, handler result and provider errors. `None` deliberately sends no reply. |
| Protected command cannot run | Provider authentication and admission do not supply kajenn router roles. |
| Free-form WhatsApp send is rejected locally | The local registry may have no tracked service window. Use an approved template or a trusted shared window view. |
| Bot cannot contact a Telegram user/admin | They must have opened the bot's private chat and must not have blocked it. Check numeric user/chat IDs. |
| Admission decision saved but some notices are missing | Keep the persisted decision; restore admissions to retry unresolved notices. Check admin reachability or WhatsApp notice templates. |
| Reminder never fires | Confirm task manager/scheduler startup, aware future timestamp, state and conversation eligibility. |
| Registry restoration fails after restart | Inspect the chained startup cause, storage access, encryption key and importable bot class. |
| Retry after Telegram webhook setup failure | Use `await telegram.activate_bot(code)` after correcting the cause; it reuses the saved registration and secret. |

## Before putting a deployment into service

Verify the public webhook with an actual provider test, run `/hello`, restart and
confirm the same registration is restored. Exercise one announcement and a
reminder with test recipients. If admission is enabled, verify every administrator
receives and can settle a request. Confirm where failed tasks and uncertain
outcomes are reviewed operationally.

Automated tests mock provider HTTP, so they cannot validate your external routing,
provider account permissions, approved templates or recipient reachability.
