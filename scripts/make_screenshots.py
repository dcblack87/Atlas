"""Regenerate the README screenshots from demo mode.

    uv run python scripts/make_screenshots.py

Always demo data, never real infrastructure — that's the rule for anything
that ends up in git.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from atlas.app import AtlasApp

OUT = Path(__file__).parent.parent / "docs" / "screenshots"


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    app = AtlasApp(None, demo=True)
    async with app.run_test(size=(110, 32)) as pilot:
        # let the demo runtime seed and the dashboard draw real numbers
        for _ in range(40):
            await pilot.pause(0.1)
            if app.runtime is not None:
                break
        await pilot.pause(1.5)
        app.save_screenshot(str(OUT / "dashboard.svg"))

        await pilot.press("2")
        await pilot.pause(1.0)
        app.save_screenshot(str(OUT / "incidents.svg"))

        await pilot.press("escape")
        await pilot.press("f2")  # eink profile
        await pilot.pause(1.0)
        app.save_screenshot(str(OUT / "dashboard-eink.svg"))
    print(f"wrote {len(list(OUT.glob('*.svg')))} screenshots to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
