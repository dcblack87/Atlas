"""AtlasApp — the Textual application shell.

Owns keybindings, display-profile switching (F2), and screen wiring. Data
never originates here: screens read the store and subscribe to the bus.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from textual.app import App
from textual.binding import Binding, BindingType

from atlas.config import Config
from atlas.model import PROFILE_ORDER, PROFILES, DisplayProfile
from atlas.tui.screens.dashboard import DashboardScreen

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
        profile_name = config.atlas.display_profile if config else "standard"
        self.profile: DisplayProfile = PROFILES[profile_name]

    def on_mount(self) -> None:
        self._apply_profile(self.profile)
        self.push_screen(DashboardScreen(self.config))

    # ── display profiles ─────────────────────────────────────────────

    def action_cycle_profile(self) -> None:
        order = PROFILE_ORDER
        current = order.index(self.profile.name)
        self._apply_profile(PROFILES[order[(current + 1) % len(order)]])

    def _apply_profile(self, profile: DisplayProfile) -> None:
        self.profile = profile
        for name in PROFILE_ORDER:
            self.remove_class(f"-profile-{name}")
        self.add_class(f"-profile-{profile.name}")
        self.notify(f"Display profile: {profile.name}", timeout=2)

    # ── navigation ───────────────────────────────────────────────────

    def action_goto(self, screen: str) -> None:
        # Screens beyond the dashboard arrive in M1+.
        if screen != "dashboard":
            self.notify(f"{screen} — coming soon", severity="warning", timeout=2)

    def action_help(self) -> None:
        self.notify("1 Dashboard · F2 display profile · q quit", timeout=4)
