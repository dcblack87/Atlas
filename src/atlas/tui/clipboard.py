"""Clipboard support.

Copies go through OSC 52, so they land on the *local* clipboard even when
Atlas runs on a server inside tmux over SSH — which is exactly how it's
meant to be used. Requirements worth knowing:

- terminal must support OSC 52 (iTerm2, kitty, WezTerm, Ghostty, Windows
  Terminal, Termius do; legacy terminals may not)
- through tmux, ``set -g set-clipboard on`` must be in tmux.conf (the
  server installer takes care of it)
"""

from __future__ import annotations

from textual.screen import Screen


def copy_text(screen: Screen, text: str, what: str) -> None:
    text = text.strip()
    if not text:
        screen.notify("nothing to copy", severity="warning", timeout=2)
        return
    screen.app.copy_to_clipboard(text)
    lines = text.count("\n") + 1
    screen.notify(f"copied {what} ({lines} lines)", timeout=2)
