"""Small display widgets shared across screens.

The cardinal rule for e-ink friendliness: a widget only repaints when its
*rendered* value changes. Callers round/format values BEFORE assignment, and
reactive attributes skip repaints for equal values, so metric jitter below
display precision costs nothing.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static

# Severity glyphs — colour-independent so they survive greyscale.
GLYPH_OK = "●"
GLYPH_WARN = "▲"
GLYPH_CRIT = "✖"


class StatTile(Vertical):
    """A titled value tile: fixed size, no reflow on value change."""

    value: reactive[str] = reactive("—")
    status: reactive[str] = reactive("ok")  # ok | warn | crit

    def __init__(self, title: str, *, id: str | None = None) -> None:
        super().__init__(id=id, classes="tile")
        self._title = title

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="tile-title")
        yield Static(self.value, classes="tile-value", id="value")

    def watch_value(self, value: str) -> None:
        if self.is_mounted:
            self.query_one("#value", Static).update(value)

    def watch_status(self, status: str) -> None:
        if not self.is_mounted:
            return
        value = self.query_one("#value", Static)
        value.remove_class("status-ok", "status-warn", "status-crit")
        value.add_class(f"status-{status}")
