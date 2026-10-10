# Copyright 2025 Softwell S.r.l.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Run explicit local pairing and lifecycle checks for the account prototype."""

import argparse
import asyncio
import json
from pathlib import Path

import segno
from tryx.backend import SqliteStore
from tryx.client import Tryx
from tryx.events import EvConnected, EvHistorySync, EvMessage, EvPairingQrCode
from tryx.types import JID

from examples.whatsapp_account.session import _Session


class _Probe:
    def __init__(self, arguments):
        self.arguments = arguments
        self.connected = asyncio.Event()
        self.history_batches = 0
        self.messages = 0
        self.session = _Session(arguments.session_dir, self.create_runtime)

    def create_runtime(self, database):
        runtime = Tryx(SqliteStore(str(database), 0))
        runtime.on(EvPairingQrCode)(self.show_qr)
        runtime.on(EvConnected)(self.mark_connected)
        runtime.on(EvMessage)(self.record_message)
        runtime.on(EvHistorySync)(self.record_history)
        return runtime

    async def show_qr(self, client, event):
        print("Scan this QR from WhatsApp > Linked devices. Keep this terminal private.")
        segno.make(event.code, micro=False).terminal(compact=True)

    async def mark_connected(self, client, event):
        self.connected.set()
        print("Connected")

    async def record_message(self, client, event):
        self.messages += 1

    async def record_history(self, client, event):
        self.history_batches += 1

    async def run(self):
        try:
            client = await self.session.start()
            connected = asyncio.create_task(self.connected.wait())
            try:
                done, _ = await asyncio.wait(
                    (connected, self.session.run_task), timeout=self.arguments.duration,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self.session.run_task in done:
                    await self.session.run_task
                    raise RuntimeError("Client exited before the probe completed")
                if connected not in done:
                    raise TimeoutError("Pairing/connection timed out; session will be preserved")
            finally:
                connected.cancel()
                await asyncio.gather(connected, return_exceptions=True)
            if self.arguments.send_to:
                await client.send_text(JID(*self.arguments.send_to.rsplit("@", 1)), self.arguments.text)
                print("Send operation returned; delivery has not been confirmed")
            if self.arguments.logout:
                await self.session.stop(logout=True)
                print("Logout requested. Verify device removal on the phone; state was retained.")
            else:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self.session.run_task), self.arguments.duration
                    )
                except TimeoutError:
                    pass
            print(json.dumps({"messages": self.messages, "history_batches": self.history_batches}))
        finally:
            await self.session.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Explicitly connect to WhatsApp")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--send-to", help="Exact WhatsApp JID, e.g. 391234567890@s.whatsapp.net")
    parser.add_argument("--text")
    parser.add_argument("--logout", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("Network access requires --live; offline checks are in the test suite")
    if bool(args.send_to) != (args.text is not None):
        parser.error("--send-to and --text must be supplied together")
    if args.duration <= 0:
        parser.error("--duration must be positive")
    asyncio.run(_Probe(args).run())


if __name__ == "__main__":
    main()
