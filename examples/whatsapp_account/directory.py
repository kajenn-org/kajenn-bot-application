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

"""Persist observed contacts, chats and text messages separately from device secrets.

Contacts never imply an open chat. Queries expose bounded pages, preserve
ambiguous names and distinguish address-book names from profile push names.
The index is a synchronized subset, not a promise of complete remote history.
"""

import os
import sqlite3
import time
import unicodedata


class _Directory:
    def __init__(self, path):
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        if path.stat().st_mode & 0o077:
            raise PermissionError("Directory database must be private (0600)")
        self.database = sqlite3.connect(path)
        self.database.row_factory = sqlite3.Row
        self.database.create_function("fold", 1, self.normalize, deterministic=True)
        self.database.executescript("""
            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, pn TEXT NOT NULL,
                lid TEXT NOT NULL, source TEXT NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, archived INTEGER,
                source TEXT NOT NULL, last_timestamp INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
                chat_id TEXT NOT NULL, id TEXT NOT NULL, sender TEXT NOT NULL,
                text TEXT, timestamp INTEGER NOT NULL, from_me INTEGER NOT NULL,
                PRIMARY KEY(chat_id,id));
            CREATE INDEX IF NOT EXISTS message_pages ON messages(chat_id,timestamp,id);
        """)

    def normalize(self, value):
        return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())

    def add_contact(self, jid, name, *, pn="", lid="", source="address_book"):
        if not jid or not name:
            return
        existing = self.database.execute(
            "SELECT * FROM contacts WHERE id=? OR pn=? OR lid=? LIMIT 1", (jid, jid, jid)
        ).fetchone()
        identifier = pn or (existing["id"] if existing else jid)
        with self.database:
            # Merge only provider-supplied PN/LID aliases, never matching names.
            self.database.execute(
                "DELETE FROM contacts WHERE id IN (?,?) AND id != ?",
                (pn, lid, identifier),
            )
            self.database.execute("""
                INSERT INTO contacts VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=CASE WHEN excluded.source='address_book' OR contacts.source!='address_book'
                          THEN excluded.name ELSE contacts.name END,
                pn=COALESCE(NULLIF(excluded.pn,''),contacts.pn),
                lid=COALESCE(NULLIF(excluded.lid,''),contacts.lid),
                source=CASE WHEN excluded.source='address_book' THEN excluded.source
                            ELSE contacts.source END, updated_at=excluded.updated_at
            """, (identifier, name, pn, lid, source, time.time()))

    def add_chat(self, jid, name="", *, archived=None, source="history", timestamp=0):
        if not jid:
            return
        with self.database:
            self.database.execute("""
                INSERT INTO chats VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=COALESCE(NULLIF(excluded.name,''),chats.name),
                archived=COALESCE(excluded.archived,chats.archived),
                last_timestamp=MAX(excluded.last_timestamp,chats.last_timestamp)
            """, (jid, name, archived, source, int(timestamp)))

    def add_message(self, chat_id, message_id, sender, text, timestamp, from_me):
        if not chat_id or not message_id:
            return
        self.add_chat(chat_id, source="message", timestamp=timestamp)
        with self.database:
            self.database.execute("""
                INSERT INTO messages VALUES(?,?,?,?,?,?) ON CONFLICT(chat_id,id) DO UPDATE SET
                text=COALESCE(excluded.text,messages.text)
            """, (chat_id, message_id, sender, text, int(timestamp), bool(from_me)))

    def get_page(self, rows, limit, offset):
        return {"items": [dict(row) for row in rows[:limit]],
                "next_offset": offset + limit if len(rows) > limit else None,
                "coverage": "observed_and_synchronized_subset"}

    def get_contacts(self, query, limit, offset):
        rows = self.database.execute("""
            SELECT * FROM contacts WHERE instr(fold(name),?) > 0
            ORDER BY fold(name),id LIMIT ? OFFSET ?
        """, (self.normalize(query), limit + 1, offset)).fetchall()
        return self.get_page(rows, limit, offset)

    def get_chats(self, query, limit, offset, include_archived):
        rows = self.database.execute("""
            SELECT h.id,COALESCE(NULLIF(c.name,''),h.name) AS name,h.archived,
                   h.source,h.last_timestamp
            FROM chats h LEFT JOIN contacts c ON (h.id=c.id OR h.id=c.pn OR h.id=c.lid)
            WHERE (? OR h.archived IS NULL OR h.archived=0)
              AND instr(fold(COALESCE(NULLIF(c.name,''),h.name)),?) > 0
            ORDER BY h.last_timestamp DESC,h.id LIMIT ? OFFSET ?
        """, (include_archived, self.normalize(query), limit + 1, offset)).fetchall()
        return self.get_page(rows, limit, offset)

    def get_messages(self, chat_id, limit, offset):
        rows = self.database.execute("""
            SELECT * FROM messages WHERE chat_id=? ORDER BY timestamp DESC,id DESC
            LIMIT ? OFFSET ?
        """, (chat_id, limit + 1, offset)).fetchall()
        return self.get_page(rows, limit, offset)

    def known_peer(self, jid):
        if not jid:
            return False
        return self.database.execute("""
            SELECT id FROM contacts WHERE id=? OR pn=? OR lid=?
            UNION SELECT id FROM chats WHERE id=? LIMIT 1
        """, (jid, jid, jid, jid)).fetchone() is not None

    @property
    def counts(self):
        return {name: self.database.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
                for name in ("contacts", "chats", "messages")}

    def close(self):
        self.database.close()
