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
import base64
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from tryx.backend import SqliteStore
from tryx.client import Tryx, ChatActionsClient
from tryx.types import JID
from tryx.events import ReceiptType
from tryx.waproto.whatsapp_pb2 import HistorySync, SyncActionValue, Message

from kajenn.exceptions import HTTPBadRequest, HTTPException

from examples.whatsapp_account.connection import _Connection
from examples.whatsapp_account.directory import _Directory
from examples.whatsapp_account.server import _Server


class TestNativeLifecycle:
    def test_patch_exports_and_unstarted_client(self, tmp_path):
        runtime = Tryx(SqliteStore(str(tmp_path / "session.db"), 0))
        client = runtime.get_client()
        assert callable(client.advanced.wait_for_client)
        with pytest.raises(RuntimeError, match="not running"):
            client.advanced.fetch_message_history(JID("1", "s.whatsapp.net"), "anchor", False, 1000, 5)
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
        sender = AsyncMock(return_value=SimpleNamespace(message_id="submitted-id"))
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


@pytest.fixture
async def native_connection(tmp_path):
    connection = _Connection(tmp_path)
    connection.directory = _Directory(tmp_path / "directory.db")
    connection.session.client = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id="reply-id")),
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id="photo-id")),
        send_document=AsyncMock(return_value=SimpleNamespace(message_id="doc-id")),
        send_audio=AsyncMock(return_value=SimpleNamespace(message_id="audio-id")),
        download_media=AsyncMock(return_value=b"file"),
        advanced=SimpleNamespace(fetch_message_history=AsyncMock(return_value="history-request"),
                                 get_pn=lambda: JID("1", "s.whatsapp.net"), get_lid=lambda: JID("2", "lid")),
        chat_actions=SimpleNamespace(
            react_message=AsyncMock(return_value="reaction-id"),
            build_message_key=ChatActionsClient.build_message_key,
            build_message_range=ChatActionsClient.build_message_range,
            mark_chat_as_read=AsyncMock(), archive_chat=AsyncMock(), unarchive_chat=AsyncMock(),
            mute_chat=AsyncMock(), unmute_chat=AsyncMock()),
    )
    yield connection
    await connection.events.stop()
    connection.directory.close()


async def test_live_message_receipts_unread_and_duplicate_events(native_connection):
    conn = native_connection
    body = Message(conversation="hello")
    info = SimpleNamespace(id="incoming", push_name="Test", timestamp=datetime.now(timezone.utc),
        source=SimpleNamespace(chat=JID("1", "s.whatsapp.net"), sender=JID("1", "s.whatsapp.net"),
                               is_from_me=False, sender_alt=None, recipient_alt=None))
    event = SimpleNamespace(data=SimpleNamespace(message_info=info, raw_proto=body))
    await conn.on_message(None, event)
    conn.directory.set_chat_state("1@s.whatsapp.net", unread=False)
    await conn.on_message(None, event)
    assert conn.directory.get_unread(10, 0)["items"] == []
    assert conn.directory.get_message("1@s.whatsapp.net", "incoming")["proto"] == body.SerializeToString()
    for kind in (ReceiptType.Read, ReceiptType.Delivered, ReceiptType.Read):
        receipt = SimpleNamespace(receipt_type=kind, source=info.source,
                                  timestamp=info.timestamp, message_ids=["outgoing"])
        await conn.on_receipt(None, receipt)
    conn.record_sent(SimpleNamespace(message_id="outgoing"), "1@s.whatsapp.net", "sent")
    status = conn.directory.get_message_status("1@s.whatsapp.net", "outgoing")
    assert status["submission"] == "submitted"
    assert {x["status"] for x in status["receipts"]} == {"read", "delivered"}
    assert len(status["receipts"]) == 2


async def test_reply_reaction_and_history_use_stored_message_identity(native_connection):
    conn = native_connection
    conn.directory.add_message("12@g.us", "anchor", "3@s.whatsapp.net", "old", 123, False)
    conn.directory.set_message_data("12@g.us", "anchor", Message(conversation="old").SerializeToString())
    reply = await conn.reply_message("12@g.us", "anchor", "answer")
    jid, body = conn.session.client.send_message.call_args.args
    context = body.extendedTextMessage.contextInfo
    assert (jid.user, jid.server) == ("12", "g.us")
    assert context.stanzaId == "anchor" and context.participant == "3@s.whatsapp.net"
    assert context.quotedMessage.conversation == "old"
    assert reply["id"] == "reply-id"
    await conn.react_message("12@g.us", "anchor", "👍")
    args = conn.session.client.chat_actions.react_message.call_args.args
    assert args[1:4] == ("anchor", "👍", False)
    assert args[4].user == "3"
    result = await conn.request_history("12@g.us", "anchor", 50)
    args = conn.session.client.advanced.fetch_message_history.call_args.args
    assert args[1:] == ("anchor", False, 123000, 50)
    assert result["status"] == "requested"
    with pytest.raises(HTTPBadRequest):
        await conn.reply_message("12@g.us", "unknown", "answer")


@pytest.mark.parametrize("kind,method", [("image", "send_photo"), ("document", "send_document"),
                                        ("audio", "send_audio")])
async def test_supplied_media_bytes_are_sent_without_opening_paths(native_connection, kind, method):
    conn = native_connection
    result = await conn.send_media("1@s.whatsapp.net", kind, b"file", "test/type", "name", "")
    assert result["status"] == "submitted"
    assert getattr(conn.session.client, method).call_args.args[1] == b"file"
    assert conn.directory.get_message("1@s.whatsapp.net", result["id"])["kind"] == kind


async def test_media_download_rejects_unbounded_size_before_network(native_connection):
    conn = native_connection
    body = Message()
    body.documentMessage.fileLength = 4
    body.documentMessage.mimetype = "application/pdf"
    conn.directory.add_message("1@s.whatsapp.net", "media", "sender", None, 1, False)
    conn.directory.set_message_data("1@s.whatsapp.net", "media", body.SerializeToString(), "document")
    result = await conn.download_media("1@s.whatsapp.net", "media", 10)
    assert base64.b64decode(result["content_base64"]) == b"file"
    conn.session.client.download_media.reset_mock()
    with pytest.raises(HTTPBadRequest):
        await conn.download_media("1@s.whatsapp.net", "media", 3)
    conn.session.client.download_media.assert_not_awaited()


@pytest.mark.parametrize("operation,value,method", [
    ("mark_read", True, "mark_chat_as_read"), ("archive_chat", True, "archive_chat"),
    ("archive_chat", False, "unarchive_chat"), ("mute_chat", True, "mute_chat"),
    ("mute_chat", False, "unmute_chat")])
async def test_chat_actions_build_real_proto_ranges(native_connection, operation, value, method):
    conn = native_connection
    conn.directory.add_message("1@s.whatsapp.net", "anchor", "1@s.whatsapp.net", "text", 10, False)
    await conn.set_chat_flag(operation, "1@s.whatsapp.net", value)
    getattr(conn.session.client.chat_actions, method).assert_awaited_once()
    if operation == "mark_read":
        message_range = conn.session.client.chat_actions.mark_chat_as_read.call_args.args[2]
        assert message_range.lastMessageTimestamp == 10


async def test_group_results_preserve_partial_failures(native_connection):
    conn = native_connection
    group = SimpleNamespace(id=JID("12", "g.us"), subject="Group", description="Description",
                            size=2, is_locked=False, is_announcement=False,
                            participants=[SimpleNamespace(jid=JID("1", "s.whatsapp.net"), is_admin=True),
                                          SimpleNamespace(jid=JID("2", "s.whatsapp.net"), is_admin=False)])
    conn.session.client.groups = SimpleNamespace(
        get_metadata=AsyncMock(return_value=group),
        create_group=AsyncMock(return_value=SimpleNamespace(gid=group.id)),
        add_participants=AsyncMock(return_value=[SimpleNamespace(jid=JID("1", "s.whatsapp.net"),
                                                               status="403", error="forbidden")]),
        remove_participants=AsyncMock(), promote_participants=AsyncMock(), demote_participants=AsyncMock())
    assert (await conn.get_group("12@g.us"))["title"] == "Group"
    page = await conn.get_group_members("12@g.us", 1, 0)
    assert page["next_offset"] == 1 and page["items"][0]["admin"]
    created = await conn.create_group("Group", ["1@s.whatsapp.net"])
    assert created["chat_id"] == "12@g.us" and not created["policy_changed"]
    result = await conn.update_group_members("12@g.us", ["1@s.whatsapp.net"], "add")
    assert result["results"][0]["error"] == "forbidden"


async def test_legacy_probe_ids_are_not_sent_back_to_the_provider(native_connection):
    conn = native_connection
    invalid_id = "<builtins.SendResult object at 0x123>"
    conn.directory.add_message("1@s.whatsapp.net", invalid_id, "self", "old", 1, True)
    with pytest.raises(HTTPException) as error:
        await conn.react_message("1@s.whatsapp.net", invalid_id, "👍")
    assert error.value.status == 409
    conn.session.client.chat_actions.react_message.assert_not_awaited()


async def test_extended_native_commands_and_deleted_history(native_connection):
    conn = native_connection
    client = conn.session.client
    client.chat_actions.edit_message = AsyncMock(return_value="edit-id")
    client.chat_actions.revoke_message = AsyncMock()
    client.chat_actions.pin_chat = AsyncMock()
    client.groups = SimpleNamespace(get_metadata=AsyncMock(return_value=SimpleNamespace(
        description="old text", description_id="revision-id")), set_description=AsyncMock())
    await conn.set_group_description("123@g.us", "New text")
    assert client.groups.set_description.call_args.args[2] == "revision-id"
    conn.directory.add_message("1@s.whatsapp.net", "mine", "self", "Before", 1, True)
    await conn.edit_message("1@s.whatsapp.net", "mine", "After")
    assert client.chat_actions.edit_message.call_args.args[2].conversation == "After"
    await conn.revoke_message("1@s.whatsapp.net", "mine")
    conn.directory.add_message("1@s.whatsapp.net", "mine", "self", "Replay", 1, True)
    conn.directory.set_message_data("1@s.whatsapp.net", "mine", Message(conversation="Replay").SerializeToString())
    row = conn.directory.get_message("1@s.whatsapp.net", "mine")
    assert row["text"] == "" and row["proto"] is None and row["status"] == "deleted"


async def test_poll_and_event_secrets_are_persisted_but_not_returned(native_connection):
    conn = native_connection
    conn.session.client.polls = SimpleNamespace(create=AsyncMock(return_value=("poll-id", list(b"secret"))))
    conn.session.client.events = SimpleNamespace(create=AsyncMock(return_value={
        "message_id": "event-id", "message_secret": list(b"event-secret")}))
    poll = await conn.create_poll("123@g.us", "Question", ["Yes", "No"], 1)
    event = await conn.create_group_event("123@g.us", "Meeting", 100, 200)
    assert "secret" not in str(poll) + str(event)
    assert conn.directory.get_setting("poll:123@g.us:poll-id")["secret"] == b"secret".hex()
    assert conn.directory.get_setting("event:123@g.us:event-id")["secret"] == b"event-secret".hex()


@pytest.mark.parametrize("kind,method", [("video", "send_video"), ("gif", "send_video"),
                                        ("sticker", "send_sticker"), ("voice", "send_audio")])
async def test_extended_media_modes(native_connection, kind, method):
    conn = native_connection
    transport = AsyncMock(return_value=SimpleNamespace(message_id="media-id"))
    setattr(conn.session.client, method, transport)
    await conn.send_media("1@s.whatsapp.net", kind, b"data", "test/type", "", "")
    assert transport.call_args.args[1] == b"data"
    if kind == "voice":
        assert transport.call_args.kwargs["ptt"] is True
    if kind == "gif":
        assert transport.call_args.kwargs["gif_playback"] is True


async def test_channel_uses_newsletter_transport_and_exact_jid(native_connection):
    conn = native_connection
    conn.session.client.newsletter = SimpleNamespace(send_message=AsyncMock(return_value="channel-id"))
    result = await conn.send_channel_text("123@newsletter", "Update")
    args = conn.session.client.newsletter.send_message.call_args.args
    assert conn.peer_id(args[0]) == "123@newsletter" and args[1].conversation == "Update"
    assert result["id"] == "channel-id"


async def test_retained_poll_vote_and_event_response(native_connection):
    conn = native_connection
    conn.session.client.polls = SimpleNamespace(vote=AsyncMock(return_value="vote-id"))
    conn.session.client.events = SimpleNamespace(respond=AsyncMock(return_value="response-id"))
    body = Message()
    body.pollCreationMessage.name = "Question"
    body.pollCreationMessage.options.add(optionName="Yes")
    body.pollCreationMessage.options.add(optionName="No")
    body.pollCreationMessage.selectableOptionsCount = 1
    body.messageContextInfo.messageSecret = b"x" * 32
    conn.directory.add_message("123@g.us", "poll", "3@lid", None, 1, False)
    conn.directory.set_message_data("123@g.us", "poll", body.SerializeToString(), "poll")
    await conn.vote_poll("123@g.us", "poll", ["Yes"])
    args = conn.session.client.polls.vote.call_args.args
    assert args[3] == b"x" * 32 and conn.peer_id(args[2]) == "3@lid"
    with pytest.raises(HTTPBadRequest):
        await conn.vote_poll("123@g.us", "poll", ["Unknown"])
    assert conn.session.client.polls.vote.await_count == 1
    update = Message()
    update.pollUpdateMessage.pollCreationMessageKey.id = "poll"
    update.pollUpdateMessage.vote.encPayload = b"invalid"
    update.pollUpdateMessage.vote.encIv = b"invalid"
    conn.directory.add_message("123@g.us", "vote", "4@lid", None, 2, False)
    conn.directory.set_message_data("123@g.us", "vote", update.SerializeToString(), "poll_vote")
    result = await conn.get_poll_results("123@g.us", "poll")
    assert result["undecryptable_updates"] == 1
    assert result["coverage"] == "observed_votes_only"
    event = Message()
    event.eventMessage.name = "Meeting"
    event.messageContextInfo.messageSecret = b"y" * 32
    conn.directory.add_message("123@g.us", "event", "3@lid", None, 1, False)
    conn.directory.set_message_data("123@g.us", "event", event.SerializeToString(), "event")
    await conn.respond_group_event("123@g.us", "event", "Going")
    assert conn.session.client.events.respond.call_args.args[3] == b"y" * 32


def test_multiaccount_mounts_keep_directories_distinct(tmp_path):
    config = tmp_path / "accounts.json"
    first, second = tmp_path / "first", tmp_path / "second"
    config.write_text('{"first": "' + str(first) + '", "second": "' + str(second) + '"}')
    server = _Server(SimpleNamespace(accounts=config, session_dir=tmp_path, resync=False))
    mounts = server.account_mounts()
    assert [options["code"] for _, options in mounts] == ["first", "second"]
    assert mounts[0][1]["connection_factory"]().path == first
    assert mounts[1][1]["connection_factory"]().path == second
    config.write_text('{"first": "' + str(first) + '", "second": "' + str(first) + '"}')
    with pytest.raises(ValueError, match="separate"):
        server.account_mounts()


async def test_poll_results_decrypt_aliases_and_use_latest_vote(native_connection):
    conn = native_connection
    secret = b"x" * 32
    conn.directory.set_setting("poll:123@g.us:poll", {
        "creator": "3@lid", "secret": secret.hex(), "options": ["Yes", "No"], "selectable_count": 1})
    conn.directory.add_alias("33@s.whatsapp.net", "3@lid")
    conn.directory.add_alias("44@s.whatsapp.net", "4@lid")
    for timestamp, option in ((1, "Yes"), (2, "No")):
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                   info=b"poll33@s.whatsapp.net44@s.whatsapp.netPoll Vote").derive(secret)
        nonce = bytes([timestamp]) * 12
        payload = Message.PollVoteMessage(selectedOptions=[hashlib.sha256(option.encode()).digest()])
        encrypted = AESGCM(key).encrypt(nonce, payload.SerializeToString(), b"poll\x0044@s.whatsapp.net")
        message = Message()
        vote = message.pollUpdateMessage
        vote.pollCreationMessageKey.id = "poll"
        vote.senderTimestampMs = timestamp * 1000
        vote.vote.encPayload = encrypted
        vote.vote.encIv = nonce
        conn.directory.add_message("123@g.us", str(timestamp), "4@lid", None, timestamp, False)
        conn.directory.set_message_data("123@g.us", str(timestamp), message.SerializeToString(), "poll_vote")
    result = await conn.get_poll_results("123@g.us", "poll")
    assert result["undecryptable_updates"] == 0
    assert result["items"] == [{"option": "Yes", "voters": []},
                              {"option": "No", "voters": ["44@s.whatsapp.net"]}]
