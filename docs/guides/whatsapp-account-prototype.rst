WhatsApp account prototype
==========================

Version: 0.1 — Last updated: 2026-10-10 — Status: UNDER REVIEW

This source-tree prototype evaluates a personal WhatsApp linked device using
Tryx and whatsapp-rust. It is a local test harness, not an exported kajenn
application or a released account integration. The existing WhatsApp application
continues to use the Business Cloud API.

Scope and ownership
-------------------

The prototype provides explicit pairing, a persistent SQLite device store,
exclusive process ownership, a bounded graceful stop, an explicit logout request,
and an optional text-send probe. It counts live messages and history-sync batches
without printing their contents. A history archive and REST/MCP routes belong to
the next integration step.

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
The wheel has the distinct local version ``1.5.0+kajenn.1``; it is not on PyPI.

.. code-block:: console

   python -m venv .venv-account
   .venv-account/bin/python -m pip install -e '.[test]' -r examples/whatsapp_account/requirements.txt
   rustup toolchain install 1.94.0 --profile minimal
   .venv-account/bin/python examples/whatsapp_account/build_tryx.py temp/tryx-build
   .venv-account/bin/python -m pip install temp/tryx-build/dist/tryx-*.whl

The small MIT-licensed patch adds three methods to ``AdvancedClient``:
``wait_for_client`` waits for local initialization, ``disconnect`` flushes and
stops the connection, and ``logout`` requests companion-device removal before
stopping. ``TRYX-LICENSE`` accompanies the patch. The repository's Python harness
is Apache-2.0.

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

Before application integration
------------------------------

The required live checks are pairing, reconnect after restart, graceful stop
while receiving, transient network failure, a message to a designated test chat,
history-sync observation and phone-confirmed revocation. They have not been
performed by the offline test suite. A synchronized history batch does not promise
access to every old message.

A future ``WhatsAppAccountApplication`` can reuse kajenn's authenticated routing
and operation/chat policy pattern. It must enforce policy where commands execute.
A browser that owns its own session is a separate execution boundary; a server's
MCP grants cannot constrain someone who also owns those local device credentials.

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
