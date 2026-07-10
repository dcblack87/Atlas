"""Dashboard — the default screen: fleet at a glance."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Grid
from textual.screen import Screen
from textual.widgets import Footer, Static

from atlas.config import Config
from atlas.tui.widgets.tiles import GLYPH_OK, StatTile


class DashboardScreen(Screen):
    """Fleet overview: health score, host tiles, incident strip.

    M0 renders static placeholders from config; M1 wires it to live data.
    """

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

    def __init__(self, config: Config | None) -> None:
        super().__init__()
        self._config = config

    def compose(self) -> ComposeResult:
        yield Static("ATLAS", id="banner")
        with Grid():
            yield StatTile("Fleet Health", id="health")
            yield StatTile("Hosts", id="hosts-count")
            yield StatTile("Apps", id="apps-count")
            yield StatTile("Open Incidents", id="incidents")
        yield Static("", id="hosts")
        yield Footer()

    def on_mount(self) -> None:
        if self._config is None:
            self.query_one("#hosts", Static).update(
                "No config loaded — run `atlas check` after creating atlas.toml."
            )
            return
        hosts = self._config.hosts
        apps = self._config.apps
        self.query_one("#hosts-count", StatTile).value = str(len(hosts))
        self.query_one("#apps-count", StatTile).value = str(len(apps))
        self.query_one("#health", StatTile).value = "—"
        self.query_one("#incidents", StatTile).value = "0"
        lines = [f"{GLYPH_OK} {h.name:<20} {h.address:<16} {', '.join(h.apps)}" for h in hosts]
        self.query_one("#hosts", Static).update("\n".join(lines))
