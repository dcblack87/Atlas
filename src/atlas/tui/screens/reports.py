"""Reports — morning and weekly briefs, formatted for reading."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, ListItem, ListView, Static

if TYPE_CHECKING:
    from atlas.app import AtlasApp


class ReportsScreen(Screen):
    DEFAULT_CSS = """
    ReportsScreen #picker { width: 30; border: round $primary 60%; }
    ReportsScreen #body-scroll { border: round $primary 60%; }
    ReportsScreen #body { padding: 0 1; }
    """

    BINDINGS: ClassVar = [
        ("escape", "app.pop_screen", "Back"),
        ("g", "generate", "Generate now"),
        ("c", "copy_brief", "Copy"),
    ]

    def action_copy_brief(self) -> None:
        from atlas.tui.clipboard import copy_text

        body = self.query_one("#body", Static).renderable
        copy_text(self, str(body), "brief")

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(id="picker")
            with VerticalScroll(id="body-scroll"):
                yield Static("select a brief — or press g to generate one now", id="body")
        yield Footer()

    @property
    def atlas(self) -> AtlasApp:
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        self.run_worker(self._load(), exclusive=True, group="reports")

    async def _load(self) -> None:
        rt = self.atlas.runtime
        if rt is None:
            return
        picker = self.query_one("#picker", ListView)
        await picker.clear()
        rows = await rt.db.fetch_all(
            "SELECT id, ts, kind FROM ai_analyses "
            "WHERE kind LIKE '%brief%' ORDER BY ts DESC LIMIT 30"
        )
        for row in rows:
            label = (
                f"{datetime.fromtimestamp(row['ts']):%m-%d %H:%M} "
                f"{'weekly' if 'weekly' in row['kind'] else 'daily'}"
            )
            picker.append(ListItem(Static(label), name=str(row["id"])))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.name:
            self.run_worker(self._show(int(event.item.name)), exclusive=True, group="reports")

    async def _show(self, analysis_id: int) -> None:
        rt = self.atlas.runtime
        if rt is None:
            return
        body = await rt.db.fetch_value(
            "SELECT response FROM ai_analyses WHERE id = ?", (analysis_id,)
        )
        self.query_one("#body", Static).update(body or "empty")

    def action_generate(self) -> None:
        self.run_worker(self._generate(), exclusive=True, group="reports")

    async def _generate(self) -> None:
        from atlas.ai.context import ContextBuilder
        from atlas.reports.briefs import generate_brief

        rt = self.atlas.runtime
        if rt is None:
            return
        self.notify("generating brief…", timeout=3)
        context = rt.context or ContextBuilder(rt.db)
        body = await generate_brief(rt.db, rt.ai, context)
        self.query_one("#body", Static).update(body)
        await self._load()
