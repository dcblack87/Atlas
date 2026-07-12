"""Card formatting for the Telegram bot — the BookingMachine house style.

HTML parse mode; emoji header + bold title, blank line, ``Label: <b>value</b>``
rows; <code> for timestamps and ids. Escape only ``& < >``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def card(emoji: str, title: str, lines: list[str]) -> str:
    return f"{emoji} <b>{esc(title)}</b>\n\n" + "\n".join(lines)


def ago(ts: float) -> str:
    delta = int(time.time() - ts)
    if delta < 0:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def when(ts: float) -> str:
    return time.strftime("%d %b %H:%M", time.localtime(ts))


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def button(label: str, command: str) -> dict:
    return {"text": label, "callback_data": f"cmd:{command}"}


def menu_button() -> dict:
    return {"text": "« Menu", "callback_data": "cmd:help"}


@dataclass(slots=True)
class BotResponse:
    text: str
    keyboard: list[list[dict]] = field(default_factory=list)
