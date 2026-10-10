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

"""Implementation tests for isolated bounded event listeners."""

import asyncio

from examples.whatsapp_account.events import _Events


async def test_events_are_pushed_without_polling_and_listeners_are_isolated():
    events = _Events()
    seen = []
    ready = asyncio.Event()

    async def collect(event):
        seen.append(event)
        ready.set()

    identifier = events.subscribe_events(collect, ["message_received"])
    events.emit("connected")
    events.emit("message_received", chat_id="1@lid", message_id="a")
    await asyncio.wait_for(ready.wait(), 1)
    assert seen == [{"kind": "message_received", "chat_id": "1@lid", "message_id": "a"}]
    await events.unsubscribe_events(identifier)
    assert events.status["subscribers"] == 0


async def test_slow_subscriber_overflow_is_visible_and_shutdown_is_bounded():
    events = _Events()
    hold = asyncio.Event()

    async def slow(event):
        await hold.wait()

    events.subscribe_events(slow)
    for _ in range(101):
        events.emit("connected")
    assert events.status["dropped"] == 1
    await asyncio.wait_for(events.stop(), 1)
