"""Downsampling and pruning: raw (48h) → hourly (90d) → daily (forever)."""

from __future__ import annotations

import logging
import time

from atlas.store.db import Database

log = logging.getLogger(__name__)

RAW_RETENTION_S = 48 * 3600
HOURLY_RETENTION_S = 90 * 24 * 3600
HOUR = 3600
DAY = 86400


async def run_retention(db: Database, now: int | None = None) -> None:
    """One retention pass. Idempotent; safe to run any time."""
    now = now or int(time.time())
    current_hour = now - (now % HOUR)
    current_day = now - (now % DAY)

    # raw -> hourly for all completed hours not yet rolled up
    await db.execute(
        f"""
        INSERT OR REPLACE INTO metrics_hourly (ts_hour, entity_key, metric, min, max, avg, last, n)
        SELECT
            ts - (ts % {HOUR}), entity_key, metric,
            MIN(value), MAX(value), AVG(value),
            (SELECT value FROM metrics_raw r2
             WHERE r2.entity_key = r.entity_key AND r2.metric = r.metric
               AND r2.ts - (r2.ts % {HOUR}) = r.ts - (r.ts % {HOUR})
             ORDER BY r2.ts DESC LIMIT 1),
            COUNT(*)
        FROM metrics_raw r
        WHERE ts < ?
        GROUP BY ts - (ts % {HOUR}), entity_key, metric
        """,
        (current_hour,),
    )

    # hourly -> daily for completed days older than the hourly horizon
    await db.execute(
        f"""
        INSERT OR REPLACE INTO metrics_daily (ts_day, entity_key, metric, min, max, avg, last, n)
        SELECT
            ts_hour - (ts_hour % {DAY}), entity_key, metric,
            MIN(min), MAX(max), AVG(avg),
            (SELECT last FROM metrics_hourly h2
             WHERE h2.entity_key = h.entity_key AND h2.metric = h.metric
               AND h2.ts_hour - (h2.ts_hour % {DAY}) = h.ts_hour - (h.ts_hour % {DAY})
             ORDER BY h2.ts_hour DESC LIMIT 1),
            SUM(n)
        FROM metrics_hourly h
        WHERE ts_hour < ?
        GROUP BY ts_hour - (ts_hour % {DAY}), entity_key, metric
        """,
        (current_day,),
    )

    # prune
    await db.execute("DELETE FROM metrics_raw WHERE ts < ?", (now - RAW_RETENTION_S,))
    await db.execute("DELETE FROM metrics_hourly WHERE ts_hour < ?", (now - HOURLY_RETENTION_S,))
    log.debug("retention pass complete")
