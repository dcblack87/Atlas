"""Cron staleness state that has to outlive the process.

The journal only reaches back JOURNAL_WINDOW. Everything older is known
solely from what a previous run persisted, so these tests cover the seam
between the collector's memory and the facts table.
"""

from pathlib import Path

import pytest

from atlas.collectors.cron import CronCollector, CronJob
from atlas.engine.scheduler import HostContext
from atlas.store.db import Database
from atlas.store.inventory import Inventory

DAY = 86400.0


@pytest.fixture
async def ctx(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    await db.open()
    yield HostContext({}, Inventory(db)), Inventory(db)
    await db.close()


def nightly() -> CronJob:
    return CronJob(
        name="Database backups",
        slug="database-backups",
        schedule="0 3 * * *",
        command="/opt/bm/scripts/backup-db.sh auto >> /var/log/bm-backup.log 2>&1",
        source="crontab",
        user="root",
    )


async def test_dark_job_keeps_going_stale_after_a_restart(ctx) -> None:
    """A run we already observed must survive losing the in-memory state.

    Without seeding, a job that stopped firing before the journal window
    drops out of the facts entirely, freezing cron.overdue_ratio at the last
    healthy value it ever wrote — which is exactly the number cron_stale then
    goes on judging, forever.
    """
    import time

    host_ctx, inventory = ctx
    job = nightly()
    entity = "cron:quotelab-prod/database-backups"
    await inventory.set_fact(entity, "cron.last_run_ts", int(time.time() - 4 * DAY))

    collector = CronCollector()  # fresh process: no memory of any run
    await collector._seed_last_runs("quotelab-prod", [job], [], host_ctx)
    obs = collector._build_observation("quotelab-prod", [job], {}, {}, [])

    assert obs.facts[(entity, "cron.last_run_ts")] > 0
    assert obs.facts[(entity, "cron.overdue_ratio")] == pytest.approx(4.0, abs=0.1)


async def test_job_never_seen_running_stays_unknown(ctx) -> None:
    """Seeding restores observations; it must not invent one.

    Weekly and monthly jobs legitimately have no journal match, and coarse
    errors here may only ever delay an alert, never fabricate one.
    """
    host_ctx, _inventory = ctx
    job = nightly()

    collector = CronCollector()
    await collector._seed_last_runs("quotelab-prod", [job], [], host_ctx)
    obs = collector._build_observation("quotelab-prod", [job], {}, {}, [])

    entity = "cron:quotelab-prod/database-backups"
    assert (entity, "cron.overdue_ratio") not in obs.facts
    assert (entity, "cron.last_run_ts") not in obs.facts
    assert obs.findings == []


async def test_journal_wins_when_it_is_newer_than_the_stored_fact(ctx) -> None:
    import time

    host_ctx, inventory = ctx
    job = nightly()
    entity = "cron:quotelab-prod/database-backups"
    await inventory.set_fact(entity, "cron.last_run_ts", int(time.time() - 4 * DAY))

    collector = CronCollector()
    await collector._seed_last_runs("quotelab-prod", [job], [], host_ctx)
    fresh = time.time() - 3600
    obs = collector._build_observation("quotelab-prod", [job], {"database-backups": fresh}, {}, [])

    assert obs.facts[(entity, "cron.overdue_ratio")] < 1
