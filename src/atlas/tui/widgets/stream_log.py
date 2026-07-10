"""StreamLog — a RichLog that respects the display profile.

On LCD, lines append live. On e-ink, lines buffer and flush as one write per
flush period so a deploy doesn't strobe the panel.
"""

from __future__ import annotations

from textual.timer import Timer
from textual.widgets import RichLog


class StreamLog(RichLog):
    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id, highlight=False, markup=False, wrap=True, auto_scroll=True)
        self._buffer: list[str] = []
        self._history: list[str] = []  # everything ever pushed — the copy source
        self._flush_timer: Timer | None = None
        self._flush_period = 0.0

    @property
    def text(self) -> str:
        """Full content (including anything still buffered) for copying."""
        return "\n".join(self._history + self._buffer)

    def set_flush_period(self, seconds: float) -> None:
        """0 = live append; >0 = coalesce writes on this clock."""
        self._flush_period = seconds
        if self._flush_timer is not None:
            self._flush_timer.stop()
            self._flush_timer = None
        if seconds > 0:
            self._flush_timer = self.set_interval(seconds, self._flush)

    def push(self, line: str) -> None:
        if self._flush_period > 0:
            self._buffer.append(line)
        else:
            self._history.append(line)
            self.write(line)

    def clear(self):  # type: ignore[override]
        self._history.clear()
        self._buffer.clear()
        return super().clear()

    def _flush(self) -> None:
        if self._buffer:
            self.write("\n".join(self._buffer))
            self._history.extend(self._buffer)
            self._buffer.clear()

    def finish(self) -> None:
        """Flush whatever remains (end of stream)."""
        self._flush()
