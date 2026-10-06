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
import shlex
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from atlas.bus import Bus, DeployEvent
from atlas.config import AppConfig, Config, HostConfig
from atlas.deploy.verify import verify_deploy
from atlas.engine.incidents import IncidentManager
from atlas.redact import scrub_secrets
from atlas.store.audit import DeploymentStore
from atlas.store.db import Database
from atlas.store.inventory import Inventory
from atlas.transport.base import CommandFailed, Transport

log = logging.getLogger(__name__)

SUPPRESS_EXTRA_S = 120
# Recorded when the hard timeout kills a deploy (the coreutils `timeout` convention).
EXIT_TIMEOUT = 124
# flock's exit status when another mutation already holds the host lock.
EXIT_LOCKED = 75
HOST_LOCK_DIR = "/run/lock"
HOST_LOCK_NAME = "atlas-mutation.lock"


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
        # One mutation at a time from this process. A second Atlas instance
        # (a laptop beside the always-on console) has its own; what keeps the
        # two apart is the lock taken on the target host, see _host_locked.
        self._lock = asyncio.Lock()

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
                async for line in transport.stream(
                    _host_locked(command, login=True), timeout=timeout
                ):
                    output_lines.append(line)
                    await self._bus.publish(DeployEvent(deployment_id, app_name, "line", line))
                    yield line
                exit_code = 0
            except CommandFailed as e:
                exit_code = e.exit_code
                output_lines.append(f"✖ deploy command exited {e.exit_code}")
                if e.exit_code == EXIT_LOCKED:
                    output_lines[-1] += f" — {_LOCK_HELD_HINT.format(host=host.name)}"
                yield output_lines[-1]
            except TimeoutError:
                exit_code = EXIT_TIMEOUT
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
            if exit_code != 0 and result.passed:
                # Health checks pass against whatever is running. A deploy that
                # died before restarting anything leaves the old version up.
                yield (
                    f"⚠ the deploy command {_describe_exit(exit_code)} but verification "
                    "passed — the previous version may still be serving"
                )

            sha_after = await self._deployed_sha(transport, app)
            verify_status = "passed" if result.passed else "failed"
            outcome = "✓" if result.passed else "✖ verification failed"
            if exit_code != 0:
                outcome += f" · deploy command {_describe_exit(exit_code)}"
            await self._audit.finish(
                deployment_id,
                exit_code=exit_code,
                sha_after=sha_after,
                # Deploy scripts echo what they install, crontab lines and
                # their tokens included. The live stream is yours to read; the
                # stored copy is not the place to keep credentials.
                output=scrub_secrets("\n".join(output_lines)),
                verify_status=verify_status,
            )
            await self._incidents.store.add_event(
                None,
                "deploy",
                f"deployed {app_name} {_short(sha_before)} → {_short(sha_after)} {outcome}",
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
                async for line in transport.stream(_host_locked(command, login=False), timeout=300):
                    output.append(line)
                    yield line
                exit_code = 0
            except CommandFailed as e:
                output.append(f"✖ remediation exited {e.exit_code}")
                if e.exit_code == EXIT_LOCKED:
                    output[-1] += f" — {_LOCK_HELD_HINT.format(host=host_name)}"
                yield output[-1]
                exit_code = e.exit_code
            except Exception as e:
                output.append(f"✖ remediation failed: {e}")
                yield output[-1]
                exit_code = 1
            await self._audit.finish(
                deployment_id,
                exit_code=exit_code,
                sha_after=None,
                output=scrub_secrets("\n".join(output)),
                verify_status="skipped",
            )
            await self._incidents.store.add_event(
                None, "note", f"remediation on {host_name}: {command}"
            )


def _short(sha: str | None) -> str:
    return sha[:7] if sha else "unknown"


_LOCK_HELD_HINT = "another mutation holds the lock on {host}, so nothing was run"


def _host_locked(command: str, *, login: bool, lock_dir: str = HOST_LOCK_DIR) -> list[str]:
    """Wrap a mutating command so only one runs per host at a time.

    The lock lives on the target host, so it holds across Atlas instances.
    It is non-blocking: a second mutation fails at once with EXIT_LOCKED
    instead of queueing behind a deploy nobody is watching. ``-o`` closes the
    lock before the command starts, so the lock is held by flock itself and is
    released when the command exits, whatever it leaves running. Hosts without
    flock (it ships with util-linux) run the command unwrapped.
    """
    script = (
        "if command -v flock >/dev/null 2>&1; then "
        f'd={shlex.quote(lock_dir)}; [ -w "$d" ] || d=/tmp; '
        f'exec flock -n -E {EXIT_LOCKED} -o "$d/{HOST_LOCK_NAME}" sh -c "$0"; '
        'else exec sh -c "$0"; fi'
    )
    return ["sh", "-lc" if login else "-c", script, command]


def _describe_exit(exit_code: int | None) -> str:
    if exit_code is None:
        return "did not finish"
    if exit_code == EXIT_TIMEOUT:
        return "timed out"
    return f"exited {exit_code}"


def _is_sha(text: str) -> bool:
    return 7 <= len(text) <= 40 and all(c in "0123456789abcdef" for c in text.lower())


def _is_safe_param(value: str) -> bool:
    """Remediation params come from inventory, but never trust a string that
    could escape into the shell."""
    return bool(value) and all(c.isalnum() or c in "-_./:" for c in value)
