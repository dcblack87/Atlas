"""Bot dispatch, auth, and card rendering — against the seeded demo fleet."""

import pytest

from atlas.notify.bot.commands import COMMANDS, bot_command_list, process
from atlas.notify.bot.format import (
    DATA_LIMIT,
    MESSAGE_LIMIT,
    button,
    chunk,
    esc,
    open_button,
    paginate,
    read_tap,
)
from atlas.runtime import Runtime


@pytest.fixture
async def rt():
    runtime = await Runtime.demo()
    yield runtime
    await runtime.stop()


class TestDispatch:
    async def test_plain_text_is_ignored(self, rt) -> None:
        assert await process(rt, "hello there") is None

    async def test_unknown_command_nudges_help(self, rt) -> None:
        response = await process(rt, "/frobnicate")
        assert response is not None and "/help" in response.text

    async def test_botname_suffix_stripped(self, rt) -> None:
        response = await process(rt, "/status@atlas_ops_bot")
        assert response is not None and "Fleet status" in response.text

    async def test_callback_routing(self, rt) -> None:
        response = await process(rt, "cmd:backups")
        assert response is not None and "Backups" in response.text

    async def test_start_is_help(self, rt) -> None:
        response = await process(rt, "/start")
        assert response is not None and "Atlas Ops" in response.text
        assert response.keyboard  # the menu grid

    async def test_menu_is_start_too(self, rt) -> None:
        response = await process(rt, "/menu")
        assert response is not None and "Atlas Ops" in response.text

    async def test_every_command_renders(self, rt) -> None:
        for name in COMMANDS:
            response = await process(rt, f"/{name}")
            assert response is not None and response.text, name
            assert "Command failed" not in response.text, name
            # every card offers a way back to the menu (the menu's own is Refresh)
            flat = [b for row in response.keyboard for b in row]
            assert any(b.get("callback_data") == "cmd:help" for b in flat), name
            assert "as of" in response.text or name in ("ack", "ack_go", "incident"), name

    async def test_every_button_on_every_card_leads_somewhere(self, rt) -> None:
        """Walk the whole menu: tap every button, then every button those cards show."""
        seen: set[str] = set()
        queue = ["cmd:help"]
        while queue:
            data = queue.pop()
            if data in seen or data.startswith("cmd:ack_go"):
                continue
            seen.add(data)
            response = await process(rt, data)
            assert response is not None, data
            assert "Unknown command" not in response.text, data
            assert "Command failed" not in response.text, data
            assert len(response.text) <= MESSAGE_LIMIT, data
            assert all(row for row in response.keyboard), data  # no empty rows
            for row in response.keyboard:
                assert len(row) <= 8, data
                for cell in row:
                    assert len(cell["callback_data"].encode()) <= DATA_LIMIT
                    queue.append(cell["callback_data"])
        names = {data.split(":")[1] for data in seen}
        assert {"status", "incidents", "incident", "ack", "crons", "backups", "hosts"} <= names


class TestCards:
    async def test_status_reflects_demo_fleet(self, rt) -> None:
        response = await process(rt, "/status")
        assert "Hosts up: <b>3/3</b>" in response.text
        assert "Open incidents: <b>2</b>" in response.text

    async def test_crons_surface_the_failing_backup(self, rt) -> None:
        response = await process(rt, "/crons")
        first_job_line = response.text.split("\n\n", 1)[1].splitlines()[0]
        assert "❌" in first_job_line and "Shopfront backup" in first_job_line

    async def test_backups_card_shows_dates(self, rt) -> None:
        response = await process(rt, "/backups")
        assert "h ago" in response.text

    async def test_incidents_card(self, rt) -> None:
        response = await process(rt, "/incidents")
        assert "Open incidents (2)" in response.text

    async def test_home_shows_the_headline_before_any_tap(self, rt) -> None:
        response = await process(rt, "/start")
        assert "Health <b>" in response.text
        assert "<b>2</b> open incidents" in response.text


class TestAcknowledge:
    async def _open_id(self, rt) -> int:
        incidents = await rt.incidents.store.open_incidents()
        return next(i["id"] for i in incidents if i["status"] == "open")

    async def test_the_list_opens_an_incident_with_an_acknowledge_button(self, rt) -> None:
        listing = await process(rt, "cmd:incidents")
        numbers = [b for row in listing.keyboard for b in row if b["text"].isdigit()]
        assert len(numbers) == 2
        card = await process(rt, numbers[0]["callback_data"])
        flat = [b["callback_data"] for row in card.keyboard for b in row]
        assert any(data.startswith("cmd:ack:") for data in flat)
        assert "cmd:incidents" in flat

    async def test_acknowledge_asks_first_and_changes_nothing(self, rt) -> None:
        incident_id = await self._open_id(rt)
        question = await process(rt, f"cmd:ack:{incident_id}")
        assert "Acknowledge this incident?" in question.text
        flat = [b["callback_data"] for row in question.keyboard for b in row]
        assert f"cmd:ack_go:{incident_id}" in flat and f"cmd:incident:{incident_id}" in flat
        assert (await rt.incidents.store.get(incident_id))["status"] == "open"

    async def test_yes_acknowledges_once(self, rt) -> None:
        incident_id = await self._open_id(rt)
        done = await process(rt, f"cmd:ack_go:{incident_id}")
        assert done.toast == "Acknowledged. Reminders stop."
        assert (await rt.incidents.store.get(incident_id))["status"] == "acked"
        assert "acknowledged" in done.text
        # the button is gone, and a second tap is told so without a second event
        assert not any(b["text"].startswith("✓") for row in done.keyboard for b in row)
        events = "SELECT COUNT(*) FROM incident_events WHERE incident_id = ? AND kind = 'acked'"
        before = await rt.db.fetch_value(events, (incident_id,))
        again = await process(rt, f"cmd:ack_go:{incident_id}")
        assert again.toast == "Already acked."
        assert await rt.db.fetch_value(events, (incident_id,)) == before == 1

    async def test_an_incident_that_is_gone_says_so(self, rt) -> None:
        for data in ("cmd:incident:999999", "cmd:ack:999999", "cmd:ack_go:999999", "cmd:ack:x"):
            response = await process(rt, data)
            assert "not on file" in response.text, data


class TestFormat:
    def test_pages(self) -> None:
        items = list(range(30))
        assert paginate(items, [], 12) == (items[:12], 0, 3)
        assert paginate(items, ["2"], 12) == (items[24:], 2, 3)
        assert paginate(items, ["99"], 12)[1] == 2  # past the end lands on the last page
        assert paginate(items, ["x"], 12)[1] == 0
        assert paginate([], [], 12) == ([], 0, 1)

    def test_button_data(self) -> None:
        assert button("Go", "incident", 7) == {"text": "Go", "callback_data": "cmd:incident:7"}
        with pytest.raises(ValueError):
            button("Too long", "incident", "x" * DATA_LIMIT)

    def test_alert_button_data(self) -> None:
        assert open_button("Ack", "ack", 7) == {"text": "Ack", "callback_data": "open:ack:7"}
        assert read_tap("open:ack:7") == ("cmd:ack:7", True)
        assert read_tap("cmd:hosts") == ("cmd:hosts", False)
        with pytest.raises(ValueError):
            open_button("Too long", "incident", "x" * DATA_LIMIT)

    def test_chunk_splits_at_line_ends(self) -> None:
        text = "\n".join(f"line {i} " + "x" * 40 for i in range(300))
        parts = chunk(text)
        assert len(parts) > 1 and all(len(part) <= MESSAGE_LIMIT for part in parts)
        assert "\n".join(parts) == text
        assert chunk("short") == ["short"]


class TestRegistry:
    def test_command_menu_registration(self) -> None:
        commands = bot_command_list()
        names = [c["command"] for c in commands]
        assert names[-1] == "help"  # menu entry last
        assert "status" in names and "crons" in names
        assert all(len(c["description"]) <= 256 for c in commands)
        # the button-only cards are not typed commands
        assert not {"incident", "ack", "ack_go"} & set(names)

    def test_esc(self) -> None:
        assert esc("<b>&") == "&lt;b&gt;&amp;"
