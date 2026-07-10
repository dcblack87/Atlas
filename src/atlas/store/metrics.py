"""Metric writes and time-series queries."""

from __future__ import annotations

import time
from dataclasses import dataclass

from atlas.model import Sample
from atlas.store.db import Database


@dataclass(slots=True)
class SeriesPoint:
    ts: int
    value: float


class Metrics:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def write(self, samples: list[Sample], ts: int | None = None) -> None:
        ts = ts or int(time.time())
        await self._db.executemany(
            "INSERT INTO metrics_raw (ts, entity_key, metric, value) VALUES (?, ?, ?, ?)",
            [(ts, s.entity, s.metric, s.value) for s in samples],
        )

    async def latest(self, entity_key: str, metric: str) -> float | None:
        return await self._db.fetch_value(
            """
            SELECT value FROM metrics_raw
            WHERE entity_key = ? AND metric = ?
            ORDER BY ts DESC LIMIT 1
            """,
            (entity_key, metric),
        )

    async def latest_snapshot(self, entity_key: str) -> dict[str, float]:
        """Most recent value of every metric an entity reports."""
        rows = await self._db.fetch_all(
            """
            SELECT metric, value FROM metrics_raw
            WHERE entity_key = ? AND ts = (
                SELECT MAX(ts) FROM metrics_raw r2
                WHERE r2.entity_key = metrics_raw.entity_key
                  AND r2.metric = metrics_raw.metric
            )
            """,
            (entity_key,),
        )
        return {row["metric"]: row["value"] for row in rows}

    async def recent(
        self, entity_key: str, metric: str, *, since_s: int, limit: int = 1000
    ) -> list[SeriesPoint]:
        """Raw series for short windows (sparklines, rule evaluation)."""
        rows = await self._db.fetch_all(
            """
            SELECT ts, value FROM metrics_raw
            WHERE entity_key = ? AND metric = ? AND ts >= ?
            ORDER BY ts DESC LIMIT ?
            """,
            (entity_key, metric, int(time.time()) - since_s, limit),
        )
        return [SeriesPoint(row["ts"], row["value"]) for row in reversed(rows)]

    async def last_n(self, entity_key: str, metric: str, n: int) -> list[float]:
        """The N most recent values, oldest first — rule hysteresis input."""
        rows = await self._db.fetch_all(
            """
            SELECT value FROM metrics_raw
            WHERE entity_key = ? AND metric = ?
            ORDER BY ts DESC LIMIT ?
            """,
            (entity_key, metric, n),
        )
        return [row["value"] for row in reversed(rows)]

    async def hourly(self, entity_key: str, metric: str, *, since_s: int) -> list[dict]:
        rows = await self._db.fetch_all(
            """
            SELECT ts_hour, min, max, avg, last, n FROM metrics_hourly
            WHERE entity_key = ? AND metric = ? AND ts_hour >= ?
            ORDER BY ts_hour
            """,
            (entity_key, metric, int(time.time()) - since_s),
        )
        return [dict(row) for row in rows]
