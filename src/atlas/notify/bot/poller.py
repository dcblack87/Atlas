"""The getUpdates loop — Atlas has no public endpoint, so the bot polls.

Auth model: exactly one chat may talk to this bot. If ``chat_id`` isn't
configured yet, /start answers with the sender's chat id to make first-time
setup self-service; everything else from strangers is silently ignored.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from atlas.config import TelegramSection
from atlas.notify.bot.commands import bot_command_list, process
from atlas.notify.bot.transport import BotAPI

if TYPE_CHECKING:
    from atlas.runtime import Runtime

log = logging.getLogger(__name__)

ERROR_BACKOFF_S = 10


class TelegramBot:
    def __init__(self, config: TelegramSection, runtime: Runtime) -> None:
        self._config = config
        self._runtime = runtime
        token = config.resolve_token()
        assert token, "TelegramBot requires a resolved token"
        self._api = BotAPI(token)

    async def run(self) -> None:
        await self._api.set_my_commands(bot_command_list())
        await self._api.set_chat_menu_button()
        log.info("telegram bot polling started")
        offset: int | None = None
        while True:
            try:
                updates = await self._api.get_updates(offset)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("telegram poll failed: %s", e)
                await asyncio.sleep(ERROR_BACKOFF_S)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    await self._handle(update)
                except Exception:
                    log.exception("telegram update handling failed")

    async def _handle(self, update: dict) -> None:
        callback = update.get("callback_query")
        if callback is not None:
            # ack first — dismisses the client-side spinner immediately
            await self._api.answer_callback(callback["id"])
            chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
            if not self._authorized(chat_id):
                return
            response = await process(self._runtime, str(callback.get("data", "")))
            if response is not None:
                await self._api.send_message(chat_id, response.text, response.keyboard)
            return

        message = update.get("message")
        if message is None:
            return
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = str(message.get("text", ""))
        if not self._authorized(chat_id):
            # self-service onboarding: tell the very first /start its chat id
            if not self._config.chat_id and text.startswith("/start"):
                await self._api.send_message(
                    chat_id,
                    "🧭 <b>Atlas Ops</b>\n\n"
                    f"Your chat id is <code>{chat_id}</code>.\n"
                    "Put it in atlas.toml under <code>[telegram] chat_id</code> "
                    "and restart Atlas.",
                )
            return
        response = await process(self._runtime, text)
        if response is not None:
            await self._api.send_message(chat_id, response.text, response.keyboard)

    def _authorized(self, chat_id: str) -> bool:
        return bool(self._config.chat_id) and chat_id == str(self._config.chat_id)
