"""Bot dispatch, auth, and card rendering — against the seeded demo fleet."""

import pytest

from atlas.notify.bot.commands import COMMANDS, bot_command_list, process
from atlas.notify.bot.format import esc
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

    async def test_every_command_renders(self, rt) -> None:
        for name in COMMANDS:
            response = await process(rt, f"/{name}")
            assert response is not None and response.text, name
            assert "Command failed" not in response.text, name
            # every card offers a way back to the menu
            flat = [b for row in response.keyboard for b in row]
            assert any(b.get("callback_data") == "cmd:help" for b in flat) or name == "help"


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


class TestRegistry:
    def test_command_menu_registration(self) -> None:
        commands = bot_command_list()
        names = [c["command"] for c in commands]
        assert names[-1] == "help"  # menu entry last
        assert "status" in names and "crons" in names
        assert all(len(c["description"]) <= 256 for c in commands)

    def test_esc(self) -> None:
        assert esc("<b>&") == "&lt;b&gt;&amp;"
