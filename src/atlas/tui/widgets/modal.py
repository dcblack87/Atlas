"""TextModal — a scrollable text overlay (AI explanations, long details)."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static


class TextModal(ModalScreen[None]):
    DEFAULT_CSS = """
    TextModal { align: center middle; }
    TextModal > VerticalScroll {
        width: 90; height: auto; max-height: 85%;
        border: heavy $primary; padding: 1 2; background: $surface;
    }
    """

    BINDINGS: ClassVar = [("escape", "dismiss", "Close"), ("q", "dismiss", "Close")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(f"[b]{self._title}[/b]\n")
            yield Static(self._body)

    def action_dismiss(self) -> None:
        self.dismiss(None)
