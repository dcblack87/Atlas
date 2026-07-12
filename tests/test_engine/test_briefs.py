"""Brief skeleton and scheduling."""

from datetime import datetime
from pathlib import Path

from atlas.demo.dataset import seed_demo
from atlas.reports.briefs import build_skeleton, due_brief, generate_brief
from atlas.store.db import Database


async def test_skeleton_from_demo(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    await db.open()
    await seed_demo(db)
    skeleton = await build_skeleton(db, window_s=86400)
    assert "fleet health" in skeleton
    assert "open incidents: 2" in skeleton  # expiring cert + failing backup cron
    assert "forecast" in skeleton  # web-2 disk forecast is seeded
    await db.close()


async def test_generate_brief_without_ai_archives_skeleton(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    await db.open()
    await seed_demo(db)
    body = await generate_brief(db, None, None)
    assert "MORNING BRIEF" in body
    archived = await db.fetch_value(
        "SELECT response FROM ai_analyses WHERE kind = 'brief' ORDER BY ts DESC"
    )
    assert archived == body
    await db.close()


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def test_due_brief_schedule() -> None:
    monday_9am = _ts("2026-07-06 09:00")
    assert due_brief(monday_9am, last_daily=0, last_weekly=monday_9am) == "daily"
    # already sent this morning
    assert due_brief(monday_9am, last_daily=monday_9am - 3600, last_weekly=monday_9am) is None
    # before 07:00 nothing fires
    assert due_brief(_ts("2026-07-06 05:00"), 0, _ts("2026-07-06 05:00")) is None
    # sunday morning prefers the weekly
    sunday_9am = _ts("2026-07-05 09:00")
    assert due_brief(sunday_9am, last_daily=sunday_9am, last_weekly=0) == "weekly"
