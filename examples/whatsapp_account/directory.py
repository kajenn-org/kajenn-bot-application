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

import json
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
        self.database.create_function("readable", 1, self.allow_read)
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
            CREATE TABLE IF NOT EXISTS aliases (pn TEXT, lid TEXT, PRIMARY KEY(pn,lid));
            CREATE TABLE IF NOT EXISTS message_data (
                chat_id TEXT, id TEXT, proto BLOB, kind TEXT, status TEXT,
                PRIMARY KEY(chat_id,id));
            CREATE TABLE IF NOT EXISTS receipts (
                chat_id TEXT, id TEXT, sender TEXT, status TEXT, timestamp INTEGER,
                PRIMARY KEY(chat_id,id,sender,status));
            CREATE TABLE IF NOT EXISTS chat_state (
                id TEXT PRIMARY KEY, unread INTEGER, muted INTEGER);
            CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS event_journal (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, kind TEXT, chat_id TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS audit (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, actor TEXT,
                operation TEXT, chat_id TEXT, outcome TEXT);
            CREATE INDEX IF NOT EXISTS message_pages ON messages(chat_id,timestamp,id);
        """)

    def allow_read(self, jid):
        return True

    def normalize(self, value):
        return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())

    def add_contact(self, jid, name, *, pn="", lid="", source="address_book"):
        if pn and lid:
            self.add_alias(pn, lid)
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
        deleted = self.database.execute(
            "SELECT 1 FROM message_data WHERE chat_id=? AND id=? AND status='deleted'",
            (chat_id, message_id)).fetchone()
        if deleted:
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
            SELECT * FROM contacts WHERE readable(id) AND instr(fold(name),?) > 0
            ORDER BY fold(name),id LIMIT ? OFFSET ?
        """, (self.normalize(query), limit + 1, offset)).fetchall()
        return self.get_page(rows, limit, offset)

    def get_chats(self, query, limit, offset, include_archived):
        rows = self.database.execute("""
            SELECT h.id,COALESCE(NULLIF(c.name,''),h.name) AS name,h.archived,
                   h.source,h.last_timestamp
            FROM chats h LEFT JOIN contacts c ON (h.id=c.id OR h.id=c.pn OR h.id=c.lid)
            WHERE readable(h.id) AND (? OR h.archived IS NULL OR h.archived=0)
              AND instr(fold(COALESCE(NULLIF(c.name,''),h.name)),?) > 0
            ORDER BY h.last_timestamp DESC,h.id LIMIT ? OFFSET ?
        """, (include_archived, self.normalize(query), limit + 1, offset)).fetchall()
        return self.get_page(rows, limit, offset)

    def get_messages(self, chat_id, limit, offset):
        rows = self.database.execute("""
            SELECT m.*,d.kind,d.status FROM messages m LEFT JOIN message_data d
            ON m.chat_id=d.chat_id AND m.id=d.id
            WHERE m.chat_id IN (SELECT value FROM json_each(?))
            AND readable(m.chat_id) ORDER BY m.timestamp DESC,m.id DESC
            LIMIT ? OFFSET ?
        """, (json.dumps(self.get_aliases(chat_id)), limit + 1, offset)).fetchall()
        return self.get_page(rows, limit, offset)

    def known_peer(self, jid):
        if not jid:
            return False
        return self.database.execute("""
            SELECT id FROM contacts WHERE id IN (SELECT value FROM json_each(?))
            UNION SELECT id FROM chats WHERE id IN (SELECT value FROM json_each(?)) LIMIT 1
        """, (json.dumps(self.get_aliases(jid)), json.dumps(self.get_aliases(jid)))).fetchone() is not None

    @property
    def counts(self):
        return {name: self.database.execute(f"SELECT count(*) FROM {name} WHERE readable({'chat_id' if name == 'messages' else 'id'})").fetchone()[0]
                for name in ("contacts", "chats", "messages")}

    def close(self):
        self.database.close()


    def add_alias(self, pn, lid):
        if not pn.endswith("@s.whatsapp.net") or not lid.endswith("@lid"):
            return
        with self.database:
            self.database.execute("INSERT OR IGNORE INTO aliases VALUES(?,?)", (pn, lid))

    def get_aliases(self, jid):
        found = {jid}
        pending = [jid]
        while pending:
            item = pending.pop()
            rows = self.database.execute(
                "SELECT id,pn,lid FROM contacts WHERE id=? OR pn=? OR lid=?",
                (item, item, item)).fetchall()
            pairs = self.database.execute("SELECT pn,lid FROM aliases WHERE pn=? OR lid=?",
                                          (item, item)).fetchall()
            for row in [*rows, *pairs]:
                for value in row:
                    if value and value not in found:
                        found.add(value)
                        pending.append(value)
        return [jid, *sorted(found - {jid})]

    def get_setting(self, name):
        row = self.database.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_setting(self, name, value):
        with self.database:
            self.database.execute("INSERT OR REPLACE INTO settings VALUES(?,?)",
                                  (name, json.dumps(value)))

    def add_audit(self, actor, operation, chat_id, outcome):
        with self.database:
            self.database.execute(
                "INSERT INTO audit(timestamp,actor,operation,chat_id,outcome) VALUES(?,?,?,?,?)",
                (time.time(), actor, operation, chat_id, outcome))

    def get_audit_log(self, limit, offset):
        rows = self.database.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ? OFFSET ?",
                                     (limit + 1, offset)).fetchall()
        return self.get_page(rows, limit, offset)

    def set_chat_state(self, jid, *, unread=None, muted=None):
        self.add_chat(jid, source="app_state")
        with self.database:
            self.database.execute("""
                INSERT INTO chat_state VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET
                unread=COALESCE(excluded.unread,chat_state.unread),
                muted=COALESCE(excluded.muted,chat_state.muted)
            """, (jid, unread, muted))

    def get_chat(self, jid):
        rows = self.database.execute("""
            SELECT h.*,s.unread,s.muted FROM chats h LEFT JOIN chat_state s ON h.id=s.id
            WHERE h.id IN (SELECT value FROM json_each(?)) AND readable(h.id)
        """, (json.dumps(self.get_aliases(jid)),)).fetchall()
        return {"items": [dict(r) for r in rows], "coverage": "observed_and_synchronized_subset"}

    def get_unread(self, limit, offset):
        rows = self.database.execute("""
            SELECT h.* FROM chats h JOIN chat_state s ON h.id=s.id
            WHERE s.unread=1 AND readable(h.id)
            ORDER BY h.last_timestamp DESC,h.id LIMIT ? OFFSET ?
        """, (limit + 1, offset)).fetchall()
        return self.get_page(rows, limit, offset)

    def search_messages(self, query, chat_id, limit, offset):
        rows = self.database.execute("""
            SELECT * FROM messages WHERE readable(chat_id) AND instr(fold(text),?) > 0
            AND (? IS NULL OR chat_id IN (SELECT value FROM json_each(?)))
            ORDER BY timestamp DESC,chat_id,id LIMIT ? OFFSET ?
        """, (self.normalize(query), chat_id, json.dumps(self.get_aliases(chat_id)) if chat_id
              else "[]", limit + 1, offset)).fetchall()
        return self.get_page(rows, limit, offset)

    def get_message(self, chat_id, message_id):
        rows = self.database.execute("""
            SELECT m.*,d.proto,d.kind,d.status FROM messages m LEFT JOIN message_data d
            ON m.chat_id=d.chat_id AND m.id=d.id
            WHERE m.chat_id IN (SELECT value FROM json_each(?)) AND m.id=?
        """, (json.dumps(self.get_aliases(chat_id)), message_id)).fetchall()
        return dict(rows[0]) if rows else None

    def set_message_data(self, chat_id, message_id, proto=None, kind="text", status="received"):
        if self.database.execute(
                "SELECT 1 FROM message_data WHERE chat_id=? AND id=? AND status='deleted'",
                (chat_id, message_id)).fetchone():
            return
        with self.database:
            self.database.execute("""
                INSERT INTO message_data VALUES(?,?,?,?,?) ON CONFLICT(chat_id,id) DO UPDATE SET
                proto=COALESCE(excluded.proto,message_data.proto), kind=excluded.kind,
                status=CASE WHEN message_data.status='submitted' THEN 'submitted'
                            ELSE excluded.status END
            """, (chat_id, message_id, proto, kind, status))

    def add_receipt(self, chat_id, message_id, sender, status, timestamp):
        with self.database:
            self.database.execute("INSERT OR REPLACE INTO receipts VALUES(?,?,?,?,?)",
                                  (chat_id, message_id, sender, status, timestamp))

    def get_message_status(self, chat_id, message_id):
        message = self.get_message(chat_id, message_id)
        rows = self.database.execute("""
            SELECT sender,status,timestamp FROM receipts
            WHERE chat_id IN (SELECT value FROM json_each(?)) AND id=? ORDER BY timestamp
        """, (json.dumps(self.get_aliases(chat_id)), message_id)).fetchall()
        return {"id": message_id, "chat_id": chat_id,
                "submission": message["status"] if message else None,
                "receipts": [dict(row) for row in rows],
                "coverage": "observed_receipts_not_all_group_members"}

    def add_event(self, kind, data):
        # Only stable identifiers are retained; never message bodies or credentials.
        selected = {key: data[key] for key in ("chat_id", "message_id", "status") if key in data}
        with self.database:
            self.database.execute(
                "INSERT INTO event_journal(timestamp,kind,chat_id,data) VALUES(?,?,?,?)",
                (time.time(), kind, selected.get("chat_id"), json.dumps(selected)))
            self.database.execute(
                "DELETE FROM event_journal WHERE seq <= (SELECT MAX(seq)-10000 FROM event_journal)")

    def get_events(self, after_id, limit):
        first = self.database.execute("SELECT MIN(seq) FROM event_journal").fetchone()[0]
        rows = self.database.execute(
            "SELECT * FROM event_journal WHERE seq>? AND (chat_id IS NULL OR readable(chat_id)) "
            "ORDER BY seq LIMIT ?", (after_id, limit + 1)).fetchall()
        return {"items": [{"id": row["seq"], "timestamp": row["timestamp"], "kind": row["kind"],
                           **json.loads(row["data"])} for row in rows[:limit]],
                "next_cursor": rows[min(limit, len(rows))-1]["seq"] if rows else after_id,
                "has_more": len(rows) > limit,
                "retention_gap": bool(after_id and first and after_id < first - 1)}
