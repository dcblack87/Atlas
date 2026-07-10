"""Capacity planning from real metrics."""

from pathlib import Path

import pytest

from atlas.engine.capacity import host_headroom, site_capacity
from atlas.model import Entity, EntityKind, Sample
from atlas.store.db import Database
from atlas.store.inventory import Inventory
from atlas.store.metrics import Metrics

MB = 1024 * 1024
GB = 1024 * MB


@pytest.fixture
async def env(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    await db.open()
    yield Inventory(db), Metrics(db)
    await db.close()


async def test_host_headroom(env) -> None:
    inv, metrics = env
    await metrics.write(
        [
            Sample("mem.total_bytes", 4 * GB, "host:a"),
            Sample("mem.used_pct", 50.0, "host:a"),
            Sample("disk.total_bytes", 80 * GB, "host:a"),
            Sample("disk.used_bytes", 8 * GB, "host:a"),
            Sample("disk.used_pct", 10.0, "host:a"),
        ]
    )
    h = await host_headroom(metrics, "a")
    assert h is not None
    assert h.ram_free_mb == pytest.approx(2048, abs=1)  # 50% of 4GB
    assert h.disk_free_mb == pytest.approx(72 * 1024, abs=1)


async def test_no_data_returns_none(env) -> None:
    inv, metrics = env
    assert await host_headroom(metrics, "a") is None


async def test_site_capacity_ram_bound(env) -> None:
    inv, metrics = env
    # a host like directorylab-1: ~2GB free, ~350MB/site
    await metrics.write(
        [
            Sample("mem.total_bytes", 4 * GB, "host:a"),
            Sample("mem.used_pct", 50.0, "host:a"),  # 2GB free
            Sample("disk.total_bytes", 80 * GB, "host:a"),
            Sample("disk.used_bytes", 8 * GB, "host:a"),  # 72GB free
        ]
    )
    await inv.upsert(Entity(EntityKind.APP, "app:sitefarm", parent="host:a"))
    for i, mem_mb in enumerate((350, 350, 350)):
        name = f"s{i}"
        await inv.upsert(
            Entity(
                EntityKind.SITE,
                f"site:sitefarm/{name}",
                parent="app:sitefarm",
                attrs={"port": 5001 + i, "container": f"sitefarm-{name}"},
            )
        )
        await metrics.write(
            [Sample("container.mem_bytes", mem_mb * MB, f"container:a/sitefarm-{name}")]
        )

    cap = await site_capacity(inv, metrics, "app:sitefarm", "a")
    assert cap is not None and cap.known
    assert cap.current_sites == 3
    assert cap.avg_site_mb == pytest.approx(350, abs=1)
    # (2048 - 500 buffer) / 350 = 4.4 -> 4 more, RAM-bound (disk has tons)
    assert cap.additional_sites == 4
    assert cap.bound_by == "ram"


async def test_site_capacity_unknown_without_container_mem(env) -> None:
    inv, metrics = env
    await metrics.write(
        [Sample("mem.total_bytes", 4 * GB, "host:a"), Sample("mem.used_pct", 50.0, "host:a")]
    )
    await inv.upsert(
        Entity(
            EntityKind.SITE,
            "site:sitefarm/s0",
            parent="app:sitefarm",
            attrs={"container": "sitefarm-s0"},
        )
    )
    cap = await site_capacity(inv, metrics, "app:sitefarm", "a")
    assert cap is not None and not cap.known  # no stats yet
