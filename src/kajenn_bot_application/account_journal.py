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

"""Encrypted bounded event, audit and approval records with atomic replacement."""

import copy
import time
import uuid

from kajenn.exceptions import HTTPException

from .account_store import _AccountStore


class _AccountJournal:
    retention = 1000

    def __init__(self, path, key):
        self.store = _AccountStore(path, key)
        self.state = None

    def open(self):
        self.state = self.store.open() or {
            "sequence": 0,
            "events": [],
            "audit": [],
            "jobs": [],
            "floors": {"events": 0, "audit": 0},
        }
        state = copy.deepcopy(self.state)
        for job in state["jobs"]:
            if job["state"] == "sending":
                job["state"] = "unconfirmed"
        if state != self.state:
            self.commit(state)

    def close(self):
        self.store.close()

    def commit(self, state):
        self.store.save(state)
        self.state = state

    def append(self, collection, account_id, **values):
        state = copy.deepcopy(self.state)
        state["sequence"] += 1
        record = dict(values, id=state["sequence"], account_id=account_id, timestamp=time.time())
        state[collection].append(record)
        removed = state[collection][: -self.retention]
        if removed:
            state["floors"][collection] = removed[-1]["id"]
        state[collection] = state[collection][-self.retention :]
        self.commit(state)

    def page(self, collection, account_id, after_id, limit, allowed) -> dict:
        records = self.state[collection]
        visible = [
            record
            for record in records
            if record["account_id"] == account_id and record["id"] > after_id and allowed(record)
        ]
        items = visible[:limit]
        return {
            "items": copy.deepcopy(items),
            "next_after_id": items[-1]["id"] if items else after_id,
            "has_more": len(visible) > limit,
            "retention_gap": after_id < self.state["floors"][collection],
            "coverage": "observed locally; bounded retention, not complete Telegram history",
        }

    def enqueue(self, account_id, chat_id, text, actor):
        state = copy.deepcopy(self.state)
        if len(state["jobs"]) >= self.retention:
            finished = next(
                (
                    job
                    for job in state["jobs"]
                    if job["state"] in ("submitted", "rejected", "cancelled")
                ),
                None,
            )
            if finished is None:
                raise HTTPException(409, "approval queue is full; resolve pending requests")
            state["jobs"].remove(finished)
        job = {
            "id": str(uuid.uuid4()),
            "account_id": account_id,
            "chat_id": chat_id,
            "text": text,
            "actor": actor,
            "timestamp": time.time(),
            "state": "pending",
            "decision_actor": None,
            "message_id": None,
        }
        state["jobs"].append(job)
        self.commit(state)
        return copy.deepcopy(job)

    def get_job(self, account_id, job_id):
        return next(
            (
                copy.deepcopy(job)
                for job in self.state["jobs"]
                if job["account_id"] == account_id and job["id"] == job_id
            ),
            None,
        )

    def update_job(self, job, **changes):
        state = copy.deepcopy(self.state)
        record = next(record for record in state["jobs"] if record["id"] == job["id"])
        record.update(changes)
        self.commit(state)
        return copy.deepcopy(record)

    def get_jobs(self, account_id, limit, offset, allowed):
        visible = [
            job for job in self.state["jobs"] if job["account_id"] == account_id and allowed(job)
        ]
        return {
            "items": copy.deepcopy(visible[offset : offset + limit]),
            "next_offset": offset + limit if len(visible) > offset + limit else None,
        }
