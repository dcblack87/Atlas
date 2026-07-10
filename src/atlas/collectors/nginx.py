"""Nginx: config validity and vhost inventory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from atlas.collectors.base import Collector, register
from atlas.config import HostConfig
from atlas.model import Entity, EntityKind, Finding, Observation, Severity
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext

_COMMAND = (
    "nginx -t 2>&1; echo ===ATLAS===; "
    "grep -rhE '^\\s*server_name' /etc/nginx/sites-enabled/ 2>/dev/null | "
    "tr -s ' \\t;' ' ' | sed 's/^ *server_name //'"
)


@register
class NginxCollector(Collector):
    name = "nginx"
    interval = 900
    owns_kinds = ("vhost",)

    async def collect(
        self, transport: Transport, host: HostConfig, ctx: HostContext
    ) -> Observation:
        result = await transport.run(["sh", "-c", _COMMAND], timeout=20)
        if "nginx" not in result.stdout and not result.ok:
            return Observation()  # no nginx on this host — nothing to report
        return parse_nginx(result.stdout, host.name)


def parse_nginx(stdout: str, host_name: str) -> Observation:
    obs = Observation()
    host_entity = f"host:{host_name}"
    test_output, _, names_output = stdout.partition("===ATLAS===")

    config_ok = "test is successful" in test_output
    obs.facts[(host_entity, "nginx.config_ok")] = config_ok
    if "test failed" in test_output:
        obs.findings.append(
            Finding(
                "nginx_config_broken",
                host_entity,
                Severity.CRITICAL,
                f"nginx config test fails on {host_name}",
                detail={"output": test_output.strip()[-500:]},
            )
        )

    seen: set[str] = set()
    for line in names_output.strip().splitlines():
        for domain in line.split():
            if domain in ("_", "localhost") or domain in seen:
                continue
            seen.add(domain)
            obs.entities.append(
                Entity(
                    EntityKind.VHOST,
                    f"vhost:{host_name}/{domain}",
                    parent=host_entity,
                    attrs={"domain": domain},
                )
            )
    return obs
