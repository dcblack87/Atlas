"""HTTP liveness and health — the highest-signal, cheapest collector.

Health URLs are loopback-bound on the remote machines (apps sit behind
nginx), so checks run *on the host* via curl through the transport, not from
where Atlas happens to be running.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from atlas.collectors.base import Collector, register
from atlas.config import AppConfig, HostConfig
from atlas.model import Finding, Observation, Sample, Severity
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext

_CURL = "curl -sS -o /dev/null -w '%{http_code} %{time_total}' --max-time 10"
_CURL_BODY = "curl -sS --max-time 10"


@register
class HttpHealthCollector(Collector):
    name = "http_health"
    interval = 30

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
            await self._check_app(transport, host, app_name, app, ctx, obs)
        return obs

    async def _check_app(
        self,
        transport: Transport,
        host: HostConfig,
        app_name: str,
        app: AppConfig,
        ctx: HostContext,
        obs: Observation,
    ) -> None:
        entity = f"app:{app_name}"

        if app.kind == "multi-site":
            # Every discovered site gets its own liveness check.
            for site in await ctx.sites_for(app_name):
                port = site["attrs"].get("port")
                if not port:
                    continue
                url = f"http://127.0.0.1:{port}/"
                up, ms, code = await _probe(transport, url)
                obs.samples.append(Sample("http.up", 1.0 if up else 0.0, site["key"]))
                if up:
                    obs.samples.append(Sample("http.response_ms", ms, site["key"]))
                else:
                    obs.findings.append(
                        Finding(
                            "health_down",
                            site["key"],
                            Severity.CRITICAL,
                            f"site {site['key'].split('/')[-1]} not answering "
                            f"(HTTP {code}) on {host.name}",
                        )
                    )
            return

        url = app.liveness_url or app.health_url
        if url is None:
            return
        up, ms, code = await _probe(transport, url)
        obs.samples.append(Sample("http.up", 1.0 if up else 0.0, entity))
        if up:
            obs.samples.append(Sample("http.response_ms", ms, entity))
        else:
            obs.findings.append(
                Finding(
                    "health_down",
                    entity,
                    Severity.CRITICAL,
                    f"{app_name} not answering (HTTP {code}) on {host.name}",
                )
            )
            return

        # Structured health endpoints get their JSON recorded as facts.
        if app.health_url:
            body = await transport.run(["sh", "-c", f"{_CURL_BODY} {app.health_url}"], timeout=15)
            if body.ok:
                health = _parse_health_json(body.stdout)
                if health is not None:
                    obs.facts[(entity, "health")] = health
                    status = str(health.get("status", "")).lower()
                    if status and status not in ("ok", "healthy"):
                        obs.findings.append(
                            Finding(
                                "health_degraded",
                                entity,
                                Severity.WARNING,
                                f"{app_name} reports status={status}",
                                detail=health,
                            )
                        )


async def _probe(transport: Transport, url: str) -> tuple[bool, float, str]:
    """Returns (is_up, response_ms, http_code). 2xx/3xx counts as up."""
    result = await transport.run(["sh", "-c", f"{_CURL} {url}"], timeout=15)
    parts = result.stdout.split()
    if not result.ok or len(parts) < 2:
        return False, 0.0, "000"
    code, time_total = parts[0], parts[1]
    up = code.startswith(("2", "3"))
    return up, round(float(time_total) * 1000, 1), code


def _parse_health_json(body: str) -> dict | None:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict):
        # BookingMachine wraps its payload in {"data": {...}}.
        inner = data.get("data")
        return inner if isinstance(inner, dict) else data
    return None
