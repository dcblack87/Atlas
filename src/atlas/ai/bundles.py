"""Context bundles: everything Claude Code needs to help, in one Markdown file.

`b` in the TUI or `atlas bundle [--app X]` on the CLI. Written to the
gitignored bundles/ directory; secret-shaped strings are scrubbed.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from atlas.ai.context import ContextBuilder
from atlas.redact import scrub

BUNDLES_DIR = Path("bundles")


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
