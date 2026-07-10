"""PostgreSQL (containerised): database sizes and connection counts.

Finds postgres containers by image, then queries through ``docker exec``
using the container's own $POSTGRES_USER — no credentials in Atlas config.
Managed/remote databases are observed indirectly via app health endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from atlas.collectors.base import Collector, register
from atlas.config import HostConfig
from atlas.model import Observation, Sample
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext

_COMMAND = (
    "docker ps --format '{{.Names}}\\t{{.Image}}' | awk -F'\\t' '$2 ~ /postgres/ {print $1}' | "
    "while read -r c; do "
    'docker exec "$c" sh -c \''
    'psql -U "${POSTGRES_USER:-postgres}" -tA -c '
    '"SELECT datname, pg_database_size(datname) FROM pg_database WHERE NOT datistemplate" '
    "&& echo ---- && "
    'psql -U "${POSTGRES_USER:-postgres}" -tA -c '
    '"SELECT count(*) FROM pg_stat_activity"'
    '\' 2>/dev/null | sed "s|^|$c\\t|"; '
    "done"
)


@register
class PostgresCollector(Collector):
    name = "postgres"
    interval = 120

    async def collect(
        self, transport: Transport, host: HostConfig, ctx: HostContext
    ) -> Observation:
        result = await transport.run(["sh", "-c", _COMMAND], timeout=30)
        return parse_postgres(result.stdout, host.name)


def parse_postgres(stdout: str, host_name: str) -> Observation:
    obs = Observation()
    sections: dict[str, list[str]] = {}
    for line in stdout.strip().splitlines():
        container, _, payload = line.partition("\t")
        if container and payload:
            sections.setdefault(container, []).append(payload)

    for container, lines in sections.items():
        entity = f"db:{host_name}/{container}"
        in_sizes = True
        total = 0.0
        for payload in lines:
            if payload == "----":
                in_sizes = False
                continue
            if in_sizes and "|" in payload:
                name, _, size = payload.partition("|")
                try:
                    size_bytes = float(size)
                except ValueError:
                    continue
                total += size_bytes
                obs.samples.append(
                    Sample("db.size_bytes", size_bytes, entity, labels={"database": name})
                )
            elif not in_sizes and payload.isdigit():
                obs.samples.append(Sample("db.connections", float(payload), entity))
        if total:
            obs.samples.append(Sample("db.total_size_bytes", total, entity))
    return obs
