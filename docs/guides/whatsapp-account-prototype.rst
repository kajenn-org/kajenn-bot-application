WhatsApp account prototype
==========================

Version: 0.3 — Last updated: 2026-10-10 — Status: UNDER REVIEW

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
from trusted Python code. There are 25 MCP tools:

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
Pagination bounds query size, not database retention; automatic retention is a
future integration concern.

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
