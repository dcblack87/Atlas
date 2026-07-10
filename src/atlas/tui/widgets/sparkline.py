"""Text sparklines from metric series.

Pure functions returning strings — widgets embed them in Statics/tables, so
a sparkline only repaints when its rendered string changes (i.e. when a new
bucket completes, never per sample).
"""

from __future__ import annotations

BLOCKS = " ▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 20) -> str:
    """Render values into block characters, newest right-aligned."""
    if not values:
        return " " * width
    values = values[-width:]
    lo, hi = min(values), max(values)
    span = hi - lo
    if span <= 0:
        # flat line: draw a stable mid-height bar, don't flicker on noise
        return (BLOCKS[3] * len(values)).rjust(width)
    chars = [BLOCKS[1 + round((v - lo) / span * (len(BLOCKS) - 2))] for v in values]
    return "".join(chars).rjust(width)


def bucketize(points: list[tuple[int, float]], bucket_s: float, width: int = 20) -> list[float]:
    """Average raw (ts, value) points into completed fixed-width buckets."""
    if not points:
        return []
    buckets: dict[int, list[float]] = {}
    for ts, value in points:
        buckets.setdefault(int(ts // bucket_s), []).append(value)
    # drop the newest (incomplete) bucket so the line only moves when a
    # bucket completes — key for e-ink stillness
    keys = sorted(buckets)[:-1] or sorted(buckets)
    return [sum(buckets[k]) / len(buckets[k]) for k in keys[-width:]]
