"""Thin Telegram Bot API client — HTML parse mode, short timeouts, silent-fail.

Transport never raises into the poller loop for send-side calls: an alert
about Telegram being down cannot travel via Telegram anyway. ``get_updates``
does raise so the poller can back off deliberately.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

SEND_TIMEOUT = 10.0
ACK_TIMEOUT = 5.0
POLL_TIMEOUT = 25  # Telegram-side long-poll hold, seconds


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
            response.raise_for_status()
            data = response.json()
        return data.get("result", []) if data.get("ok") else []

    async def send_message(
        self, chat_id: str, text: str, keyboard: list[list[dict]] | None = None
    ) -> bool:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return await self._post("sendMessage", payload, timeout=SEND_TIMEOUT)

    async def answer_callback(self, callback_id: str) -> bool:
        return await self._post(
            "answerCallbackQuery", {"callback_query_id": callback_id}, timeout=ACK_TIMEOUT
        )

    async def set_my_commands(self, commands: list[dict]) -> bool:
        return await self._post("setMyCommands", {"commands": commands}, timeout=ACK_TIMEOUT)

    async def set_chat_menu_button(self) -> bool:
        return await self._post(
            "setChatMenuButton", {"menu_button": {"type": "commands"}}, timeout=ACK_TIMEOUT
        )

    async def _post(self, method: str, payload: dict, *, timeout: float) -> bool:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self._base}/{method}", json=payload)
                if response.status_code != 200:
                    log.warning(
                        "telegram %s -> HTTP %s: %s",
                        method,
                        response.status_code,
                        response.text[:200],
                    )
                return response.status_code == 200
        except httpx.HTTPError as e:
            log.warning("telegram %s failed: %s", method, e)
            return False
