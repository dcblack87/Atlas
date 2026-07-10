"""The declarative rule table.

Metric rules breach on thresholds with hysteresis: ``for_samples``
consecutive breaching values open, ``clear_samples`` consecutive clear values
resolve. Fact rules judge current state (cert expiry, backup age) directly.
Collector findings bypass rules — they arrive pre-judged and flow straight
into the incident manager with absence-based resolution.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.model import Finding, Severity


@dataclass(frozen=True, slots=True)
class MetricRule:
    id: str
    metric: str
    warn: float | None = None
    crit: float | None = None
    below: bool = False  # breach when value falls BELOW the threshold
    for_samples: int = 3
    clear_samples: int = 3
    title: str = "{entity}: {metric} at {value}"

    def judge(self, values: list[float]) -> Severity | None:
        """Judge the most recent values (oldest first). None = healthy."""
        if len(values) < self.for_samples:
            return None
        window = values[-self.for_samples :]
        if self.crit is not None and all(self._breaches(v, self.crit) for v in window):
            return Severity.CRITICAL
        if self.warn is not None and all(self._breaches(v, self.warn) for v in window):
            return Severity.WARNING
        return None

    def cleared(self, values: list[float]) -> bool:
        threshold = self.warn if self.warn is not None else self.crit
        if threshold is None or len(values) < self.clear_samples:
            return False
        return all(not self._breaches(v, threshold) for v in values[-self.clear_samples :])

    def _breaches(self, value: float, threshold: float) -> bool:
        return value < threshold if self.below else value > threshold

    def finding(self, entity: str, severity: Severity, value: float) -> Finding:
        return Finding(
            self.id,
            entity,
            severity,
            self.title.format(entity=_pretty(entity), metric=self.metric, value=round(value, 1)),
            detail={"metric": self.metric, "value": value, "warn": self.warn, "crit": self.crit},
        )


@dataclass(frozen=True, slots=True)
class FactRule:
    """Judges a numeric fact where smaller is worse (days remaining) or
    larger is worse (hours since)."""

    id: str
    fact: str
    warn: float | None = None
    crit: float | None = None
    below: bool = False
    title: str = "{entity}: {fact} = {value}"

    def judge(self, value: float) -> Severity | None:
        if self.crit is not None and (value < self.crit if self.below else value > self.crit):
            return Severity.CRITICAL
        if self.warn is not None and (value < self.warn if self.below else value > self.warn):
            return Severity.WARNING
        return None

    def finding(self, entity: str, severity: Severity, value: float) -> Finding:
        return Finding(
            self.id,
            entity,
            severity,
            self.title.format(entity=_pretty(entity), fact=self.fact, value=round(value, 1)),
            detail={"fact": self.fact, "value": value},
        )


def _pretty(entity: str) -> str:
    return entity.split(":", 1)[-1]


METRIC_RULES: tuple[MetricRule, ...] = (
    MetricRule(
        "disk_high",
        "disk.used_pct",
        warn=80,
        crit=90,
        title="disk on {entity} at {value}%",
    ),
    MetricRule(
        "mem_high",
        "mem.used_pct",
        warn=90,
        crit=97,
        title="memory on {entity} at {value}%",
    ),
    MetricRule(
        "swap_high",
        "swap.used_pct",
        warn=80,
        title="swap on {entity} at {value}%",
    ),
    MetricRule(
        "http_down",
        "http.up",
        crit=1,
        below=True,
        for_samples=2,
        clear_samples=1,
        title="{entity} is not responding",
    ),
    MetricRule(
        "load_high",
        "cpu.load_per_core",
        warn=2.0,
        crit=4.0,
        for_samples=5,
        title="load on {entity} at {value} per core",
    ),
)

FACT_RULES: tuple[FactRule, ...] = (
    FactRule(
        "cert_expiry",
        "cert.days_remaining",
        warn=21,
        crit=7,
        below=True,
        title="certificate {entity} expires in {value} days",
    ),
    FactRule(
        "backup_stale",
        "backup.age_hours",
        warn=30,
        crit=54,
        title="newest backup for {entity} is {value}h old",
    ),
    FactRule(
        "deploy_drift",
        "drift.commits_behind",
        warn=5,
        title="{entity} is {value} commits behind origin/main",
    ),
    FactRule(
        "disk_forecast",
        "forecast.disk_full_days",
        warn=14,
        crit=5,
        below=True,
        title="disk on {entity} full in ~{value} days at current growth",
    ),
)
