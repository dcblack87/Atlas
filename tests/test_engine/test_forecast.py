"""Forecast math and the end-to-end fact pipeline."""

from pathlib import Path

import pytest

from atlas.engine.forecast import days_until, least_squares, run_forecasts
from atlas.model import Entity, EntityKind
from atlas.store.db import Database
from atlas.store.inventory import Inventory


def test_least_squares_recovers_slope() -> None:
    points = [(float(x), 2.0 * x + 5.0) for x in range(10)]
    fit = least_squares(points)
    assert fit is not None
    slope, intercept = fit
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(5.0)


def test_days_until_crossing() -> None:
    # 1% per day starting at 50% -> 50 days to 100%
    day = 86400.0
    points = [(i * day, 50.0 + i) for i in range(14)]
    days = days_until(points, 100.0, now=13 * day)
    assert days == pytest.approx(37.0, abs=0.5)


def test_flat_or_receding_gives_none() -> None:
    day = 86400.0
    assert days_until([(i * day, 50.0) for i in range(10)], 100, now=0) is None
    assert days_until([(i * day, 50.0 - i) for i in range(10)], 100, now=0) is None


async def test_run_forecasts_writes_fact(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    await db.open()
    inventory = Inventory(db)
    await inventory.upsert(Entity(EntityKind.HOST, "host:web-1"))
    now = 1_800_000_000
    hour = 3600
    # +0.05%/hour: ~41 days from 50% to 100%
    for i in range(48):
        ts_hour = now - (48 - i) * hour
        await db.execute(
            "INSERT INTO metrics_hourly (ts_hour, entity_key, metric, min, max, avg, last, n)"
            " VALUES (?, 'host:web-1', 'disk.used_pct', 0, 0, ?, 0, 60)",
            (ts_hour, 50.0 + i * 0.05),
        )
    await run_forecasts(db, now=now)
    days = await inventory.get_fact("host:web-1", "forecast.disk_full_days")
    assert days is not None
    assert 30 < float(days) < 55
    await db.close()
