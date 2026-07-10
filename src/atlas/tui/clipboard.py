"""Clipboard support.

Two delivery paths, used together:

- **OSC 52** (via Textual) — reaches the *local* clipboard even when Atlas
  runs on a server inside tmux over SSH. Supported by iTerm2, kitty,
  WezTerm, Ghostty, Windows Terminal, and Termius; through tmux it needs
  ``set -g set-clipboard on`` (the server launcher sets it).
- **pbcopy fallback** — when Atlas runs locally on macOS (no SSH session),
  the clipboard is written directly. This covers Terminal.app, which
  silently ignores OSC 52.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from textual.screen import Screen

log = logging.getLogger(__name__)


def _is_local_macos() -> bool:
    return sys.platform == "darwin" and not (
        os.environ.get("SSH_TTY") or os.environ.get("SSH_CONNECTION")
    )


def copy_text(screen: Screen, text: str, what: str) -> None:
    text = text.strip()
    if not text:
        screen.notify("nothing to copy", severity="warning", timeout=2)
        return
    screen.app.copy_to_clipboard(text)  # OSC 52 — for remote/tmux sessions
    if _is_local_macos():
        try:
            subprocess.run(["pbcopy"], input=text.encode(), timeout=5, check=True)
        except (OSError, subprocess.SubprocessError):
            log.warning("pbcopy fallback failed", exc_info=True)
    lines = text.count("\n") + 1
    screen.notify(f"copied {what} ({lines} lines)", timeout=2)
