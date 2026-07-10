"""Tiny in-process pub/sub.

Collectors publish, the engine and TUI subscribe. This is the only coupling
between layers besides the store: nothing imports across layers directly.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from atlas.model import Finding, Sample

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SamplesEvent:
    host: str
    collector: str
    samples: list[Sample]


@dataclass(slots=True)
class FindingsEvent:
    host: str
    collector: str
    findings: list[Finding]


@dataclass(slots=True)
class InventoryChanged:
    added: list[str] = field(default_factory=list)  # entity keys
    removed: list[str] = field(default_factory=list)


@dataclass(slots=True)
class IncidentEvent:
    incident_id: int
    kind: str  # opened | escalated | resolved | acked
    severity: str
    title: str
    entity: str


@dataclass(slots=True)
class DeployEvent:
    deployment_id: int
    app: str
    kind: str  # started | line | finished | verified
    payload: Any = None


@dataclass(slots=True)
class CollectorStatus:
    host: str
    collector: str
    ok: bool
    error: str | None = None


class Bus:
    """Type-keyed pub/sub. Subscribers may be sync or async callables."""

    def __init__(self) -> None:
        self._subs: dict[type, list[Callable[[Any], Any]]] = defaultdict(list)

    def subscribe(self, event_type: type, handler: Callable[[Any], Any]) -> Callable[[], None]:
        self._subs[event_type].append(handler)

        def unsubscribe() -> None:
            self._subs[event_type].remove(handler)

        return unsubscribe

    async def publish(self, event: Any) -> None:
        for handler in list(self._subs[type(event)]):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # A broken subscriber must never take down collection.
                log.exception("bus subscriber failed for %s", type(event).__name__)

    def publish_soon(self, event: Any) -> None:
        """Fire-and-forget publish from sync code already inside the loop."""
        asyncio.get_running_loop().create_task(self.publish(event))
