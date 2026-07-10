"""Incidents — open incidents up top, the fleet timeline below."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Static

from atlas.model import DisplayProfile
from atlas.tui.widgets.tiles import GLYPH_CRIT, GLYPH_WARN

if TYPE_CHECKING:
    from atlas.app import AtlasApp

COLUMNS = ("", "severity", "incident", "entity", "opened", "status")


class IncidentsScreen(Screen):
    DEFAULT_CSS = """
    IncidentsScreen #open-title, IncidentsScreen #timeline-title {
        height: 1; padding: 0 1; text-style: bold;
    }
    IncidentsScreen #open { height: 40%; }
    IncidentsScreen #timeline { padding: 0 1; }
    """

    BINDINGS: ClassVar = [
        ("escape", "app.pop_screen", "Back"),
        ("a", "acknowledge", "Ack"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._timer: Timer | None = None
        self._rows: set[str] = set()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Open incidents", id="open-title")
            yield DataTable(id="open")
            yield Static("Timeline (24h)", id="timeline-title")
            yield Static("", id="timeline")
        yield Footer()

    @property
    def atlas(self) -> AtlasApp:
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        table = self.query_one("#open", DataTable)
        table.cursor_type = "row"
        for column in COLUMNS:
            table.add_column(column or " ", key=column or "glyph")
        self._start_clock(self.atlas.profile)

    def on_profile_changed(self, profile: DisplayProfile) -> None:
        self._start_clock(profile)

    def _start_clock(self, profile: DisplayProfile) -> None:
        if self._timer is not None:
            self._timer.stop()
        self.refresh_data()
        self._timer = self.set_interval(profile.refresh_period, self.refresh_data)

    def refresh_data(self) -> None:
        self.run_worker(self._refresh(), exclusive=True, group="incidents-refresh")

    async def _refresh(self) -> None:
        rt = self.atlas.runtime
        if rt is None or rt.incidents is None:
            return
        open_incidents = await rt.incidents.store.open_incidents()

        table = self.query_one("#open", DataTable)
        wanted = {str(i["id"]) for i in open_incidents}
        for stale in self._rows - wanted:
            table.remove_row(stale)
        self._rows &= wanted
        for incident in open_incidents:
            key = str(incident["id"])
            glyph = GLYPH_CRIT if incident["severity"] == "critical" else GLYPH_WARN
            cells = (
                glyph,
                incident["severity"],
                incident["title"],
                incident["entity_key"],
                _ago(incident["opened_at"]),
                incident["status"],
            )
            if key not in self._rows:
                table.add_row(*cells, key=key)
                self._rows.add(key)
            else:
                for column, value in zip(COLUMNS, cells, strict=True):
                    column_key = column or "glyph"
                    if table.get_cell(key, column_key) != value:
                        table.update_cell(key, column_key, value)

        events = await rt.incidents.store.timeline(since_s=24 * 3600, limit=40)
        lines = [
            f"{datetime.fromtimestamp(e['ts']):%H:%M}  {e['kind']:<14} {e['body']}" for e in events
        ]
        text = "\n".join(lines) if lines else "quiet — nothing happened in 24h"
        widget = self.query_one("#timeline", Static)
        if getattr(widget, "_atlas_last", None) != text:
            widget._atlas_last = text  # type: ignore[attr-defined]
            widget.update(text)

    async def action_acknowledge(self) -> None:
        rt = self.atlas.runtime
        table = self.query_one("#open", DataTable)
        if rt is None or rt.incidents is None or table.cursor_row is None:
            return
        row_key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        if row_key:
            await rt.incidents.store.acknowledge(int(row_key))
            self.refresh_data()


def _ago(ts: int) -> str:
    delta = int(datetime.now().timestamp()) - ts
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"
