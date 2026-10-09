# Administration over REST and MCP

**Document version:** 0.1 · **Last updated:** 2026-10-08 · **Status:** 🔴 UNDER REVIEW

This interface requires kajenn 0.4.1 or newer, providing the unified
`discover(scope)` API for OpenAPI and MCP.

The application limits discovery to its administrative branch. kajenn applies
the authenticated caller's filters for both schema generation and MCP tool
listing, using the same filtering mechanism as execution.

Each Telegram or WhatsApp application also exposes its own administrative routing
class. An administrative operation calls the owning application's Python API
without executing a command on a registered bot. The `bot_code` selects the
registered credentials and configuration used for delivery.

For an application mounted at `/mybotapp`:

| Surface | URL | Purpose |
|---|---|---|
| MCP | `/mybotapp/_mcp` | Streamable HTTP MCP endpoint |
| REST | `/mybotapp/_admin/<operation>` | POST a JSON object of arguments |
| OpenAPI | `/mybotapp/_meta/schema_json` | Administrative API schema |
| Swagger UI | `/mybotapp/_meta/docs` | Browse the administrative API |

The MCP tool name is in the JSON-RPC body, not an extra URL segment. Tool names
are operation names such as `send_message`, without an `_admin.` prefix.
All REST operations use POST, including reads. String results such as a reminder
or task identifier use kajenn's plain-text response; dictionary and list results
use JSON. MCP wraps results in its standard tool response.

Administrative endpoints are available on both receiving and send-only
deployments. Existing webhook URLs are unchanged: Telegram still receives at
`/mybotapp/<bot_code>`, while WhatsApp receives at `/mybotapp`. Internal task
entry points are neither administrative routes nor MCP tools.

## Authentication

Every administrative operation requires the kajenn `admin` role. Configure the
hosting server's authentication for both the `rest` and `mcp` channels. For an
existing identity application with an authentication route, the server options
can include:

```python
channels = {
    "rest": {"authentication_route": "/identity/check"},
    "mcp": {"authentication_route": "/identity/check"},
}
```

The identity service verifies the presented credential and returns the identity
and its tags according to kajenn's authentication contract. A credential intended
for administration must resolve to tags containing `admin`. OpenAPI and Swagger
also require that role. Anonymous and non-admin users cannot discover or invoke
the administrative tools.

Use the host application's credentials for these calls. A Telegram BotFather
token, WhatsApp token, webhook secret or provider admission decision does not
grant a kajenn administrative role. The role grants access to all bot instances
owned by this application; there is no per-bot administrative permission model.

## Send directly through the application

REST example, using a host credential in `KAJENN_ADMIN_TOKEN`:

```bash
curl --fail-with-body https://example.com/mybotapp/_admin/send_message \
  -H "Authorization: Bearer $KAJENN_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"bot_code":"team","chat_id":42,"text":"You have a new PR"}'
```

Equivalent MCP `tools/call` body, sent to `/mybotapp/_mcp` with the same
Authorization header:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "send_message",
    "arguments": {
      "bot_code": "team",
      "chat_id": 42,
      "text": "You have a new PR"
    }
  }
}
```

Telegram chat IDs are integers. WhatsApp IDs and BSUIDs are strings, for example
`"391234"` or `"IT.123456789"`. WhatsApp free-form messages still require an open
service window; use `send_template` with an approved template outside that
window. Administration does not bypass provider restrictions.

## Register bot instances

Remote registration selects a class from a deployment-controlled catalog. Supply
`bot_classes` to the application constructor, mapping aliases to importable bot
classes or `module:Class` references:

```python
options = {
    "code": "mybotapp",
    "persistence_route": "registry/bots",
    "bot_classes": {"review": "my_package:ReviewBot"},
}
```

The same catalog can be declared in the provider grammar:

```python
app.telegram(
    persistence_route="registry/bots",
    bot_classes={"review": "my_package:ReviewBot"},
)
```

For WhatsApp, use `app.whatsapp(...)` with its required `api_version` and provider
settings. An explicit constructor catalog overrides the grammar catalog. An
empty catalog disables remote registration but does not affect existing instances
or trusted Python calls to `register_bot`.

Call `list_bot_classes` to discover the allowed aliases. For Telegram, invoke
`register_bot` with `code`, `bot_class` (the alias), `token`, and optional `name`,
`icon`, `config`. WhatsApp additionally requires `phone_number_id` and
`business_account_id`. A caller cannot submit an arbitrary module to import.
The deployment must install and authorize the class first.

`register_bot`, `activate_bot`, `list_bots` and `get_bot_registration` return only
explicit registration metadata. They exclude provider tokens, webhook credentials
and raw bot configuration. The trusted Python API's `get_bot_registration` keeps
its original full-record contract.

## Available operations

| Group | Shared operations |
|---|---|
| Registry | `list_bot_classes`, `list_bots`, `get_bot_registration`, `register_bot`, `activate_bot` |
| Delivery | `send_message`, `send_text`, `send_announcement`, `queue_announcement` |
| Conversations | `create_conversation`, `get_conversation`, `send_conversation_message`, `update_conversation_context`, `close_conversation` |
| Reminders | `schedule_reminder`, `get_reminder`, `cancel_reminder` |

Telegram additionally exposes `send_poll`, `get_poll`, `stop_poll`. WhatsApp
additionally exposes `send_template`, `get_message`; its announcement and reminder
operations also accept a `template` object. `tools/list` and the OpenAPI schema
provide the exact signatures for the mounted provider.

A reminder's `when` argument is an ISO 8601 string with a timezone, for example
`2026-12-01T09:00:00+01:00`. Context updates require the current `revision`.
Queued announcements and reminders use the existing task manager and persistence
route. `close_conversation` cannot approve or reject admission requests.

Creating or sending into a conversation is administrative. Later replies from
participants are still delivered to the bot route selected for that conversation.
