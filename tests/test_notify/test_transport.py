"""The Telegram client against a fake Telegram: what is sent, and what is
made of Telegram's answers."""

import json

import httpx
import pytest

from atlas.notify.bot import transport
from atlas.notify.bot.transport import BotAPI, PollConflict


class FakeTelegram:
    """Answers each call with the next queued reply for its method, or a plain ok."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.replies: dict[str, list[httpx.Response]] = {}

    def queue(self, method: str, status: int, body: dict) -> None:
        self.replies.setdefault(method, []).append(httpx.Response(status, json=body))

    def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content) if request.content else dict(request.url.params)
        self.calls.append((method, payload))
        queued = self.replies.get(method)
        if queued:
            return queued.pop(0)
        return httpx.Response(200, json={"ok": True, "result": []})

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]


@pytest.fixture
def telegram(monkeypatch) -> FakeTelegram:
    fake = FakeTelegram()
    real = httpx.AsyncClient

    def client(**kwargs):
        return real(transport=httpx.MockTransport(fake.handle), **kwargs)

    monkeypatch.setattr(transport.httpx, "AsyncClient", client)

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(transport.asyncio, "sleep", no_wait)
    return fake


KEYBOARD = [[{"text": "« Menu", "callback_data": "cmd:help"}]]


async def test_a_message_goes_as_html_with_its_buttons(telegram) -> None:
    result = await BotAPI("t").send_message("1", "<b>hi</b>", KEYBOARD)
    assert result.ok
    method, payload = telegram.calls[0]
    assert method == "sendMessage" and payload["parse_mode"] == "HTML"
    assert payload["reply_markup"] == {"inline_keyboard": KEYBOARD}


async def test_a_long_message_goes_in_parts_with_the_buttons_on_the_last(telegram) -> None:
    text = "\n".join(f"line {i} " + "x" * 40 for i in range(300))
    assert (await BotAPI("t").send_message("1", text, KEYBOARD)).ok
    assert len(telegram.calls) > 1
    assert all(len(payload["text"]) <= 4096 for _, payload in telegram.calls)
    assert all("reply_markup" not in payload for _, payload in telegram.calls[:-1])
    assert "reply_markup" in telegram.calls[-1][1]
    assert "\n".join(payload["text"] for _, payload in telegram.calls) == text


async def test_a_refused_message_reports_telegrams_reason(telegram) -> None:
    telegram.queue("sendMessage", 400, {"ok": False, "description": "Bad Request: can't parse"})
    result = await BotAPI("t").send_message("1", "<b>oops")
    assert not result.ok and "can't parse" in (result.error or "") and result.status == 400


async def test_a_refresh_that_changes_nothing_counts_as_done(telegram) -> None:
    telegram.queue(
        "editMessageText", 400, {"ok": False, "description": "Bad Request: message is not modified"}
    )
    assert (await BotAPI("t").edit_message("1", 7, "same", KEYBOARD)).ok
    assert telegram.methods() == ["editMessageText"]


async def test_a_message_too_old_to_edit_is_answered_with_a_new_one(telegram) -> None:
    telegram.queue(
        "editMessageText", 400, {"ok": False, "description": "Bad Request: message can't be edited"}
    )
    assert (await BotAPI("t").edit_message("1", 7, "fresh", KEYBOARD)).ok
    assert telegram.methods() == ["editMessageText", "sendMessage"]


async def test_a_short_rate_limit_is_waited_out_once(telegram) -> None:
    limited = {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 2}}
    telegram.queue("sendMessage", 429, limited)
    assert (await BotAPI("t").send_message("1", "hi")).ok
    assert telegram.methods() == ["sendMessage", "sendMessage"]


async def test_a_long_rate_limit_is_reported_not_waited_for(telegram) -> None:
    limited = {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 600}}
    telegram.queue("sendMessage", 429, limited)
    result = await BotAPI("t").send_message("1", "hi")
    assert not result.ok and result.status == 429
    assert telegram.methods() == ["sendMessage"]


async def test_the_command_list_can_be_for_one_chat(telegram) -> None:
    commands = [{"command": "status", "description": "Fleet overview"}]
    await BotAPI("t").set_my_commands(commands, "555")
    await BotAPI("t").set_my_commands(commands)
    assert telegram.calls[0][1]["scope"] == {"type": "chat", "chat_id": "555"}
    assert "scope" not in telegram.calls[1][1]


async def test_a_tap_can_be_answered_with_a_line(telegram) -> None:
    await BotAPI("t").answer_callback("tap", "Acknowledged.")
    await BotAPI("t").answer_callback("tap")
    assert telegram.calls[0][1] == {"callback_query_id": "tap", "text": "Acknowledged."}
    assert telegram.calls[1][1] == {"callback_query_id": "tap"}


async def test_two_readers_of_one_bot_is_named_as_such(telegram) -> None:
    telegram.queue("getUpdates", 409, {"ok": False, "description": "Conflict: terminated by other"})
    with pytest.raises(PollConflict):
        await BotAPI("t").get_updates(None)
