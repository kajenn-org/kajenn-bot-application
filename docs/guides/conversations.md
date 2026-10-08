# Conversations and approvals

**Document version:** 0.2 · **Last updated:** 2026-10-08 · **Status:** 🔴 UNDER REVIEW

Use a conversation when an outgoing notification needs a correlated response.
Each conversation has an ID, participants, a handler route, context and a revision.
Several conversations can be open for the same user at once.

## Ask two people about the same request

The following recipe uses a registered `TeamBot` from [writing a bot](writing-bots.md).
Run it in trusted application code after the bot application has started:

```python
request = await telegram.create_conversation(
    "engineering",
    participants=[
        {"user_id": mario_id, "chat_id": mario_id},
        {"user_id": anna_id, "chat_id": anna_id},
    ],
    route="review",
    context={"pr": 45},
)
for user_id in (mario_id, anna_id):
    await telegram.send_conversation_message(
        "engineering", request["id"], user_id,
        "Please review PR 45",
        buttons={"Reviewed": "reviewed", "Need help": "help"},
    )
```

Both users must be reachable by the Telegram bot. For WhatsApp, use string
participant IDs and observe the service-window/template rules. A participant
may have a distinct `chat_id`; provide it explicitly when the same user belongs
to more than one chat in a conversation.

The buttons invoke the conversation's `review` handler with `action="reviewed"`
or `action="help"`. Replies use the stored message and chat to select the request.
They are sent only to that participant, not broadcast to the other participants.
Your business handler decides whether a response satisfies the review.

## Keep simultaneous conversations separate

A reply reference identifies its conversation. A button must also match a stored
offered action and the expected participant. Plain text without a reply reference
is dispatched only when exactly one open conversation matches. With two possible
requests, the bot asks the user to reply to the relevant message.

The `sender` dictionary is provider-specific. Do not assume Telegram integer IDs
and WhatsApp string IDs refer to the same application identity.

## Update and conclude a request

Read a fresh snapshot and supply its revision when replacing context:

```python
current = await telegram.get_conversation("engineering", request["id"])
await telegram.update_conversation_context(
    "engineering", request["id"],
    {**current["context"], "review_status": "ready"},
    revision=current["revision"],
)
await telegram.close_conversation("engineering", request["id"])
```

A stale revision raises rather than overwriting another update. Use
`state="cancelled"` to cancel. `expires_at` on creation is a Unix timestamp;
expiry is observed on the next read or interaction, not by a timer. A closed,
cancelled or expired request does not dispatch further replies.

If a handler sends a final message and closes the conversation, it should return
`None`. Send the message before closing so the participant is still eligible.

## Schedule a reminder tied to the request

Before the conversation closes:

```python
from datetime import datetime, timedelta, timezone

reminder_id = await telegram.schedule_reminder(
    "engineering", mario_id, "PR 45 still needs a review",
    when=datetime.now(timezone.utc) + timedelta(hours=2),
    conversation_id=request["id"], user_id=mario_id,
)
```

The task manager must be running. If the conversation has concluded when the
reminder executes, it is skipped. Read `get_reminder(reminder_id)` for its state;
cancellation is possible only while pending. Delivery with an uncertain outcome
is not silently repeated.

## Require admission before bot use

Admission is a separate, built-in conversation type. Enable it in a registration:

```python
config = {
    "settings": {"dataset": "engineering"},
    "access": {
        "approval_required": True,
        "admins": [first_admin_id, second_admin_id],
        "approval_policy": "first",
    },
}
```

With `first`, the first valid processed decision concludes the request. With
`all`, all administrators must approve; one rejection concludes it. Each admin's
vote is immutable. Rejected requests remain rejected when the user sends `/start`
again. Configured administrators are implicitly admitted.

On Telegram, each administrator must already have started a private chat with the
bot. Settled request messages show who approved or rejected and lose their buttons.
WhatsApp sends settlement messages and may need configured templates outside
administrator service windows. The persisted decision is the authority even if
one of those outgoing notifications fails; the application retries unresolved
notices when restoring admissions.

Admission gives access to bot interactions. It does not create application users,
tokens or router permissions. General conversation APIs cannot edit or close
admission records. See [Telegram admission](telegram.md#optional-administrator-admission)
and [WhatsApp admission](whatsapp.md#conversations-and-administrator-admission)
for provider-specific configuration.
