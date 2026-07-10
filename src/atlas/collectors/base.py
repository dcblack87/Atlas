"""The collector contract.

A collector is read-only by architectural invariant: it observes and judges,
it never mutates. If something needs fixing, the collector emits a finding
and the remediation flows through the audited deploy gate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from atlas.config import HostConfig
from atlas.model import Observation
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext

REGISTRY: dict[str, type[Collector]] = {}


def register(cls: type[Collector]) -> type[Collector]:
    REGISTRY[cls.name] = cls
    return cls


class Collector(ABC):
    """One concern, one file, one cadence."""

    name: str
    interval: float  # seconds between runs
    # Entity kinds this collector is authoritative for. When it reports
    # entities, anything of these kinds it *didn't* report (within its host
    # scope) is marked inactive.
    owns_kinds: tuple[str, ...] = ()

    def applies_to(self, host: HostConfig) -> bool:
        """Override to skip hosts where this collector is meaningless."""
        return True

    @abstractmethod
    async def collect(
        self, transport: Transport, host: HostConfig, ctx: HostContext
    ) -> Observation:
        """Gather one observation. Prefer a single composite command."""
