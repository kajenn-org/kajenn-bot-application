# Writing a bot

**Document version:** 0.2 · **Last updated:** 2026-10-08 · **Status:** 🔴 UNDER REVIEW

A bot is an importable `RoutingClass`. Its methods define behavior; registrations
create separate instances with their own credentials and configuration. Keep
your bot package importable in every deployment that restores its registrations.

## Define commands and configuration

Place this code in your application's Python package, for example `team_bot/__init__.py`:

```python
from genro_builders.builder import element
from genro_routes import RoutingClass, route
from kajenn_bot_application.telegram import TelegramBotInstanceGrammar


class TeamBotGrammar(TelegramBotInstanceGrammar):
    @element(node_label="settings", sub_tags="")
    def settings(self, greeting: str = "Hello", dataset: str = "team") -> None:
        """Choose a greeting and the application dataset for this instance."""


class TeamBot(RoutingClass):
    grammar = TeamBotGrammar

    def __init__(self, application, code, config):
        self.application = application
        self.code = code
        self.config = config
        super().__init__()

    @route()
    def hello(self, text: str = "") -> str:
        return f"{self.config('settings.greeting')} [{self.config('settings.dataset')}]"

    @route()
    async def echo(self, text: str = "") -> str:
        return text or "Send /echo followed by some text."

    @route()
    async def review(self, text="", sender=None, conversation=None, action=""):
        if conversation is None:
            return "Reply to a review request to continue."
        return f"PR {conversation['context']['pr']}: {action or text}"
```

The grammar accepts a flat mapping of element names to attribute dictionaries.
Unknown elements and attributes fail validation at registration. Inheriting
`TelegramBotInstanceGrammar` also provides the optional `access` element for
administrator admission. For a WhatsApp bot, inherit `WhatsAppBotInstanceGrammar`
from `kajenn_bot_application.whatsapp`; it adds notification-template settings.
Common classes can inherit `BotInstanceGrammar`, provided their handlers and
configuration respect each provider's identifier and capability contracts.

## Register independent instances

After application startup, trusted Python code can register the class:

```python
from team_bot import TeamBot

await telegram.register_bot(
    code="engineering",
    bot_class=TeamBot,
    token=engineering_token,
    name="Engineering assistant",
    config={"settings": {"greeting": "Welcome", "dataset": "engineering"}},
)
```

Register another code with a different Telegram token and another `dataset` to
reuse the implementation with separate state. For WhatsApp, registration also
supplies `phone_number_id` and `business_account_id`; each instance owns a unique
phone number within that application.

`name` and `icon` are local registration metadata. They do not edit the provider's
profile. The dataset value is interpreted by your handler, not by the bot base.
Pass credentials through your deployment's secret mechanism rather than literals.

## Handler contracts

| Handler | Inputs | Return value |
|---|---|---|
| Command | `text`: everything after `/command` | A reply string or `None` |
| Conversation | `text`, provider `sender`, conversation snapshot, `action` | A reply to the same participant, or `None` |
| Telegram poll callback | `event`, persisted `poll` snapshot | Return value is not sent as a reply |

Synchronous methods run through the kajenn worker pool. Asynchronous methods run
on the event loop; do not perform blocking work inside them. Exceptions appear
in the task outcome. Telegram command replies are one plain-text message;
explicitly call `send_text` for long output and return `None`. WhatsApp command
replies already use `send_text`.

The application can also dispatch a slash command matching a conversation or
poll method. Defaults such as those in `review` let the method reject a direct
command gracefully when no correlated event is present.

## Authorization and identity

The router's authorization plugin is active for bot routes. A valid webhook
proves provider delivery, and admission records who may interact with the bot.
Neither creates a kajenn avatar or grants router roles. A route protected with
`auth_rule="admin"` remains unavailable to ordinary bot senders.

Use the configured admission administrators for approval workflows. Application
account linking and token/permission provisioning belong to your hosting
application; this package does not implement those decisions implicitly.

## Bot-class inheritance

You can subclass a shared bot class to inherit commands and extend its grammar.
Every registration still constructs its own router and configuration. Keep
per-instance state on the instance or in persistence, never in module globals.
Store durable conversation state with the conversation API so replies remain
correlated after a restart.

The runnable examples are
[Telegram DemoBot](https://github.com/kajenn-org/kajenn-bot-application/blob/main/examples/telegram_bot/__init__.py)
and [WhatsApp DemoBot](https://github.com/kajenn-org/kajenn-bot-application/blob/main/examples/whatsapp_bot/__init__.py).
