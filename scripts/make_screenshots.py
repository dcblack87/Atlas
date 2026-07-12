"""Regenerate the README screenshots from demo mode.

    uv run python scripts/make_screenshots.py

Always demo data, never real infrastructure. That's the rule for anything
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

        # apps drill-down: shopfront (deploy drift), then sitefarm (capacity)
        await pilot.press("3")
        await pilot.pause(0.5)
        await pilot.press("down", "down")
        await pilot.pause(1.0)
        app.save_screenshot(str(OUT / "apps.svg"))
        await pilot.press("down")
        await pilot.pause(1.0)
        app.save_screenshot(str(OUT / "apps-sitefarm.svg"))
        await pilot.press("escape")

        await pilot.press("6")
        await pilot.pause(1.0)
        app.save_screenshot(str(OUT / "cost.svg"))
        await pilot.press("escape")

        await pilot.press("7")
        await pilot.pause(1.0)
        app.save_screenshot(str(OUT / "security.svg"))
        await pilot.press("escape")

        await pilot.press("f2")  # eink profile
        await pilot.pause(1.5)
        app.save_screenshot(str(OUT / "dashboard-eink.svg"))

        await pilot.press("f2")  # glance profile
        await pilot.pause(1.5)
        app.save_screenshot(str(OUT / "dashboard-glance.svg"))
    print(f"wrote {len(list(OUT.glob('*.svg')))} screenshots to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
