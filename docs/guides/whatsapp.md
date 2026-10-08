# WhatsApp bots

**Document version:** 0.1 · **Last updated:** 2026-10-08 · **Status:** 🔴 UNDER REVIEW

`WhatsAppBotApplication` extends [BotBaseApplication](bots.md) and uses the official
WhatsApp Business Cloud API. Each application mount represents one Meta app
connection; register multiple existing business phone numbers as independent bot
instances. Each bot is a `RoutingClass` with its own grammar and configuration.
Use separate mounts for separate Meta app connections and persistence namespaces.

For installation and environment setup, see [getting started](../getting-started.md).
The examples below assume an initialized application and registered bot.

## Configure a receiver

Provision the business account, number and access token in Meta first. The example
uses customer-owned credentials. It does not create Meta accounts, register phone
numbers remotely, perform Embedded Signup or change subscriptions automatically.

```python
from kajenn_bot_application import WhatsAppBotApplication

app = cfg.applications().application(
    code="whatsapp", app_class=WhatsAppBotApplication,
)
app.whatsapp(
    persistence_route="registry/bots",
    api_version="v25.0",
    webhook_url="https://example.com/whatsapp",
    app_secret=secret_resolver,
    verify_token=verification_resolver,
)
```

Select an API version supported by your Meta app explicitly; there is no moving
version default. Examples and protocol fixtures use `v25.0`. Keep secrets in
resolvers or protected configuration. The app secret signs webhook payloads; the
verify token is a separate string chosen for the subscription handshake. Neither
is the business number's API access token.

In Meta's app configuration, set the callback URL to the mount URL above and set
the matching verify token. Subscribe to the `messages` field for the relevant
business accounts. Your token needs `whatsapp_business_messaging` for messaging
and the management access required to read the configured number during
registration. See the official [permissions reference](https://developers.facebook.com/documentation/business-messaging/whatsapp/permissions).

GET verification validates the token and returns the challenge. POST validates
`X-Hub-Signature-256` over the exact body bytes before decoding JSON. The app maps
each account/phone-number pair to its registered bot. It ignores unknown mappings
and unsupported webhook fields/types; malformed supported events return 400.
Only the application's mount root is a webhook endpoint, not `/<bot code>`.
See [webhook setup](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint).

## Register a bot class and number

```python
from examples.whatsapp_bot import DemoBot

whatsapp = server.applications["whatsapp"]
await whatsapp.register_bot(
    code="team",
    bot_class=DemoBot,
    token=access_token,
    phone_number_id="106540352242922",
    business_account_id="102290129340398",
    name="Team assistant",
    config={"settings": {"greeting": "Hello", "dataset": "team"}},
)
```

The trusted Python API validates configuration and reads the number through Graph
API before saving the registration. It does not verify or provision every remote
business association or webhook subscription. Duplicate phone numbers within one
application are rejected. Different numbers may share an access token. Name/icon
are local metadata, not changes to the WhatsApp business profile.

The example handles `/hello` and `/echo text`. These are application text commands,
not native WhatsApp command registration. Sync handlers use the server pool;
async handlers run on its loop. Replies use `send_text` and split when needed.
Telegram's command behavior remains unchanged. Router authorization remains
active: webhook signatures and bot admission do not supply application roles.

## Send locally and receive centrally

Mount the same application locally, omitting `webhook_url`, `app_secret` and
`verify_token`. Register the same business number/token under a separate local
registry namespace. A sender never sets, deletes or subscribes a webhook, and
inbound HTTP returns 404. Simple sends do not require the task manager.

```python
app.whatsapp(persistence_route="registry/bots", api_version="v25.0")
await whatsapp.send_template(
    "team", "391234567890",
    name="new_pr", language="it",
    components=[{
        "type": "body",
        "parameters": [{"type": "text", "text": "PR 45"}],
    }],
)
```

Meta must have approved that exact template/language/components combination.
The library does not choose a template, obtain approval or infer recipient consent.
The hosting application supplies recipients who have opted in and handles opt-out.
Bot admission is a separate decision from permission to send business messages.

Free-form replies are permitted within the 24-hour customer service window;
outside it, approved templates are required. The receiver persists the newest
inbound message timestamp, and late messages cannot extend the window. A local
sender with a separate registry has no such knowledge: use templates, call the
central receiver, or supply a trusted shared window view through persistence.
Unknown windows are treated as closed. See the [Business Messaging Policy](https://whatsappbusiness.com/policy/).

Recipient IDs are strings: numeric WhatsApp IDs as supplied by Meta, or a BSUID
such as `IT.123456789`. BSUID addressing uses `recipient` instead of `to`; Meta's
rollout and the selected API version determine availability. Preserve BSUIDs
exactly. When an inbound message includes both a phone number and a BSUID, this
implementation uses the phone number; otherwise it uses `from_user_id`. It does
not automatically merge separate participant identities or admission records.
Status aliases are correlated only within their specific message record.
See [business-scoped user IDs](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids).

## Text, buttons and media

```python
await whatsapp.send_message("team", recipient, "Short reply")
await whatsapp.send_text("team", recipient, long_text)
await whatsapp.send_buttons(
    "team", recipient, "Approve this change?",
    {"Approve": "approve", "Reject": "reject"},
)
await whatsapp.send_document(
    "team", recipient, document_bytes, filename="report.pdf", caption="Report",
)
await whatsapp.send_media("team", recipient, "image", image_media_id)
```

These free-form operations require an open window. `send_message` accepts 1-4096
characters; `send_text` splits and preserves content. Interactive reply buttons
accept 1-3 choices, a body up to 1024 characters, labels up to 20 and payloads up to
256. Standalone buttons have no automatic conversation handler; use
`send_conversation_message` for correlated actions.

Media kinds are `image`, `document`, `audio` and `video`. Supply a provider media
ID, an HTTPS URL, or bytes with a filename. Upload limits are 5 MB for images,
100 MB for documents and 16 MB for audio/video. Meta validates the actual format
and codecs. Uploading bytes and sending the resulting ID are separate API calls;
a failed send can leave an uploaded asset. Audio has no caption; other captions
are bounded to 1024 characters. Local file paths are never opened implicitly.
See [media](https://developers.facebook.com/documentation/business-messaging/whatsapp/media).

## Conversations and administrator admission

The [Telegram conversation API](telegram.md#concurrent-conversations-with-multiple-participants)
also applies here: create a conversation with explicit user/chat participants,
send a correlated message, update context with its revision and close/cancel it.
Use string IDs for WhatsApp participants and administrators. Replies use their
context message ID; buttons also check the expected sender, destination and
offered action. Ambiguous free text asks the user to reply to a specific message.
There is no implicit fan-out to other participants or cross-provider identity link.

Bot grammars may inherit `WhatsAppBotInstanceGrammar`. Its `access` element has the
same `approval_required`, `admins` and `approval_policy="first"|"all"` options as
Telegram. Optional notification templates cover administrators outside their
service windows:

```python
config = {
    "access": {
        "approval_required": True,
        "admins": ["391111111111", "392222222222"],
        "approval_policy": "first",
    },
    "notifications": {
        "approval_template": {"name": "bot_access_request", "language": "it"},
        "resolution_template": {"name": "bot_access_result", "language": "it"},
    },
}
```

These two template definitions accept exactly `name` and `language`. Provision
both with one body text parameter. The approval template additionally needs two
quick-reply buttons in Approve, Reject order. Their callback payloads carry the
conversation and action IDs. This is an explicit template contract, not an
inference about arbitrary business templates.

A decision is saved before notifications. WhatsApp sends a follow-up saying who
approved/rejected; it does not edit the original request or remove old buttons.
Old actions cannot change a completed decision. Missing templates or API failures
leave notifications pending for startup or a subsequent valid interaction.
General conversation buttons outside the service window require the caller to
reopen the interaction explicitly; no business template is silently selected.

## Announcements, reminders and delivery status

```python
results = await whatsapp.send_announcement("team", recipients, "Release ready")
task_id = await whatsapp.queue_announcement(
    "team", recipients, template={"name": "release_notice", "language": "it"},
)
reminder = await whatsapp.schedule_reminder(
    "team", recipient, when=aware_future_datetime,
    template={"name": "review_reminder", "language": "it"},
)
```

Choose text or a template explicitly. Announcements deduplicate recipients and
report `accepted`, `partial`, `failed` or `uncertain` per destination. A closed
window for one recipient does not prevent processing later recipients. Queued
results live in the task spool; task success does not mean every recipient got a
message. An interrupted batch is not automatically replayed.

Reminders use the existing task scheduler and re-check eligibility at execution.
They survive restart, can be cancelled while pending and skip closed conversations.
Supply `conversation_id` and `user_id` to bind one to a participant; a template
reminder's message ID is also tracked for replies. An interrupted `sending` state
becomes `uncertain` on the next execution and is not resent automatically.
Reminder success is `accepted`, not `sent`. It describes API acceptance; delivery
is read separately through message receipts. The reminder record retains accepted
`message_ids` for lookup with `get_message`. Schedules use the task store's normal
storage policy and contain no access tokens, but do contain text/template inputs.

Every send returns a record with `message_id`, `recipient`, `status="accepted"`
and its submission timestamp. Use `get_message(bot_code, message_id)` on the
receiver for stored status events (`sent`, `delivered`, `read`, `failed`, `deleted`).
Out-of-order events do not regress delivery/read progress. Event history retains
failures even if another status wins. A status may arrive before API acceptance
is persisted. A local registry does not automatically receive central receipts;
query the central application or provide shared persistence for that use case.

## Persistence and recovery

The application's single route keeps the common registration/receipt/conversation
operations described in [Telegram persistence](telegram.md#persistence-route-contract).
WhatsApp additionally calls:

| Operation | Payload | Required result/semantics |
|---|---|---|
| `get_window` | `bot_code`, `recipient` | Record with `last_inbound`, or `None` |
| `advance_window` | `bot_code`, `recipient`, `last_inbound` | Atomic maximum of timestamp; never regress |
| `get_message` | `bot_code`, `message_id` | Receipt record, or `None` |
| `save_message` | `bot_code`, `message_id`, `recipient`, optional `recipient_ids`, `status`, `timestamp`, `error_codes` | Atomic merge; retain distinct status/timestamp/error events and known recipient aliases |

The example assigns progress ranks accepted < sent < failed/deleted < delivered
< read. It refuses conflicting recipients unless the verified event supplies an
alias shared with the stored record. API acceptance cannot overwrite a receipt.
Custom providers must preserve these merge semantics and application/bot scoping.

`examples/whatsapp_bot/config.py` reuses the encrypted filesystem registry example
and adds windows and receipts, with hashed file names. Its shared directory is
`telegram_registry/<application>/`; application namespaces keep the data apart.
The route returns 404 over HTTP. It supports one receiving process per registry,
not distributed locking. Registrations, conversations, windows and receipts need
an explicit retention policy in the hosting persistence provider.

Deduplication receipts last eight days from initial acceptance, covering Meta's
seven-day webhook retry horizon. Individual messages and status transitions have
separate keys; batches are fully validated before staging. Tasks and receipts are
separate durable writes. A retry after partial staging finds the existing tasks;
do not purge such tasks until receipt persistence has recovered. Spool cleanup
after successful receipt writes does not remove deduplication protection.

## Retry policy and deployment limits

`retry_attempts` (1-10, default 3), `retry_delay` (default 1 second) and
`send_interval` (default zero) belong to the `whatsapp` grammar element. Requests
are serialized per number within this application. Connection establishment
failures and explicit rate-limit rejections are retried with bounded backoff;
`Retry-After` delays are respected. Exhausted throttling retains its cooldown.
Other API rejections are terminal. HTTP 5xx, read/write failures and malformed
success responses are uncertain and are not automatically retried. If receipt
persistence fails after acceptance, the result is also uncertain.

Errors omit tokens, authorization headers and provider descriptions. Separate
processes do not share throttling state. The task spool exposes handler failures;
no exactly-once sending or automatic crash replay is promised. This adapter does
not implement native polls, groups/channels, inbound media handlers, personal
account sessions, contact administration or application identity provisioning.

## Run the examples

Set a stable `GENRO_STORAGE_KEY` and these environment variables through your
normal secret/configuration mechanism:

- `KAJENN_WHATSAPP_API_VERSION` (for example `v25.0`).
- `KAJENN_WHATSAPP_TOKEN`, `KAJENN_WHATSAPP_PHONE_NUMBER_ID`,
  `KAJENN_WHATSAPP_BUSINESS_ACCOUNT_ID` to register the optional `team` bot.
- For reception: `KAJENN_WHATSAPP_WEBHOOK_URL`, `KAJENN_WHATSAPP_APP_SECRET`,
  `KAJENN_WHATSAPP_VERIFY_TOKEN`.

```bash
kajenn serve examples/whatsapp_bot/config.py
# A separate installation with its own storage, sending only:
kajenn serve examples/whatsapp_bot/local_config.py
```

Open an opted-in test conversation and send `/hello` or `/echo example`. Outbound
notifications from a local job use the Python API; the local recipe sends nothing
automatically. Automated tests mock Graph HTTP while exercising real routing,
tasks, encrypted storage and restart recovery. A live-number smoke test is a
separate deployment verification.
