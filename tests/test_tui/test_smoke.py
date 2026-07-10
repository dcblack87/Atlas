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
