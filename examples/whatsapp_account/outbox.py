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

"""Persistent text outbox with one-shot dispatch and explicit approval.

A crashed or failed dispatch becomes unconfirmed, never automatically retried.
The worker sleeps until the next due job or an explicit queue change.
"""

import asyncio
import time
import uuid

from kajenn.exceptions import HTTPBadRequest, HTTPForbidden


class _Outbox:
    def __init__(self, application):
        self.app = application
        self.database = application.connection.directory.database
        self.changed = asyncio.Event()
        self.database.execute("""
            CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY, chat_id TEXT, text TEXT, due REAL, state TEXT,
                actor TEXT, decision_actor TEXT, message_id TEXT, error TEXT)
        """)
        with self.database:
            self.database.execute("UPDATE outbox SET state='unconfirmed' WHERE state='sending'")
        self.task = asyncio.create_task(self.run())

    def enqueue(self, chat_id, text, due, approval):
        identifier = uuid.uuid4().hex
        state = "pending" if approval else "scheduled"
        with self.database:
            self.database.execute("INSERT INTO outbox VALUES(?,?,?,?,?,?,?,?,?)",
                                  (identifier, chat_id, text, due, state, self.app.actor, None, None, None))
        self.changed.set()
        return {"id": identifier, "state": state, "due": due}

    def list_jobs(self, limit, offset):
        rows = self.database.execute(
            "SELECT id,chat_id,text,due,state,actor,decision_actor,message_id,error FROM outbox "
            "WHERE readable(chat_id) ORDER BY due,id LIMIT ? OFFSET ?", (limit + 1, offset)).fetchall()
        return self.app.connection.directory.get_page(rows, limit, offset)

    def decide(self, identifier, decision):
        row = self.database.execute("SELECT * FROM outbox WHERE id=?", (identifier,)).fetchone()
        if row is None or not self.app.access.allowed(row["chat_id"], "write"):
            raise HTTPBadRequest("No accessible outbox entry")
        if decision in ("approve", "reject") and row["state"] == "pending":
            state = "scheduled" if decision == "approve" else "rejected"
        elif decision == "cancel" and row["state"] in ("pending", "scheduled"):
            state = "cancelled"
        else:
            return {"id": identifier, "state": row["state"], "decision_actor": row["decision_actor"]}
        with self.database:
            self.database.execute("UPDATE outbox SET state=?,decision_actor=? WHERE id=?",
                                  (state, self.app.actor, identifier))
        self.changed.set()
        return {"id": identifier, "state": state, "decision_actor": self.app.actor}

    async def run(self):
        while True:
            self.changed.clear()
            row = self.database.execute(
                "SELECT * FROM outbox WHERE state='scheduled' ORDER BY due,id LIMIT 1").fetchone()
            delay = max(0, row["due"] - time.time()) if row else None
            if delay == 0:
                await self.dispatch(row["id"])
                continue
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def dispatch(self, identifier):
        async with self.app.lock:
            row = self.database.execute("SELECT * FROM outbox WHERE id=?", (identifier,)).fetchone()
            if row["state"] != "scheduled":
                return
            try:
                self.app.access.require("schedule_message", row["chat_id"])
                self.app.access.require("send_text", row["chat_id"])
            except HTTPForbidden:
                self.finish(identifier, "blocked", error="policy")
                return
            if not self.app.connection.connected:
                self.finish(identifier, "blocked", error="disconnected")
                return
            self.finish(identifier, "sending")
            store = self.app.connection.directory
            store.add_audit(row["actor"], "outbox_dispatch", row["chat_id"], "started")
            try:
                async with asyncio.timeout(45):
                    result = await self.app.connection.send_text(row["chat_id"], row["text"])
            except asyncio.CancelledError:
                self.finish(identifier, "unconfirmed", error="interrupted")
                store.add_audit(row["actor"], "outbox_dispatch", row["chat_id"], "unconfirmed")
                raise
            except Exception:
                self.finish(identifier, "unconfirmed", error="provider_failure")
                store.add_audit(row["actor"], "outbox_dispatch", row["chat_id"], "unconfirmed")
            else:
                self.finish(identifier, "submitted", message_id=result["id"])
                store.add_audit(row["actor"], "outbox_dispatch", row["chat_id"], "submitted")

    def finish(self, identifier, state, error=None, message_id=None):
        with self.database:
            self.database.execute("UPDATE outbox SET state=?,error=?,message_id=? WHERE id=?",
                                  (state, error, message_id, identifier))

    async def stop(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
