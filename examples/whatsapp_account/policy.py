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

"""Persistent account grants, independent of the caller's avatar roles.

Exact entries override wildcard grants. All provider-known aliases must allow
access, so a PN/LID spelling cannot bypass an explicit restriction. Empty policy
denies operations. Administrative policy routes require a separate avatar role.
"""

import copy
import re

from kajenn.exceptions import HTTPBadRequest, HTTPForbidden


OPERATIONS = {
    "get_status": None, "get_sync_status": None, "get_contacts": None,
    "get_chats": None, "get_chat": "read", "get_unread": None,
    "get_messages": "read", "search_messages": "read", "get_message_status": "read",
    "send_text": "write", "reply_message": "write", "react_message": "write",
    "send_media": "write", "download_media": "read", "mark_read": "write",
    "archive_chat": "write", "mute_chat": "write", "request_history": "read",
    "get_group": "read", "get_group_members": "read", "create_group": None,
    "update_group_members": "admin",
}
JID_PATTERN = re.compile(r"[0-9]+(?:-[0-9]+)?@(?:s\.whatsapp\.net|lid|g\.us)\Z")


class _Policy:
    def __init__(self, directory, initial=None):
        self.directory = directory
        saved = directory.get_setting("policy")
        self.value = self.validate(saved if saved is not None else
                                   (initial if initial is not None else {"operations": [], "chats": {}}))
        if saved is None:
            directory.set_setting("policy", self.value)
        directory.database.create_function("readable", 1, self.readable)

    def validate(self, value):
        if not isinstance(value, dict) or set(value) != {"operations", "chats"}:
            raise HTTPBadRequest("policy requires operations and chats")
        ops, chats = value["operations"], value["chats"]
        if not isinstance(ops, list) or any(not isinstance(x, str) or x not in
                                          {*OPERATIONS, "*"} for x in ops):
            raise HTTPBadRequest("unknown policy operation")
        if not isinstance(chats, dict):
            raise HTTPBadRequest("chats must map exact JIDs or * to grants")
        for jid, grants in chats.items():
            if not isinstance(jid, str) or (jid != "*" and not JID_PATTERN.fullmatch(jid)):
                raise HTTPBadRequest("chat keys must be exact JIDs or *")
            if not isinstance(grants, list) or any(not isinstance(g, str) or g not in
                                                 ("read", "write", "admin") for g in grants):
                raise HTTPBadRequest("chat grants must be read, write or admin")
        return copy.deepcopy(value)

    def readable(self, jid):
        return self.allowed(jid, "read")

    def allowed(self, jid, grant):
        chats = self.value["chats"]
        aliases = self.directory.get_aliases(jid)
        exact = [chats[a] for a in aliases if a in chats]
        return all(grant in grants for grants in exact) if exact else grant in chats.get("*", [])

    def require(self, operation, jid=None):
        if not ({operation, "*"} & set(self.value["operations"])):
            raise HTTPForbidden("account operation is not permitted")
        if jid is not None and not self.allowed(jid, OPERATIONS[operation]):
            raise HTTPForbidden("account operation is not permitted in this chat")

    def set_policy(self, value):
        checked = self.validate(value)
        self.directory.set_setting("policy", checked)
        self.value = checked
        return copy.deepcopy(checked)
