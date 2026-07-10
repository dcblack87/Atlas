"""Deploy — preflight, typed confirmation, live stream, verification.

The screen only orchestrates UI; every command goes through the
DeployOrchestrator's gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, ListItem, ListView, Static

from atlas.deploy.orchestrator import DeployError, Preflight
from atlas.tui.widgets.confirm import TypedConfirm
from atlas.tui.widgets.stream_log import StreamLog

if TYPE_CHECKING:
    from atlas.app import AtlasApp


class DeployScreen(Screen):
    DEFAULT_CSS = """
    DeployScreen #picker { width: 28; border: round $primary 60%; }
    DeployScreen #right { border: round $primary 60%; }
    DeployScreen #preflight { height: auto; max-height: 12; padding: 0 1; }
    DeployScreen #stream { min-height: 10; }
    """

    BINDINGS: ClassVar = [
        ("escape", "app.pop_screen", "Back"),
        ("d", "deploy", "Deploy"),
        ("r", "rollback", "Rollback"),
        ("c", "copy_output", "Copy"),
    ]

    def action_copy_output(self) -> None:
        from atlas.tui.clipboard import copy_text

        copy_text(self, self.query_one("#stream", StreamLog).text, "deploy output")

    def __init__(self) -> None:
        super().__init__()
        self._preflight: Preflight | None = None
        self._deploying = False

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(id="picker")
            with Vertical(id="right"):
                yield Static("select an app to run preflight", id="preflight")
                yield StreamLog(id="stream")
        yield Footer()

    @property
    def atlas(self) -> AtlasApp:
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        picker = self.query_one("#picker", ListView)
        if self.atlas.config:
            for name in self.atlas.config.apps:
                picker.append(ListItem(Static(name), name=name))
        stream = self.query_one("#stream", StreamLog)
        profile = self.atlas.profile
        stream.set_flush_period(0 if profile.name == "standard" else 2.0)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if not self._deploying:
            self.run_worker(self._run_preflight(), exclusive=True, group="preflight")

    def _selected_app(self) -> str | None:
        item = self.query_one("#picker", ListView).highlighted_child
        return item.name if item else None

    async def _run_preflight(self) -> None:
        rt = self.atlas.runtime
        app_name = self._selected_app()
        if rt is None or rt.deployer is None or app_name is None:
            return
        widget = self.query_one("#preflight", Static)
        widget.update(f"preflight for {app_name}…")
        try:
            pf = await rt.deployer.preflight(app_name)
        except (DeployError, Exception) as e:
            widget.update(f"preflight failed: {e}")
            self._preflight = None
            return
        self._preflight = pf
        drift = {
            True: "✓ deployed sha matches origin",
            False: "▲ origin has newer commits",
            None: "sha comparison unavailable",
        }[pf.up_to_date]
        lines = [
            f"app       {pf.app}   (host {pf.host}, path {pf.path})",
            f"command   {pf.command}",
            f"deployed  {pf.deployed_sha or 'unknown'}",
            f"origin    {pf.remote_sha or 'unknown'}",
            drift,
        ]
        if pf.open_incidents:
            lines.append(f"▲ open incidents: {'; '.join(pf.open_incidents[:3])}")
        lines.append("press d to deploy")
        widget.update("\n".join(lines))

    # ── actions ──────────────────────────────────────────────────────

    def action_deploy(self) -> None:
        pf = self._preflight
        if pf is None or self._deploying:
            return
        detail = (
            f"host {pf.host}\npath {pf.path}\ncommand {pf.command}\n"
            f"sha {_short(pf.deployed_sha)} → {_short(pf.remote_sha)}"
        )
        if pf.open_incidents:
            detail += f"\n▲ {len(pf.open_incidents)} open incident(s) on this app"

        def on_confirm(phrase: str | None) -> None:
            if phrase is not None:
                self.run_worker(self._deploy(pf.app, phrase), exclusive=True, group="deploy")

        self.app.push_screen(
            TypedConfirm(f"Deploy {pf.app}", detail, required_phrase=pf.app), on_confirm
        )

    def action_rollback(self) -> None:
        pf = self._preflight
        rt = self.atlas.runtime
        if pf is None or self._deploying or rt is None:
            return
        self.run_worker(self._offer_rollback(pf), exclusive=True, group="deploy")

    async def _offer_rollback(self, pf: Preflight) -> None:
        rt = self.atlas.runtime
        assert rt is not None and rt.deployer is not None
        last = await rt.deployer.audit.last_for_app(pf.app)
        sha = last["git_sha_before"] if last else None
        if not sha:
            self.notify("no previous deploy recorded — nothing to roll back to", timeout=4)
            return
        detail = (
            f"Redeploy PREVIOUS commit {sha[:7]} on {pf.host}.\n"
            f"⚠ database migrations are NOT reversed.\n"
            f"command: git checkout {sha[:7]} && {pf.command}"
        )

        def on_confirm(phrase: str | None) -> None:
            if phrase is not None:
                self.run_worker(
                    self._deploy(pf.app, phrase, checkout_sha=sha),
                    exclusive=True,
                    group="deploy",
                )

        self.app.push_screen(
            TypedConfirm(f"Rollback {pf.app}", detail, required_phrase=pf.app), on_confirm
        )

    async def _deploy(self, app_name: str, phrase: str, checkout_sha: str | None = None) -> None:
        rt = self.atlas.runtime
        if rt is None or rt.deployer is None:
            return
        stream = self.query_one("#stream", StreamLog)
        self._deploying = True
        try:
            async for line in rt.deployer.deploy(app_name, phrase, checkout_sha=checkout_sha):
                stream.push(line)
        except DeployError as e:
            stream.push(f"✖ {e}")
        finally:
            stream.finish()
            self._deploying = False
            await self._run_preflight()


def _short(sha: str | None) -> str:
    return sha[:7] if sha else "unknown"
