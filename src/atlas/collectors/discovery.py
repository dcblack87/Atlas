"""Discovery — the zero-config magic.

Reads each app's shape from the machine itself: git shas, compose services,
per-site ports. New sites in multi-tenant apps appear in inventory within one
cycle with no config change; the diff lands on the timeline.
"""

from __future__ import annotations

import re
import shlex
from typing import TYPE_CHECKING

from atlas.collectors.base import Collector, register
from atlas.config import AppConfig, HostConfig
from atlas.model import Entity, EntityKind, Observation
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext


@register
class DiscoveryCollector(Collector):
    name = "discovery"
    interval = 600
    owns_kinds = ("app", "site")

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
            entity = f"app:{app_name}"
            obs.entities.append(
                Entity(
                    EntityKind.APP,
                    entity,
                    parent=f"host:{host.name}",
                    attrs={"kind": app.kind, "path": app.path},
                )
            )

            # Deployed git state — feeds preflight and (M5) drift detection.
            git = await transport.run(
                [
                    "sh",
                    "-c",
                    f"git -C {app.path} rev-parse HEAD 2>/dev/null; "
                    f"git -C {app.path} rev-parse --abbrev-ref HEAD 2>/dev/null; "
                    f"git -C {app.path} log -1 --format=%ct 2>/dev/null",
                ],
                timeout=15,
            )
            lines = git.stdout.split()
            if len(lines) >= 2:
                obs.facts[(entity, "git.sha")] = lines[0]
                obs.facts[(entity, "git.branch")] = lines[1]
            if len(lines) >= 3 and lines[2].isdigit():
                obs.facts[(entity, "git.committed_at")] = int(lines[2])

            if app.kind == "multi-site":
                obs.entities.extend(await self._discover_sites(transport, app_name, app))
        return obs

    async def _discover_sites(
        self, transport: Transport, app_name: str, app: AppConfig
    ) -> list[Entity]:
        """Read sites/<name>/.port files — the multi-site convention's source of truth."""
        result = await transport.run(
            [
                "sh",
                "-c",
                f"for f in {shlex.quote(str(app.sites_dir))}/*/.port; do "
                f"[ -f \"$f\" ] && printf '%s\\t%s\\n' "
                f'"$(basename "$(dirname "$f")")" "$(cat "$f")"; '
                f"done",
            ],
            timeout=15,
        )
        return parse_sites(result.stdout, app_name, app.container_prefix or "")


# A site's folder name becomes part of a container name and of later commands, and
# anything on the server that can create a folder chooses it. Only plain names count.
_SITE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


def parse_sites(stdout: str, app_name: str, container_prefix: str) -> list[Entity]:
    sites: list[Entity] = []
    for line in stdout.strip().splitlines():
        name, _, port = line.partition("\t")
        name, port = name.strip(), port.strip()
        if not _SITE_NAME.match(name) or not port.isdigit():
            continue
        sites.append(
            Entity(
                EntityKind.SITE,
                f"site:{app_name}/{name}",
                parent=f"app:{app_name}",
                attrs={"port": int(port), "container": f"{container_prefix}{name}"},
            )
        )
    return sites
