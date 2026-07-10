"""Dashboard — the default screen: fleet at a glance.

Rendering is clock-driven, not event-driven: the screen polls the store on
the display profile's cadence (1s LCD, 10s e-ink, 30s glance) and updates a
widget only when its *rendered* string actually changed. Metric jitter below
display precision never causes a repaint — that's what keeps e-ink still.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.containers import Grid
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Static

from atlas.engine.health import health_scores
from atlas.model import DisplayProfile
from atlas.tui.widgets.sparkline import bucketize, sparkline
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
    DashboardScreen #hosts, DashboardScreen #apps {
        margin: 1 1;
        height: auto;
    }
    """

    BINDINGS: ClassVar = [("c", "copy_summary", "Copy")]

    _timer: Timer | None = None

    def action_copy_summary(self) -> None:
        from datetime import datetime

        from atlas.tui.clipboard import copy_text

        tiles = {
            tile_id: self.query_one(f"#{tile_id}", StatTile).value
            for tile_id in ("health", "hosts-count", "apps-count", "incidents")
        }
        parts = [
            f"ATLAS fleet summary — {datetime.now():%Y-%m-%d %H:%M}",
            f"health {tiles['health']} | hosts {tiles['hosts-count']} | "
            f"apps {tiles['apps-count']} | open incidents {tiles['incidents']}",
            getattr(self.query_one("#hosts", Static), "_atlas_last", "") or "",
            getattr(self.query_one("#apps", Static), "_atlas_last", "") or "",
        ]
        copy_text(self, "\n\n".join(p for p in parts if p), "fleet summary")

    def compose(self) -> ComposeResult:
        yield Static("ATLAS", id="banner")
        with Grid():
            yield StatTile("Fleet Health", id="health")
            yield StatTile("Hosts", id="hosts-count")
            yield StatTile("Apps", id="apps-count")
            yield StatTile("Open Incidents", id="incidents")
        yield Static("starting collectors…", id="hosts")
        yield Static("", id="apps")
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

        scores = await health_scores(rt.incidents.store)
        fleet = scores["fleet"]
        health_tile = self.query_one("#health", StatTile)
        health_tile.value = f"{fleet}/100"
        health_tile.status = "ok" if fleet >= 90 else "warn" if fleet >= 60 else "crit"

        open_incidents = await rt.incidents.store.open_incidents()
        crit = sum(1 for i in open_incidents if i["severity"] == "critical")
        incidents_tile = self.query_one("#incidents", StatTile)
        incidents_tile.value = "0" if not open_incidents else f"{len(open_incidents)} ({crit} crit)"
        incidents_tile.status = "crit" if crit else "warn" if open_incidents else "ok"

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
            spark = ""
            if self.atlas.profile.show_sparklines:
                points = await rt.metrics.recent(host["key"], "load.1m", since_s=6 * 3600)
                buckets = bucketize(
                    [(p.ts, p.value) for p in points],
                    self.atlas.profile.sparkline_bucket,
                    width=16,
                )
                spark = "  " + sparkline(buckets, width=16)
            lines.append(
                f"{glyph} {name:<18}"
                f" load {_fmt(load, '{:>5.2f}')}"
                f"  mem {_fmt(mem, '{:>3.0f}%')}"
                f"  disk {_fmt(disk, '{:>3.0f}%')}"
                f"  containers {_fmt(running, '{:>2.0f}')}"
                f"{spark}"
            )
        text = "\n".join(lines) if lines else "no hosts discovered yet"
        self._update_static("hosts", text)
        await self._refresh_apps(rt, apps)

    async def _refresh_apps(self, rt, apps: list[dict]) -> None:
        """One row per app; multi-site apps expand to one row per site."""
        lines = ["APPS"]
        for app in apps:
            name = app["key"].removeprefix("app:")
            host = (app["parent"] or "").removeprefix("host:")
            sites = await rt.inventory.entities(kind="site", parent=app["key"])
            if sites:
                lines.append(f"{GLYPH_OK} {name:<22} {host:<16} {len(sites)} sites")
                for site in sites:
                    site_name = site["key"].split("/")[-1]
                    snap = await rt.metrics.latest_snapshot(site["key"])
                    glyph, latency = _liveness(snap)
                    lines.append(f"    {glyph} {site_name:<20} {latency}")
            else:
                snap = await rt.metrics.latest_snapshot(app["key"])
                glyph, latency = _liveness(snap)
                lines.append(f"{glyph} {name:<22} {host:<16} {latency}")
        self._update_static("apps", "\n".join(lines) if len(lines) > 1 else "")

    def _update_static(self, widget_id: str, text: str) -> None:
        widget = self.query_one(f"#{widget_id}", Static)
        if getattr(widget, "_atlas_last", None) != text:
            widget._atlas_last = text  # type: ignore[attr-defined]
            widget.update(text)

    def _set_tile(self, tile_id: str, value: str) -> None:
        self.query_one(f"#{tile_id}", StatTile).value = value


def _liveness(snap: dict[str, float]) -> tuple[str, str]:
    """(glyph, latency-label) from an entity's http samples."""
    up = snap.get("http.up")
    if up is None:
        return GLYPH_OK, "—"
    if up < 1:
        return GLYPH_CRIT, "DOWN"
    ms = snap.get("http.response_ms")
    return GLYPH_OK, f"{ms:>4.0f}ms" if ms is not None else "up"


def _fmt(value: float | None, spec: str) -> str:
    return spec.format(value) if value is not None else "  — "
