"""AtlasApp — the Textual application shell.

Owns keybindings, display-profile switching (F2), and the runtime lifecycle.
Data never originates here: screens read the store through ``self.runtime``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from textual.app import App
from textual.binding import Binding, BindingType

from atlas.config import Config
from atlas.model import PROFILE_ORDER, PROFILES, DisplayProfile
from atlas.runtime import Runtime
from atlas.tui.screens.dashboard import DashboardScreen
from atlas.tui.screens.host import HostScreen
from atlas.tui.screens.incidents import IncidentsScreen

_THEMES_DIR = Path(__file__).parent / "tui" / "themes"


class AtlasApp(App[None]):
    """The Atlas terminal UI."""

    TITLE = "Atlas"
    CSS_PATH: ClassVar = [
        _THEMES_DIR / "standard.tcss",
        _THEMES_DIR / "eink.tcss",
        _THEMES_DIR / "glance.tcss",
    ]

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("1", "goto('dashboard')", "Dashboard"),
        Binding("2", "goto('incidents')", "Incidents"),
        Binding("h", "goto('hosts')", "Hosts"),
        Binding("f2", "cycle_profile", "Display"),
        Binding("question_mark", "help", "Help", key_display="?"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config: Config | None = None, *, demo: bool = False) -> None:
        # E-ink first: animations are opt-out at the process level, before
        # the first frame renders. Standard profile keeps them off too — a
        # monitoring tool has no business animating.
        os.environ.setdefault("TEXTUAL_ANIMATIONS", "none")
        super().__init__()
        self.config = config
        self.demo = demo
        self.runtime: Runtime | None = None
        profile_name = config.atlas.display_profile if config else "standard"
        self.profile: DisplayProfile = PROFILES[profile_name]

    def on_mount(self) -> None:
        self._apply_profile(self.profile, announce=False)
        self.push_screen(DashboardScreen())
        if self.demo:
            self.run_worker(self._start_demo(), exclusive=True)
        elif self.config is not None:
            self.run_worker(self._start_runtime(), exclusive=True)

    async def _start_runtime(self) -> None:
        assert self.config is not None
        self.runtime = await Runtime.start(self.config)

    async def _start_demo(self) -> None:
        self.runtime = await Runtime.demo()
        self.notify("Demo fleet loaded — nothing here is real", timeout=4)

    async def action_quit(self) -> None:
        await self._stop_runtime()
        self.exit()

    async def on_unmount(self) -> None:
        await self._stop_runtime()

    async def _stop_runtime(self) -> None:
        if self.runtime is not None:
            runtime, self.runtime = self.runtime, None
            await runtime.stop()

    # ── display profiles ─────────────────────────────────────────────

    def action_cycle_profile(self) -> None:
        order = PROFILE_ORDER
        current = order.index(self.profile.name)
        self._apply_profile(PROFILES[order[(current + 1) % len(order)]])

    def _apply_profile(self, profile: DisplayProfile, *, announce: bool = True) -> None:
        self.profile = profile
        for name in PROFILE_ORDER:
            self.remove_class(f"-profile-{name}")
        self.add_class(f"-profile-{profile.name}")
        for screen in self.screen_stack:
            handler = getattr(screen, "on_profile_changed", None)
            if handler is not None:
                handler(profile)
        if announce:
            self.notify(f"Display profile: {profile.name}", timeout=2)

    # ── navigation ───────────────────────────────────────────────────

    def action_goto(self, target: str) -> None:
        match target:
            case "dashboard":
                while len(self.screen_stack) > 1:
                    self.pop_screen()
            case "hosts":
                if not isinstance(self.screen, HostScreen):
                    self.push_screen(HostScreen())
            case "incidents":
                if not isinstance(self.screen, IncidentsScreen):
                    self.push_screen(IncidentsScreen())
            case _:
                self.notify(f"{target} — coming soon", severity="warning", timeout=2)

    def action_help(self) -> None:
        self.notify("1 Dashboard · 2 Incidents · h Hosts · F2 display profile · q quit", timeout=4)
