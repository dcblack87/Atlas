"""The getUpdates loop — Atlas has no public endpoint, so the bot polls.

Auth model: exactly one chat may talk to this bot. If ``chat_id`` isn't
configured yet, /start answers with the sender's chat id to make first-time
setup self-service; everything else from strangers is silently ignored.

A tap changes the message it was pressed on, so walking the menu does not
fill the chat; a typed command gets a new message. Every tap is answered,
so a button never spins for ever.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from atlas.config import TelegramSection
from atlas.notify.bot.commands import bot_command_list, process
from atlas.notify.bot.format import read_tap
from atlas.notify.bot.transport import BotAPI, PollConflict

if TYPE_CHECKING:
    from atlas.runtime import Runtime

log = logging.getLogger(__name__)

ERROR_BACKOFF_S = 10
CONFLICT_BACKOFF_S = 60


class TelegramBot:
    def __init__(
        self, config: TelegramSection, runtime: Runtime, api: BotAPI | None = None
    ) -> None:
        self._config = config
        self._runtime = runtime
        token = config.resolve_token()
        assert api is not None or token, "TelegramBot requires a resolved token"
        self._api = api or BotAPI(token or "")

    async def register_menu(self) -> None:
        """The command list and the Menu button, set on every start.

        The list is for the configured chat only: a stranger who finds the bot
        sees no commands. Until a chat is configured the only command anyone
        sees is /start, which is how the chat id is learned.
        """
        chat_id = str(self._config.chat_id) if self._config.chat_id else None
        if chat_id is None:
            await self._api.set_my_commands(
                [{"command": "start", "description": "Show this chat's id for atlas.toml"}]
            )
            return
        await self._api.delete_my_commands()
        listed = await self._api.set_my_commands(bot_command_list(), chat_id)
        if not listed.ok:
            log.warning("telegram command list was not accepted: %s", listed.error)
        # Only a one-to-one chat has a Menu button; a group shows a / button instead.
        await self._api.set_chat_menu_button(chat_id)

    async def run(self) -> None:
        await self.register_menu()
        log.info("telegram bot polling started")
        offset: int | None = None
        while True:
            try:
                updates = await self._api.get_updates(offset)
            except asyncio.CancelledError:
                raise
            except PollConflict:
                log.error(
                    "telegram: something else is reading this bot's updates (a second Atlas "
                    "on the same token, or a webhook). Only one may. Retrying in %ss.",
                    CONFLICT_BACKOFF_S,
                )
                await asyncio.sleep(CONFLICT_BACKOFF_S)
                continue
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
            message = callback.get("message") or {}
            chat_id = str(message.get("chat", {}).get("id", ""))
            if not self._authorized(chat_id):
                await self._api.answer_callback(callback["id"])
                return
            command, fresh = read_tap(str(callback.get("data", "")))
            response = await process(self._runtime, command)
            # answered after the work, so the tap can say what happened
            await self._api.answer_callback(callback["id"], response.toast if response else None)
            if response is None:
                return
            message_id = message.get("message_id")
            # a card changes in place; a tap under an alert answers below it,
            # so the alert stays in the chat as it was sent
            if isinstance(message_id, int) and not fresh:
                await self._api.edit_message(chat_id, message_id, response.text, response.keyboard)
            else:
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
