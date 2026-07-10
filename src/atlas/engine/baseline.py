"""Baseline learning: what "normal" looks like for this fleet, per hour-of-week.

A rolling 4-week window of hourly rollups gives each (entity, metric) a mean
and standard deviation for the current hour-of-week. Sustained deviation
beyond three standard deviations is an anomaly finding — and if a deploy happened near the start of
the deviation, the finding says so. Plain SQL, no ML dependencies. This is
the "Atlas learns your infrastructure" layer.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from atlas.bus import Bus, FindingsEvent
from atlas.model import Finding, Severity
from atlas.store.db import Database

BASELINE_METRICS = ("mem.used_pct", "load.1m", "container.mem_bytes", "http.response_ms")
WINDOW_S = 28 * 24 * 3600
MIN_SAMPLES = 8  # need at least ~2 weeks of this hour-of-week
SIGMA = 3.0
SUSTAIN_RAW_SAMPLES = 3
MIN_RELATIVE_LIFT = 1.5  # ignore statistically-significant but tiny wiggles


@dataclass(slots=True)
class Anomaly:
    entity: str
    metric: str
    value: float
    mean: float
    stddev: float
    deploy_note: str | None


async def detect_anomalies(db: Database, now: float | None = None) -> list[Anomaly]:
    now = now or time.time()
    hour_of_week = _hour_of_week(now)
    anomalies: list[Anomaly] = []

    pairs = await db.fetch_all(
        f"""
        SELECT DISTINCT entity_key, metric FROM metrics_hourly
        WHERE metric IN ({",".join("?" * len(BASELINE_METRICS))}) AND ts_hour >= ?
        """,
        (*BASELINE_METRICS, int(now - WINDOW_S)),
    )
    # A 4-week window holds only 4 samples of any single hour-of-week, so the
    # baseline population includes the two neighbouring hours (12 samples) —
    # load at 14:00 Tuesday is comparable to 13:00-15:00 Tuesday.
    hours = [(hour_of_week - 1) % 168, hour_of_week, (hour_of_week + 1) % 168]
    for pair in pairs:
        entity, metric = pair["entity_key"], pair["metric"]
        rows = await db.fetch_all(
            """
            SELECT avg FROM metrics_hourly
            WHERE entity_key = ? AND metric = ? AND ts_hour >= ?
              AND ((ts_hour / 3600 + 96) % 168) IN (?, ?, ?)
            """,
            # unix epoch was a Thursday; +96h aligns hour-of-week to Monday 00
            (entity, metric, int(now - WINDOW_S), *hours),
        )
        values = [float(r["avg"]) for r in rows]
        if len(values) < MIN_SAMPLES:
            continue
        mean = sum(values) / len(values)
        stddev = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        if stddev == 0 and mean == 0:
            continue
        threshold = mean + SIGMA * max(stddev, abs(mean) * 0.05)
        recent = await db.fetch_all(
            """
            SELECT value FROM metrics_raw WHERE entity_key = ? AND metric = ?
            ORDER BY ts DESC LIMIT ?
            """,
            (entity, metric, SUSTAIN_RAW_SAMPLES),
        )
        recent_values = [float(r["value"]) for r in recent]
        if len(recent_values) < SUSTAIN_RAW_SAMPLES:
            continue
        if all(v > threshold for v in recent_values) and (
            mean == 0 or recent_values[0] / mean >= MIN_RELATIVE_LIFT
        ):
            anomalies.append(
                Anomaly(
                    entity=entity,
                    metric=metric,
                    value=recent_values[0],
                    mean=mean,
                    stddev=stddev,
                    deploy_note=await _correlate_deploy(db, now),
                )
            )
    return anomalies


async def _correlate_deploy(db: Database, now: float) -> str | None:
    row = await db.fetch_one(
        "SELECT app, git_sha_after, started_at FROM deployments "
        "WHERE started_at >= ? ORDER BY started_at DESC LIMIT 1",
        (int(now - 6 * 3600),),
    )
    if row is None:
        return None
    ago_min = int((now - row["started_at"]) / 60)
    sha = (row["git_sha_after"] or "?")[:7]
    return f"deploy of {row['app']} ({sha}) {ago_min} min earlier"


async def run_anomaly_detection(db: Database, bus: Bus) -> None:
    for anomaly in await detect_anomalies(db):
        ratio = anomaly.value / anomaly.mean if anomaly.mean else float("inf")
        title = (
            f"{anomaly.entity.split(':', 1)[-1]}: {anomaly.metric} is "
            f"{ratio:.1f}x its 4-week norm for this hour"
        )
        if anomaly.deploy_note:
            title += f" — began after {anomaly.deploy_note}"
        await bus.publish(
            FindingsEvent(
                "baseline",
                "baseline",
                [
                    Finding(
                        "anomaly",
                        anomaly.entity,
                        Severity.WARNING,
                        title,
                        detail={
                            "metric": anomaly.metric,
                            "value": anomaly.value,
                            "baseline_mean": round(anomaly.mean, 2),
                            "baseline_stddev": round(anomaly.stddev, 2),
                            "deploy": anomaly.deploy_note,
                        },
                    )
                ],
            )
        )


def _hour_of_week(ts: float) -> int:
    """Same arithmetic as the SQL bucket: UTC hours, Monday-aligned."""
    return int((ts // 3600 + 96) % 168)
