"""Chat — ask the fleet questions in plain English."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input

from atlas.ai.client import AIDisabled, BudgetExhausted
from atlas.tui.widgets.stream_log import StreamLog

if TYPE_CHECKING:
    from atlas.app import AtlasApp


class ChatScreen(Screen):
    DEFAULT_CSS = """
    ChatScreen #transcript { border: round $primary 60%; }
    ChatScreen Input { dock: bottom; }
    """

    # ctrl+y (not plain c) because the question Input owns printable keys
    BINDINGS: ClassVar = [
        ("escape", "app.pop_screen", "Back"),
        ("ctrl+y", "copy_answer", "Copy answer"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._last_answer = ""

    def action_copy_answer(self) -> None:
        from atlas.tui.clipboard import copy_text

        copy_text(self, self._last_answer, "last answer")

    def compose(self) -> ComposeResult:
        with Vertical():
            yield StreamLog(id="transcript")
        yield Input(placeholder="ask about your fleet — e.g. why is web-2 slow?", id="question")
        yield Footer()

    @property
    def atlas(self) -> AtlasApp:
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        question = self.query_one("#question", Input)
        question.cursor_blink = False
        question.focus()
        transcript = self.query_one("#transcript", StreamLog)
        # chat streams are bursty; buffer lightly even on LCD
        transcript.set_flush_period(0.5 if self.atlas.profile.name == "standard" else 3.0)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        question = event.value.strip()
        if question:
            event.input.value = ""
            self.run_worker(self._ask(question), exclusive=True, group="chat")

    async def _ask(self, question: str) -> None:
        rt = self.atlas.runtime
        transcript = self.query_one("#transcript", StreamLog)
        transcript.push(f"you › {question}")  # noqa: RUF001
        if rt is None or rt.chat is None:
            transcript.push("atlas › AI is not configured (set ANTHROPIC_API_KEY)")  # noqa: RUF001
            transcript.finish()
            return
        transcript.push("atlas ›")  # noqa: RUF001
        buffer = ""
        self._last_answer = ""
        try:
            async for delta in rt.chat.ask(question):
                buffer += delta
                self._last_answer += delta
                # flush completed lines; StreamLog handles e-ink coalescing
                while "\n" in buffer:
                    line, _, buffer = buffer.partition("\n")
                    transcript.push(line)
            if buffer:
                transcript.push(buffer)
        except (BudgetExhausted, AIDisabled) as e:
            transcript.push(f"⏸ {e}")
        except Exception as e:
            transcript.push(f"✖ {e}")
        finally:
            transcript.push("")
            transcript.finish()
