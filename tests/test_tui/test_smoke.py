"""TUI smoke tests via Textual Pilot: screens mount, keys navigate."""

from pathlib import Path

from atlas.app import AtlasApp
from atlas.config import load_config
from atlas.tui.screens.dashboard import DashboardScreen
from atlas.tui.screens.host import HostScreen

REPO_ROOT = Path(__file__).parent.parent.parent


def example_config():
    return load_config(REPO_ROOT / "atlas.example.toml")


async def test_dashboard_mounts_without_config() -> None:
    app = AtlasApp(None, demo=True)
    async with app.run_test() as pilot:
        assert isinstance(app.screen, DashboardScreen)
        await pilot.pause()


async def test_profile_cycles() -> None:
    app = AtlasApp(None, demo=True)
    async with app.run_test() as pilot:
        assert app.profile.name == "standard"
        await pilot.press("f2")
        assert app.profile.name == "eink"
        assert app.has_class("-profile-eink")
        await pilot.press("f2")
        assert app.profile.name == "glance"
        await pilot.press("f2")
        assert app.profile.name == "standard"


async def test_host_screen_navigation() -> None:
    app = AtlasApp(example_config(), demo=True)  # demo=True: no runtime/SSH
    async with app.run_test() as pilot:
        await pilot.press("h")
        assert isinstance(app.screen, HostScreen)
        await pilot.press("escape")
        assert isinstance(app.screen, DashboardScreen)


async def test_dashboard_key_never_blanks_the_screen() -> None:
    """Pressing 1 while already on the dashboard must not pop it off the
    stack (the bottom of the Textual stack is a blank default screen)."""
    app = AtlasApp(example_config(), demo=True)
    async with app.run_test() as pilot:
        await pilot.press("1")
        assert isinstance(app.screen, DashboardScreen)
        await pilot.press("1")  # twice — the original repro
        assert isinstance(app.screen, DashboardScreen)
        # and from two screens deep it returns to the dashboard
        await pilot.press("h")
        await pilot.press("1")
        assert isinstance(app.screen, DashboardScreen)


async def test_every_screen_mounts() -> None:
    from atlas.tui.screens.chat import ChatScreen
    from atlas.tui.screens.cost import CostScreen
    from atlas.tui.screens.incidents import IncidentsScreen
    from atlas.tui.screens.logs import LogsScreen
    from atlas.tui.screens.reports import ReportsScreen
    from atlas.tui.screens.security import SecurityScreen

    app = AtlasApp(example_config(), demo=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        for key, screen_type in [
            ("2", IncidentsScreen),
            ("6", CostScreen),
            ("7", SecurityScreen),
            ("8", ReportsScreen),
            ("l", LogsScreen),
        ]:
            await pilot.press(key)
            assert isinstance(app.screen, screen_type), f"{key} -> {screen_type.__name__}"
            await pilot.press("escape")
            assert isinstance(app.screen, DashboardScreen)
        # chat's Input swallows number keys; check it mounts and pops cleanly
        await pilot.press("5")
        assert isinstance(app.screen, ChatScreen)
        await pilot.press("escape")
        # deploy is blocked in demo mode
        await pilot.press("4")
        assert isinstance(app.screen, DashboardScreen)
