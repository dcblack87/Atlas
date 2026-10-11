"""Card formatting for the Telegram bot — one house style for every card.

HTML parse mode; emoji header + bold title, blank line, ``Label: <b>value</b>``
rows; <code> for timestamps and ids. Escape only ``& < >``. Every card ends
with the time it was read and the same two buttons: Refresh and Menu.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

MESSAGE_LIMIT = 4096  # Telegram refuses a longer message
DATA_LIMIT = 64  # and silently drops a button whose data is longer, in bytes


def esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def card(emoji: str, title: str, lines: list[str]) -> str:
    return f"{emoji} <b>{esc(title)}</b>\n\n" + "\n".join([*lines, "", as_of()])


def as_of() -> str:
    return f"<i>as of {time.strftime('%H:%M')}</i>"


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


def button(label: str, command: str, *args: object) -> dict:
    """A button that comes back as ``cmd:<command>[:<arg>…]``."""
    data = ":".join(["cmd", command, *(str(arg) for arg in args)])
    if len(data.encode()) > DATA_LIMIT:
        raise ValueError(f"button data over {DATA_LIMIT} bytes: {data}")
    return {"text": label, "callback_data": data}


def open_button(label: str, command: str, *args: object) -> dict:
    """A button under an alert or a brief: ``open:<command>[:<arg>…]``.

    It opens the same card as ``button``, in a new message. An alert is the
    record of what happened, so a tap under it must not write over it.
    """
    data = ":".join(["open", command, *(str(arg) for arg in args)])
    if len(data.encode()) > DATA_LIMIT:
        raise ValueError(f"button data over {DATA_LIMIT} bytes: {data}")
    return {"text": label, "callback_data": data}


def read_tap(data: str) -> tuple[str, bool]:
    """What a pressed button carried, as a command, and whether to answer in a new message."""
    if data.startswith("open:"):
        return "cmd:" + data[5:], True
    return data, False


def menu_button() -> dict:
    return {"text": "« Menu", "callback_data": "cmd:help"}


def footer(command: str, *args: object) -> list[dict]:
    """The closing row of every card: the same card again, and the way home."""
    return [button("↻ Refresh", command, *args), menu_button()]


def paginate(items: list, args: list[str], size: int) -> tuple[list, int, int]:
    """One page of ``items``: (the page, its number from 0, how many pages)."""
    pages = max(1, math.ceil(len(items) / size))
    asked = int(args[0]) if args and args[0].isdigit() else 0
    page = min(asked, pages - 1)
    return items[page * size : (page + 1) * size], page, pages


def pager(command: str, page: int, pages: int) -> list[dict]:
    """Previous and Next, each only where there is a page to go to."""
    row = []
    if page > 0:
        row.append(button("« Prev", command, page - 1))
    if page < pages - 1:
        row.append(button("Next »", command, page + 1))
    return row


def page_note(page: int, pages: int) -> list[str]:
    return [f"<i>Page {page + 1} of {pages}</i>"] if pages > 1 else []


def chunk(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split a long message at line ends so each part fits.

    A hard slice can land inside a tag, and Telegram then refuses the whole
    message. A single line longer than the limit is still cut hard; the cards
    keep their own lines short.
    """
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


@dataclass(slots=True)
class BotResponse:
    text: str
    keyboard: list[list[dict]] = field(default_factory=list)
    toast: str | None = None  # the short line Telegram shows over the chat after a tap

    def __post_init__(self) -> None:
        # A card builds its rows from conditions; a row that came out empty is dropped.
        self.keyboard = [row for row in self.keyboard if row]
