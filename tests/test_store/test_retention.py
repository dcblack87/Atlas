"""Downsampling and pruning."""

from pathlib import Path

import pytest

from atlas.model import Sample
from atlas.store.db import Database
from atlas.store.metrics import Metrics
from atlas.store.retention import DAY, HOUR, RAW_RETENTION_S, run_retention


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "t.db")
    await database.open()
    yield database
    await database.close()


async def test_raw_rolls_up_and_prunes(db: Database) -> None:
    metrics = Metrics(db)
    now = 1_800_000_000
    old_hour = now - RAW_RETENTION_S - 10 * HOUR

    # ancient data: must roll up then be pruned from raw
    for i, value in enumerate([10.0, 20.0, 30.0]):
        await metrics.write([Sample("m", value, "host:a")], ts=old_hour + i * 60)
    # current data: stays raw
    await metrics.write([Sample("m", 55.0, "host:a")], ts=now - 30)

    await run_retention(db, now=now)

    hourly = await db.fetch_all(
        "SELECT * FROM metrics_hourly WHERE entity_key='host:a' AND ts_hour = ?",
        (old_hour - old_hour % HOUR,),
    )
    assert len(hourly) == 1
    row = hourly[0]
    assert (row["min"], row["max"], row["n"]) == (10.0, 30.0, 3)
    assert row["avg"] == pytest.approx(20.0)
    assert row["last"] == 30.0

    raw = await db.fetch_all("SELECT ts FROM metrics_raw")
    assert len(raw) == 1  # only the fresh sample survived


async def test_hourly_rolls_to_daily(db: Database) -> None:
    now = 1_800_000_000
    old_day = now - 2 * DAY
    for i in range(3):
        await db.execute(
            "INSERT INTO metrics_hourly (ts_hour, entity_key, metric, min, max, avg, last, n)"
            " VALUES (?, 'host:a', 'm', ?, ?, ?, ?, 10)",
            (old_day + i * HOUR, float(i), float(i + 10), float(i + 5), float(i + 5)),
        )
    await run_retention(db, now=now)
    daily = await db.fetch_all("SELECT * FROM metrics_daily")
    assert len(daily) == 1
    assert daily[0]["min"] == 0.0
    assert daily[0]["max"] == 12.0
    assert daily[0]["n"] == 30


async def test_idempotent(db: Database) -> None:
    metrics = Metrics(db)
    now = 1_800_000_000
    await metrics.write([Sample("m", 5.0, "host:a")], ts=now - 2 * HOUR)
    await run_retention(db, now=now)
    await run_retention(db, now=now)
    hourly = await db.fetch_all("SELECT * FROM metrics_hourly")
    assert len(hourly) == 1
