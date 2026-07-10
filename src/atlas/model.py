"""Shared value types used across layers.

These are deliberately plain dataclasses: collectors produce them, the store
persists them, the engine judges them, the TUI renders them. No layer should
need anything richer than this module to talk to another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class EntityKind(StrEnum):
    HOST = "host"
    APP = "app"
    SITE = "site"
    CONTAINER = "container"
    VHOST = "vhost"
    CRON = "cron"
    CERT = "cert"
    DATABASE = "db"


@dataclass(slots=True)
class Sample:
    """One time-series point, e.g. disk.used_pct=83.1 for host:web-1."""

    metric: str
    value: float
    entity: str  # "host:web-1" | "app:shopfront" | "site:acme" | "container:web-redis"
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Finding:
    """A collector- or rule-level judgment about the world."""

    rule_id: str
    entity: str
    severity: Severity
    title: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Entity:
    """An inventory item discovered on a host."""

    kind: EntityKind
    key: str  # globally unique, e.g. "container:web-redis"
    parent: str | None = None  # parent entity key
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Observation:
    """Everything one collector run learned."""

    samples: list[Sample] = field(default_factory=list)
    facts: dict[tuple[str, str], Any] = field(default_factory=dict)  # (entity, name) -> value
    findings: list[Finding] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DisplayProfile:
    """Presentation parameters for one display class.

    E-ink screens pay for every cell change with a visible flash, so the
    eink/glance profiles trade latency for stillness: updates are coalesced
    and flushed on a slow clock, sparklines advance only when a bucket
    completes, and values are rounded before they reach a widget so that
    jitter below display precision never causes a repaint.
    """

    name: str
    refresh_period: float  # seconds between coalesced UI flushes
    sparkline_bucket: float  # seconds per sparkline bucket
    show_sparklines: bool


PROFILES: dict[str, DisplayProfile] = {
    p.name: p
    for p in (
        DisplayProfile("standard", refresh_period=1.0, sparkline_bucket=60, show_sparklines=True),
        DisplayProfile("eink", refresh_period=10.0, sparkline_bucket=600, show_sparklines=True),
        DisplayProfile("glance", refresh_period=30.0, sparkline_bucket=600, show_sparklines=False),
    )
}

PROFILE_ORDER = ["standard", "eink", "glance"]
