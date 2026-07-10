"""Capacity planning: how much headroom is left, and how many more
multi-tenant sites a host can take before it needs upgrading.

Grounded entirely in collected metrics — host memory/disk totals from the
system collector, per-container memory from docker stats — so the estimate
reflects what the fleet actually consumes, not a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.store.inventory import Inventory
from atlas.store.metrics import Metrics

MB = 1024 * 1024
# Leave this much RAM free for the OS, traffic spikes, and DB growth rather
# than packing a host to 100%.
RAM_BUFFER_MB = 500
# A site's on-disk footprint beyond its DB is dominated by uploads, which we
# don't meter per-site; assume a generous default so disk rarely false-limits.
DISK_PER_SITE_MB = 500


@dataclass(slots=True)
class HostHeadroom:
    host: str
    ram_total_mb: float
    ram_free_mb: float
    ram_used_pct: float
    disk_total_mb: float
    disk_free_mb: float
    disk_used_pct: float
    load_per_core: float | None


@dataclass(slots=True)
class SiteCapacity:
    current_sites: int
    avg_site_mb: float
    ram_free_mb: float
    disk_free_mb: float
    additional_sites: int
    bound_by: str  # "ram" | "disk" | "unknown"

    @property
    def known(self) -> bool:
        return self.avg_site_mb > 0


async def host_headroom(metrics: Metrics, host_name: str) -> HostHeadroom | None:
    snap = await metrics.latest_snapshot(f"host:{host_name}")
    total = snap.get("mem.total_bytes")
    used_pct = snap.get("mem.used_pct")
    if total is None or used_pct is None:
        return None
    ram_total_mb = total / MB
    ram_free_mb = ram_total_mb * (1 - used_pct / 100)
    disk_total_mb = (snap.get("disk.total_bytes") or 0) / MB
    disk_used_mb = (snap.get("disk.used_bytes") or 0) / MB
    return HostHeadroom(
        host=host_name,
        ram_total_mb=ram_total_mb,
        ram_free_mb=ram_free_mb,
        ram_used_pct=used_pct,
        disk_total_mb=disk_total_mb,
        disk_free_mb=disk_total_mb - disk_used_mb,
        disk_used_pct=snap.get("disk.used_pct") or 0,
        load_per_core=snap.get("cpu.load_per_core"),
    )


async def site_capacity(
    inventory: Inventory, metrics: Metrics, app_key: str, host_name: str
) -> SiteCapacity | None:
    """Estimate how many more sites a multi-tenant app's host can hold."""
    sites = await inventory.entities(kind="site", parent=app_key)
    if not sites:
        return None
    headroom = await host_headroom(metrics, host_name)
    if headroom is None:
        return None

    # Average per-site container memory from docker stats samples.
    site_mems: list[float] = []
    for site in sites:
        container = site["attrs"].get("container")
        if not container:
            continue
        mem = await metrics.latest(f"container:{host_name}/{container}", "container.mem_bytes")
        if mem:
            site_mems.append(mem / MB)
    avg_site_mb = sum(site_mems) / len(site_mems) if site_mems else 0.0

    if avg_site_mb <= 0:
        return SiteCapacity(
            len(sites), 0.0, headroom.ram_free_mb, headroom.disk_free_mb, 0, "unknown"
        )

    by_ram = int(max(0.0, headroom.ram_free_mb - RAM_BUFFER_MB) // avg_site_mb)
    by_disk = int(max(0.0, headroom.disk_free_mb - RAM_BUFFER_MB) // DISK_PER_SITE_MB)
    additional = min(by_ram, by_disk)
    bound_by = "ram" if by_ram <= by_disk else "disk"
    return SiteCapacity(
        current_sites=len(sites),
        avg_site_mb=avg_site_mb,
        ram_free_mb=headroom.ram_free_mb,
        disk_free_mb=headroom.disk_free_mb,
        additional_sites=additional,
        bound_by=bound_by,
    )
