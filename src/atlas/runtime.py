"""Runtime — the non-UI core: database, bus, scheduler.

Owned by whatever front-end is running (TUI or headless); the TUI reads
through it but never reaches around it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from atlas.bus import Bus
from atlas.config import Config
from atlas.engine.scheduler import Scheduler
from atlas.store.db import Database
from atlas.store.inventory import Inventory
from atlas.store.metrics import Metrics

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    config: Config
    db: Database
    bus: Bus
    scheduler: Scheduler

    @property
    def inventory(self) -> Inventory:
        return self.scheduler.inventory

    @property
    def metrics(self) -> Metrics:
        return self.scheduler.metrics

    @classmethod
    async def start(cls, config: Config) -> Runtime:
        db = Database(config.atlas.db_path)
        await db.open()
        bus = Bus()
        scheduler = Scheduler(config, db, bus)
        await scheduler.start()
        return cls(config, db, bus, scheduler)

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.db.close()
