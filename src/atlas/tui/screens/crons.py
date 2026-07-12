"""Crons — every scheduled job across the fleet, with staleness and failures.

One table: crontab entries, /etc/cron.d jobs, and celery-beat tasks, judged
by the cron collector. Failed and late jobs sort to the top so the answer to
"is anything silently broken?" is always the first row.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Static

from atlas.model import DisplayProfile
from atlas.tui.widgets.tiles import GLYPH_CRIT, GLYPH_OK, GLYPH_WARN

if TYPE_CHECKING:
    from atlas.app import AtlasApp

COLUMNS = ("", "host", "job", "schedule", "last run", "source")

# sort weight per status: broken things first
_WEIGHT = {"failed": 0, "late": 1, "unknown": 2, "ok": 3}


class CronsScreen(Screen):
    DEFAULT_CSS = """
    CronsScreen #crons-title { height: 1; padding: 0 1; text-style: bold; }
    """

    BINDINGS: ClassVar = [
        ("escape", "app.pop_screen", "Back"),
        ("c", "copy_table", "Copy"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._timer: Timer | None = None
        self._rows: set[str] = set()
        self._last_text = ""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Cron jobs", id="crons-title")
            yield DataTable(id="crons")
        yield Footer()

    @property
    def atlas(self) -> AtlasApp:
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        table = self.query_one("#crons", DataTable)
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
        self._timer = self.set_interval(max(profile.refresh_period, 5), self.refresh_data)

    def refresh_data(self) -> None:
        self.run_worker(self._refresh(), exclusive=True, group="crons-refresh")

    async def _refresh(self) -> None:
        rt = self.atlas.runtime
        if rt is None:
            return
        jobs = await rt.inventory.entities(kind="cron")

        rows: list[tuple[str, tuple[str, ...], int]] = []
        copy_lines: list[str] = []
        for job in jobs:
            key = job["key"]  # cron:<host>/<slug>
            facts = await rt.inventory.facts_for(key)
            host, _, _slug = key.removeprefix("cron:").partition("/")
            name = str(job["attrs"].get("name", _slug))
            schedule = str(facts.get("cron.schedule", job["attrs"].get("schedule", "?")))
            status = _status(facts)
            glyph = {"failed": GLYPH_CRIT, "late": GLYPH_WARN, "ok": GLYPH_OK}.get(status, "·")
            last_run = facts.get("cron.last_run_ts")
            when = _ago(int(last_run)) if isinstance(last_run, int | float) else "—"
            source = str(facts.get("cron.source", "?"))
            cells = (glyph, host, name[:40], schedule, when, source)
            rows.append((key, cells, _WEIGHT.get(status, 2)))
            copy_lines.append(f"{status:<8} {host:<16} {name:<40} {schedule:<14} {when}")

        rows.sort(key=lambda r: (r[2], r[1][1], r[1][2]))
        self._last_text = "\n".join(copy_lines)

        table = self.query_one("#crons", DataTable)
        wanted = {key for key, _, _ in rows}
        for stale in self._rows - wanted:
            table.remove_row(stale)
        self._rows &= wanted
        for key, cells, _weight in rows:
            if key not in self._rows:
                table.add_row(*cells, key=key)
                self._rows.add(key)
            else:
                for column, value in zip(COLUMNS, cells, strict=True):
                    column_key = column or "glyph"
                    if table.get_cell(key, column_key) != value:
                        table.update_cell(key, column_key, value)

        title = self.query_one("#crons-title", Static)
        failed = sum(1 for _, _, w in rows if w == 0)
        late = sum(1 for _, _, w in rows if w == 1)
        summary = f"Cron jobs ({len(rows)})"
        if failed:
            summary += f" — {failed} failing"
        if late:
            summary += f" — {late} late"
        if getattr(title, "_atlas_last", None) != summary:
            title._atlas_last = summary  # type: ignore[attr-defined]
            title.update(summary)

    def action_copy_table(self) -> None:
        from atlas.tui.clipboard import copy_text

        copy_text(self, self._last_text, "cron jobs")


def _status(facts: dict) -> str:
    if facts.get("cron.last_status") == "failed":
        return "failed"
    ratio = facts.get("cron.overdue_ratio")
    if isinstance(ratio, int | float):
        if ratio >= 2:
            return "late"
        return "ok"
    if facts.get("cron.last_status") == "ok":
        return "ok"
    return "unknown"


def _ago(ts: int) -> str:
    delta = int(datetime.now().timestamp()) - ts
    if delta < 0:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"
