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

"""Offline Telegram transport for account contract tests."""

from datetime import datetime, timezone
from types import SimpleNamespace
from telethon import errors, types, utils
from telethon.crypto import AuthKey

CHAT = -1000000000100
OTHER = -1000000000200


class FakeTelegram:
    def __init__(self, session, api_id, api_hash, **kwargs):
        self.session = session
        self.connected = False
        self.authorized = bool(session.auth_key)
        self.calls = []
        self.failure = None
        self.me = SimpleNamespace(id=7, username="developer", first_name="Developer", bot=False)
        self.messages = [self.message(i) for i in range(5, 0, -1)]
        self.settings = kwargs

    def message(self, code, chat_id=CHAT, out=True):
        return SimpleNamespace(id=code, chat_id=chat_id, out=out,
                               sender_id=7, date=datetime(2026, 10, code, tzinfo=timezone.utc),
                               raw_text=f"Message {code}", media=None)

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return self.me

    async def send_code_request(self, phone):
        self.calls.append(("login", phone))
        return SimpleNamespace(phone_code_hash="secret-hash")

    async def sign_in(self, **kwargs):
        if kwargs.get("code") == "2fa":
            raise errors.SessionPasswordNeededError(None)
        if kwargs.get("code") == "invalid":
            raise errors.PhoneCodeInvalidError(None)
        self.authorized = True
        self.session.set_dc(2, "149.154.167.51", 443)
        self.session.auth_key = AuthKey(b"s" * 256)
        return self.me

    async def log_out(self):
        self.calls.append(("logout",))
        self.authorized = False
        self.connected = False
        self.session = None
        return True

    async def get_input_entity(self, peer):
        self.calls.append(("resolve", peer))
        if not isinstance(peer, (int, str)):
            return peer
        if peer == "me":
            return types.InputPeerSelf()
        if peer < -1000000000000:
            return types.InputPeerChannel(-peer - 1000000000000, 1234)
        return types.InputPeerUser(peer, 1234)

    async def get_input_users(self, users):
        return [types.InputUser(user, 1234) for user in users]

    async def iter_dialogs(self, **kwargs):
        for code, name in ((CHAT, "Development"), (OTHER, "Private")):
            yield SimpleNamespace(id=code, name=name, is_group=True, is_channel=True, is_user=False,
                                  input_entity=getattr(self, "dialog_entity", None))

    async def iter_messages(self, entity, **kwargs):
        self.calls.append(("history", entity, kwargs))
        items = [m for m in self.messages
                 if (not kwargs.get("offset_id") or m.id < kwargs["offset_id"])
                 and (not kwargs.get("offset_date") or m.date < kwargs["offset_date"])
                 and (not kwargs.get("search") or kwargs["search"] in m.raw_text)]
        for message in items[:kwargs["limit"]]:
            yield message

    async def get_messages(self, entity, ids):
        self.calls.append(("get_messages", entity, ids))
        return [next((m for m in self.messages if m.id == code), None) for code in ids]

    async def send_message(self, entity, text, **kwargs):
        self.calls.append(("send", entity, text, kwargs))
        if self.failure:
            raise self.failure
        return self.message(10, utils.get_peer_id(entity))

    async def send_file(self, entity, file, **kwargs):
        self.calls.append(("file", entity, file.read(), file.name, kwargs))
        return self.message(10, utils.get_peer_id(entity))

    async def edit_message(self, entity, message_id, text, **kwargs):
        self.calls.append(("edit", entity, message_id, text, kwargs))
        return self.message(message_id)

    async def delete_messages(self, entity, ids, **kwargs):
        self.calls.append(("delete", entity, ids, kwargs))

    async def iter_participants(self, entity, **kwargs):
        for code in range(1, 4):
            yield SimpleNamespace(id=code, first_name=f"Person {code}", last_name=None,
                                  username=f"person{code}", bot=False)

    async def kick_participant(self, entity, user):
        self.calls.append(("kick", entity, user))

    async def edit_admin(self, entity, user, **kwargs):
        self.calls.append(("admin", entity, user, kwargs))

    async def __call__(self, request):
        self.calls.append(("request", request))
        return SimpleNamespace(chats=[types.Channel(id=300, title="New", photo=types.ChatPhotoEmpty(),
                                                    date=datetime.now(timezone.utc),
                                                    broadcast=True, access_hash=1234)])
