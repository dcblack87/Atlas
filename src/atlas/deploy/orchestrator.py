"""The deploy orchestrator — the ONLY module allowed to mutate a server.

Every mutation flows through here: app deploys, guided rollbacks, and
allowlisted remediations. The gate is uniform — an explicit typed
confirmation phrase, a fleet-wide lock, a hard timeout, streamed output,
post-run verification, and an audit row. An invariant test asserts no other
module constructs a mutating command.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from atlas.bus import Bus, DeployEvent
from atlas.config import AppConfig, Config, HostConfig
from atlas.deploy.verify import verify_deploy
from atlas.engine.incidents import IncidentManager
from atlas.store.audit import DeploymentStore
from atlas.store.db import Database
from atlas.store.inventory import Inventory
from atlas.transport.base import Transport

log = logging.getLogger(__name__)

SUPPRESS_EXTRA_S = 120


class DeployError(Exception):
    pass


@dataclass(slots=True)
class Preflight:
    app: str
    host: str
    path: str
    command: str
    deployed_sha: str | None
    remote_sha: str | None
    open_incidents: list[str] = field(default_factory=list)

    @property
    def up_to_date(self) -> bool | None:
        if self.deployed_sha is None or self.remote_sha is None:
            return None
        return self.deployed_sha == self.remote_sha


class DeployOrchestrator:
    def __init__(
        self,
        config: Config,
        db: Database,
        bus: Bus,
        transport_for: Callable[[HostConfig], Transport],
        incidents: IncidentManager,
    ) -> None:
        self._config = config
        self._bus = bus
        self._transport_for = transport_for
        self._incidents = incidents
        self._inventory = Inventory(db)
        self._audit = DeploymentStore(db)
        self._lock = asyncio.Lock()  # one mutation at a time, fleet-wide

    @property
    def audit(self) -> DeploymentStore:
        return self._audit

    def _resolve(self, app_name: str) -> tuple[HostConfig, AppConfig]:
        host = self._config.host_for_app(app_name)
        app = self._config.apps.get(app_name)
        if host is None or app is None:
            raise DeployError(f"unknown app {app_name!r}")
        return host, app

    # ── preflight (read-only) ────────────────────────────────────────

    async def preflight(self, app_name: str) -> Preflight:
        host, app = self._resolve(app_name)
        transport = self._transport_for(host)
        deployed = await self._deployed_sha(transport, app)
        remote = await self._remote_sha(transport, app)
        open_incidents = [
            i["title"]
            for i in await self._incidents.store.open_incidents()
            if i["entity_key"].startswith((f"app:{app_name}", f"site:{app_name}"))
        ]
        return Preflight(
            app=app_name,
            host=host.name,
            path=app.path,
            command=app.deploy_command,
            deployed_sha=deployed,
            remote_sha=remote,
            open_incidents=open_incidents,
        )

    async def _deployed_sha(self, transport: Transport, app: AppConfig) -> str | None:
        result = await transport.run(
            ["sh", "-c", f"git -C {app.path} rev-parse HEAD 2>/dev/null"], timeout=15
        )
        sha = result.stdout.strip()
        return sha if len(sha) == 40 else None

    async def _remote_sha(self, transport: Transport, app: AppConfig) -> str | None:
        result = await transport.run(
            ["sh", "-c", f"git -C {app.path} ls-remote origin HEAD 2>/dev/null | cut -f1"],
            timeout=30,
        )
        sha = result.stdout.strip()
        return sha if len(sha) == 40 else None

    # ── execution (the gate) ─────────────────────────────────────────

    async def deploy(
        self, app_name: str, confirmed_phrase: str, *, checkout_sha: str | None = None
    ) -> AsyncIterator[str]:
        """Run the app's deploy command, streaming output lines.

        ``confirmed_phrase`` must equal the app name — the UI enforces it
        interactively, this enforces it structurally. ``checkout_sha`` is
        the guided-rollback path: check out a specific commit first.
        """
        if not self._config.deploy.enabled:
            raise DeployError("deploys are disabled in config")
        if confirmed_phrase != app_name:
            raise DeployError("confirmation phrase does not match the app name")
        host, app = self._resolve(app_name)

        command = f"cd {app.path} && {app.deploy_command}"
        if checkout_sha is not None:
            if not _is_sha(checkout_sha):
                raise DeployError(f"not a git sha: {checkout_sha!r}")
            command = f"cd {app.path} && git checkout {checkout_sha} && {app.deploy_command}"

        async for line in self._execute(host, app, app_name, command, confirmed_phrase):
            yield line

    async def _execute(
        self,
        host: HostConfig,
        app: AppConfig,
        app_name: str,
        command: str,
        confirmed_phrase: str,
    ) -> AsyncIterator[str]:
        transport = self._transport_for(host)
        timeout = self._config.deploy.timeout_seconds

        async with self._lock:
            sha_before = await self._deployed_sha(transport, app)
            deployment_id = await self._audit.start(
                app_name, host.name, command, sha_before, confirmed_phrase
            )
            # Don't page yourself for your own deploy bouncing health checks.
            self._incidents.suppress(f"app:{app_name}", timeout + SUPPRESS_EXTRA_S)
            self._incidents.suppress(f"site:{app_name}", timeout + SUPPRESS_EXTRA_S)
            await self._bus.publish(DeployEvent(deployment_id, app_name, "started", command))

            output_lines: list[str] = []
            exit_code: int | None = None
            started = time.monotonic()
            try:
                async for line in transport.stream(["sh", "-lc", command], timeout=timeout):
                    output_lines.append(line)
                    await self._bus.publish(DeployEvent(deployment_id, app_name, "line", line))
                    yield line
                exit_code = 0
            except TimeoutError:
                output_lines.append(f"✖ deploy timed out after {timeout:.0f}s — killed")
                yield output_lines[-1]
            except Exception as e:
                output_lines.append(f"✖ deploy failed: {e}")
                yield output_lines[-1]

            duration = time.monotonic() - started
            yield f"— finished in {duration:.0f}s, verifying —"

            sites = await self._inventory.entities(kind="site", parent=f"app:{app_name}")
            result = await verify_deploy(transport, host, app_name, app, sites)
            for check_line in result.describe().splitlines():
                yield check_line

            sha_after = await self._deployed_sha(transport, app)
            verify_status = "passed" if result.passed else "failed"
            await self._audit.finish(
                deployment_id,
                exit_code=exit_code,
                sha_after=sha_after,
                output="\n".join(output_lines),
                verify_status=verify_status,
            )
            await self._incidents.store.add_event(
                None,
                "deploy",
                f"deployed {app_name} "
                f"{_short(sha_before)} → {_short(sha_after)} "
                f"{'✓' if result.passed else '✖ verification failed'}",
            )
            await self._bus.publish(DeployEvent(deployment_id, app_name, "verified", verify_status))
            if not result.passed:
                from atlas.model import Finding, Severity

                await self._incidents.raise_finding(
                    Finding(
                        "deploy_verification_failed",
                        f"app:{app_name}",
                        Severity.CRITICAL,
                        f"deploy of {app_name} failed verification",
                        detail={"deployment_id": deployment_id},
                    ),
                    ignore_suppression=True,  # a failed deploy must page even in its own window
                )

    # ── remediations (same gate) ─────────────────────────────────────

    async def remediate(
        self, host_name: str, template: str, params: dict[str, str], confirmed_phrase: str
    ) -> AsyncIterator[str]:
        """Run an allowlisted remediation. The phrase must equal the host name."""
        if template not in self._config.deploy.remediations:
            raise DeployError(f"remediation not in allowlist: {template!r}")
        if confirmed_phrase != host_name:
            raise DeployError("confirmation phrase does not match the host name")
        host = next((h for h in self._config.hosts if h.name == host_name), None)
        if host is None:
            raise DeployError(f"unknown host {host_name!r}")
        safe_params = {k: v for k, v in params.items() if _is_safe_param(v)}
        try:
            command = template.format(**safe_params)
        except KeyError as e:
            raise DeployError(f"missing remediation parameter: {e}") from e

        transport = self._transport_for(host)
        async with self._lock:
            deployment_id = await self._audit.start(
                f"remediation:{template}", host_name, command, None, confirmed_phrase
            )
            output: list[str] = []
            try:
                async for line in transport.stream(["sh", "-c", command], timeout=300):
                    output.append(line)
                    yield line
                exit_code = 0
            except Exception as e:
                output.append(f"✖ remediation failed: {e}")
                yield output[-1]
                exit_code = 1
            await self._audit.finish(
                deployment_id,
                exit_code=exit_code,
                sha_after=None,
                output="\n".join(output),
                verify_status="skipped",
            )
            await self._incidents.store.add_event(
                None, "note", f"remediation on {host_name}: {command}"
            )


def _short(sha: str | None) -> str:
    return sha[:7] if sha else "unknown"


def _is_sha(text: str) -> bool:
    return 7 <= len(text) <= 40 and all(c in "0123456789abcdef" for c in text.lower())


def _is_safe_param(value: str) -> bool:
    """Remediation params come from inventory, but never trust a string that
    could escape into the shell."""
    return bool(value) and all(c.isalnum() or c in "-_./:" for c in value)
