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
from atlas.tui.screens.apps import AppsScreen
from atlas.tui.screens.chat import ChatScreen
from atlas.tui.screens.cost import CostScreen
from atlas.tui.screens.dashboard import DashboardScreen
from atlas.tui.screens.deploy import DeployScreen
from atlas.tui.screens.host import HostScreen
from atlas.tui.screens.incidents import IncidentsScreen
from atlas.tui.screens.logs import LogsScreen
from atlas.tui.screens.reports import ReportsScreen
from atlas.tui.screens.security import SecurityScreen

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
        Binding("3", "goto('apps')", "Apps"),
        Binding("4", "goto('deploy')", "Deploy"),
        Binding("5", "goto('chat')", "Chat"),
        Binding("6", "goto('cost')", "Cost"),
        Binding("7", "goto('security')", "Security"),
        Binding("8", "goto('reports')", "Reports"),
        Binding("h", "goto('hosts')", "Hosts"),
        Binding("l", "goto('logs')", "Logs"),
        Binding("b", "bundle", "Bundle"),
        Binding("f2", "cycle_profile", "Display"),
        # e-ink tablets and phone keyboards rarely have function keys
        Binding("p", "cycle_profile", "Display", show=False),
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
                # pop back to the dashboard — never past it (the bottom of the
                # Textual stack is a blank default screen)
                while not isinstance(self.screen, DashboardScreen) and len(self.screen_stack) > 1:
                    self.pop_screen()
                if not isinstance(self.screen, DashboardScreen):
                    self.push_screen(DashboardScreen())
            case "hosts":
                if not isinstance(self.screen, HostScreen):
                    self.push_screen(HostScreen())
            case "incidents":
                if not isinstance(self.screen, IncidentsScreen):
                    self.push_screen(IncidentsScreen())
            case "apps":
                if not isinstance(self.screen, AppsScreen):
                    self.push_screen(AppsScreen())
            case "deploy":
                if self.demo:
                    self.notify("deploys are disabled in demo mode", timeout=3)
                elif not isinstance(self.screen, DeployScreen):
                    self.push_screen(DeployScreen())
            case "logs":
                if not isinstance(self.screen, LogsScreen):
                    self.push_screen(LogsScreen())
            case "chat":
                if not isinstance(self.screen, ChatScreen):
                    self.push_screen(ChatScreen())
            case "cost":
                if not isinstance(self.screen, CostScreen):
                    self.push_screen(CostScreen())
            case "security":
                if not isinstance(self.screen, SecurityScreen):
                    self.push_screen(SecurityScreen())
            case "reports":
                if not isinstance(self.screen, ReportsScreen):
                    self.push_screen(ReportsScreen())
            case _:
                self.notify(f"{target} — coming soon", severity="warning", timeout=2)

    def action_bundle(self) -> None:
        self.run_worker(self._write_bundle(), exclusive=True, group="bundle")

    async def _write_bundle(self) -> None:
        if self.runtime is None:
            return
        from atlas.ai.bundles import write_bundle
        from atlas.ai.context import ContextBuilder

        context = self.runtime.context or ContextBuilder(self.runtime.db)
        path = await write_bundle(context)
        self.notify(f"context bundle written: {path}", timeout=6)

    def action_help(self) -> None:
        self.notify(
            "1 Dashboard · 2 Incidents · 3 Apps · 4 Deploy · 5 Chat · 6 Cost · "
            "7 Security · 8 Reports · h Hosts · l Logs · b Bundle · c Copy · "
            "F2/p display profile · q quit",
            timeout=4,
        )
