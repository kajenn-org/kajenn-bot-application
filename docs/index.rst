kajenn-bot-application
======================

Document version: 0.2 — Last updated: 2026-10-08 — Status: 🔴 UNDER REVIEW

Telegram and WhatsApp bots, mounted as kajenn applications.
Receive centrally, send from local services, and coordinate conversations with
multiple participants using ordinary Python handlers.

The package provides independent bot instances, class-owned configuration,
optional administrator admission, announcements, media and persistent reminders.
It depends on kajenn and has its own release cycle.

Start here
----------

* :doc:`getting-started` — install, configure credentials and get the first reply.
* :doc:`guides/writing-bots` — define commands, configuration and reusable classes.
* :doc:`guides/conversations` — correlate replies, involve multiple participants and require approval.
* :doc:`guides/operations` — deploy, inspect outcomes and diagnose failures.

Provider behavior
-----------------

Telegram uses BotFather tokens and supports native polls. WhatsApp uses Business
Cloud API credentials, approved templates and delivery receipts. Provider limits
and identity formats remain explicit; a shared base does not make their APIs
interchangeable.

.. toctree::
   :maxdepth: 1
   :caption: Learn

   getting-started
   guides/bots
   guides/writing-bots
   guides/conversations

.. toctree::
   :maxdepth: 1
   :caption: Configure and operate

   guides/telegram
   guides/telegram-account
   guides/whatsapp
   guides/persistence
   guides/administration
   guides/operations

.. toctree::
   :maxdepth: 1
   :caption: Reference and contribute

   api
   development
   migration

Project links
-------------

`Source and issues <https://github.com/kajenn-org/kajenn-bot-application>`_ ·
`Builds <https://github.com/kajenn-org/kajenn-bot-application/actions>`_ ·
`Coverage <https://app.codecov.io/gh/kajenn-org/kajenn-bot-application>`_

Apache-2.0. Copyright Softwell S.r.l.
Based on genropy history and genro-modules.
