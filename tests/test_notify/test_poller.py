"""The poller: who is answered, how a tap differs from a typed command, and
what is registered with Telegram on start. Against a fake client."""

import pytest

from atlas.config import TelegramSection
from atlas.notify.bot.commands import bot_command_list
from atlas.notify.bot.poller import TelegramBot
from atlas.notify.bot.transport import Result
from atlas.runtime import Runtime

OWNER = "555000111"


class FakeAPI:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def send_message(self, chat_id, text, keyboard=None) -> Result:
        self.calls.append(("send", chat_id, text, keyboard))
        return Result(True)

    async def edit_message(self, chat_id, message_id, text, keyboard=None) -> Result:
        self.calls.append(("edit", chat_id, message_id, text, keyboard))
        return Result(True)

    async def answer_callback(self, callback_id, text=None) -> Result:
        self.calls.append(("answer", callback_id, text))
        return Result(True)

    async def set_my_commands(self, commands, chat_id=None) -> Result:
        self.calls.append(("commands", commands, chat_id))
        return Result(True)

    async def delete_my_commands(self) -> Result:
        self.calls.append(("delete_commands",))
        return Result(True)

    async def set_chat_menu_button(self, chat_id=None) -> Result:
        self.calls.append(("menu_button", chat_id))
        return Result(True)

    def kinds(self) -> list[str]:
        return [call[0] for call in self.calls]


@pytest.fixture
async def rt():
    runtime = await Runtime.demo()
    yield runtime
    await runtime.stop()


def _bot(rt, chat_id: str | None = OWNER) -> tuple[TelegramBot, FakeAPI]:
    api = FakeAPI()
    config = TelegramSection(enabled=True, bot_token="x", chat_id=chat_id)
    return TelegramBot(config, rt, api=api), api  # type: ignore[arg-type]


def _typed(text: str, chat_id: str = OWNER) -> dict:
    return {"update_id": 1, "message": {"chat": {"id": int(chat_id)}, "text": text}}


def _tapped(data: str, chat_id: str = OWNER) -> dict:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "tap-1",
            "data": data,
            "message": {"message_id": 77, "chat": {"id": int(chat_id)}},
        },
    }


class TestWhoIsAnswered:
    async def test_a_typed_command_from_the_owner_gets_a_new_message(self, rt) -> None:
        bot, api = _bot(rt)
        await bot._handle(_typed("/status"))
        assert api.kinds() == ["send"]
        assert api.calls[0][1] == OWNER and "Fleet status" in api.calls[0][2]

    async def test_a_tap_changes_the_message_it_was_pressed_on(self, rt) -> None:
        bot, api = _bot(rt)
        await bot._handle(_tapped("cmd:hosts"))
        assert api.kinds() == ["answer", "edit"]
        assert api.calls[1][1:3] == (OWNER, 77) and "Hosts" in api.calls[1][3]

    async def test_a_tap_under_an_alert_answers_below_it(self, rt) -> None:
        bot, api = _bot(rt)
        await bot._handle(_tapped("open:incidents"))
        # a new message, not an edit: the alert stays in the chat as it was sent
        assert api.kinds() == ["answer", "send"]
        assert api.calls[1][1] == OWNER and "ncident" in api.calls[1][2]

    async def test_acknowledge_under_an_alert_asks_first(self, rt) -> None:
        bot, api = _bot(rt)
        incident = next(
            i for i in await rt.incidents.store.open_incidents() if i["status"] == "open"
        )
        await bot._handle(_tapped(f"open:ack:{incident['id']}"))
        assert api.kinds() == ["answer", "send"]
        assert (await rt.incidents.store.get(incident["id"]))["status"] == "open"

    async def test_a_tap_says_what_happened_over_the_chat(self, rt) -> None:
        bot, api = _bot(rt)
        incident = next(
            i for i in await rt.incidents.store.open_incidents() if i["status"] == "open"
        )
        await bot._handle(_tapped(f"cmd:ack_go:{incident['id']}"))
        assert api.calls[0] == ("answer", "tap-1", "Acknowledged. Reminders stop.")

    async def test_another_chat_is_not_answered(self, rt) -> None:
        bot, api = _bot(rt)
        await bot._handle(_typed("/status", "999"))
        assert api.calls == []

    async def test_another_chats_tap_is_dismissed_and_does_nothing(self, rt) -> None:
        bot, api = _bot(rt)
        incident = next(
            i for i in await rt.incidents.store.open_incidents() if i["status"] == "open"
        )
        await bot._handle(_tapped(f"cmd:ack_go:{incident['id']}", "999"))
        assert api.calls == [("answer", "tap-1", None)]
        assert (await rt.incidents.store.get(incident["id"]))["status"] == "open"

    async def test_ordinary_words_are_ignored(self, rt) -> None:
        bot, api = _bot(rt)
        await bot._handle(_typed("morning all"))
        assert api.calls == []

    async def test_first_start_with_no_chat_configured_learns_the_chat_id(self, rt) -> None:
        bot, api = _bot(rt, chat_id=None)
        await bot._handle(_typed("/start", "424242"))
        assert api.kinds() == ["send"] and "424242" in api.calls[0][2]
        await bot._handle(_typed("/status", "424242"))
        assert api.kinds() == ["send"]  # still nothing but its own id


class TestRegistration:
    async def test_the_list_is_for_the_owners_chat_only(self, rt) -> None:
        bot, api = _bot(rt)
        await bot.register_menu()
        assert api.calls == [
            ("delete_commands",),
            ("commands", bot_command_list(), OWNER),
            ("menu_button", OWNER),
        ]

    async def test_with_no_chat_only_start_is_listed(self, rt) -> None:
        bot, api = _bot(rt, chat_id=None)
        await bot.register_menu()
        assert api.kinds() == ["commands"]
        assert [c["command"] for c in api.calls[0][1]] == ["start"]
        assert api.calls[0][2] is None
