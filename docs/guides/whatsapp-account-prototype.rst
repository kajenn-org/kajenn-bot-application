WhatsApp account prototype
==========================

Version: 0.4 — Last updated: 2026-10-10 — Status: UNDER REVIEW

This source-tree prototype evaluates a personal WhatsApp linked device using
Tryx and whatsapp-rust. It includes a source-tree ``WhatsAppAccountApplication`` and a local test
harness. It is not exported in the published package. The existing WhatsApp application
continues to use the Business Cloud API.

Scope and ownership
-------------------

The prototype provides explicit pairing, a persistent SQLite device store,
exclusive process ownership, a bounded graceful stop, an explicit logout request,
and an optional text-send probe. It counts live messages and history-sync batches
without printing their contents. An authenticated local server can also index observed contacts, chats and text
messages and expose bounded queries through REST/MCP.

The session directory must be private (0700); the database must be private (0600).
Only one process may open a directory through this controller. A shutdown timeout
retains that ownership while the flush continues; waiting for stop again observes
the same operation. Transient startup errors do not delete saved credentials.
Each restart uses a new controller and the same directory and device ID (0).

The SQLite store contains plaintext device secrets. Filesystem permissions are
not encryption at rest. Use a test account on a protected local volume; the
production design must settle encryption and key ownership before publication.
The harness targets macOS/Linux, including its no-follow filesystem checks.

Build the experimental dependency
---------------------------------

Run commands from the repository root with Python 3.11 or newer, Git and Rust
1.94.0 available. Building needs a native compiler and access to GitHub/crates.io.
The builder fixes both upstream revisions and supplies a resolved Cargo lockfile.
The wheel has the distinct local version ``1.5.0+kajenn.4``; it is not on PyPI.

.. code-block:: console

   python -m venv .venv-account
   .venv-account/bin/python -m pip install -e '.[test]' -r examples/whatsapp_account/requirements.txt
   rustup toolchain install 1.94.0 --profile minimal
   .venv-account/bin/python examples/whatsapp_account/build_tryx.py temp/tryx-build
   .venv-account/bin/python -m pip install temp/tryx-build/dist/tryx-*.whl

The MIT-licensed lifecycle patch adds three methods to ``AdvancedClient``:
``wait_for_client`` waits for local initialization, ``disconnect`` flushes and
stops the connection, and ``logout`` requests companion-device removal before
stopping. ``TRYX-LICENSE`` accompanies the patch. The repository's Python harness
is Apache-2.0. Additional patches expose app-state replay and fix nested
Protobuf action lookup for contact and chat updates. The history patch exposes
``fetch_message_history`` from the pinned Rust client; its result acknowledges
the request, not completion of history transfer.

Offline verification
--------------------

.. code-block:: console

   .venv-account/bin/python -m pytest tests/whatsapp_account -q --no-cov
   .venv-account/bin/python -m pytest examples/whatsapp_account/test_native.py -q --no-cov

The implementation tests use a deterministic runtime to verify ownership,
cancellation, timeouts, restart and error preservation. Native tests load the
compiled extension, exercise SQLite persistence and verify the new methods.
Neither test group starts a WhatsApp network connection or associates an account.

Explicit live check
-------------------

.. code-block:: console

   .venv-account/bin/python -m examples.whatsapp_account.run \
     --session-dir "$HOME/.kajenn/whatsapp-test" --live --duration 60

Scan the QR from the phone's Linked devices screen. The terminal is private:
QR output authorizes device association. The connection timeout and observation
window are each bounded by ``--duration``. On timeout or normal exit, the harness
requests a graceful stop. Run the command again with the same directory to verify
that another QR is unnecessary. No account is connected without ``--live``.

An optional send requires both ``--send-to <exact-jid>`` and ``--text <message>``.
The receiver and text are supplied explicitly; no contact is inferred. A returned
send operation is not proof of recipient delivery.

Use ``--logout`` to request removal while connected. The upstream logout method
returns no server acknowledgement and still stops after a failed removal request.
Therefore the harness does not claim confirmed revocation or erase the saved
state. Verify removal on the phone; remove the linked device there if necessary.
Deleting a local file alone does not revoke a linked device.

Authenticated local MCP server
------------------------------

After explicit pairing, start the server with the same private directory:

.. code-block:: console

   .venv-account/bin/python -m examples.whatsapp_account.server \
     --session-dir "$HOME/.kajenn/whatsapp-test" --port 8766 --resync

The endpoint is ``http://127.0.0.1:8766/whatsapp/_mcp``. The server creates a
private ``mcp.token`` inside the session directory. A client must supply its
contents as the ``Authorization: Bearer <token>`` header; never paste it into
chat, logs or source control. This command does not register an MCP client or
start a background system service. Stop it normally to flush the session.

The local token identifies the account owner. The example gives that avatar
``whatsapp_account_read``, ``whatsapp_account_write``, ``whatsapp_account_manage``
and ``admin`` roles, with an explicitly permissive initial account policy.
Applications embedding the class must provide their own identity route and
initial policy; an omitted policy denies operations. No credentials or pairing
operations are exposed as tools.

Commands and roles
~~~~~~~~~~~~~~~~~~

Kajenn filters MCP discovery and execution by avatar roles. All commands are also
available on the application's REST routing surface. The application additionally
checks its persistent operation and chat policy on execution, including calls
from trusted Python code. There are 76 MCP tools. The original command families are:

.. list-table:: Account commands
   :header-rows: 1
   :widths: 24 48 28

   * - Role
     - Commands
     - Contract
   * - ``whatsapp_account_read``
     - ``get_status``, ``get_sync_status``
     - Connection, visible counts, observed coverage and callback errors.
   * - ``whatsapp_account_read``
     - ``get_contacts``, ``get_chats``, ``get_chat``, ``get_unread``
     - Bounded local queries; unread is an observed flag, not inferred from age.
   * - ``whatsapp_account_read``
     - ``get_messages``, ``search_messages``, ``get_message_status``
     - Local text search, retained message IDs and observed per-recipient receipts.
   * - ``whatsapp_account_read``
     - ``download_media``, ``request_history``
     - Media retrieval and asynchronous history requests from stored messages.
   * - ``whatsapp_account_read``
     - ``get_group``, ``get_group_members``
     - Remote group metadata and paginated participant results.
   * - ``whatsapp_account_write``
     - ``send_text``, ``reply_message``, ``react_message``, ``send_media``
     - Exact recipients, explicit content and known reply/reaction targets.
   * - ``whatsapp_account_write``
     - ``mark_read``, ``archive_chat``, ``mute_chat``
     - Boolean state changes; ``False`` reverses the requested state.
   * - ``whatsapp_account_manage``
     - ``create_group``, ``update_group_members``
     - Explicit participants; provider-side administrator permissions still apply.
   * - ``admin``
     - ``get_policy``, ``set_policy``, ``get_audit_log``
     - Durable account grants and operation audit metadata.

Queries use ``limit`` (1–100) and ``offset`` (0–1,000,000). Follow ``next_offset``
with unchanged filters. Contact lookup preserves ambiguous names. A contact does
not imply an existing chat. ``search_messages`` accepts ``query`` and an optional
``chat_id``; it searches the local synchronized subset only. Policy filtering
happens before pagination and covers contacts, chats, search, unread and counts.

``send_text(chat_id, text)`` and ``reply_message(chat_id, message_id, text)`` accept
up to 4,000 characters. Replies quote retained Protobuf content and the original
sender. ``react_message`` accepts an emoji string, or an empty string to remove
one. No command resolves a recipient by choosing among names. The returned ID
comes from the provider's ``SendResult.message_id``; ``submitted`` is not delivery
confirmation. ``get_message_status`` lists observed receipts per recipient. A
read receipt from one group member does not imply that everyone read the message.
Receipts may precede local submission records and can arrive out of order.

``send_media`` accepts ``kind`` (``image``, ``document`` or ``audio``),
``content_base64``, ``mimetype``, optional display ``filename`` and ``caption``.
Audio captions are rejected. Supplied bytes are limited to 5 MiB; local paths and
URLs are not accepted. ``download_media`` identifies an already synchronized
message and returns base64 content. It rejects unknown or oversized advertised
lengths before downloading, and oversized returned bytes afterward. The native
library downloads into memory; this is not a streaming transport limit. Media
payloads sent by helpers become downloadable only if the provider later supplies
the corresponding media descriptor in an observed message.

``request_history(chat_id, message_id, count)`` uses a retained message as the
oldest known anchor; timestamps are converted to milliseconds for the native
request. A returned ``requested`` status does not promise that the primary phone
will send any history. Inspect later history events and synchronized messages.
The command cannot discover unknown conversations or manufacture a missing anchor.
Messages indexed by the early counting harness have no retained quote/media
payload; those operations return a conflict until a payload is synchronized.
Unusable send-result representations retained by the early prototype are also
rejected as anchors instead of being sent back as provider message IDs.

``get_group`` and ``get_group_members`` accept an exact group JID allowed by
policy. Participant output is paginated after the provider returns its group
snapshot. ``create_group(title, participants)`` requires explicitly named known
individual JIDs with write grants. It does not add grants for the new group.
``update_group_members`` accepts ``add``, ``remove``, ``promote`` or ``demote``;
adding requires known individuals with write grants. Add/remove responses retain
per-participant provider failures. A returned operation must not be interpreted
as confirmation that every participant changed successfully.

Account policy and audit
~~~~~~~~~~~~~~~~~~~~~~~~

Operation grants and chat grants are independent of avatar roles. For example:

.. code-block:: json

   {
     "operations": ["get_contacts", "get_chats", "get_messages", "send_text"],
     "chats": {
       "*": ["read"],
       "123@s.whatsapp.net": ["read", "write"],
       "456@s.whatsapp.net": []
     }
   }

``*`` in operations grants all operational commands. An exact chat entry replaces
the wildcard. For provider-linked PN/LID aliases, every explicit entry must grant
the operation; using another spelling cannot bypass a denial. Read, write and
chat-administration grants are separate. Administrative policy commands use the
``admin`` avatar role and remain available for recovery from an empty operation
policy. Tool discovery reflects avatar roles; account policy can further deny a
discovered tool at execution time.

Policy changes are serialized with operations and saved in ``directory.db``.
Saved policy takes precedence over constructor defaults on restart. Invalid
policies leave existing grants unchanged. Audit records contain actor identity,
operation, chat ID, time and outcome, without content or credentials. They record
operations that reach the application; transport-level authorization failures
are handled by kajenn. A timeout or cancellation after a mutation starts has an
uncertain remote outcome. The application records it as unconfirmed and never
retries automatically.

Event subscriptions without polling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Trusted Python integrations can subscribe while the event loop is running:

.. code-block:: python

   subscription = app.subscribe_events(
       listener.on_account_event,
       kinds=["message_received", "message_receipt", "disconnected"],
   )
   # During integration shutdown:
   await app.unsubscribe_events(subscription)

The listener is an async method receiving a dictionary with ``kind`` and relevant
IDs. Available events are ``connected``, ``disconnected``, ``message_received``,
``message_submitted``, ``message_receipt`` and ``history_received``. Message bodies
and credentials are not included. Chat events are filtered against the current
read policy when delivered. Each listener has a bounded 100-event queue and a
five-second processing timeout. Drops and callback failures are exposed in
``get_status``. Subscriptions are in-process, ephemeral and at-most-once, not a
durable delivery queue or unsolicited MCP notifications to every client. A server
integration can use its listener to drive its own notification mechanism.

``directory.db`` stores contacts, observed chat metadata and messages with private
filesystem permissions, but without encryption. The session is held by one
process. Contacts alone do not establish the existence of a chat. Names from the
address book take precedence over profile names. Provider PN/LID aliases can be
merged, but matching names alone are never sufficient.

``--resync`` requests a snapshot replay of app-state metadata. A completed replay
means events were dispatched, not that every callback succeeded; inspect
``callback_errors`` and record counts. Results explicitly report partial
coverage. The snapshot does not reconcile deleted contacts. The initial pairing
harness counts history batches without retaining their contents: restarting the
server cannot guarantee that those old messages will be resent. New history
batches and live text messages are retained while the server runs. There is no
claim of access to every old message or every chat visible on another device.
Pagination bounds query size, not database retention. The event journal retains
10,000 entries per account; message, audit and outbox retention need a deployment
policy. Outgoing message deletion records a local tombstone, preventing subsequent
history replay from restoring the deleted body.

Expanded command reference
~~~~~~~~~~~~~~~~~~~~~~~~~~

All commands below use the same avatar filtering, account operation policy,
chat grants, serialized execution and content-free audit as the original tools.
No command accepts an arbitrary SDK method name. Newsletter JIDs use the
``@newsletter`` server and require the dedicated channel commands; they cannot
be used as group participants or ordinary text-chat targets.

.. list-table:: Complete command inventory
   :header-rows: 1
   :widths: 25 20 55

   * - Tool
     - Avatar role
     - Contract
   * - ``archive_chat``
     - ``whatsapp_account_write``
     - Archive or unarchive a chat on WhatsApp.
   * - ``block_contact``
     - ``whatsapp_account_manage``
     - Block or unblock an exact contact.
   * - ``create_channel``
     - ``whatsapp_account_manage``
     - Create a WhatsApp newsletter channel.
   * - ``create_community``
     - ``whatsapp_account_manage``
     - Create a WhatsApp community.
   * - ``create_group``
     - ``whatsapp_account_manage``
     - Create a group with explicitly selected known participants.
   * - ``create_group_event``
     - ``whatsapp_account_write``
     - Create a scheduled group event using Unix timestamps; never returns its secret.
   * - ``create_label``
     - ``whatsapp_account_manage``
     - Create an account label with a WhatsApp color index.
   * - ``create_poll``
     - ``whatsapp_account_write``
     - Create a poll with two to twelve unique options; retain its secret privately.
   * - ``deactivate_community``
     - ``whatsapp_account_manage``
     - Deactivate a community administered by this account.
   * - ``decide_group_requests``
     - ``whatsapp_account_manage``
     - Approve or reject explicit group membership requests.
   * - ``decide_message``
     - ``admin``
     - Approve, reject or cancel a queued text; the first applicable decision wins.
   * - ``delete_label``
     - ``whatsapp_account_manage``
     - Delete an account label.
   * - ``delete_message``
     - ``whatsapp_account_write``
     - Delete an observed message for this account only, preserving downloaded media.
   * - ``download_media``
     - ``whatsapp_account_read``
     - Download bounded media from a synchronized message.
   * - ``edit_message``
     - ``whatsapp_account_write``
     - Edit an observed outgoing message; provider time limits still apply.
   * - ``follow_channel``
     - ``whatsapp_account_manage``
     - Follow or unfollow a newsletter channel.
   * - ``get_audit_log``
     - ``admin``
     - Read operation metadata without message text or credentials.
   * - ``get_channel``
     - ``whatsapp_account_read``
     - Read newsletter channel metadata.
   * - ``get_channel_messages``
     - ``whatsapp_account_read``
     - Read one provider page from a newsletter; server IDs differ from message IDs.
   * - ``get_channels``
     - ``whatsapp_account_read``
     - List permitted subscribed newsletter channels.
   * - ``get_chat``
     - ``whatsapp_account_read``
     - Read locally observed metadata for one chat.
   * - ``get_chats``
     - ``whatsapp_account_read``
     - List locally observed permitted chats.
   * - ``get_community_groups``
     - ``whatsapp_account_read``
     - List permitted community subgroups.
   * - ``get_contacts``
     - ``whatsapp_account_read``
     - Search permitted contacts; preserve ambiguous names.
   * - ``get_events``
     - ``whatsapp_account_read``
     - Replay permitted event identifiers from the bounded persistent journal.
   * - ``get_group``
     - ``whatsapp_account_read``
     - Fetch group metadata from WhatsApp.
   * - ``get_group_invite``
     - ``whatsapp_account_manage``
     - Get a group invitation link, optionally revoking the previous link.
   * - ``get_group_members``
     - ``whatsapp_account_read``
     - Fetch a page of current group participants.
   * - ``get_group_requests``
     - ``whatsapp_account_manage``
     - List pending group membership requests.
   * - ``get_message_status``
     - ``whatsapp_account_read``
     - Get submission state and per-recipient observed receipts.
   * - ``get_messages``
     - ``whatsapp_account_read``
     - Read synchronized messages, including provider-known aliases.
   * - ``get_outbox``
     - ``whatsapp_account_read``
     - Read permitted queued texts and dispatch outcomes.
   * - ``get_policy``
     - ``admin``
     - Read account operation and chat grants.
   * - ``get_poll_results``
     - ``whatsapp_account_read``
     - Aggregate retained poll votes; reports gaps and decryption failures.
   * - ``get_privacy``
     - ``whatsapp_account_read``
     - Read account privacy settings.
   * - ``get_profile_picture``
     - ``whatsapp_account_read``
     - Get profile picture metadata; availability depends on privacy settings.
   * - ``get_status``
     - ``whatsapp_account_read``
     - Get connection state and visible record counts.
   * - ``get_sync_status``
     - ``whatsapp_account_read``
     - Get history coverage and callback failures without initiating sync.
   * - ``get_unread``
     - ``whatsapp_account_read``
     - List chats observed as unread; unknown states are excluded.
   * - ``leave_group``
     - ``whatsapp_account_manage``
     - Leave a group.
   * - ``link_community_groups``
     - ``whatsapp_account_manage``
     - Link or unlink subgroups; requires admin grants on every affected group.
   * - ``mark_read``
     - ``whatsapp_account_write``
     - Mark a chat read or unread on WhatsApp.
   * - ``mute_channel``
     - ``whatsapp_account_write``
     - Mute or unmute a followed newsletter channel.
   * - ``mute_chat``
     - ``whatsapp_account_write``
     - Mute or unmute a chat on WhatsApp.
   * - ``pin_chat``
     - ``whatsapp_account_write``
     - Pin or unpin a chat on this account.
   * - ``react_channel_message``
     - ``whatsapp_account_write``
     - React to a newsletter message using its numeric server ID.
   * - ``react_message``
     - ``whatsapp_account_write``
     - React to a synchronized message; empty reaction removes it.
   * - ``reply_message``
     - ``whatsapp_account_write``
     - Reply quoting a synchronized message.
   * - ``request_history``
     - ``whatsapp_account_read``
     - Request older history from a known anchor; completion is asynchronous.
   * - ``respond_group_event``
     - ``whatsapp_account_write``
     - Respond Going, NotGoing or Maybe to a retained group event.
   * - ``revoke_message``
     - ``whatsapp_account_write``
     - Revoke your own observed message for everyone; provider limits apply.
   * - ``save_contact``
     - ``whatsapp_account_manage``
     - Save a contact using an exact personal JID.
   * - ``schedule_message``
     - ``whatsapp_account_write``
     - Queue a text for a Unix timestamp; approval is required by default.
   * - ``search_messages``
     - ``whatsapp_account_read``
     - Search local text in permitted chats only.
   * - ``send_channel_text``
     - ``whatsapp_account_write``
     - Publish text to a newsletter channel you administer.
   * - ``send_chat_state``
     - ``whatsapp_account_write``
     - Send a typing, recording or paused indication.
   * - ``send_media``
     - ``whatsapp_account_write``
     - Send supplied image, document or audio bytes, never server paths.
   * - ``send_text``
     - ``whatsapp_account_write``
     - Send explicit text to an exact known JID.
   * - ``set_chat_label``
     - ``whatsapp_account_write``
     - Assign or remove a label from a chat.
   * - ``set_disappearing_default``
     - ``whatsapp_account_manage``
     - Set the default disappearing-message duration for new chats.
   * - ``set_group_approval``
     - ``whatsapp_account_manage``
     - Enable or disable approval of group membership requests.
   * - ``set_group_description``
     - ``whatsapp_account_manage``
     - Change a group description using its current revision.
   * - ``set_group_disappearing``
     - ``whatsapp_account_manage``
     - Set disappearing messages for a group.
   * - ``set_group_member_add``
     - ``whatsapp_account_manage``
     - Choose who may add group members.
   * - ``set_group_setting``
     - ``whatsapp_account_manage``
     - Set an explicitly supported group permission or sharing setting.
   * - ``set_group_title``
     - ``whatsapp_account_manage``
     - Change a group title.
   * - ``set_policy``
     - ``admin``
     - Replace account grants durably; administrator only.
   * - ``set_presence``
     - ``whatsapp_account_manage``
     - Set account online availability.
   * - ``set_privacy``
     - ``whatsapp_account_manage``
     - Set a named privacy setting; unsupported combinations are rejected by WhatsApp.
   * - ``set_profile_about``
     - ``whatsapp_account_manage``
     - Set the account about text.
   * - ``set_profile_name``
     - ``whatsapp_account_manage``
     - Set the account display name.
   * - ``star_message``
     - ``whatsapp_account_write``
     - Star or unstar an observed message.
   * - ``transcribe_message``
     - ``whatsapp_account_read``
     - Transcribe retained audio with the configured engine; no automatic sends.
   * - ``update_channel``
     - ``whatsapp_account_manage``
     - Change newsletter channel title and description.
   * - ``update_group_members``
     - ``whatsapp_account_manage``
     - Add, remove, promote or demote explicitly selected group participants.
   * - ``vote_poll``
     - ``whatsapp_account_write``
     - Vote in a retained poll; an empty selection withdraws a vote.

Media and mutations
~~~~~~~~~~~~~~~~~~~

``send_media`` accepts ``image``, ``document``, ``audio``, ``voice``, ``video``,
``gif`` and ``sticker`` with the existing 5 MiB byte limit. Voice sets the PTT
flag; GIF uses video playback and requires suitable video bytes, not an arbitrary
GIF file. Stickers require provider-compatible sticker bytes. The adapter does
not transcode or infer MIME types. Audio, voice and sticker captions are rejected.

``edit_message`` and ``revoke_message`` only target retained outgoing messages.
``delete_message`` deletes for this account; ``revoke_message`` requests deletion
for everyone. WhatsApp may reject changes because of age, permissions or message
type. A returned response is not proof that every other device has synchronized.
Neither deletion tool deletes separately downloaded files. Stars and pins are
provider operations; their local listing state is not yet indexed.

Group description changes use ``description_id`` as the optimistic concurrency
token. A conflict is returned to the caller, never retried with a stale value.
Community linking checks the community and every affected group's administration
grant. Unlinking does not request orphan-member removal. Group admission results
preserve individual errors. Provider administrator rights remain necessary.

Polls and group events
~~~~~~~~~~~~~~~~~~~~~~

Polls allow two to twelve unique choices and an explicit selection count. Poll
and event secrets are retained in the private database and never returned by
MCP. Voting and RSVP require either locally created state or a retained original
message containing its secret and creator. Missing data produces a conflict;
the application cannot reconstruct an unavailable secret.

``vote_poll`` accepts an empty list to withdraw a vote. ``get_poll_results``
aggregates retained encrypted updates, tries provider-known PN/LID aliases and
uses the newest observed vote per person. It reports ``observed_votes_only``,
``undecryptable_updates`` and ``scan_truncated``. The bounded scan considers at
most 10,000 retained messages in the chat. Absence of votes is never evidence that
nobody voted remotely. ``create_group_event`` takes Unix timestamps in seconds;
``respond_group_event`` accepts ``Going``, ``NotGoing`` or ``Maybe``.

Persistent text scheduling and approval
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``schedule_message(chat_id, text, due, approval_required=True)`` records a text
for a future Unix timestamp, at most one year ahead. It returns a stable job ID.
Approval is the caller's explicit choice, defaulting to required; this option is
not a mandatory organization-wide approval rule. Restrict the scheduling tool
if callers must not be able to choose direct scheduling.

An ``admin`` uses ``decide_message(job_id, decision)`` with ``approve``, ``reject``
or ``cancel``. The first approval/rejection wins; later requests return the
recorded state and decision actor. Cancellation is possible while pending or
scheduled. ``get_outbox`` exposes only chats with a current read grant.

The worker waits for the next deadline or a queue-change event, without fixed
interval polling. At dispatch it checks current ``schedule_message`` and
``send_text`` grants. Policy denial or disconnection marks the entry ``blocked``;
they are not retried automatically. An interrupted, timed-out or failed send is
``unconfirmed``. Startup converts leftover ``sending`` entries to ``unconfirmed``.
A confirmed local result is ``submitted``, not delivered. The worker never
replays an uncertain send; inspect the chat before creating a replacement job.
Scheduled messages retain the submitter's identity but not their bearer token;
account policy is rechecked, while avatar-role revocation alone does not cancel
an already delegated job. Cancel jobs explicitly when withdrawing delegation.
Scheduling currently supports text only. A reminder is a scheduled text to an
explicit recipient; it does not install a hidden recurring task.

Durable event replay
~~~~~~~~~~~~~~~~~~~~

Live subscriptions remain push-based and ephemeral. In addition, ``get_events``
accepts ``after_id`` and ``limit`` to recover persisted event identifiers after a
client reconnects. It returns ``next_cursor``, ``has_more`` and ``retention_gap``.
The journal retains the latest 10,000 events, contains no bodies or secrets and
filters chat permissions before pagination. This is a recovery API, not an
automatic polling monitor or remote callback service.

Several accounts in one server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Pass ``--accounts /absolute/path/accounts.json`` with a mapping such as:

.. code-block:: json

   {
     "personal": "/private/path/personal-session",
     "support": "/private/path/support-session"
   }

Each account must have been paired separately and have a distinct private
session directory. Endpoints become ``/personal/_mcp`` and ``/support/_mcp``.
Each application has its own database, policy, event journal, queue and lock.
``--session-dir`` still owns server storage and ``mcp.token``. This example uses
one shared owner token for every mounted account: it is not tenant isolation.
Use a proper Kajenn identity application for independently authorized operators.

Remaining boundaries
~~~~~~~~~~~~~~~~~~~~

This expansion does not claim complete parity with the WhatsApp UI. Remaining
work includes whole-chat deletion/clearing semantics against incomplete history,
profile/group photo uploads, channel media and subscriber administration,
status/story publishing, contact registration lookup, richer label listings,
privacy exception lists, recurring schedules and attachment scheduling.
The SDK exposes some of these primitives, but this prototype does not expose
unverified workflows through a generic escape hatch. Audio/video calling needs
an active media transport and remains a separate integration. Full remote
history is not guaranteed by the linked-device protocol.

Offline tests validate schemas, permission denial, persistence, restart recovery,
provider argument construction and protobuf handling. They do not prove that
WhatsApp accepts every mutation on a real account. No personal messages, group
changes, profile changes or new device associations are performed by the tests.

Before publication
------------------

Further live checks include transient network failure, sending to a designated
test chat, graceful shutdown while receiving and phone-confirmed revocation.
The offline suite does not perform those operations. Production integration also
needs encryption and key ownership, retention, deployment packaging and
end-to-end live validation of media, groups, receipts and history requests.
A browser that owns its own session is a separate execution boundary; server MCP
grants cannot constrain someone who owns the local device credentials.

Browser path
------------

The same Rust protocol family is exposed through ``@oxidezap/baileyrs/host`` and
``@oxidezap/whatsapp-rust-bridge/host``. Python and JavaScript adapters are different
packages and may pin different revisions; session portability is not assumed.
A browser may control the central application, or own its own linked device.
One active executor owns each session; automations that must survive closing the
browser belong on the server.

The reference Oxidezap browser client uses a deferred IndexedDB SQLite backend
when OPFS is unavailable. A tab crash can lose the latest unflushed writes. A
production browser integration needs persistence/recovery and multiple-tab tests,
plus checks of its real network origin and media support. Successful bundling and
WASM initialization alone are not end-to-end browser validation.

References
----------

* `Tryx <https://github.com/krypton-byte/tryx>`_
* `whatsapp-rust <https://github.com/oxidezap/whatsapp-rust>`_
* `baileyrs <https://github.com/oxidezap/baileyrs>`_
* `Oxidezap browser storage <https://github.com/oxidezap/client/blob/main/crates/session/src/store/web.rs>`_

On-demand voice transcription
-----------------------------

``transcribe_message(chat_id, message_id, language="it")`` transcribes retained
voice notes and audio messages. Use ``language="auto"`` for language detection.
The command requires the ``whatsapp_account_read`` avatar role, the independent
``transcribe_message`` operation grant and a read grant for the chat. Message
text, transcripts and audio are not added to audit records. Transcripts are
returned only; they are neither persisted nor sent as WhatsApp replies.

The default has no speech engine and returns 503 before downloading audio.
For local recognition, install ``faster-whisper`` in the server's Python
environment and obtain a compatible CTranslate2 model directory explicitly.
Start the server with ``--transcription-model /absolute/path/to/model``.
The model must already exist locally: the worker sets offline mode and uses
``local_files_only=True``. No hosted transcription service is selected implicitly.
See the `faster-whisper documentation <https://github.com/SYSTRAN/faster-whisper>`_
for compatible models and installation requirements.

Audio is downloaded from WhatsApp under the existing 5 MiB limit. A subprocess
receives it through stdin and decodes it in memory. Recognition is limited to
five minutes of decoded audio, 20,000 transcript characters and a 170-second
worker timeout (180 seconds for the complete operation). Audio decoding occurs
before the duration check. CPU inference uses two threads; a model is loaded for
each request. Cancellation terminates and reaps the worker. The account's normal
operation lock remains held during transcription. No automatic transcription or
background monitoring is enabled.

Applications can instead inject a trusted object implementing
``async transcribe(content: bytes, mimetype: str, language: str) -> dict`` through
the ``transcriber`` constructor argument. It returns ``text``, ``language`` and
optional engine metadata. An external provider must be explicitly configured by
the deployer, who determines where audio is sent. The MCP caller cannot select
an endpoint, executable, model path or credentials.
