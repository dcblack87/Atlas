"""Security — fleet posture at a glance."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Static

from atlas.model import DisplayProfile
from atlas.tui.widgets.tiles import GLYPH_OK, GLYPH_WARN

if TYPE_CHECKING:
    from atlas.app import AtlasApp


class SecurityScreen(Screen):
    DEFAULT_CSS = """
    SecurityScreen #body { padding: 0 1; }
    """

    BINDINGS: ClassVar = [("escape", "app.pop_screen", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("collecting…", id="body")
        yield Footer()

    @property
    def atlas(self) -> AtlasApp:
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        self._start_clock(self.atlas.profile)

    def on_profile_changed(self, profile: DisplayProfile) -> None:
        self._start_clock(profile)

    def _start_clock(self, profile: DisplayProfile) -> None:
        if self._timer is not None:
            self._timer.stop()
        self.refresh_data()
        self._timer = self.set_interval(max(profile.refresh_period, 30), self.refresh_data)

    def refresh_data(self) -> None:
        self.run_worker(self._refresh(), exclusive=True, group="security-refresh")

    async def _refresh(self) -> None:
        rt = self.atlas.runtime
        if rt is None:
            return
        lines: list[str] = []
        for host in await rt.inventory.entities(kind="host"):
            name = host["key"].removeprefix("host:")
            facts = await rt.inventory.facts_for(host["key"])
            snap = await rt.metrics.latest_snapshot(host["key"])
            failed = snap.get("security.failed_auth_1h")
            updates = facts.get("security.pending_updates")
            security_updates = facts.get("security.pending_security_updates")
            reboot = facts.get("security.reboot_required")
            ports = facts.get("security.public_ports")
            has_security_updates = isinstance(security_updates, int) and security_updates > 0
            glyph = GLYPH_WARN if (reboot or has_security_updates) else GLYPH_OK
            lines.append(f"{glyph} {name}")
            lines.append(f"    failed ssh (1h)     {_n(failed)}")
            lines.append(
                f"    pending updates     {_n(updates)}"
                + (f" ({security_updates} security)" if security_updates else "")
            )
            lines.append(f"    reboot required     {'yes ▲' if reboot else 'no'}")
            if isinstance(ports, list):
                lines.append(f"    public ports        {' '.join(str(p) for p in ports)}")
            lines.append("")

        rows = await rt.db.fetch_all(
            "SELECT entity_key, value FROM facts WHERE name = 'cert.days_remaining'"
            " ORDER BY CAST(value AS REAL)"
        )
        if rows:
            lines.append("CERTIFICATES")
            for row in rows:
                days = float(json.loads(row["value"]))
                glyph = GLYPH_WARN if days < 21 else GLYPH_OK
                lines.append(f"  {glyph} {row['entity_key'].removeprefix('cert:'):<40} {days:.0f}d")

        text = "\n".join(lines) if lines else "no security data yet"
        widget = self.query_one("#body", Static)
        if getattr(widget, "_atlas_last", None) != text:
            widget._atlas_last = text  # type: ignore[attr-defined]
            widget.update(text)


def _n(value: object) -> str:
    return f"{value:.0f}" if isinstance(value, int | float) else "—"
