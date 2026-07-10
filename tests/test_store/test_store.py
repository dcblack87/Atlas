"""Store round-trips: migrations, inventory diffing, metric queries."""

from pathlib import Path

import pytest

from atlas.model import Entity, EntityKind, Sample
from atlas.store.db import Database
from atlas.store.inventory import Inventory
from atlas.store.metrics import Metrics


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    await database.open()
    yield database
    await database.close()


async def test_migrations_apply_once(db: Database) -> None:
    version = await db.fetch_value("PRAGMA user_version")
    assert version >= 1
    # reopening must be a no-op, not a failure
    await db.close()
    await db.open()
    assert await db.fetch_value("PRAGMA user_version") == version


async def test_inventory_sync_diffs(db: Database) -> None:
    inv = Inventory(db)
    parents, kinds = ["host:a"], ["container"]

    def container(name: str) -> Entity:
        return Entity(EntityKind.CONTAINER, f"container:a/{name}", parent="host:a")

    diff = await inv.sync(parents, kinds, [container("web"), container("db")])
    assert sorted(diff.added) == ["container:a/db", "container:a/web"]

    # steady state: no changes
    diff = await inv.sync(parents, kinds, [container("web"), container("db")])
    assert not diff.changed

    # one vanishes, one appears
    diff = await inv.sync(parents, kinds, [container("web"), container("cache")])
    assert diff.added == ["container:a/cache"]
    assert diff.removed == ["container:a/db"]

    active = await inv.entities(kind="container", parent="host:a")
    assert {e["key"] for e in active} == {"container:a/web", "container:a/cache"}


async def test_inventory_sync_scoped_by_kind(db: Database) -> None:
    """A docker sync must not deactivate discovery's app entities."""
    inv = Inventory(db)
    await inv.sync(["host:a"], ["app"], [Entity(EntityKind.APP, "app:x", parent="host:a")])
    diff = await inv.sync(
        ["host:a"], ["container"], [Entity(EntityKind.CONTAINER, "container:a/c", parent="host:a")]
    )
    assert diff.removed == []
    assert await inv.entities(kind="app") != []


async def test_facts_roundtrip(db: Database) -> None:
    inv = Inventory(db)
    await inv.set_fact("app:x", "git.sha", "abc123")
    await inv.set_fact("app:x", "health", {"status": "ok", "database": True})
    assert await inv.get_fact("app:x", "git.sha") == "abc123"
    facts = await inv.facts_for("app:x")
    assert facts["health"] == {"status": "ok", "database": True}


async def test_metrics_latest_and_series(db: Database) -> None:
    metrics = Metrics(db)
    for i, value in enumerate([10.0, 20.0, 30.0]):
        await metrics.write(
            [Sample("disk.used_pct", value, "host:a"), Sample("mem.used_pct", value / 2, "host:a")],
            ts=1000 + i * 60,
        )
    assert await metrics.latest("host:a", "disk.used_pct") == 30.0
    snap = await metrics.latest_snapshot("host:a")
    assert snap == {"disk.used_pct": 30.0, "mem.used_pct": 15.0}
    assert await metrics.last_n("host:a", "disk.used_pct", 2) == [20.0, 30.0]
