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

"""Offline checks against the compiled Tryx extension; no client run loop starts."""

import asyncio

import pytest
from tryx.backend import SqliteStore
from tryx.client import Tryx


class TestNativeLifecycle:
    def test_patch_exports_and_unstarted_client(self, tmp_path):
        runtime = Tryx(SqliteStore(str(tmp_path / "session.db"), 0))
        client = runtime.get_client()
        assert callable(client.advanced.wait_for_client)
        for method in (client.advanced.disconnect, client.advanced.logout):
            with pytest.raises(RuntimeError, match="not running"):
                method()

    async def test_cancel_waiting_for_local_initialization(self, tmp_path):
        runtime = Tryx(SqliteStore(str(tmp_path / "session.db"), 0))
        waiting = asyncio.ensure_future(runtime.get_client().advanced.wait_for_client())
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting

    async def test_device_records_survive_reopen(self, tmp_path):
        path = str(tmp_path / "session.db")
        first = SqliteStore(path, 0)
        device_id = await first.create_new_device()
        reopened = SqliteStore(path, 0)
        assert await reopened.device_exists(device_id)
        assert any(device.id == device_id for device in await reopened.list_devices())
