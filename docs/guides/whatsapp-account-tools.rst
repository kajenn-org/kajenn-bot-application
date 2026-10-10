WhatsApp account tool reference
===============================

Version: 0.1 — Last updated: 2026-10-10 — Status: UNDER REVIEW

This reference covers all 76 REST/MCP tools in the source-tree prototype.
It is checked against the explicit routing signatures and policy map; no native
WhatsApp dependency is imported to build this documentation.

See :doc:`whatsapp-account-prototype` for installation, pairing, policy, protocol
examples, response envelopes, partial history, scheduling and failure handling.
The signatures below use Python notation to show types and defaults; pass a JSON
object of named arguments to MCP, omitting optional arguments to use defaults.
All return dictionaries, wrapped by MCP in its tool-result envelope.

The avatar role is required at discovery and invocation. Except for the three
administrative policy/audit tools, the persistent policy must also grant the tool
name (or ``*``). A primary chat grant applies to ``chat_id``; account-wide calls
can still filter returned chats or validate participants. See the guide for those
additional checks. A grant never overrides WhatsApp's own administrator rights.

.. contents:: Tools
   :local:
   :depth: 1

archive_chat
~~~~~~~~~~~~

.. code-block:: python

   archive_chat(chat_id: str, archived: bool=True) -> dict

Archive or unarchive a chat on WhatsApp.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

block_contact
~~~~~~~~~~~~~

.. code-block:: python

   block_contact(chat_id: str, blocked: bool=True) -> dict

Block or unblock an exact contact.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

create_channel
~~~~~~~~~~~~~~

.. code-block:: python

   create_channel(title: str, description: str='') -> dict

Create a WhatsApp newsletter channel.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: account-wide; additional target filtering may apply.

create_community
~~~~~~~~~~~~~~~~

.. code-block:: python

   create_community(title: str, description: str='') -> dict

Create a WhatsApp community.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: account-wide; additional target filtering may apply.

create_group
~~~~~~~~~~~~

.. code-block:: python

   create_group(title: str, participants: list[str]) -> dict

Create a group with explicitly selected known participants.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: account-wide; additional target filtering may apply.

create_group_event
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   create_group_event(chat_id: str, title: str, start_time: int, end_time: int, description: str='') -> dict

Create a scheduled group event using Unix timestamps; never returns its secret.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

create_label
~~~~~~~~~~~~

.. code-block:: python

   create_label(label_id: str, name: str, color: int) -> dict

Create an account label with a WhatsApp color index.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: account-wide; additional target filtering may apply.

create_poll
~~~~~~~~~~~

.. code-block:: python

   create_poll(chat_id: str, title: str, options: list[str], selectable_count: int=1) -> dict

Create a poll with two to twelve unique options; retain its secret privately.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

deactivate_community
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   deactivate_community(chat_id: str) -> dict

Deactivate a community administered by this account.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

decide_group_requests
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   decide_group_requests(chat_id: str, participants: list[str], approve: bool) -> dict

Approve or reject explicit group membership requests.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

decide_message
~~~~~~~~~~~~~~

.. code-block:: python

   decide_message(job_id: str, decision: str) -> dict

Approve, reject or cancel a queued text; the first applicable decision wins.

Avatar role: ``admin``. Primary chat grant: account-wide; additional target filtering may apply.

delete_label
~~~~~~~~~~~~

.. code-block:: python

   delete_label(label_id: str) -> dict

Delete an account label.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: account-wide; additional target filtering may apply.

delete_message
~~~~~~~~~~~~~~

.. code-block:: python

   delete_message(chat_id: str, message_id: str) -> dict

Delete an observed message for this account only, preserving downloaded media.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

download_media
~~~~~~~~~~~~~~

.. code-block:: python

   download_media(chat_id: str, message_id: str) -> dict

Download bounded media from a synchronized message.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

edit_message
~~~~~~~~~~~~

.. code-block:: python

   edit_message(chat_id: str, message_id: str, text: str) -> dict

Edit an observed outgoing message; provider time limits still apply.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

follow_channel
~~~~~~~~~~~~~~

.. code-block:: python

   follow_channel(chat_id: str, follow: bool=True) -> dict

Follow or unfollow a newsletter channel.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

get_audit_log
~~~~~~~~~~~~~

.. code-block:: python

   get_audit_log(limit: int=50, offset: int=0) -> dict

Read operation metadata without message text or credentials.

Avatar role: ``admin``. Administrator recovery operation; no operation-policy grant required.

get_channel
~~~~~~~~~~~

.. code-block:: python

   get_channel(chat_id: str) -> dict

Read newsletter channel metadata.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

get_channel_messages
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   get_channel_messages(chat_id: str, limit: int=50, before: int=0) -> dict

Read one provider page from a newsletter; server IDs differ from message IDs.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

get_channels
~~~~~~~~~~~~

.. code-block:: python

   get_channels(limit: int=50, offset: int=0) -> dict

List permitted subscribed newsletter channels.

Avatar role: ``whatsapp_account_read``. Primary chat grant: account-wide; additional target filtering may apply.

get_chat
~~~~~~~~

.. code-block:: python

   get_chat(chat_id: str) -> dict

Read locally observed metadata for one chat.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

get_chats
~~~~~~~~~

.. code-block:: python

   get_chats(query: str='', limit: int=50, offset: int=0, include_archived: bool=False) -> dict

List locally observed permitted chats.

Avatar role: ``whatsapp_account_read``. Primary chat grant: account-wide; additional target filtering may apply.

get_community_groups
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   get_community_groups(chat_id: str, limit: int=50, offset: int=0) -> dict

List permitted community subgroups.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

get_contacts
~~~~~~~~~~~~

.. code-block:: python

   get_contacts(query: str='', limit: int=50, offset: int=0) -> dict

Search permitted contacts; preserve ambiguous names.

Avatar role: ``whatsapp_account_read``. Primary chat grant: account-wide; additional target filtering may apply.

get_events
~~~~~~~~~~

.. code-block:: python

   get_events(after_id: int=0, limit: int=50) -> dict

Replay permitted event identifiers from the bounded persistent journal.

Avatar role: ``whatsapp_account_read``. Primary chat grant: account-wide; additional target filtering may apply.

get_group
~~~~~~~~~

.. code-block:: python

   get_group(chat_id: str) -> dict

Fetch group metadata from WhatsApp.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

get_group_invite
~~~~~~~~~~~~~~~~

.. code-block:: python

   get_group_invite(chat_id: str, reset: bool=False) -> dict

Get a group invitation link, optionally revoking the previous link.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

get_group_members
~~~~~~~~~~~~~~~~~

.. code-block:: python

   get_group_members(chat_id: str, limit: int=50, offset: int=0) -> dict

Fetch a page of current group participants.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

get_group_requests
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   get_group_requests(chat_id: str, limit: int=50, offset: int=0) -> dict

List pending group membership requests.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

get_message_status
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   get_message_status(chat_id: str, message_id: str) -> dict

Get submission state and per-recipient observed receipts.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

get_messages
~~~~~~~~~~~~

.. code-block:: python

   get_messages(chat_id: str, limit: int=50, offset: int=0) -> dict

Read synchronized messages, including provider-known aliases.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

get_outbox
~~~~~~~~~~

.. code-block:: python

   get_outbox(limit: int=50, offset: int=0) -> dict

Read permitted queued texts and dispatch outcomes.

Avatar role: ``whatsapp_account_read``. Primary chat grant: account-wide; additional target filtering may apply.

get_policy
~~~~~~~~~~

.. code-block:: python

   get_policy() -> dict

Read account operation and chat grants.

Avatar role: ``admin``. Administrator recovery operation; no operation-policy grant required.

get_poll_results
~~~~~~~~~~~~~~~~

.. code-block:: python

   get_poll_results(chat_id: str, message_id: str) -> dict

Aggregate retained poll votes; reports gaps and decryption failures.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

get_privacy
~~~~~~~~~~~

.. code-block:: python

   get_privacy() -> dict

Read account privacy settings.

Avatar role: ``whatsapp_account_read``. Primary chat grant: account-wide; additional target filtering may apply.

get_profile_picture
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   get_profile_picture(chat_id: str, preview: bool=True) -> dict

Get profile picture metadata; availability depends on privacy settings.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

get_status
~~~~~~~~~~

.. code-block:: python

   get_status() -> dict

Get connection state and visible record counts.

Avatar role: ``whatsapp_account_read``. Primary chat grant: account-wide; additional target filtering may apply.

get_sync_status
~~~~~~~~~~~~~~~

.. code-block:: python

   get_sync_status() -> dict

Get history coverage and callback failures without initiating sync.

Avatar role: ``whatsapp_account_read``. Primary chat grant: account-wide; additional target filtering may apply.

get_unread
~~~~~~~~~~

.. code-block:: python

   get_unread(limit: int=50, offset: int=0) -> dict

List chats observed as unread; unknown states are excluded.

Avatar role: ``whatsapp_account_read``. Primary chat grant: account-wide; additional target filtering may apply.

leave_group
~~~~~~~~~~~

.. code-block:: python

   leave_group(chat_id: str) -> dict

Leave a group.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

link_community_groups
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   link_community_groups(chat_id: str, groups: list[str], linked: bool=True) -> dict

Link or unlink subgroups; requires admin grants on every affected group.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

mark_read
~~~~~~~~~

.. code-block:: python

   mark_read(chat_id: str, read: bool=True) -> dict

Mark a chat read or unread on WhatsApp.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

mute_channel
~~~~~~~~~~~~

.. code-block:: python

   mute_channel(chat_id: str, muted: bool=True) -> dict

Mute or unmute a followed newsletter channel.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

mute_chat
~~~~~~~~~

.. code-block:: python

   mute_chat(chat_id: str, muted: bool=True) -> dict

Mute or unmute a chat on WhatsApp.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

pin_chat
~~~~~~~~

.. code-block:: python

   pin_chat(chat_id: str, pinned: bool=True) -> dict

Pin or unpin a chat on this account.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

react_channel_message
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   react_channel_message(chat_id: str, server_id: int, reaction: str) -> dict

React to a newsletter message using its numeric server ID.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

react_message
~~~~~~~~~~~~~

.. code-block:: python

   react_message(chat_id: str, message_id: str, reaction: str) -> dict

React to a synchronized message; empty reaction removes it.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

reply_message
~~~~~~~~~~~~~

.. code-block:: python

   reply_message(chat_id: str, message_id: str, text: str) -> dict

Reply quoting a synchronized message.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

request_history
~~~~~~~~~~~~~~~

.. code-block:: python

   request_history(chat_id: str, message_id: str, count: int=50) -> dict

Request older history from a known anchor; completion is asynchronous.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

respond_group_event
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   respond_group_event(chat_id: str, message_id: str, response: str) -> dict

Respond Going, NotGoing or Maybe to a retained group event.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

revoke_message
~~~~~~~~~~~~~~

.. code-block:: python

   revoke_message(chat_id: str, message_id: str) -> dict

Revoke your own observed message for everyone; provider limits apply.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

save_contact
~~~~~~~~~~~~

.. code-block:: python

   save_contact(chat_id: str, name: str) -> dict

Save a contact using an exact personal JID.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

schedule_message
~~~~~~~~~~~~~~~~

.. code-block:: python

   schedule_message(chat_id: str, text: str, due: int, approval_required: bool=True) -> dict

Queue a text for a Unix timestamp; approval is required by default.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

search_messages
~~~~~~~~~~~~~~~

.. code-block:: python

   search_messages(query: str, chat_id: str | None=None, limit: int=50, offset: int=0) -> dict

Search local text in permitted chats only.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

send_channel_text
~~~~~~~~~~~~~~~~~

.. code-block:: python

   send_channel_text(chat_id: str, text: str) -> dict

Publish text to a newsletter channel you administer.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

send_chat_state
~~~~~~~~~~~~~~~

.. code-block:: python

   send_chat_state(chat_id: str, state: str) -> dict

Send a typing, recording or paused indication.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

send_media
~~~~~~~~~~

.. code-block:: python

   send_media(chat_id: str, kind: str, content_base64: str, mimetype: str, filename: str='', caption: str='') -> dict

Send supplied image, document or audio bytes, never server paths.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

send_text
~~~~~~~~~

.. code-block:: python

   send_text(chat_id: str, text: str) -> dict

Send explicit text to an exact known JID.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

set_chat_label
~~~~~~~~~~~~~~

.. code-block:: python

   set_chat_label(chat_id: str, label_id: str, assigned: bool=True) -> dict

Assign or remove a label from a chat.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

set_disappearing_default
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   set_disappearing_default(seconds: int) -> dict

Set the default disappearing-message duration for new chats.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: account-wide; additional target filtering may apply.

set_group_approval
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   set_group_approval(chat_id: str, enabled: bool) -> dict

Enable or disable approval of group membership requests.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

set_group_description
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   set_group_description(chat_id: str, description: str) -> dict

Change a group description using its current revision.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

set_group_disappearing
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   set_group_disappearing(chat_id: str, seconds: int) -> dict

Set disappearing messages for a group.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

set_group_member_add
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   set_group_member_add(chat_id: str, admins_only: bool) -> dict

Choose who may add group members.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

set_group_setting
~~~~~~~~~~~~~~~~~

.. code-block:: python

   set_group_setting(chat_id: str, setting: str, enabled: bool) -> dict

Set an explicitly supported group permission or sharing setting.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

set_group_title
~~~~~~~~~~~~~~~

.. code-block:: python

   set_group_title(chat_id: str, title: str) -> dict

Change a group title.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

set_policy
~~~~~~~~~~

.. code-block:: python

   set_policy(policy: dict) -> dict

Replace account grants durably; administrator only.

Avatar role: ``admin``. Administrator recovery operation; no operation-policy grant required.

set_presence
~~~~~~~~~~~~

.. code-block:: python

   set_presence(available: bool) -> dict

Set account online availability.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: account-wide; additional target filtering may apply.

set_privacy
~~~~~~~~~~~

.. code-block:: python

   set_privacy(category: str, value: str) -> dict

Set a named privacy setting; unsupported combinations are rejected by WhatsApp.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: account-wide; additional target filtering may apply.

set_profile_about
~~~~~~~~~~~~~~~~~

.. code-block:: python

   set_profile_about(text: str) -> dict

Set the account about text.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: account-wide; additional target filtering may apply.

set_profile_name
~~~~~~~~~~~~~~~~

.. code-block:: python

   set_profile_name(name: str) -> dict

Set the account display name.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: account-wide; additional target filtering may apply.

star_message
~~~~~~~~~~~~

.. code-block:: python

   star_message(chat_id: str, message_id: str, starred: bool=True) -> dict

Star or unstar an observed message.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

transcribe_message
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   transcribe_message(chat_id: str, message_id: str, language: str='it') -> dict

Transcribe retained audio on request using the configured engine; no automatic sends.

Avatar role: ``whatsapp_account_read``. Primary chat grant: ``read``.

update_channel
~~~~~~~~~~~~~~

.. code-block:: python

   update_channel(chat_id: str, title: str, description: str='') -> dict

Change newsletter channel title and description.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

update_group_members
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   update_group_members(chat_id: str, participants: list[str], action: str) -> dict

Add, remove, promote or demote explicitly selected group participants.

Avatar role: ``whatsapp_account_manage``. Primary chat grant: ``admin``.

vote_poll
~~~~~~~~~

.. code-block:: python

   vote_poll(chat_id: str, message_id: str, options: list[str]) -> dict

Vote in a retained poll; an empty selection withdraws a vote.

Avatar role: ``whatsapp_account_write``. Primary chat grant: ``write``.

