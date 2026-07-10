"""Post-deploy verification — runs regardless of the deploy's exit code.

Per app kind: containers running, health endpoints answering, and for
multi-site apps every discovered site individually.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from atlas.config import AppConfig, HostConfig
from atlas.transport.base import Transport

GRACE_S = 15
POLL_TIMEOUT_S = 180
POLL_INTERVAL_S = 10

_CURL = "curl -sS -o /dev/null -w '%{http_code}' --max-time 10"


@dataclass(slots=True)
class VerifyResult:
    passed: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)  # (name, ok, detail)

    def describe(self) -> str:
        lines = [f"{'✓' if ok else '✖'} {name}: {detail}" for name, ok, detail in self.checks]
        verdict = "VERIFICATION PASSED" if self.passed else "VERIFICATION FAILED"
        return "\n".join([*lines, verdict])


async def verify_deploy(
    transport: Transport,
    host: HostConfig,
    app_name: str,
    app: AppConfig,
    sites: list[dict],
) -> VerifyResult:
    """Poll until healthy or the timeout lapses; the last poll is the verdict."""
    await asyncio.sleep(GRACE_S)
    deadline = time.monotonic() + POLL_TIMEOUT_S
    result = VerifyResult(passed=False)
    while True:
        result = await _check_once(transport, host, app_name, app, sites)
        if result.passed or time.monotonic() > deadline:
            return result
        await asyncio.sleep(POLL_INTERVAL_S)


async def _check_once(
    transport: Transport,
    host: HostConfig,
    app_name: str,
    app: AppConfig,
    sites: list[dict],
) -> VerifyResult:
    checks: list[tuple[str, bool, str]] = []

    # container state
    if app.kind == "single-container" and app.container:
        state = await _container_state(transport, app.container)
        checks.append((f"container {app.container}", state == "running", state))
    elif app.kind == "compose":
        broken = await transport.run(
            [
                "sh",
                "-c",
                f"cd {app.path} && docker compose ps --format '{{{{.Name}}}}\\t{{{{.State}}}}' "
                f"| awk -F'\\t' '$2 != \"running\"'",
            ],
            timeout=30,
        )
        bad = broken.stdout.strip()
        checks.append(("compose services", broken.ok and not bad, bad or "all running"))
    elif app.kind == "multi-site":
        for site in sites:
            container = site["attrs"].get("container")
            if container:
                state = await _container_state(transport, container)
                checks.append((f"container {container}", state == "running", state))

    # HTTP probes
    probes: list[tuple[str, str]] = []
    if app.health_url:
        probes.append((f"health {app.health_url}", app.health_url))
    elif app.liveness_url:
        probes.append((f"liveness {app.liveness_url}", app.liveness_url))
    if app.kind == "multi-site":
        for site in sites:
            port = site["attrs"].get("port")
            if port:
                name = site["key"].split("/")[-1]
                probes.append((f"site {name}", f"http://127.0.0.1:{port}/"))
    for name, url in probes:
        code = await _http_code(transport, url)
        checks.append((name, code.startswith(("2", "3")), f"HTTP {code}"))

    return VerifyResult(passed=all(ok for _, ok, _ in checks), checks=checks)


async def _container_state(transport: Transport, container: str) -> str:
    result = await transport.run(
        ["sh", "-c", f"docker inspect --format '{{{{.State.Status}}}}' {container} 2>/dev/null"],
        timeout=15,
    )
    return result.stdout.strip() or "missing"


async def _http_code(transport: Transport, url: str) -> str:
    result = await transport.run(["sh", "-c", f"{_CURL} {url}"], timeout=15)
    return result.stdout.strip() or "000"
