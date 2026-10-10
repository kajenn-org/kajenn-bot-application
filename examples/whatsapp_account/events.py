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

"""Bounded in-process event subscriptions; no polling or arbitrary remote callbacks.

Listeners are trusted Python integrations and receive identifiers, not message
bodies or credentials. Slow listeners have independent queues; dropped events and
callback failures are observable. Subscriptions are ephemeral and at-most-once.
"""

import asyncio
import copy
import uuid


class _Events:
    def __init__(self):
        self.listeners = {}
        self.journal = None
        self.dropped = 0
        self.failed = 0

    def subscribe_events(self, callback, kinds=None):
        identifier = uuid.uuid4().hex
        queue = asyncio.Queue(maxsize=100)
        task = asyncio.create_task(self.deliver(callback, queue))
        self.listeners[identifier] = (queue, task, set(kinds) if kinds else None)
        return identifier

    async def unsubscribe_events(self, identifier):
        queue, task, kinds = self.listeners.pop(identifier)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def emit(self, kind, **data):
        if self.journal is not None:
            self.journal(kind, data)
        for queue, task, kinds in self.listeners.values():
            if kinds is not None and kind not in kinds:
                continue
            try:
                queue.put_nowait(copy.deepcopy({"kind": kind, **data}))
            except asyncio.QueueFull:
                self.dropped += 1

    async def deliver(self, callback, queue):
        while True:
            event = await queue.get()
            try:
                async with asyncio.timeout(5):
                    await callback(event)
            except Exception:
                self.failed += 1
            finally:
                queue.task_done()

    @property
    def status(self):
        return {"subscribers": len(self.listeners), "dropped": self.dropped,
                "callback_failures": self.failed, "delivery": "ephemeral_at_most_once"}

    async def stop(self):
        for identifier in list(self.listeners):
            await self.unsubscribe_events(identifier)
