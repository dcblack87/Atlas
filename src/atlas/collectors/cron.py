"""Cron: crontab inventory. Absence of an expected cron is a silent failure
mode — inventory makes it visible."""

from __future__ import annotations

from typing import TYPE_CHECKING

from atlas.collectors.base import Collector, register
from atlas.config import HostConfig
from atlas.model import Observation, Sample
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext


@register
class CronCollector(Collector):
    name = "cron"
    interval = 900

    async def collect(
        self, transport: Transport, host: HostConfig, ctx: HostContext
    ) -> Observation:
        result = await transport.run(["sh", "-c", "crontab -l 2>/dev/null"], timeout=15)
        return parse_crontab(result.stdout, host.name)


def parse_crontab(stdout: str, host_name: str) -> Observation:
    obs = Observation()
    entity = f"host:{host_name}"
    entries = [
        line.strip()
        for line in stdout.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    obs.samples.append(Sample("cron.entries", float(len(entries)), entity))
    obs.facts[(entity, "cron.entries")] = entries[:50]
    return obs
