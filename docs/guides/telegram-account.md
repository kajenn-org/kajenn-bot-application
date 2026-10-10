# Personal Telegram account

**Document version:** 0.3 · **Last updated:** 2026-10-10 · **Status:** 🔴 UNDER REVIEW

`TelegramAccountApplication` connects one personal Telegram account through
MTProto. It is a separate application in the same distribution as the Telegram
and WhatsApp bots. It does not inherit bot registration, webhook processing or
bot conversations. Multiple accounts require separate mounts, state files and
keys. The base application is included in `0.2.0b1`. The `0.3.0b1` source version extends it to
37 MCP tools; the additions below are not yet published on PyPI.

Use it for a local developer service: an authenticated MCP client can read a
permitted group's history, send a message, or create and administer a channel
using the developer's account. Telegram still enforces membership, posting and
administrator rights. A channel post may display the channel as its author;
using a personal session does not override Telegram's sender presentation.

## Install and enroll locally

Install the beta with `python -m pip install "kajenn-bot-application==0.2.0b1"`.
To use the enrollment and configuration examples below, clone the repository,
use the current source checkout for the expanded commands, and install it with
`python -m pip install .`. Tag `v0.2.0b1` contains the original command set.
The examples are included in the source checkout, not the installed wheel.
The package includes Telethon, cryptography and filelock; kajenn 0.4.1 or newer
provides authenticated REST/MCP discovery. There is no webhook or polling loop:
the application maintains an MTProto connection for requested operations.

Create an application at [my.telegram.org](https://my.telegram.org) to obtain
`api_id` and `api_hash`. Configure these environment variables in a private shell
or a secret manager, outside source control:

| Variable | Value |
|---|---|
| `KAJENN_TELEGRAM_API_ID` | Numeric Telegram application ID |
| `KAJENN_TELEGRAM_API_HASH` | Telegram application secret |
| `KAJENN_TELEGRAM_ACCOUNT_SESSION` | Absolute path to a dedicated encrypted state file |
| `KAJENN_TELEGRAM_ACCOUNT_KEY` | Stable Fernet key for that file |
| `KAJENN_TELEGRAM_OWNER_TOKEN` | Separate random token for policy management |
| `KAJENN_TELEGRAM_OPERATOR_TOKEN` | Random token for the MCP client |

Generate a Fernet key once using `Fernet.generate_key()` from cryptography.
Keep the key separately from the state file, and reuse it after restart.
The session grants account access, not only the narrower chat grants enforced
by this application. Encrypting a session protects the stored file, not a
compromised running process that already holds its decrypted credentials.

With the server stopped, run the included local enrollment example:

```bash
python -m examples.telegram_account.login
```

It prompts privately for the phone number, Telegram login code, and two-step
password when required. Code delivery is chosen by Telegram; do not assume SMS.
Login credentials are never exposed as REST/MCP parameters. The code hash and
password are not persisted. The session appears as a Telegram device named
`kajenn Telegram account`; it can be revoked from Telegram's device settings.

Start the example server on loopback:

```bash
kajenn serve examples/telegram_account/config.py --host 127.0.0.1 --port 8000
```

Enrollment and the server must not run concurrently against the same session.
A process lock rejects a second owner. Disconnecting or stopping the application
preserves authorization; revoking the session logs the Telegram device out.
The encrypted state is atomically replaced, with file permissions `0600` on
POSIX. Existing parent directory permissions are not changed. Use an OS account
and directory accessible only to the intended user; Windows ACLs are managed by
the deployment. Losing the encryption key requires a new login; first revoke the
old device in Telegram if its authorization cannot be recovered.

## Mount on an existing server

```python
from kajenn_bot_application import TelegramAccountApplication

applications = [(TelegramAccountApplication, {
    "code": "personal",
    "api_id": 123456,
    "api_hash": api_hash_from_secret_store,
    "session_path": "/private/account/state.enc",
    "encryption_key": key_from_secret_store,
    "policy": {"operations": [], "chats": {}},
})]
```

Alternatively use the application's `telegram_account(...)` grammar element,
as the example configuration does. Its arguments match those above. `policy`
is the initial policy for a new state file; persisted administrator changes
survive restart and take precedence. Invalid credentials, malformed policy or
undecryptable storage refuse startup without replacing an existing state file.
Telegram flood waits, server errors and connection failures during startup also
leave the encrypted session and account binding untouched. Only an explicit
unauthorized response invalidates the stored authorization.

## Authentication and administration

Configure the host server's authentication routes for both `rest` and `mcp`.
The example has two credentials: the operator receives `telegram_account`; the
owner receives both `telegram_account` and `admin`. Replace the example token
checker with the site's identity provider in production. Use a dedicated
operator credential for MCP rather than handing it the owner credential.
These role names are shared across mounts on a server; separate owners should
use isolated servers or a host identity setup that isolates application access.
A separate encrypted file alone does not isolate a shared operator credential.

| Surface | URL | Required role |
|---|---|---|
| Operational MCP | `/personal/_mcp` | `telegram_account` |
| Operational REST | `/personal/_account/<operation>` | `telegram_account` |
| Policy read/write | `/personal/_admin/get_policy`, `/personal/_admin/set_policy` | `admin` |
| Revoke device | `/personal/_admin/revoke_session` | `admin` |
| OpenAPI/Swagger | `/personal/_meta/schema_json`, `/personal/_meta/docs` | `admin` or `telegram_account` |

REST operations are POST requests with JSON bodies. Management routes are
excluded from the MCP tool tree. Schema discovery uses kajenn's caller filters;
an operator's schema does not include administrator routes. Operational tool
names describe available operations; the account policy is checked when invoked.
`get_status` returns connection state and the last verified account identity
and does not require an operation grant. It returns immediately even while a
Telegram operation is waiting. External revocation is detected by
subsequent Telegram requests or startup, not by a background polling task. `get_policy` returns the rules, never credentials.

The application checks operation/chat grants inside its Python methods, so
trusted in-process calls follow the same account policy. Authentication of a
Python caller is the responsibility of the hosting application; a Python caller
with direct access to the application can invoke the trusted management methods.

## Configure the account's powers

New accounts deny every Telegram operation. A policy has exactly two fields:

```json
{
  "operations": ["get_chats", "get_messages", "send_text"],
  "chats": {
    "-1001234567890": ["read", "write"]
  }
}
```

Chat keys are canonical numeric Telegram peer IDs, including the `-100...`
channel/supergroup form. Names and usernames are not authorization keys. Use
`get_chats` to find IDs; if discovery is needed before choosing allowed chats,
the owner can temporarily grant `get_chats` and wildcard `read`, then narrow it.
A duplicate title is never resolved automatically for a mutation.

To deliberately grant broad powers:

```json
{
  "operations": ["*"],
  "chats": {"*": ["read", "write", "admin"]}
}
```

An exact chat entry replaces the wildcard for that chat. For example, an empty
list excludes a chat from otherwise broad grants. `create_channel` and
`create_group` need an operation grant but no existing chat grant. New chats are
not automatically added to a restricted policy; the owner grants access to the
returned numeric ID. Telegram's own account permissions are never elevated.

Apply a policy with the owner credential:

```bash
curl --fail-with-body http://127.0.0.1:8000/personal/_admin/set_policy \
  -H "Authorization: Bearer $KAJENN_TELEGRAM_OWNER_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @policy.json
```

`policy.json` contains `{"policy": {...}}`, with the complete replacement policy.
For `/get_policy` and `/revoke_session`, send `{}`. A policy change is saved before
it becomes active. It is serialized with ongoing operations and cannot undo an
operation already sent to Telegram. Provider operations run one at a time, with
a 45-second timeout per operation (180 seconds for transcription); this keeps policy changes, logout and message
mutations ordered. Status and policy reads do not join that queue. The encrypted
file is written and synchronized only when the session, identity or policy
changes, not after every history read.

**Direct actions and queued approvals are distinct grants.** Configure the MCP
client to confirm direct sends, deletions, invitations and role changes. For
administrator-approved texts, grant `request_message` and `decide_message` but
omit direct sending operations (including `send_text`, `send_media`,
`send_document`, `forward_message` and `schedule_message`). An operator can then
propose text but only an authenticated administrator can decide it. Neither a
message nor a tool argument can claim the administrator role.

## Operations

| Operation | Chat grant | Behavior |
|---|---|---|
| `get_chats(limit=100, offset=0)` | `read` on each returned chat | List permitted dialogs with IDs and names |
| `get_messages(chat_id, ...)` | `read` | History and text search, newest first |
| `get_members(chat_id, limit=100, offset=0)` | `read` | Permitted member metadata, without phone numbers |
| `send_text(chat_id, text, reply_to=None)` | `write` | Plain text, up to 4096 UTF-16 code units |
| `send_document(chat_id, filename, content_base64, caption="")` | `write` | Supplied content, at most 5 MiB; no server path or URL |
| `edit_message(chat_id, message_id, text)` | `write` | Edit an outgoing message in that chat |
| `delete_messages(chat_id, message_ids)` | `write` | Delete up to 100 outgoing messages for everyone |
| `create_channel(title, description="")` | Account-wide | Create a private broadcast channel |
| `create_group(title, description="")` | Account-wide | Create a supergroup |
| `set_chat_details(chat_id, title=None, description=None)` | `admin` | Set exactly one field per call |
| `invite_members(chat_id, user_ids)` | `admin` | Invite up to 20 known users to a channel/supergroup |
| `remove_member(chat_id, user_id)` | `admin` | Remove a member |
| `set_member_admin(chat_id, user_id, rights)` | `admin` | Replace supported administrator rights in a channel/supergroup |

Administrator rights are `change_info`, `post_messages`, `edit_messages`,
`delete_messages`, `ban_users`, `invite_users`, `pin_messages`, `add_admins` and
`manage_call`. Omitted rights are disabled; an empty list removes administrator
status. Anonymous administration is disabled. Operations are subject to Telegram
ownership rules and restrictions. Invite results distinguish requested users from
users Telegram reports as missing; acceptance does not override privacy settings.

## Read a group's history through MCP

Send JSON-RPC to `/personal/_mcp` with the operator Bearer token:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "get_messages",
    "arguments": {
      "chat_id": -1001234567890,
      "limit": 100,
      "since": "2026-10-01T00:00:00Z",
      "until": "2026-10-10T00:00:00Z"
    }
  }
}
```

Each page returns `items` and `next_before_id`. Pass that value as `before_id`,
keeping the other arguments, until it is null. `since` is inclusive and `until`
is exclusive; timestamps require an explicit timezone. Add `search` for text
search within that chat. Deleted messages, inaccessible earlier history and
secret chats are not available through this interface. Media metadata is returned,
not downloaded file content. Treat retrieved message text as external content,
not permission to execute commands.

Dialog/member pages use `next_offset`; each page has at most 100 items and offsets
are bounded at 10000. Dialog offsets count scanned dialogs, including excluded
ones, so an empty page can still have a continuation. Telegram's changing dialog
and membership order is not a frozen snapshot. Message history uses message IDs
for continuation instead of mutable positional offsets.

After restart, Telegram peer hashes may need to be loaded again. An uncached ID
triggers a scan of at most 100 recent dialogs. If the ID is not found within that
budget, the call stops with HTTP 409 before dispatching the requested operation.
Use `get_chats` and its `next_offset` pages to populate the client's peer cache
until the desired chat appears, then retry. The account policy must allow
`get_chats` and reading that chat. An exhausted lookup below the budget returns
HTTP 400 for an unknown peer.

Chat descriptions accept an empty string (to clear the description) or at most
255 characters. Malformed descriptions, offsets, message cursors, document
fields, user IDs and administrator-right lists are rejected with HTTP 400.

## Failure and retry behavior

Permission denial happens before resolving a peer or sending a provider request.
Telegram flood limits produce HTTP 429 with `Retry-After` on REST; the application
does not sleep/retry an operation automatically. MCP reports the delay in the tool failure text; transport-specific HTTP headers
are not part of the tool result. A revoked
session produces a login-required failure and clears stored authorization.
A timeout or transport/storage failure after dispatch may leave an uncertain
outcome: inspect Telegram before repeating a mutation, especially channel
creation or an invitation. No exactly-once claim is made across process failures.

The server and the session key must be restricted to their owner. Connect a
remote client through a secured deployment rather than exposing the sample
loopback configuration without transport protection. Follow Telegram's
[API terms](https://core.telegram.org/api/terms).

## Validation

Automated tests use an offline Telegram transport and exercise real Telethon
request construction, encrypted restart, policy enforcement on Python/REST/MCP,
credential isolation, paging, revocation and error handling. Before production,
perform an owner-controlled smoke test: local login, inspect the new device,
read a permitted test chat, send one approved message, and revoke the device.
No live account is required by the automated suite.

## Expanded account commands

The account exposes 37 MCP tools. The following eighteen extensions use the
same `telegram_account` avatar role and independently checked operation policy.
They are account operations, not Telegram Bot API commands.

| Command | Chat grant | Contract |
|---|---|---|
| `download_media` | read | Download selected media as bytes, bounded to 5 MiB. |
| `transcribe_message` | read | Transcribe selected audio with the configured engine; no automatic reply. |
| `react_message` | write | Set an emoji reaction, or remove it with an empty string. |
| `mark_read` | write | Mark messages read up to the selected message. |
| `archive_chat` | write | Archive or unarchive a chat. |
| `mute_chat` | write | Mute a chat until 2038 or restore notifications. |
| `pin_message` | admin | Pin or unpin a message without a notification. |
| `block_contact` | admin | Block or unblock a personal Telegram contact. |
| `set_profile` | Account-wide | Set account name and about text; empty optional fields clear them. |
| `get_contacts` | Account-wide | List readable contacts with policy filtering before pagination. |
| `forward_message` | write | Forward one message; requires source history permission and destination write permission. |
| `schedule_message` | write | Schedule text on Telegram using an ISO 8601 date with timezone. |
| `get_scheduled_messages` | read | Read messages scheduled on Telegram for this chat. |
| `cancel_scheduled_message` | write | Cancel an outgoing scheduled message by its scheduled-message ID. |
| `create_poll` | write | Create an anonymous poll with two to ten options. |
| `get_poll` | read | Read poll choices and provider results; unknown counts remain null. |
| `vote_poll` | write | Vote using option indexes from get_poll; an empty list retracts your vote. |
| `send_media` | write | Send photo, video, audio, voice or sticker bytes; never read server paths. |

### Media and local transcription

`send_media` accepts `photo`, `video`, `audio`, `voice` and `sticker`. Supply
base64 bytes and a simple filename with a compatible extension; the application
does not transcode formats or read arbitrary paths. Stickers carry an explicit
Telegram sticker attribute. Media must fit within 5 MiB. Download validates both
the advertised length and the received byte count before returning content.

`transcribe_message(chat_id, message_id, language="it")` requires an audio or
voice message. `language="auto"` enables language detection. The downloaded audio
is passed to the same optional speech engine used by the WhatsApp account
prototype. It returns text without storing the transcript or sending a reply.
The feature returns 503 when no engine is configured, before downloading media.

Install the optional dependencies from this checkout:

```bash
python -m pip install '.[transcription]'
```

Provide an existing local CTranslate2 model directory with the constructor
argument `transcription_model="/absolute/path/to/model"`, or with the same field
in the `telegram_account(...)` grammar. Models are not downloaded implicitly.
The shared worker uses offline faster-whisper, a subprocess, a five-minute audio
limit and a 170-second worker timeout. The complete operation has a 180-second
timeout and holds the account operation lock; cancellation terminates the worker.

A trusted application may instead inject `transcriber=engine`, implementing
`async transcribe(content, mimetype, language) -> dict`. The MCP caller cannot
choose a server path, executable or external endpoint. Hosted speech recognition
is never selected implicitly.

### Forwarding, polls and scheduling

Forwarding requires the `forward_message` grant and destination `write`, plus
`get_messages` and source `read`. Source and destination are checked before the
forward request. Numeric IDs and message membership are validated explicitly.
Contact listing filters by readable user IDs before pagination.

Polls are anonymous, with two to ten unique options. `get_poll` returns option
indexes and the provider's current counts; missing counts remain null. Use these
indexes in `vote_poll`; an empty list requests vote retraction. Telegram may
reject voting in a closed poll or a disallowed chat. Poll closing and quizzes
are not included in these additions.

`schedule_message` accepts `due` as ISO 8601 with timezone, within one year.
Telegram stores the scheduled message, so stopping the application does not
cancel it. `get_scheduled_messages` is paginated; cancellation uses the returned
scheduled-message ID, which must not be confused with a delivered-message ID.
This is native Telegram scheduling, without the WhatsApp prototype's local
approval queue. Once submitted, later local policy changes do not cancel it:
use `cancel_scheduled_message` explicitly while the caller has permission.

### Verification and remaining differences

Tests use a fake Telegram transport, actual Telethon request types and real TL
serialization for polls. They cover deny-by-default policy, cross-chat forwarding,
media limits, transcription delegation, invalid inputs and caller-filtered MCP
discovery. They do not send messages or change a real Telegram account.

These additions do not establish full parity with the WhatsApp prototype.
Telegram account privacy settings, profile/group photos and admission workflows
remain separate work. Existing encrypted session persistence is preserved. Multiple accounts
continue to use separate application mounts and state files.


## Persistent events, audit and approved texts

These five additional tools are available through MCP and REST:

| Tool | Avatar role | Operation and chat policy |
|---|---|---|
| `get_events(after_id=0, limit=100)` | `telegram_account` | `get_events`; read on returned chats |
| `request_message(chat_id, text)` | `telegram_account` | `request_message`; write on destination |
| `get_message_requests(limit=100, offset=0)` | `telegram_account` or `admin` | `get_message_requests`; read on returned chats |
| `decide_message(request_id, decision)` | `admin` | `decide_message`; read and write on destination |
| `get_audit_log(after_id=0, limit=100)` | `admin` | `get_audit_log`; read on chat-specific records |

The server's existing avatar filtering controls discovery and invocation.
Calling Python methods is a trusted administrative integration; it still checks
operation/chat policy but has no HTTP avatar. Such audit records use
`trusted-python`. Login, policy changes and session revocation remain outside MCP.

### Event replay

The connected Telethon client delivers new, edited and deleted message events;
there is no application polling loop. The application stores identifiers and a
local observation timestamp for readable chats, never message bodies. New
message events (`message_received`) include incoming and outgoing messages.
Events from unreadable chats are dropped on receipt, and current permissions
are applied again before pagination. Use `get_messages` for authorized content.

Save `next_after_id` and supply it as `after_id` on the next call; `has_more`
indicates another page. The cursor survives application restarts.
`retention_gap` reports records removed by the bounded journal. Replaying events
is a pull API; it does not initiate MCP notifications or deliver callbacks to
remote consumers. This is an observation journal, not a complete historical feed:
updates missed while disconnected or before login are not backfilled. Duplicate
provider events may produce multiple journal records.

Telegram sometimes omits the chat from deletion updates. These updates are
ignored rather than attributed to a guessed chat; `get_status` reports
`unscoped_deletions`. Callback persistence failures increment `event_errors`.
Both diagnostic counters reset when the application object is recreated. See
[Telethon deletion events](https://docs.telethon.dev/en/stable/modules/events.html#telethon.events.messagedeleted.MessageDeleted)
for provider coverage limitations.

### Approval contract

1. `request_message` persists an immutable text proposal in `pending` state and
   returns its request ID, destination, text and proposing avatar identity.
2. An administrator reviews it using `get_message_requests` and calls
   `decide_message` with `approve`, `reject` or `cancel`.
3. Approval rechecks the operation and destination grants, records the deciding
   identity and `sending` state durably, then sends the exact stored text.
4. A successful send becomes `submitted` with the provider message ID. Concurrent
   or repeated decisions return the settled record without resending. Rejection
   and cancellation are final and send nothing.

`decide_message` is its own sending grant: it does not require `send_text`.
This allows approval-only policies without enabling direct operator sends.
For example, an administrator can configure:

```json
{
  "operations": ["request_message", "get_message_requests", "decide_message", "get_events", "get_audit_log"],
  "chats": {"-1001234567890": ["read", "write"]}
}
```

A failed or cancelled provider call becomes `unconfirmed`, as does a `sending`
record found at restart. No automatic retry occurs. Inspect Telegram before
creating a replacement request. A submitted message cannot be cancelled through
this queue; deleting it requires the separate `delete_messages` permission.
Approval sends immediately and does not schedule background work. Native
`schedule_message` remains a separate Telegram scheduling feature.

Records are bound to the Telegram account ID. Logging in as another account does
not expose or dispatch the previous account's jobs, audit or events. Changing
back to the original account restores access subject to current grants.

### Storage and audit scope

The journal is an additional file named `<session_path>.journal.enc`, encrypted
with the configured session key and restricted to mode 0600. It reuses the
session store's exclusive lease and atomic replacement implementation. Keep the
session and journal files together in backups. No additional storage service is
required, and the session-file write-on-change contract is unchanged.

Retention is 1,000 events, 1,000 audit entries and 1,000 text requests per mount.
The oldest settled request is removed when space is needed; pending and
unconfirmed requests are never silently discarded. A queue containing only
unresolved requests rejects new proposals when full. Snapshots are rewritten
atomically on each change; this filesystem implementation targets modest traffic.

Audit records contain operation, authenticated identity, optional chat ID,
timestamp and result category. They exclude text, credentials and provider
exception details. They cover operations entering the execution wrapper, not
HTTP requests rejected by routing/authentication, argument validation before the
wrapper, status reads, local login, policy changes or session revocation. Queue
records additionally retain the proposing and deciding identity. Reading audit
does not itself append audit records. An audit persistence failure before an
operation prevents that operation from reaching Telegram.
