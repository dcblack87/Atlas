"""Dashboard — the default screen: fleet at a glance.

Rendering is clock-driven, not event-driven: the screen polls the store on
the display profile's cadence (1s LCD, 10s e-ink, 30s glance) and updates a
widget only when its *rendered* string actually changed. Metric jitter below
display precision never causes a repaint — that's what keeps e-ink still.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Grid
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Static

from atlas.model import DisplayProfile
from atlas.tui.widgets.tiles import GLYPH_CRIT, GLYPH_OK, GLYPH_WARN, StatTile

if TYPE_CHECKING:
    from atlas.app import AtlasApp


class DashboardScreen(Screen):
    DEFAULT_CSS = """
    DashboardScreen #banner {
        height: 3;
        content-align: center middle;
        text-style: bold;
    }
    DashboardScreen Grid {
        grid-size: 4;
        grid-gutter: 1;
        height: 9;
        margin: 0 1;
    }
    DashboardScreen #hosts {
        margin: 1 1;
    }
    """

    _timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static("ATLAS", id="banner")
        with Grid():
            yield StatTile("Fleet Health", id="health")
            yield StatTile("Hosts", id="hosts-count")
            yield StatTile("Apps", id="apps-count")
            yield StatTile("Open Incidents", id="incidents")
        yield Static("starting collectors…", id="hosts")
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
        self._timer = self.set_interval(profile.refresh_period, self.refresh_data)

    def refresh_data(self) -> None:
        self.run_worker(self._refresh(), exclusive=True, group="dashboard-refresh")

    async def _refresh(self) -> None:
        atlas = self.atlas
        if atlas.runtime is None:
            return
        rt = atlas.runtime

        hosts = await rt.inventory.entities(kind="host")
        apps = await rt.inventory.entities(kind="app")
        sites = await rt.inventory.entities(kind="site")

        self._set_tile("hosts-count", str(len(hosts)))
        apps_label = str(len(apps)) if not sites else f"{len(apps)} (+{len(sites)} sites)"
        self._set_tile("apps-count", apps_label)
        self._set_tile("health", "—")  # scored in M2
        self._set_tile("incidents", "0")

        lines = []
        for host in hosts:
            snap = await rt.metrics.latest_snapshot(host["key"])
            name = host["key"].removeprefix("host:")
            up = snap.get("host.up", 1.0) > 0
            glyph = GLYPH_OK if up else GLYPH_CRIT
            if not up:
                lines.append(f"{glyph} {name:<18} UNREACHABLE")
                continue
            load = snap.get("load.1m")
            mem = snap.get("mem.used_pct")
            disk = snap.get("disk.used_pct")
            running = snap.get("docker.running")
            if disk is not None and disk >= 90:
                glyph = GLYPH_CRIT
            elif (disk is not None and disk >= 80) or (mem is not None and mem >= 90):
                glyph = GLYPH_WARN
            lines.append(
                f"{glyph} {name:<18}"
                f" load {_fmt(load, '{:>5.2f}')}"
                f"  mem {_fmt(mem, '{:>3.0f}%')}"
                f"  disk {_fmt(disk, '{:>3.0f}%')}"
                f"  containers {_fmt(running, '{:>2.0f}')}"
            )
        text = "\n".join(lines) if lines else "no hosts discovered yet"
        hosts_widget = self.query_one("#hosts", Static)
        if getattr(hosts_widget, "_atlas_last", None) != text:
            hosts_widget._atlas_last = text  # type: ignore[attr-defined]
            hosts_widget.update(text)

    def _set_tile(self, tile_id: str, value: str) -> None:
        self.query_one(f"#{tile_id}", StatTile).value = value


def _fmt(value: float | None, spec: str) -> str:
    return spec.format(value) if value is not None else "  — "
