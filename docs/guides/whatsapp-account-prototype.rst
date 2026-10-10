WhatsApp account prototype
==========================

Version: 0.2 — Last updated: 2026-10-10 — Status: UNDER REVIEW

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
The wheel has the distinct local version ``1.5.0+kajenn.3``; it is not on PyPI.

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
Protobuf action lookup for contact and chat updates.

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

Authentication creates a local-owner avatar with the ``whatsapp_account`` tag.
Kajenn filters tool discovery and execution using that avatar. The tools are:

* ``get_status``: connection state, record counts, replay state and callback errors.
* ``get_contacts``: bounded name search, preserving ambiguous matches.
* ``get_chats``: observed conversations, excluding known archived chats by default.
* ``get_messages``: locally retained text messages for an exact known JID.
* ``send_text``: an explicitly requested message to an exact known JID.

Contact lookup never chooses among names automatically. Sending requires a JID
returned by contacts or chats; success means submitted, not delivered. Pairing,
logout, credentials and resynchronization are not exposed as remote tools.
This prototype grants all five tools to the owner; per-operation grants are not
implemented here.

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
needs encryption and key ownership, retention and more granular policy.
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
