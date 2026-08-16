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
#
# `-R`, not `-r`. This is the whole collector.
#
# `grep -r` follows a symlink only when it is named on the command line, never one it meets while
# walking a directory — and `sites-enabled/` is a directory of symlinks into `sites-available/`,
# which is the standard nginx convention and how most of this fleet is laid out. With `-r` the
# grep silently returns nothing for those vhosts: no output, no error, no facts, no incident. A
# certificate that is not discovered is not reported as missing, it is reported as nothing at all.
#
# Measured across the fleet before the change: ballcourt-prod 0 of 4 certificates found,
# quotelab-prod 2 of 7, directorylab-1 6 of 10 — including bookingmachine's and dcblack's. The
# `cert_expiry` rule (warn 21 days, crit 7) was therefore watching less than half of them, and on
# one host nothing at all.
_COMMAND = (
    "grep -RhE '^\\s*ssl_certificate ' /etc/nginx/sites-enabled/ 2>/dev/null | "
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
