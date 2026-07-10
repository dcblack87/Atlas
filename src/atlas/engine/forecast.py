"""Forecasting: ordinary least squares over hourly rollups.

Answers "when does this line cross that threshold" — disk full in N days,
database doubling, memory creeping. Stdlib math; forecasts land as facts so
rules can threshold them and the AI can cite them.
"""

from __future__ import annotations

import time

from atlas.store.db import Database
from atlas.store.inventory import Inventory

FORECAST_METRICS = {
    "disk.used_pct": ("forecast.disk_full_days", 100.0),
    "mem.used_pct": ("forecast.mem_full_days", 100.0),
}
WINDOW_S = 14 * 24 * 3600
MIN_POINTS = 24


def least_squares(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Return (slope_per_second, intercept), or None if degenerate."""
    n = len(points)
    if n < 2:
        return None
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    ss_xx = sum((x - mean_x) ** 2 for x, _ in points)
    if ss_xx == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / ss_xx
    return slope, mean_y - slope * mean_x


def days_until(points: list[tuple[float, float]], ceiling: float, now: float) -> float | None:
    """Days until the trend line crosses the ceiling; None if flat/receding."""
    fit = least_squares(points)
    if fit is None:
        return None
    slope, intercept = fit
    if slope <= 0:
        return None
    crossing = (ceiling - intercept) / slope
    days = (crossing - now) / 86400
    return round(days, 1) if days > 0 else 0.0


async def run_forecasts(db: Database, now: float | None = None) -> None:
    inventory = Inventory(db)
    now = now or time.time()
    for host in await inventory.entities(kind="host"):
        for metric, (fact_name, ceiling) in FORECAST_METRICS.items():
            rows = await db.fetch_all(
                """
                SELECT ts_hour, avg FROM metrics_hourly
                WHERE entity_key = ? AND metric = ? AND ts_hour >= ?
                ORDER BY ts_hour
                """,
                (host["key"], metric, int(now - WINDOW_S)),
            )
            if len(rows) < MIN_POINTS:
                continue
            points = [(float(r["ts_hour"]), float(r["avg"])) for r in rows]
            days = days_until(points, ceiling, now)
            if days is not None and days < 365:
                await inventory.set_fact(host["key"], fact_name, days)
