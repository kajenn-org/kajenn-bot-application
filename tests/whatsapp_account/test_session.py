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

"""Implementation tests for the isolated WhatsApp account prototype."""

import asyncio

import pytest
from filelock import FileLock, Timeout

from examples.whatsapp_account.session import _Session


class FakeRuntime:
    def __init__(self, path):
        self.path = path
        self.advanced = self
        self.ready = asyncio.Event()
        self.ended = asyncio.Event()
        self.flush = asyncio.Event()
        self.flush.set()
        self.stops = 0
        self.logouts = 0
        self.stop_failure = None
        self.failure = None
        self.connected = True

    def get_client(self):
        return self

    def is_connected(self):
        return self.connected

    async def run(self):
        if self.failure:
            raise self.failure
        self.ready.set()
        await self.ended.wait()

    async def wait_for_client(self):
        await self.ready.wait()

    async def disconnect(self):
        self.stops += 1
        if self.stop_failure:
            raise self.stop_failure
        await self.flush.wait()
        self.path.write_bytes(b"flushed session")
        self.ended.set()

    async def logout(self):
        self.logouts += 1
        await self.disconnect()


async def test_stop_flushes_before_unlock_and_preserves_session(tmp_path):
    folder = tmp_path / "account"
    session = _Session(folder, FakeRuntime)
    await session.start()
    with pytest.raises(Timeout):
        FileLock(folder / "owner.lock").acquire(timeout=0)
    await session.stop()
    assert (folder / "session.db").read_bytes() == b"flushed session"
    with FileLock(folder / "owner.lock").acquire(timeout=0):
        pass
    restarted = _Session(folder, FakeRuntime)
    await restarted.start()
    assert restarted.client.path.read_bytes() == b"flushed session"
    await restarted.stop()


async def test_timeout_keeps_owner_until_flush_finishes(tmp_path):
    session = _Session(tmp_path / "account", FakeRuntime, timeout=0.01)
    await session.start()
    session.client.flush.clear()
    with pytest.raises(TimeoutError):
        await session.stop()
    with pytest.raises(Timeout):
        await _Session(session.directory, FakeRuntime).start()
    session.client.flush.set()
    await session.stop()
    assert session.client.stops == 1
    assert not session.lease.is_locked


async def test_cancelling_stop_does_not_cancel_flush(tmp_path):
    session = _Session(tmp_path / "account", FakeRuntime)
    await session.start()
    session.client.flush.clear()
    stop = asyncio.create_task(session.stop())
    await asyncio.sleep(0)
    stop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop
    assert session.lease.is_locked
    session.client.flush.set()
    await session.stop()
    assert session.client.stops == 1


async def test_logout_requires_connection_and_preserves_files(tmp_path):
    session = _Session(tmp_path / "account", FakeRuntime)
    await session.start()
    session.client.connected = False
    with pytest.raises(RuntimeError, match="Connect"):
        await session.stop(logout=True)
    session.client.connected = True
    await session.stop(logout=True)
    assert session.client.logouts == 1
    assert session.client.path.exists()


async def test_startup_failure_preserves_existing_state(tmp_path):
    folder = tmp_path / "account"
    folder.mkdir(mode=0o700)
    database = folder / "session.db"
    database.write_bytes(b"saved identity")
    database.chmod(0o600)
    runtime = FakeRuntime(database)
    runtime.failure = ConnectionError("transient")
    session = _Session(folder, lambda path: runtime)
    with pytest.raises(ConnectionError, match="transient"):
        await session.start()
    assert database.read_bytes() == b"saved identity"
    assert not session.lease.is_locked


async def test_missing_patch_fails_before_network_and_releases_owner(tmp_path):
    runtime = FakeRuntime(tmp_path / "unused")
    runtime.logout = None
    session = _Session(tmp_path / "account", lambda path: runtime)
    with pytest.raises(RuntimeError, match="lifecycle patch"):
        await session.start()
    assert session.run_task is None
    assert not session.lease.is_locked


async def test_non_private_directory_is_rejected(tmp_path):
    folder = tmp_path / "account"
    folder.mkdir(mode=0o755)
    with pytest.raises(PermissionError):
        await _Session(folder, FakeRuntime).start()


async def test_stop_before_start_is_safe_and_controller_is_single_use(tmp_path):
    session = _Session(tmp_path / "account", FakeRuntime)
    await session.stop()
    await session.start()
    await session.stop()
    with pytest.raises(RuntimeError, match="new session controller"):
        await session.start()


async def test_failed_disconnect_can_be_retried_without_losing_owner(tmp_path):
    session = _Session(tmp_path / "account", FakeRuntime)
    await session.start()
    session.client.stop_failure = ConnectionError("flush failed")
    with pytest.raises(ConnectionError, match="flush failed"):
        await session.stop()
    assert session.lease.is_locked
    session.client.stop_failure = None
    await session.stop()
    assert session.client.stops == 2
    assert not session.lease.is_locked


async def test_concurrent_stop_requests_share_one_flush(tmp_path):
    session = _Session(tmp_path / "account", FakeRuntime)
    await session.start()
    await asyncio.gather(session.stop(), session.stop())
    assert session.client.stops == 1
