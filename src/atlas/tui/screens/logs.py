"""Logs — read-only tail of any container's logs."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, ListItem, ListView, Static

from atlas.tui.widgets.stream_log import StreamLog

if TYPE_CHECKING:
    from atlas.app import AtlasApp

TAIL_LINES = 200


class LogsScreen(Screen):
    DEFAULT_CSS = """
    LogsScreen #picker { width: 34; border: round $primary 60%; }
    LogsScreen #log { border: round $primary 60%; }
    """

    BINDINGS: ClassVar = [
        ("escape", "app.pop_screen", "Back"),
        ("c", "copy_log", "Copy"),
    ]

    def action_copy_log(self) -> None:
        from atlas.tui.clipboard import copy_text

        copy_text(self, self.query_one("#log", StreamLog).text, "log tail")

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(id="picker")
            yield StreamLog(id="log")
        yield Footer()

    @property
    def atlas(self) -> AtlasApp:
        return self.app  # type: ignore[return-value]

    async def on_mount(self) -> None:
        stream = self.query_one("#log", StreamLog)
        stream.set_flush_period(0 if self.atlas.profile.name == "standard" else 2.0)
        rt = self.atlas.runtime
        picker = self.query_one("#picker", ListView)
        if rt is None:
            return
        for container in await rt.inventory.entities(kind="container"):
            picker.append(ListItem(StaticName(container["key"]), name=container["key"]))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        key = event.item.name
        if key:
            self.run_worker(self._tail(key), exclusive=True, group="logs")

    async def _tail(self, container_key: str) -> None:
        """container_key = 'container:<host>/<name>'"""
        rt = self.atlas.runtime
        if rt is None or rt.scheduler is None or self.atlas.config is None:
            return
        _, _, rest = container_key.partition(":")
        host_name, _, container = rest.partition("/")
        host = next((h for h in self.atlas.config.hosts if h.name == host_name), None)
        if host is None:
            return
        transport = rt.scheduler.transport_for(host)
        stream = self.query_one("#log", StreamLog)
        stream.clear()
        stream.push(f"— docker logs --tail {TAIL_LINES} {container} —")
        result = await transport.run(
            ["sh", "-c", f"docker logs --tail {TAIL_LINES} {container} 2>&1"], timeout=30
        )
        for line in result.stdout.splitlines():
            stream.push(line)
        stream.finish()


class StaticName(Static):
    def __init__(self, key: str) -> None:
        super().__init__(key.removeprefix("container:"))
