"""Thin Telegram Bot API client — HTML parse mode, short timeouts, silent-fail.

Transport never raises into the poller loop for send-side calls: an alert
about Telegram being down cannot travel via Telegram anyway. Every call
reports what Telegram said, so a refused message is logged with its reason
rather than vanishing. ``get_updates`` does raise so the poller can back off
deliberately.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from atlas.notify.bot.format import chunk

log = logging.getLogger(__name__)

SEND_TIMEOUT = 10.0
ACK_TIMEOUT = 5.0
POLL_TIMEOUT = 25  # Telegram-side long-poll hold, seconds
MAX_RETRY_AFTER_S = 5  # wait out a rate limit this long, once; longer is reported


class PollConflict(Exception):
    """Someone else is reading this bot's updates: a second Atlas, or a webhook."""


@dataclass(frozen=True, slots=True)
class Result:
    ok: bool
    error: str | None = None
    status: int = 200


class BotAPI:
    def __init__(self, token: str) -> None:
        self._base = f"https://api.telegram.org/bot{token}"

    async def get_updates(self, offset: int | None) -> list[dict]:
        params: dict[str, Any] = {
            "timeout": POLL_TIMEOUT,
            "allowed_updates": '["message","callback_query"]',
        }
        if offset is not None:
            params["offset"] = offset
        async with httpx.AsyncClient(timeout=POLL_TIMEOUT + 10) as client:
            response = await client.get(f"{self._base}/getUpdates", params=params)
            if response.status_code == 409:
                raise PollConflict(response.text[:200])
            response.raise_for_status()
            data = response.json()
        return data.get("result", []) if data.get("ok") else []

    async def send_message(
        self, chat_id: str, text: str, keyboard: list[list[dict]] | None = None
    ) -> Result:
        """One message; a long one goes in parts, the buttons on the last."""
        parts = chunk(text)
        result = Result(False, "nothing to send", 0)
        for index, part in enumerate(parts):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": part,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if keyboard and index == len(parts) - 1:
                payload["reply_markup"] = {"inline_keyboard": keyboard}
            result = await self._post("sendMessage", payload, timeout=SEND_TIMEOUT)
            if not result.ok:
                return result
        return result

    async def edit_message(
        self, chat_id: str, message_id: int, text: str, keyboard: list[list[dict]] | None = None
    ) -> Result:
        """Change the message a button was pressed on.

        Telegram refuses an edit that changes nothing — which is what Refresh
        does when nothing moved — so that counts as done. A message too long
        or too old to edit is answered with a new one instead.
        """
        if len(text) > 4096:
            return await self.send_message(chat_id, text, keyboard)
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        result = await self._post("editMessageText", payload, timeout=SEND_TIMEOUT, quiet=True)
        if result.ok or "message is not modified" in (result.error or ""):
            return Result(True)
        if result.status == 400:
            return await self.send_message(chat_id, text, keyboard)
        log.warning("telegram editMessageText -> %s", result.error)
        return result

    async def answer_callback(self, callback_id: str, text: str | None = None) -> Result:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text[:200]
        return await self._post("answerCallbackQuery", payload, timeout=ACK_TIMEOUT)

    async def set_my_commands(self, commands: list[dict], chat_id: str | None = None) -> Result:
        """The command list, for one chat when given: nobody else sees it."""
        payload: dict[str, Any] = {"commands": commands}
        if chat_id:
            payload["scope"] = {"type": "chat", "chat_id": chat_id}
        return await self._post("setMyCommands", payload, timeout=ACK_TIMEOUT)

    async def delete_my_commands(self) -> Result:
        """Clear the list everyone sees, so a stranger who finds the bot sees nothing."""
        return await self._post("deleteMyCommands", {}, timeout=ACK_TIMEOUT)

    async def set_chat_menu_button(self, chat_id: str | None = None) -> Result:
        payload: dict[str, Any] = {"menu_button": {"type": "commands"}}
        if chat_id:
            payload["chat_id"] = chat_id
        # A group has no Menu button; Telegram says so with a 400, which is not news.
        return await self._post("setChatMenuButton", payload, timeout=ACK_TIMEOUT, quiet=True)

    async def _post(
        self, method: str, payload: dict, *, timeout: float, quiet: bool = False
    ) -> Result:
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(f"{self._base}/{method}", json=payload)
            except httpx.HTTPError as e:
                log.warning("telegram %s failed: %s", method, e)
                return Result(False, str(e) or type(e).__name__, 0)
            if response.status_code == 200:
                return Result(True)
            try:
                data = response.json()
            except ValueError:
                data = {}
            description = str(data.get("description") or response.text[:200])
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if (
                response.status_code == 429
                and attempt == 0
                and isinstance(retry_after, int | float)
                and retry_after <= MAX_RETRY_AFTER_S
            ):
                await asyncio.sleep(retry_after)
                continue
            if not quiet:
                log.warning("telegram %s -> HTTP %s: %s", method, response.status_code, description)
            return Result(False, description, response.status_code)
        return Result(False, "rate limited", 429)
