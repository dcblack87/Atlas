"""The scheduler: one asyncio loop per (host, collector) pair.

Failure posture: a collector failure backs off exponentially and surfaces as
a status event after repeated strikes; a transport-level ``HostUnreachable``
short-circuits every collector on that host into one host_down signal rather
than N collector errors.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from atlas.bus import Bus, CollectorStatus, FindingsEvent, InventoryChanged, SamplesEvent
from atlas.collectors import REGISTRY
from atlas.collectors.base import Collector
from atlas.config import AppConfig, Config, HostConfig
from atlas.model import Entity, EntityKind, Finding, Sample, Severity
from atlas.store.db import Database
from atlas.store.inventory import Inventory
from atlas.store.metrics import Metrics
from atlas.transport import LocalTransport, SSHTransport, Transport
from atlas.transport.base import HostUnreachable

log = logging.getLogger(__name__)

MAX_BACKOFF_S = 900
STRIKES_BEFORE_REPORT = 3


class HostContext:
    """What collectors may know about the world beyond their own command."""

    def __init__(self, apps: dict[str, AppConfig], inventory: Inventory) -> None:
        self.apps = apps
        self._inventory = inventory

    async def sites_for(self, app_name: str) -> list[dict]:
        return await self._inventory.entities(kind="site", parent=f"app:{app_name}")


class Scheduler:
    def __init__(self, config: Config, db: Database, bus: Bus) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.inventory = Inventory(db)
        self.metrics = Metrics(db)
        self._transports: dict[str, Transport] = {}
        self._tasks: list[asyncio.Task] = []
        self._host_down: dict[str, bool] = {}

    def transport_for(self, host: HostConfig) -> Transport:
        if host.name not in self._transports:
            if host.is_local:
                self._transports[host.name] = LocalTransport(host.name)
            else:
                self._transports[host.name] = SSHTransport(host.name, host.address, self.config.ssh)
        return self._transports[host.name]

    async def start(self) -> None:
        ctx = HostContext(self.config.apps, self.inventory)
        for host in self.config.hosts:
            await self.inventory.upsert(
                Entity(EntityKind.HOST, f"host:{host.name}", attrs={"address": host.address})
            )
            transport = self.transport_for(host)
            for cls in REGISTRY.values():
                collector = cls()
                if not collector.applies_to(host):
                    continue
                self._tasks.append(
                    asyncio.create_task(
                        self._loop(collector, transport, host, ctx),
                        name=f"collect:{host.name}:{collector.name}",
                    )
                )
        log.info("scheduler started: %d collector loops", len(self._tasks))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _loop(
        self, collector: Collector, transport: Transport, host: HostConfig, ctx: HostContext
    ) -> None:
        # Stagger startup so N collectors don't slam a host at t=0.
        await asyncio.sleep(random.uniform(0, min(collector.interval, 10)))
        strikes = 0
        while True:
            started = time.monotonic()
            try:
                obs = await collector.collect(transport, host, ctx)
                await self._store(collector, host, obs)
                if strikes >= STRIKES_BEFORE_REPORT or self._host_down.get(host.name):
                    self._host_down[host.name] = False
                    await self.bus.publish(CollectorStatus(host.name, collector.name, ok=True))
                strikes = 0
                sleep_s = collector.interval * random.uniform(0.9, 1.1)
            except HostUnreachable as e:
                strikes += 1
                if strikes >= STRIKES_BEFORE_REPORT and not self._host_down.get(host.name):
                    self._host_down[host.name] = True
                    await self._report_host_down(host, str(e))
                sleep_s = min(collector.interval * (2 ** min(strikes, 5)), MAX_BACKOFF_S)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                strikes += 1
                log.exception("%s failed on %s", collector.name, host.name)
                if strikes >= STRIKES_BEFORE_REPORT:
                    await self.bus.publish(
                        CollectorStatus(host.name, collector.name, ok=False, error=str(e))
                    )
                sleep_s = min(collector.interval * (2 ** min(strikes, 5)), MAX_BACKOFF_S)
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(sleep_s - elapsed, 1.0))

    async def _store(self, collector: Collector, host: HostConfig, obs) -> None:
        if obs.entities and collector.owns_kinds:
            parents = [f"host:{host.name}"] + [f"app:{a}" for a in host.apps]
            diff = await self.inventory.sync(parents, list(collector.owns_kinds), obs.entities)
            if diff.changed:
                await self.bus.publish(InventoryChanged(diff.added, diff.removed))
        if obs.samples:
            await self.metrics.write(obs.samples)
            await self.bus.publish(SamplesEvent(host.name, collector.name, obs.samples))
        for (entity_key, name), value in obs.facts.items():
            await self.inventory.set_fact(entity_key, name, value)
        if obs.findings:
            await self.bus.publish(FindingsEvent(host.name, collector.name, obs.findings))

    async def _report_host_down(self, host: HostConfig, reason: str) -> None:
        entity = f"host:{host.name}"
        await self.metrics.write([Sample("host.up", 0.0, entity)])
        await self.bus.publish(
            FindingsEvent(
                host.name,
                "transport",
                [
                    Finding(
                        "host_down",
                        entity,
                        Severity.CRITICAL,
                        f"{host.name} unreachable: {reason}",
                    )
                ],
            )
        )
