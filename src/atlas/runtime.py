"""Runtime — the non-UI core: database, bus, engine, scheduler.

Owned by whatever front-end is running (TUI or headless); the TUI reads
through it but never reaches around it. Demo mode is the same runtime minus
the scheduler, over a seeded throwaway database.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from atlas.bus import Bus
from atlas.config import Config
from atlas.deploy.orchestrator import DeployOrchestrator
from atlas.engine.incidents import SWEEP_INTERVAL_S, IncidentManager
from atlas.engine.scheduler import Scheduler
from atlas.notify.telegram import TelegramNotifier
from atlas.store.db import Database
from atlas.store.inventory import Inventory
from atlas.store.metrics import Metrics
from atlas.store.retention import run_retention

log = logging.getLogger(__name__)

RETENTION_INTERVAL_S = 3600


@dataclass
class Runtime:
    config: Config | None
    db: Database
    bus: Bus
    inventory: Inventory
    metrics: Metrics
    incidents: IncidentManager
    scheduler: Scheduler | None = None
    notifier: TelegramNotifier | None = None
    deployer: DeployOrchestrator | None = None
    _tasks: list[asyncio.Task] = field(default_factory=list)

    @classmethod
    async def start(cls, config: Config) -> Runtime:
        db = Database(config.atlas.db_path)
        await db.open()
        bus = Bus()
        incidents = IncidentManager(db, bus)
        incidents.attach()
        notifier = TelegramNotifier(config.telegram, db, bus)
        notifier.attach()
        scheduler = Scheduler(config, db, bus)
        await scheduler.start()
        deployer = DeployOrchestrator(config, db, bus, scheduler.transport_for, incidents)
        runtime = cls(
            config, db, bus, Inventory(db), Metrics(db), incidents, scheduler, notifier, deployer
        )
        runtime._tasks.append(asyncio.create_task(runtime._housekeeping(), name="housekeeping"))
        return runtime

    @classmethod
    async def demo(cls) -> Runtime:
        """A seeded fleet, no SSH, no secrets — the thirty-second tour."""
        from atlas.demo.dataset import seed_demo

        db_path = Path(tempfile.mkdtemp(prefix="atlas-demo-")) / "demo.db"
        db = Database(db_path)
        await db.open()
        await seed_demo(db)
        bus = Bus()
        incidents = IncidentManager(db, bus)
        incidents.attach()
        return cls(None, db, bus, Inventory(db), Metrics(db), incidents)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.scheduler is not None:
            await self.scheduler.stop()
        await self.db.close()

    async def _housekeeping(self) -> None:
        """Sweep fact rules + stale assertions every minute; retention hourly."""
        last_retention = 0.0
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_S)
            try:
                await self.incidents.sweep()
                loop_time = asyncio.get_running_loop().time()
                if loop_time - last_retention > RETENTION_INTERVAL_S:
                    last_retention = loop_time
                    await run_retention(self.db)
            except Exception:
                log.exception("housekeeping pass failed")
