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

"""WhatsApp HTTP delivery, service windows, uploads and explicit template sends.

Only connection establishment and explicit throttling rejections are retried.
5xx/read/write failures can have sent a message and are reported as uncertain.
Requests are serialized per business number within the application, never globally
across independent local/central deployments. Credentials stay out of exceptions.
"""

from __future__ import annotations

import asyncio
import copy
import json
import math
import mimetypes
import re
import time
from typing import Any

import httpx

from .bot import _BotAPIError
from .bot_delivery import _Reminders

MEDIA_LIMITS = {
    "image": 5_000_000,
    "document": 100_000_000,
    "audio": 16_000_000,
    "video": 16_000_000,
}


class _Delivery(_Reminders):
    """Provider-specific message eligibility, submission and receipt persistence."""

    acceptance_state = "accepted"

    def __init__(self, application: Any) -> None:
        self.application = application
        self.locks: dict[str, asyncio.Lock] = {}
        self.cooldowns: dict[str, float] = {}
        self.last_request: dict[str, float] = {}
        self.reminder_lock = asyncio.Lock()

    async def wait(self, delay: float) -> None:
        await asyncio.sleep(delay)

    async def request(
        self, registration: dict[str, Any], method: str, path: str, **kwargs: Any
    ) -> Any:
        attempts = self.application.config("whatsapp.retry_attempts", default=3)
        delay = self.application.config("whatsapp.retry_delay", default=1.0)
        interval = self.application.config("whatsapp.send_interval", default=0.0)
        if type(attempts) is not int or not 1 <= attempts <= 10:
            raise ValueError("retry_attempts must be between 1 and 10")
        if not all(
            isinstance(v, (float, int)) and math.isfinite(v) and v >= 0 for v in (delay, interval)
        ):
            raise ValueError("delivery delays must be finite and nonnegative")
        key = registration["phone_number_id"]
        async with self.locks.setdefault(key, asyncio.Lock()):
            for attempt in range(attempts):
                remaining = (
                    max(self.cooldowns.get(key, 0), self.last_request.get(key, 0) + interval)
                    - time.monotonic()
                )
                if remaining > 0:
                    await self.wait(remaining)
                self.cooldowns.pop(key, None)
                retry_delay = delay * 2**attempt
                try:
                    response = await self.application.client.request(
                        method,
                        f"https://graph.facebook.com/{self.application.api_version}/{path}",
                        headers={"Authorization": f"Bearer {registration['token']}"},
                        **kwargs,
                    )
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                    if attempt + 1 == attempts:
                        raise _BotAPIError("WhatsApp connection failed") from None
                except httpx.HTTPError:
                    raise _BotAPIError(
                        "WhatsApp transport failed (outcome uncertain)", outcome_uncertain=True
                    ) from None
                else:
                    try:
                        result = response.json()
                    except ValueError:
                        raise _BotAPIError(
                            "WhatsApp returned invalid JSON (outcome uncertain)",
                            outcome_uncertain=method != "GET",
                        ) from None
                    if not isinstance(result, dict):
                        raise _BotAPIError(
                            "WhatsApp returned an invalid response",
                            outcome_uncertain=method != "GET",
                        )
                    if response.is_success and "error" not in result:
                        return result
                    error = result.get("error") or {}
                    code = error.get("code") if isinstance(error, dict) else None
                    throttled = response.status_code == 429 or code in (130429, 131056)
                    if not throttled:
                        raise _BotAPIError(
                            f"WhatsApp request failed (HTTP {response.status_code}, code {code if type(code) is int else 'unknown'})",
                            outcome_uncertain=response.status_code >= 500,
                        )
                    retry_after = response.headers.get("retry-after", "0")
                    try:
                        seconds = float(retry_after)
                    except ValueError:
                        seconds = 0
                    if math.isfinite(seconds):
                        retry_delay = max(retry_delay, seconds)
                    self.cooldowns[key] = time.monotonic() + retry_delay
                    if attempt + 1 == attempts:
                        raise _BotAPIError("WhatsApp rate limit exceeded")
                finally:
                    self.last_request[key] = time.monotonic()
                await self.wait(retry_delay)
                self.cooldowns.pop(key, None)

    def get_text_parts(self, text: str) -> list[str]:
        if not isinstance(text, str) or not text:
            raise ValueError("WhatsApp text cannot be empty")
        return [text[i : i + 4096] for i in range(0, len(text), 4096)]

    def get_template(
        self, name: str, language: str, components: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]{1,512}", name):
            raise ValueError("invalid template name")
        if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}(?:_[A-Z]{2})?", language):
            raise ValueError("invalid template language")
        if components is not None and (
            not isinstance(components, list) or any(not isinstance(c, dict) for c in components)
        ):
            raise ValueError("template components must be a list of objects")
        result = {
            "name": name,
            "language": {"code": language},
            "components": copy.deepcopy(components or []),
        }
        json.dumps(result)
        return result

    async def window_open(self, bot_code: str, recipient: str) -> bool:
        record = await self.application._persist(
            "get_window", {"bot_code": bot_code, "recipient": recipient}
        )
        return record is not None and 0 <= time.time() - record["last_inbound"] < 24 * 60 * 60

    async def require_window(self, bot_code: str, recipient: str) -> None:
        if not self.application._valid_recipient(recipient):
            raise ValueError("recipient must be a nonempty provider identifier")
        if not await self.window_open(bot_code, recipient):
            raise ValueError(
                "WhatsApp service window is closed or unknown; supply an approved template"
            )

    async def send(
        self, bot_code: str, recipient: str, payload: dict[str, Any], *, template: bool = False
    ) -> dict[str, Any]:
        registration = self.application.registrations[bot_code]
        if not self.application._valid_recipient(recipient):
            raise ValueError("recipient must be a nonempty provider identifier")
        if not template:
            await self.require_window(bot_code, recipient)
        result = await self.request(
            registration,
            "POST",
            f"{registration['phone_number_id']}/messages",
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                **({"recipient": recipient} if "." in recipient else {"to": recipient}),
                **payload,
            },
        )
        try:
            message_id = result["messages"][0]["id"]
            if not isinstance(message_id, str) or not message_id:
                raise ValueError("missing message id")
        except (KeyError, IndexError, TypeError, ValueError):
            raise _BotAPIError(
                "WhatsApp message acceptance is uncertain", outcome_uncertain=True
            ) from None
        record = {
            "bot_code": bot_code,
            "message_id": message_id,
            "recipient": recipient,
            "status": "accepted",
            "timestamp": time.time(),
            "error_codes": [],
        }
        try:
            await self.application._persist("save_message", record)
        except Exception:
            raise _BotAPIError(
                "WhatsApp accepted the message but receipt persistence failed (outcome uncertain)",
                outcome_uncertain=True,
            ) from None
        return record

    async def send_media(
        self,
        bot_code: str,
        recipient: str,
        kind: str,
        media: str | bytes,
        *,
        filename: str | None = None,
        caption: str = "",
    ) -> Any:
        if kind not in MEDIA_LIMITS:
            raise ValueError("unsupported WhatsApp media kind")
        if len(caption) > 1024 or (kind == "audio" and caption):
            raise ValueError("invalid media caption")
        await self.require_window(bot_code, recipient)
        registration = self.application.registrations[bot_code]
        if isinstance(media, bytes):
            if not filename or not media or len(media) > MEDIA_LIMITS[kind]:
                raise ValueError("upload requires a filename and bytes within the media size limit")
            mime = mimetypes.guess_type(filename)[0]
            if mime is None:
                raise ValueError("filename must identify a supported MIME type")
            uploaded = await self.request(
                registration,
                "POST",
                f"{registration['phone_number_id']}/media",
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (filename, media, mime)},
            )
            if not isinstance(uploaded.get("id"), str):
                raise _BotAPIError("WhatsApp upload returned no media ID")
            reference = {"id": uploaded["id"]}
        elif isinstance(media, str) and media:
            if media.startswith("https://"):
                reference = {"link": media}
            elif "://" in media:
                raise ValueError("media URLs must use HTTPS")
            else:
                reference = {"id": media}
        else:
            raise ValueError("media must be an ID, HTTPS URL or bytes")
        if caption:
            reference["caption"] = caption
        if kind == "document" and filename:
            reference["filename"] = filename
        return await self.send(bot_code, recipient, {"type": kind, kind: reference})

    def validate_reminder(self, text, message_options):
        if message_options is None:
            self.get_text_parts(text)
        else:
            if text:
                raise ValueError("choose text or template explicitly")
            self.get_template(**message_options)

    async def send_reminder(self, record, conversation=None):
        template = record.get("message_options")
        record["message_ids"] = []
        if template is None:
            if conversation is not None:
                sent = await self.application.conversations.send_record_message(
                    conversation, record["user_id"], record["text"], chat_id=record["chat_id"]
                )
                record["message_ids"].append(sent["message_id"])
            else:
                for part in self.get_text_parts(record["text"]):
                    sent = await self.application.send_message(
                        record["bot_code"], record["chat_id"], part
                    )
                    record["message_ids"].append(sent["message_id"])
            return
        sent = await self.application.send_template(
            record["bot_code"], record["chat_id"], **template
        )
        record["message_ids"].append(sent["message_id"])
        if conversation is not None:
            conversation["messages"].append(
                {
                    "user_id": record["user_id"],
                    "chat_id": record["chat_id"],
                    "message_id": sent["message_id"],
                    "actions": [],
                    "text": f"Template: {template['name']}",
                    "resolution": None,
                }
            )
            await self.application.conversations.save_record(conversation)
