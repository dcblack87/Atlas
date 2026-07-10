"""System metrics: load, CPU count, memory, disk, uptime.

One composite command per run, sentinel-separated, parsed locally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from atlas.collectors.base import Collector, register
from atlas.config import HostConfig
from atlas.model import Observation, Sample
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext

SENTINEL = "===ATLAS==="

# Every path absolute; every section cheap. Sections:
#   loadavg | nproc | meminfo | df / | uptime-since
_COMMAND = (
    f"cat /proc/loadavg; echo {SENTINEL}; "
    f"nproc; echo {SENTINEL}; "
    f"grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo; echo {SENTINEL}; "
    f"df -B1 --output=target,size,used / /opt 2>/dev/null || df -B1 /; echo {SENTINEL}; "
    f"uptime -s"
)


@register
class SystemCollector(Collector):
    name = "system"
    interval = 60

    async def collect(
        self, transport: Transport, host: HostConfig, ctx: HostContext
    ) -> Observation:
        result = await transport.run(["sh", "-c", _COMMAND], timeout=15)
        return parse_system(result.stdout, host.name)


def parse_system(stdout: str, host_name: str) -> Observation:
    entity = f"host:{host_name}"
    obs = Observation()
    sections = [s.strip() for s in stdout.split(SENTINEL)]
    if len(sections) < 5:
        return obs

    loadavg, nproc, meminfo, df, uptime_since = sections[:5]

    # load
    parts = loadavg.split()
    cores = float(nproc or 1)
    if len(parts) >= 3:
        load1 = float(parts[0])
        obs.samples.append(Sample("load.1m", load1, entity))
        obs.samples.append(Sample("load.5m", float(parts[1]), entity))
        obs.samples.append(Sample("cpu.load_per_core", round(load1 / cores, 3), entity))
    obs.facts[(entity, "cpu.cores")] = int(cores)

    # memory
    mem: dict[str, int] = {}
    for line in meminfo.splitlines():
        name, _, rest = line.partition(":")
        mem[name.strip()] = int(rest.split()[0]) * 1024  # kB -> bytes
    if "MemTotal" in mem and "MemAvailable" in mem:
        total, available = mem["MemTotal"], mem["MemAvailable"]
        obs.samples.append(Sample("mem.total_bytes", total, entity))
        obs.samples.append(
            Sample("mem.used_pct", round(100 * (total - available) / total, 1), entity)
        )
    if mem.get("SwapTotal"):
        used = mem["SwapTotal"] - mem.get("SwapFree", 0)
        obs.samples.append(Sample("swap.used_pct", round(100 * used / mem["SwapTotal"], 1), entity))

    # disk — one sample set per mount, "/" reported unlabelled for rules
    for line in df.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 3 or not cols[0].startswith("/"):
            continue
        mount, size, used = cols[0], float(cols[1]), float(cols[2])
        pct = round(100 * used / size, 1) if size else 0.0
        if mount == "/":
            obs.samples.append(Sample("disk.used_pct", pct, entity))
            obs.samples.append(Sample("disk.used_bytes", used, entity))
            obs.samples.append(Sample("disk.total_bytes", size, entity))
        else:
            obs.samples.append(Sample("disk.used_pct", pct, entity, labels={"mount": mount}))

    obs.facts[(entity, "boot_time")] = uptime_since
    return obs
