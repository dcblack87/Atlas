"""Demo mode: the public-repo front door must always work."""

from atlas.demo.dataset import seed_demo
from atlas.runtime import Runtime
from atlas.store.db import Database


async def test_demo_seed(tmp_path) -> None:
    db = Database(tmp_path / "demo.db")
    await db.open()
    await seed_demo(db)
    hosts = await db.fetch_value("SELECT COUNT(*) FROM entities WHERE kind='host'")
    sites = await db.fetch_value("SELECT COUNT(*) FROM entities WHERE kind='site'")
    raw = await db.fetch_value("SELECT COUNT(*) FROM metrics_raw")
    open_incidents = await db.fetch_value(
        "SELECT COUNT(*) FROM incidents WHERE status != 'resolved'"
    )
    assert hosts == 3
    assert sites == 3
    assert raw > 1000  # a day of history for sparklines
    assert open_incidents == 1  # the expiring-cert story
    await db.close()


async def test_demo_runtime_starts_and_stops() -> None:
    runtime = await Runtime.demo()
    assert runtime.scheduler is None  # never collects anything real
    snap = await runtime.metrics.latest_snapshot("host:web-1")
    assert "load.1m" in snap
    await runtime.stop()
