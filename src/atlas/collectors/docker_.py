"""Docker: container inventory, state, restart counts, and (periodic) stats.

`docker ps` runs every cycle; `docker stats --no-stream` is expensive, so it
runs only every Nth cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from atlas.collectors.base import Collector, register
from atlas.config import HostConfig
from atlas.model import Entity, EntityKind, Finding, Observation, Sample, Severity
from atlas.transport.base import Transport

if TYPE_CHECKING:
    from atlas.engine.scheduler import HostContext

STATS_EVERY_N_RUNS = 5

_PS_CMD = (
    "docker ps -a --no-trunc --format "
    "'{{.Names}}\\t{{.State}}\\t{{.Status}}\\t{{.Image}}\\t{{.Ports}}'"
    " && echo ===RESTARTS=== && "
    "docker ps -a --format '{{.Names}}' | xargs -r docker inspect "
    "--format '{{.Name}}\\t{{.RestartCount}}\\t{{.State.Health.Status}}'"
)

_STATS_CMD = (
    "docker stats --no-stream --format '{{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}\\t{{.MemPerc}}'"
)


@register
class DockerCollector(Collector):
    name = "docker"
    interval = 60
    owns_kinds = ("container",)

    def __init__(self) -> None:
        self._runs = 0

    async def collect(
        self, transport: Transport, host: HostConfig, ctx: HostContext
    ) -> Observation:
        result = await transport.run(["sh", "-c", _PS_CMD], timeout=20)
        if not result.ok and "Cannot connect" in result.stderr + result.stdout:
            return Observation(
                findings=[
                    Finding(
                        "docker_daemon_down",
                        f"host:{host.name}",
                        Severity.CRITICAL,
                        f"Docker daemon unreachable on {host.name}",
                    )
                ]
            )
        obs = parse_docker_ps(result.stdout, host.name)

        self._runs += 1
        if self._runs % STATS_EVERY_N_RUNS == 1:  # first run and every Nth after
            stats = await transport.run(["sh", "-c", _STATS_CMD], timeout=30)
            if stats.ok:
                obs.samples.extend(parse_docker_stats(stats.stdout, host.name))
        return obs


def container_key(host_name: str, container: str) -> str:
    return f"container:{host_name}/{container}"


def parse_docker_ps(stdout: str, host_name: str) -> Observation:
    obs = Observation()
    host_entity = f"host:{host_name}"
    ps_part, _, inspect_part = stdout.partition("===RESTARTS===")

    restarts: dict[str, tuple[int, str]] = {}
    for line in inspect_part.strip().splitlines():
        cols = line.split("\t")
        if len(cols) >= 2:
            name = cols[0].lstrip("/")
            health = cols[2] if len(cols) > 2 else ""
            try:
                restarts[name] = (int(cols[1]), health)
            except ValueError:
                continue

    running = 0
    for line in ps_part.strip().splitlines():
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        name, state, status, image = cols[0], cols[1], cols[2], cols[3]
        key = container_key(host_name, name)
        restart_count, health = restarts.get(name, (0, ""))
        obs.entities.append(
            Entity(
                EntityKind.CONTAINER,
                key,
                parent=host_entity,
                attrs={"image": image, "state": state, "status": status, "health": health},
            )
        )
        obs.samples.append(Sample("container.up", 1.0 if state == "running" else 0.0, key))
        obs.samples.append(Sample("container.restarts", float(restart_count), key))
        obs.facts[(key, "state")] = state
        obs.facts[(key, "health")] = health or None
        if state == "running":
            running += 1
        if state == "restarting":
            obs.findings.append(
                Finding(
                    "container_restarting",
                    key,
                    Severity.CRITICAL,
                    f"{name} is stuck restarting on {host_name}",
                    detail={"status": status},
                )
            )
        if health == "unhealthy":
            obs.findings.append(
                Finding(
                    "container_unhealthy",
                    key,
                    Severity.WARNING,
                    f"{name} reports unhealthy on {host_name}",
                )
            )

    obs.samples.append(Sample("docker.running", float(running), host_entity))
    return obs


def parse_docker_stats(stdout: str, host_name: str) -> list[Sample]:
    samples: list[Sample] = []
    for line in stdout.strip().splitlines():
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        key = container_key(host_name, cols[0])
        try:
            samples.append(Sample("container.cpu_pct", float(cols[1].rstrip("%")), key))
            samples.append(Sample("container.mem_pct", float(cols[3].rstrip("%")), key))
            mem_used = cols[2].split("/")[0].strip()
            samples.append(Sample("container.mem_bytes", _parse_size(mem_used), key))
        except ValueError:
            continue
    return samples


_UNITS = {
    "B": 1,
    "KiB": 2**10,
    "MiB": 2**20,
    "GiB": 2**30,
    "TiB": 2**40,
    "kB": 10**3,
    "MB": 10**6,
    "GB": 10**9,
    "TB": 10**12,
}


def _parse_size(text: str) -> float:
    for unit, factor in sorted(_UNITS.items(), key=lambda kv: -len(kv[0])):
        if text.endswith(unit):
            return float(text.removesuffix(unit)) * factor
    return float(text)
