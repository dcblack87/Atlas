"""TLS certificates: expiry for every cert nginx actually serves."""

from __future__ import annotations

from typing import TYPE_CHECKING

from atlas.collectors.base import Collector, register
from atlas.config import HostConfig
from atlas.model import Observation
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext

# For each ssl_certificate path in enabled sites: "<path>\t<seconds-remaining>"
_COMMAND = (
    "grep -rhE '^\\s*ssl_certificate ' /etc/nginx/sites-enabled/ 2>/dev/null | "
    "awk '{print $2}' | tr -d ';' | sort -u | while read -r cert; do "
    '[ -f "$cert" ] || continue; '
    'end=$(openssl x509 -enddate -noout -in "$cert" 2>/dev/null | cut -d= -f2); '
    '[ -n "$end" ] || continue; '
    'end_s=$(date -d "$end" +%s 2>/dev/null) || continue; '
    "printf '%s\\t%s\\n' \"$cert\" $((end_s - $(date +%s))); "
    "done"
)


@register
class CertsCollector(Collector):
    name = "certs"
    interval = 6 * 3600

    async def collect(
        self, transport: Transport, host: HostConfig, ctx: HostContext
    ) -> Observation:
        result = await transport.run(["sh", "-c", _COMMAND], timeout=30)
        return parse_certs(result.stdout, host.name)


def parse_certs(stdout: str, host_name: str) -> Observation:
    obs = Observation()
    for line in stdout.strip().splitlines():
        path, _, seconds = line.partition("\t")
        try:
            remaining_s = int(seconds)
        except ValueError:
            continue
        name = path.strip().rsplit("/", 1)[-1]
        entity = f"cert:{host_name}/{name}"
        obs.facts[(entity, "cert.days_remaining")] = round(remaining_s / 86400, 1)
        obs.facts[(entity, "cert.path")] = path.strip()
    return obs
