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

"""Implementation tests for the synchronized WhatsApp directory."""

from examples.whatsapp_account.directory import _Directory


def test_names_aliases_ambiguity_and_chat_separation(tmp_path):
    directory = _Directory(tmp_path / "directory.db")
    directory.add_contact("11@lid", "Carla Test", pn="11@s.whatsapp.net", lid="11@lid")
    directory.add_contact("22@s.whatsapp.net", "Carla Test")
    directory.add_contact("11@lid", "Profile nickname", source="profile")
    contacts = directory.get_contacts("  CARLA   test ", 10, 0)["items"]
    assert len(contacts) == 2
    assert directory.get_chats("", 10, 0, False)["items"] == []
    directory.add_chat("11@lid", source="app_state")
    assert directory.get_chats("carla", 10, 0, False)["items"][0]["name"] == "Carla Test"
    assert directory.counts["contacts"] == 2
    directory.close()


def test_pages_literal_search_archived_and_reopen(tmp_path):
    path = tmp_path / "directory.db"
    directory = _Directory(path)
    for i in range(3):
        directory.add_contact(f"{i}@s.whatsapp.net", f"Name {i}")
    assert directory.get_contacts("", 2, 0)["next_offset"] == 2
    assert len(directory.get_contacts("", 2, 2)["items"]) == 1
    assert directory.get_contacts("%", 10, 0)["items"] == []
    directory.add_chat("1@s.whatsapp.net", archived=True)
    directory.add_chat("2@s.whatsapp.net", archived=False)
    assert len(directory.get_chats("", 10, 0, False)["items"]) == 1
    assert len(directory.get_chats("", 10, 0, True)["items"]) == 2
    directory.add_message("2@s.whatsapp.net", "a", "other", "Hello", 1, False)
    directory.add_message("2@s.whatsapp.net", "a", "other", None, 1, False)
    directory.close()
    reopened = _Directory(path)
    messages = reopened.get_messages("2@s.whatsapp.net", 10, 0)
    assert len(messages["items"]) == 1
    assert messages["items"][0]["text"] == "Hello"
    assert path.stat().st_mode & 0o077 == 0
    reopened.close()
