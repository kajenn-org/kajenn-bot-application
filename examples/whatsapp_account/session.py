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

"""Own one experimental WhatsApp session in a private directory.

The factory supplies a patched Tryx runtime. Startup waits for local client
initialization, not login. Stop flushes the engine and waits for its run loop;
a timed-out stop keeps the filesystem lease and can be awaited again. Logout
requests remote device removal but cannot confirm it. No state is deleted.
"""

import asyncio
import os
from pathlib import Path

from filelock import FileLock


class _Session:
    def __init__(self, directory: Path, factory, timeout: float = 30):
        self.directory = directory
        self.factory = factory
        self.timeout = timeout
        self.lease = FileLock(directory / "owner.lock")
        self.runtime = None
        self.client = None
        self.run_task = None
        self.ready_task = None
        self.stop_task = None
        self.used = False
        self.command_lock = asyncio.Lock()

    async def start(self):
        async with self.command_lock:
            if self.used:
                raise RuntimeError("Create a new session controller for each run")
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.directory.is_symlink() or self.directory.stat().st_mode & 0o077:
                raise PermissionError("Session directory must be private (mode 0700)")
            self.lease.acquire(timeout=0)
            self.used = True
            try:
                database = self.directory / "session.db"
                descriptor = os.open(database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                os.close(descriptor)
                if database.stat().st_mode & 0o077:
                    raise PermissionError("Session database must be private (mode 0600)")
                self.runtime = self.factory(database)
                self.client = self.runtime.get_client()
                for name in ("wait_for_client", "disconnect", "logout"):
                    if not callable(getattr(self.client.advanced, name, None)):
                        raise RuntimeError("Install the Tryx lifecycle patch before starting")
                self.run_task = asyncio.ensure_future(self.runtime.run())
                self.ready_task = asyncio.ensure_future(self.client.advanced.wait_for_client())
            except BaseException:
                self.lease.release()
                raise
        done, _ = await asyncio.wait(
            (self.run_task, self.ready_task),
            timeout=self.timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self.run_task in done:
            await self.stop()
            raise RuntimeError("Client exited during startup")
        if self.ready_task not in done:
            raise TimeoutError("Client initialization timed out; call stop before restarting")
        await self.ready_task
        return self.client

    async def stop(self, *, logout: bool = False):
        async with self.command_lock:
            if self.run_task is None:
                return
            if (self.stop_task is not None and self.stop_task.done()
                    and self.stop_task.exception() is not None and not self.run_task.done()):
                self.stop_task = None
            if self.stop_task is None:
                if logout and not self.client.is_connected():
                    raise RuntimeError("Connect before requesting remote logout")
                self.stop_task = asyncio.create_task(self.finish(logout))
            elif logout:
                raise RuntimeError("Shutdown already started; logout was not requested")
            task = self.stop_task
        # Cancellation/timeout of a caller must not cancel the engine's flush.
        await asyncio.wait_for(asyncio.shield(task), timeout=self.timeout)

    async def finish(self, logout: bool):
        try:
            if not self.run_task.done():
                done, _ = await asyncio.wait(
                    (self.run_task, self.ready_task), return_when=asyncio.FIRST_COMPLETED
                )
                if self.run_task not in done:
                    await self.ready_task
                    if logout:
                        await self.client.advanced.logout()
                    else:
                        await self.client.advanced.disconnect()
            await self.run_task
        finally:
            # A provider failure while still running must retain exclusive ownership.
            if self.run_task.done():
                if self.ready_task is not None:
                    self.ready_task.cancel()
                    await asyncio.gather(self.ready_task, return_exceptions=True)
                self.lease.release()
