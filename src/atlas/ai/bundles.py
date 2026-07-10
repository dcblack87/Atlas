"""Context bundles: everything Claude Code needs to help, in one Markdown file.

`b` in the TUI or `atlas bundle [--app X]` on the CLI. Written to the
gitignored bundles/ directory; secret-shaped strings are scrubbed.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from atlas.ai.context import ContextBuilder

BUNDLES_DIR = Path("bundles")

# Long high-entropy tokens, key=value secrets, URL credentials, PEM blocks.
_SECRETY = re.compile(
    r"(?:(?:api[_-]?key|token|secret|password|passwd)\s*[=:]\s*)\S+"
    r"|[a-z][a-z0-9+.-]*://[^\s@/]+:[^\s@]+@"  # scheme://user:pass@
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"
    r"|\b[A-Za-z0-9+/_-]{40,}\b",
    re.IGNORECASE,
)


def scrub(text: str) -> str:
    return _SECRETY.sub("[redacted]", text)


async def write_bundle(context: ContextBuilder, app: str | None = None) -> Path:
    entity_keys = [f"app:{app}"] if app else []
    inventory = await context.inventory_block()
    detail = await context.entity_block(entity_keys, window_s=48 * 3600)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"atlas-context-{app or 'fleet'}-{stamp}.md"
    BUNDLES_DIR.mkdir(exist_ok=True)
    path = BUNDLES_DIR / name

    body = f"""# Atlas context bundle — {app or "whole fleet"}

Generated {datetime.now():%Y-%m-%d %H:%M} (unix {int(time.time())}) by Atlas.
Paste this into Claude Code and ask it to diagnose or plan against it.

## Fleet

```
{scrub(inventory)}
```

## Current state

```
{scrub(detail)}
```
"""
    path.write_text(body)
    return path
