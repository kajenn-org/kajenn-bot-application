Application API
===============

Document version: 0.3 — Last updated: 2026-10-10 — Status: 🔴 UNDER REVIEW

Import concrete applications from ``kajenn_bot_application``. Call management
and sending APIs from trusted application code after startup. Grammar classes
are imported from their corresponding provider modules. The same application
mount exposes administrative REST and MCP routes; see :doc:`guides/administration`.
Remote registry reads return non-secret metadata rather than full records.

Configuration and input examples are in :doc:`getting-started`,
:doc:`guides/telegram` and :doc:`guides/whatsapp`.

Shared conversation and task APIs
---------------------------------

.. autoclass:: kajenn_bot_application.BotBaseApplication
   :members: get_bot, get_bot_registration, create_conversation, get_conversation, send_conversation_message, update_conversation_context, close_conversation, send_text, send_document, send_announcement, queue_announcement, schedule_reminder, get_reminder, cancel_reminder, activate_bot
   :undoc-members:

``get_bot_registration`` returns trusted registration data, including credentials.
Do not serialize it into logs or public responses. For persistence contracts,
see :doc:`guides/persistence`.

Telegram
--------

.. autoclass:: kajenn_bot_application.TelegramBotApplication
   :members: register_bot, send_message, send_typing, send_media, send_poll, get_poll, stop_poll
   :undoc-members:

.. autoclass:: kajenn_bot_application.telegram.TelegramBotGrammar
   :members:

.. autoclass:: kajenn_bot_application.telegram.TelegramBotInstanceGrammar

WhatsApp Business
-----------------

.. autoclass:: kajenn_bot_application.WhatsAppBotApplication
   :members: register_bot, send_message, send_template, send_buttons, send_media, get_message, send_announcement, queue_announcement, schedule_reminder
   :undoc-members:

.. autoclass:: kajenn_bot_application.whatsapp.WhatsAppBotGrammar
   :members:

.. autoclass:: kajenn_bot_application.whatsapp.WhatsAppBotInstanceGrammar
   :members:

Shared instance grammar
-----------------------

.. autoclass:: kajenn_bot_application.bot.BotInstanceGrammar
   :members:

Personal Telegram account
-------------------------

See :doc:`guides/telegram-account` for local enrollment, policy and operation contracts.

.. autoclass:: kajenn_bot_application.TelegramAccountApplication
   :members: start_login, complete_login, get_policy, set_policy, revoke_session, get_status, get_chats, get_messages, send_text, send_document, edit_message, delete_messages, create_channel, create_group, set_chat_details, get_members, invite_members, remove_member, set_member_admin, download_media, transcribe_message, react_message, mark_read, archive_chat, mute_chat, pin_message, block_contact, set_profile, get_contacts, forward_message, schedule_message, get_scheduled_messages, cancel_scheduled_message, create_poll, get_poll, vote_poll, send_media, get_events, get_audit_log, request_message, get_message_requests, decide_message
   :undoc-members:

Personal WhatsApp account
-------------------------

The source-tree prototype is not exported by ``kajenn_bot_application`` and is
not included in its wheel. Its 76 routed signatures, roles and primary grants
are documented in :doc:`guides/whatsapp-account-tools`; setup and operational
contracts are in :doc:`guides/whatsapp-account-prototype`.
