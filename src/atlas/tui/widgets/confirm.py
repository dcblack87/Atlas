"""TypedConfirm — the gate in front of every mutation.

The user must type the target's name exactly. No default button, no Enter
shortcut until the phrase matches, Esc always aborts. The typed phrase is
returned so the orchestrator can store it in the audit row.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class TypedConfirm(ModalScreen[str | None]):
    """Dismisses with the typed phrase on confirm, None on abort."""

    DEFAULT_CSS = """
    TypedConfirm {
        align: center middle;
    }
    TypedConfirm > Vertical {
        width: 72;
        height: auto;
        max-height: 80%;
        border: heavy $warning;
        padding: 1 2;
        background: $surface;
    }
    TypedConfirm #detail { margin-bottom: 1; }
    TypedConfirm #hint { color: $text-muted; }
    TypedConfirm Button { margin-top: 1; }
    """

    BINDINGS: ClassVar = [("escape", "abort", "Abort")]

    def __init__(self, title: str, detail: str, required_phrase: str) -> None:
        super().__init__()
        self._title = title
        self._detail = detail
        self._required = required_phrase

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"[b]{self._title}[/b]")
            yield Static(self._detail, id="detail")
            yield Static(f'Type "{self._required}" to confirm — Esc aborts', id="hint")
            yield Input(placeholder=self._required, id="phrase")
            yield Button("Confirm", id="confirm", disabled=True, variant="warning")

    def on_mount(self) -> None:
        phrase = self.query_one("#phrase", Input)
        phrase.cursor_blink = False  # e-ink
        phrase.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#confirm", Button).disabled = event.value != self._required

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value == self._required:
            self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self.dismiss(self.query_one("#phrase", Input).value)

    def action_abort(self) -> None:
        self.dismiss(None)
