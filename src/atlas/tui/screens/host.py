"""Host detail — pick a host on the left, see its containers on the right.

Tables update cell-by-cell on the profile clock; rows are only added/removed
when inventory actually changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import DataTable, Footer, ListItem, ListView, Static

from atlas.model import DisplayProfile

if TYPE_CHECKING:
    from atlas.app import AtlasApp

COLUMNS = ("container", "state", "health", "restarts", "cpu %", "mem")


class HostScreen(Screen):
    DEFAULT_CSS = """
    HostScreen #picker { width: 26; border: round $primary 60%; }
    HostScreen #detail { border: round $primary 60%; }
    HostScreen #summary { height: 3; padding: 0 1; }
    """

    BINDINGS: ClassVar = [
        ("escape", "app.pop_screen", "Back"),
        ("E", "explain", "Explain (AI)"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._timer: Timer | None = None
        self._rows: set[str] = set()

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(id="picker")
            with Horizontal(id="detail"):
                yield Static("", id="summary")
                yield DataTable(id="containers")
        yield Footer()

    @property
    def atlas(self) -> AtlasApp:
        return self.app  # type: ignore[return-value]

    async def on_mount(self) -> None:
        table = self.query_one("#containers", DataTable)
        table.cursor_type = "row"
        for column in COLUMNS:
            table.add_column(column, key=column)
        picker = self.query_one("#picker", ListView)
        if self.atlas.config:
            for host in self.atlas.config.hosts:
                picker.append(ListItem(Static(host.name), name=host.name))
        self._start_clock(self.atlas.profile)

    def on_profile_changed(self, profile: DisplayProfile) -> None:
        self._start_clock(profile)

    def _start_clock(self, profile: DisplayProfile) -> None:
        if self._timer is not None:
            self._timer.stop()
        self.refresh_data()
        self._timer = self.set_interval(profile.refresh_period, self.refresh_data)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        self.run_worker(self._refresh(), exclusive=True, group="host-refresh")

    def _selected_host(self) -> str | None:
        picker = self.query_one("#picker", ListView)
        item = picker.highlighted_child
        return item.name if item else None

    async def _refresh(self) -> None:
        atlas = self.atlas
        host_name = self._selected_host()
        if atlas.runtime is None or host_name is None:
            return
        rt = atlas.runtime
        host_key = f"host:{host_name}"

        snap = await rt.metrics.latest_snapshot(host_key)
        summary = (
            f"{host_name}   "
            f"load {snap.get('load.1m', 0):.2f}   "
            f"mem {snap.get('mem.used_pct', 0):.0f}%   "
            f"disk {snap.get('disk.used_pct', 0):.0f}%"
        )
        widget = self.query_one("#summary", Static)
        if getattr(widget, "_atlas_last", None) != summary:
            widget._atlas_last = summary  # type: ignore[attr-defined]
            widget.update(summary)

        containers = await rt.inventory.entities(kind="container", parent=host_key)
        table = self.query_one("#containers", DataTable)
        wanted = {c["key"] for c in containers}
        for stale in self._rows - wanted:
            table.remove_row(stale)
        self._rows &= wanted

        for c in containers:
            key, attrs = c["key"], c["attrs"]
            name = key.split("/", 1)[-1]
            csnap = await rt.metrics.latest_snapshot(key)
            cells = {
                "container": name,
                "state": attrs.get("state", "?"),
                "health": attrs.get("health") or "—",
                "restarts": f"{csnap.get('container.restarts', 0):.0f}",
                "cpu %": _fmt_opt(csnap.get("container.cpu_pct"), "{:.1f}"),
                "mem": _fmt_opt(csnap.get("container.mem_pct"), "{:.0f}%"),
            }
            if key not in self._rows:
                table.add_row(*(cells[col] for col in COLUMNS), key=key)
                self._rows.add(key)
            else:
                for col in COLUMNS:
                    if table.get_cell(key, col) != cells[col]:
                        table.update_cell(key, col, cells[col])

    def action_explain(self) -> None:
        host_name = self._selected_host()
        if host_name is not None:
            self.run_worker(self._explain(f"host:{host_name}"), exclusive=True, group="explain")

    async def _explain(self, entity_key: str) -> None:
        from atlas.ai.client import AIDisabled, BudgetExhausted
        from atlas.tui.widgets.modal import TextModal

        rt = self.atlas.runtime
        if rt is None or rt.insights is None:
            self.notify("AI is not configured (set ANTHROPIC_API_KEY)", timeout=4)
            return
        self.notify("asking Claude…", timeout=3)
        try:
            text = await rt.insights.explain_entity(entity_key)
        except (BudgetExhausted, AIDisabled) as e:
            self.notify(str(e), severity="warning", timeout=5)
            return
        self.app.push_screen(TextModal(f"{entity_key} — AI summary", text))


def _fmt_opt(value: float | None, spec: str) -> str:
    return spec.format(value) if value is not None else "—"
