"""Baseline anomaly detection: normal is quiet, deviation is a finding."""

from pathlib import Path

import pytest

from atlas.engine.baseline import _hour_of_week, detect_anomalies
from atlas.model import Sample
from atlas.store.db import Database
from atlas.store.metrics import Metrics

HOUR = 3600
WEEK = 168 * HOUR


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "t.db")
    await database.open()
    yield database
    await database.close()


async def seed_baseline(db: Database, now: int, value: float) -> None:
    """Four weeks of hourly history at `value` for the current hour-of-week."""
    how = _hour_of_week(now)
    for week in range(1, 5):
        for offset in (-1, 0, 1):  # a few neighbouring hours too
            ts_hour = (now - week * WEEK) + offset * HOUR
            ts_hour -= ts_hour % HOUR
            if _hour_of_week(ts_hour) != how and offset == 0:
                continue
            await db.execute(
                "INSERT OR REPLACE INTO metrics_hourly "
                "(ts_hour, entity_key, metric, min, max, avg, last, n)"
                " VALUES (?, 'host:a', 'mem.used_pct', ?, ?, ?, ?, 60)",
                (ts_hour, value, value, value, value),
            )
    # need >= MIN_SAMPLES rows for this hour-of-week: add more weeks
    for week in range(5, 10):
        ts_hour = now - week * WEEK
        ts_hour -= ts_hour % HOUR
        await db.execute(
            "INSERT OR REPLACE INTO metrics_hourly "
            "(ts_hour, entity_key, metric, min, max, avg, last, n)"
            " VALUES (?, 'host:a', 'mem.used_pct', ?, ?, ?, ?, 60)",
            (ts_hour, value, value, value, value),
        )


async def test_normal_values_are_quiet(db: Database) -> None:
    now = 1_800_000_000
    await seed_baseline(db, now, 40.0)
    metrics = Metrics(db)
    for i in range(3):
        await metrics.write([Sample("mem.used_pct", 41.0, "host:a")], ts=now - 120 + i * 60)
    assert await detect_anomalies(db, now=now) == []


async def test_sustained_spike_is_anomalous(db: Database) -> None:
    now = 1_800_000_000
    await seed_baseline(db, now, 40.0)
    metrics = Metrics(db)
    for i in range(3):
        await metrics.write([Sample("mem.used_pct", 92.0, "host:a")], ts=now - 120 + i * 60)
    anomalies = await detect_anomalies(db, now=now)
    assert len(anomalies) == 1
    assert anomalies[0].entity == "host:a"
    assert anomalies[0].value == 92.0


async def test_anomaly_correlates_deploy(db: Database) -> None:
    now = 1_800_000_000
    await seed_baseline(db, now, 40.0)
    await db.execute(
        "INSERT INTO deployments (app, host, started_at, command, git_sha_after,"
        " confirmed_phrase) VALUES ('shopfront', 'a', ?, 'deploy', ?, 'shopfront')",
        (now - 1200, "8f31a2c" + "0" * 33),
    )
    metrics = Metrics(db)
    for i in range(3):
        await metrics.write([Sample("mem.used_pct", 92.0, "host:a")], ts=now - 120 + i * 60)
    anomalies = await detect_anomalies(db, now=now)
    assert len(anomalies) == 1
    assert anomalies[0].deploy_note is not None
    assert "shopfront" in anomalies[0].deploy_note
    assert "8f31a2c" in anomalies[0].deploy_note


async def test_single_spike_not_sustained_is_quiet(db: Database) -> None:
    now = 1_800_000_000
    await seed_baseline(db, now, 40.0)
    metrics = Metrics(db)
    await metrics.write([Sample("mem.used_pct", 41.0, "host:a")], ts=now - 180)
    await metrics.write([Sample("mem.used_pct", 95.0, "host:a")], ts=now - 120)
    await metrics.write([Sample("mem.used_pct", 41.0, "host:a")], ts=now - 60)
    assert await detect_anomalies(db, now=now) == []
