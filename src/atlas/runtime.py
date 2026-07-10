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

from atlas.ai.chat import ChatSession
from atlas.ai.client import AIClient
from atlas.ai.context import ContextBuilder
from atlas.ai.insights import InsightEngine
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
    ai: AIClient | None = None
    context: ContextBuilder | None = None
    insights: InsightEngine | None = None
    chat: ChatSession | None = None
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
        runtime._wire_ai(config, db, bus)
        runtime._tasks.append(asyncio.create_task(runtime._housekeeping(), name="housekeeping"))
        return runtime

    def _wire_ai(self, config: Config, db: Database, bus: Bus) -> None:
        if not (config.ai.enabled and config.ai.resolve_api_key()):
            log.info("AI layer disabled (no key or disabled in config)")
            return
        self.ai = AIClient(config.ai, db)
        self.context = ContextBuilder(db)
        self.insights = InsightEngine(self.ai, self.context, self.incidents.store, bus)
        self.insights.attach()
        self.chat = ChatSession(self.ai, self.context)

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
        """The slow heartbeat: incident sweep every minute; retention,
        forecasts, baselines, and cloud costs hourly; briefs when due."""
        import time as _time

        from atlas.cost.hcloud import collect_costs
        from atlas.engine.baseline import run_anomaly_detection
        from atlas.engine.forecast import run_forecasts
        from atlas.reports.briefs import due_brief, generate_brief

        last_hourly = 0.0
        # seed from the archive so a restart doesn't re-send today's brief
        last_daily_brief = float(
            await self.db.fetch_value(
                "SELECT COALESCE(MAX(ts), 0) FROM ai_analyses WHERE kind LIKE 'brief%'"
            )
            or 0
        )
        last_weekly_brief = float(
            await self.db.fetch_value(
                "SELECT COALESCE(MAX(ts), 0) FROM ai_analyses WHERE kind LIKE 'weekly_brief%'"
            )
            or 0
        )
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_S)
            try:
                await self.incidents.sweep()
                loop_time = asyncio.get_running_loop().time()
                if loop_time - last_hourly > RETENTION_INTERVAL_S:
                    last_hourly = loop_time
                    await run_retention(self.db)
                    await run_forecasts(self.db)
                    await run_anomaly_detection(self.db, self.bus)
                    if self.config is not None:
                        await collect_costs(self.config.hcloud, self.db)
                due = due_brief(_time.time(), last_daily_brief, last_weekly_brief)
                if due is not None:
                    body = await generate_brief(
                        self.db, self.ai, self.context, weekly=due == "weekly"
                    )
                    log.info("generated %s brief (%d chars)", due, len(body))
                    if due == "weekly":
                        last_weekly_brief = _time.time()
                    else:
                        last_daily_brief = _time.time()
            except Exception:
                log.exception("housekeeping pass failed")
