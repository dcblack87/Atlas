"""Security posture: failed SSH auth, pending updates, reboot-required,
listening ports. Read-only observations; findings surface on the Security
screen and in the morning brief."""

from __future__ import annotations

from typing import TYPE_CHECKING

from atlas.collectors.base import Collector, register
from atlas.config import HostConfig
from atlas.model import Finding, Observation, Sample, Severity
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext

SENTINEL = "===ATLAS==="

_COMMAND = (
    # failed ssh auth attempts in the last hour (journald, fall back to auth.log)
    "(journalctl -u ssh -u sshd --since '-1h' 2>/dev/null || tail -5000 /var/log/auth.log"
    " 2>/dev/null) | grep -c 'Failed password\\|Invalid user' 2>/dev/null; "
    f"echo {SENTINEL}; "
    "cat /var/run/reboot-required 2>/dev/null | head -1; "
    f"echo {SENTINEL}; "
    "cat /var/lib/update-notifier/updates-available 2>/dev/null | grep -oE '^[0-9]+' | head -2; "
    f"echo {SENTINEL}; "
    "ss -tlnH 2>/dev/null | awk '{print $4}' | grep -vE '^(127\\.|\\[::1\\])' | "
    "grep -oE '[0-9]+$' | sort -un | tr '\\n' ' '"
)


@register
class SecurityCollector(Collector):
    name = "security"
    interval = 1800

    async def collect(
        self, transport: Transport, host: HostConfig, ctx: HostContext
    ) -> Observation:
        result = await transport.run(["sh", "-c", _COMMAND], timeout=30)
        return parse_security(result.stdout, host.name)


def parse_security(stdout: str, host_name: str) -> Observation:
    obs = Observation()
    entity = f"host:{host_name}"
    sections = [s.strip() for s in stdout.split(SENTINEL)]
    if len(sections) < 4:
        return obs
    failed_auth, reboot_required, updates, ports = sections[:4]

    if failed_auth.isdigit():
        count = int(failed_auth)
        obs.samples.append(Sample("security.failed_auth_1h", float(count), entity))
        if count > 200:
            obs.findings.append(
                Finding(
                    "ssh_bruteforce",
                    entity,
                    Severity.WARNING,
                    f"{count} failed SSH logins on {host_name} in the last hour",
                )
            )

    obs.facts[(entity, "security.reboot_required")] = bool(reboot_required)

    update_lines = updates.splitlines()
    if update_lines and update_lines[0].isdigit():
        obs.facts[(entity, "security.pending_updates")] = int(update_lines[0])
    if len(update_lines) > 1 and update_lines[1].isdigit():
        obs.facts[(entity, "security.pending_security_updates")] = int(update_lines[1])

    if ports:
        obs.facts[(entity, "security.public_ports")] = sorted(
            int(p) for p in ports.split() if p.isdigit()
        )
    return obs
