"""Backups: freshness and size of the newest artefact per app.

Convention over configuration: every managed app keeps dumps under
``<path>/backups`` (possibly nested, e.g. daily/weekly/monthly).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from atlas.collectors.base import Collector, register
from atlas.config import HostConfig
from atlas.model import Observation, Sample
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext


@register
class BackupsCollector(Collector):
    name = "backups"
    interval = 900

    def applies_to(self, host: HostConfig) -> bool:
        return bool(host.apps)

    async def collect(
        self, transport: Transport, host: HostConfig, ctx: HostContext
    ) -> Observation:
        obs = Observation()
        for app_name in host.apps:
            app = ctx.apps.get(app_name)
            if app is None:
                continue
            backups_path = app.backups_path or f"{app.path}/backups"
            result = await transport.run(
                [
                    "sh",
                    "-c",
                    f"find {backups_path} -type f "
                    f"\\( -name '*.gz' -o -name '*.dump' -o -name '*.sql' -o -name '*.enc' \\) "
                    f"-printf '%T@ %s\\n' 2>/dev/null | sort -rn | head -1",
                ],
                timeout=20,
            )
            parsed = parse_newest_backup(result.stdout)
            if parsed is None:
                continue
            mtime, size = parsed
            entity = f"app:{app_name}"
            age_hours = round((time.time() - mtime) / 3600, 1)
            obs.facts[(entity, "backup.age_hours")] = age_hours
            obs.facts[(entity, "backup.last_ts")] = int(mtime)
            obs.samples.append(Sample("backup.size_bytes", size, entity))
        return obs


def parse_newest_backup(stdout: str) -> tuple[float, float] | None:
    line = stdout.strip().splitlines()[0] if stdout.strip() else ""
    parts = line.split()
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None
