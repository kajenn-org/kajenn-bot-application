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

"""Offline checks against the compiled Tryx extension; no client run loop starts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tryx.backend import SqliteStore
from tryx.client import Tryx
from tryx.types import JID
from tryx.waproto.whatsapp_pb2 import HistorySync, SyncActionValue

from examples.whatsapp_account.connection import _Connection
from examples.whatsapp_account.directory import _Directory


class TestNativeLifecycle:
    def test_patch_exports_and_unstarted_client(self, tmp_path):
        runtime = Tryx(SqliteStore(str(tmp_path / "session.db"), 0))
        client = runtime.get_client()
        assert callable(client.advanced.wait_for_client)
        for method in (client.advanced.disconnect, client.advanced.logout,
                       client.advanced.resync_directory):
            with pytest.raises(RuntimeError, match="not running"):
                method()

    async def test_cancel_waiting_for_local_initialization(self, tmp_path):
        runtime = Tryx(SqliteStore(str(tmp_path / "session.db"), 0))
        waiting = asyncio.ensure_future(runtime.get_client().advanced.wait_for_client())
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting

    async def test_device_records_survive_reopen(self, tmp_path):
        path = str(tmp_path / "session.db")
        first = SqliteStore(path, 0)
        device_id = await first.create_new_device()
        reopened = SqliteStore(path, 0)
        assert await reopened.device_exists(device_id)
        assert any(device.id == device_id for device in await reopened.list_devices())


class TestNativeDirectory:
    async def test_protobuf_contact_archive_and_history(self, tmp_path):
        connection = _Connection(tmp_path)
        connection.directory = _Directory(tmp_path / "directory.db")
        try:
            contact = SyncActionValue.ContactAction(
                fullName="Test Contact", pnJid="123@s.whatsapp.net", lidJid="456@lid")
            await connection.on_contact(None, SimpleNamespace(data=SimpleNamespace(
                jid=JID("123", "s.whatsapp.net"), action=contact)))
            archive = SyncActionValue.ArchiveChatAction(archived=True)
            await connection.on_archive(None, SimpleNamespace(data=SimpleNamespace(
                jid=JID("123", "s.whatsapp.net"), action=archive)))
            assert connection.directory.get_chats("", 50, 0, False)["items"] == []
            assert connection.directory.get_chats("", 50, 0, True)["items"][0][
                "id"] == "123@s.whatsapp.net"
            assert len(connection.directory.get_contacts("test", 50, 0)["items"]) == 1
            history = HistorySync()
            chat = history.conversations.add(id="123@s.whatsapp.net", archived=False)
            message = chat.messages.add().message
            message.key.id = "test-history-message"
            message.message.conversation = "History text"
            message.messageTimestamp = 10
            await connection.on_history(None, SimpleNamespace(proto=history))
            assert len(connection.directory.get_chats("test", 50, 0, False)["items"]) == 1
            assert connection.directory.get_messages("123@s.whatsapp.net", 50, 0)[
                "items"][0]["text"] == "History text"
        finally:
            connection.directory.close()

    async def test_callback_failure_is_visible_without_exposing_payload(self, tmp_path):
        connection = _Connection(tmp_path)
        await connection.guard(connection.on_contact)(None, SimpleNamespace())
        assert connection.callback_errors == 1
        assert connection.callback_failures == {"on_contact": "AttributeError"}


    async def test_send_builds_native_jid_and_records_submitted_message(self, tmp_path):
        connection = _Connection(tmp_path)
        connection.directory = _Directory(tmp_path / "directory.db")
        sender = AsyncMock(return_value="submitted-id")
        connection.session.client = SimpleNamespace(send_text=sender)
        try:
            await connection.send_text("123@s.whatsapp.net", "Test text")
            jid, text = sender.call_args.args
            assert isinstance(jid, JID)
            assert (jid.user, jid.server) == ("123", "s.whatsapp.net")
            assert text == "Test text"
            assert connection.directory.get_messages("123@s.whatsapp.net", 1, 0)[
                "items"][0]["id"] == "submitted-id"
        finally:
            connection.directory.close()
