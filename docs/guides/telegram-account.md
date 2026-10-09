# Personal Telegram account

**Document version:** 0.1 · **Last updated:** 2026-10-09 · **Status:** 🔴 UNDER REVIEW

`TelegramAccountApplication` connects one personal Telegram account through
MTProto. It is a separate application in the same distribution as the Telegram
and WhatsApp bots. It does not inherit bot registration, webhook processing or
bot conversations. Multiple accounts require separate mounts, state files and
keys. This feature is under development; use the source branch containing it.

Use it for a local developer service: an authenticated MCP client can read a
permitted group's history, send a message, or create and administer a channel
using the developer's account. Telegram still enforces membership, posting and
administrator rights. A channel post may display the channel as its author;
using a personal session does not override Telegram's sender presentation.

## Install and enroll locally

Install this repository into a virtual environment with `python -m pip install .`.
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
a 45-second timeout per operation; this keeps policy changes, logout and message
mutations ordered. Status and policy reads do not join that queue. The encrypted
file is written and synchronized only when the session, identity or policy
changes, not after every history read.

**Confirmations belong to the calling client.** Configure the MCP client to ask
before sends, deletions, invitations and role changes. The application enforces
grants but does not provide a second approval queue or treat tool arguments as
proof of human approval. Administrators can constrain individual operation names
in addition to chat categories.

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
      "until": "2026-10-09T00:00:00Z"
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
