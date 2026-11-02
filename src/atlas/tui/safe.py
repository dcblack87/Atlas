"""Text from servers, made safe to put on screen.

Container logs, site and cron names, incident titles and health JSON all come
from the machines Atlas watches, so a compromised app chooses them. Two things
must never get through:

- terminal control sequences, which can rewrite the screen or, with tmux's
  set-clipboard on, replace what the operator will paste next (OSC 52);
- Textual markup, which Static widgets and DataTable cells parse in plain
  strings, so ``[@click=app.quit]`` or ``[link=...]`` would become live UI.

``clean`` removes the first; ``plain`` also returns a Text, which is rendered
as it is and never parsed for markup.
"""

from __future__ import annotations

import re

from rich.text import Text

# CSI (colours, cursor moves), OSC (titles, clipboard, hyperlinks) and other ESC sequences
_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])")
# Every other control character except tab and newline, including the C1 range
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(value: object) -> str:
    """The text with terminal control sequences and characters removed."""
    return _CONTROL.sub("", _ESCAPE.sub("", str(value)))


def plain(value: object) -> Text:
    """Literal, cleaned text: shown exactly as written, never read as markup."""
    return Text(clean(value))
