"""SSH transport — one pooled asyncssh connection per host.

SSH multiplexes natively: every ``run()`` opens a fresh channel over the same
TCP connection, so there is no per-command handshake cost. A per-host
semaphore caps concurrent channels — a monitoring tool must never be the
thing that loads the box.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import time
from collections.abc import AsyncIterator
from pathlib import Path

import asyncssh

from atlas.config import SSHSection
from atlas.transport.base import HostUnreachable, Result

log = logging.getLogger(__name__)

MAX_CHANNELS_PER_HOST = 4
RECONNECT_MIN_S = 5.0
RECONNECT_MAX_S = 60.0


class SSHTransport:
    def __init__(self, host: str, address: str, ssh: SSHSection) -> None:
        self.host = host
        self.address = address
        self._ssh = ssh
        self._conn: asyncssh.SSHClientConnection | None = None
        self._connect_lock = asyncio.Lock()
        self._channels = asyncio.Semaphore(MAX_CHANNELS_PER_HOST)
        self._backoff = RECONNECT_MIN_S
        self._next_attempt = 0.0

    async def _connection(self) -> asyncssh.SSHClientConnection:
        conn = self._conn
        if conn is not None and not conn.is_closed():
            return conn
        async with self._connect_lock:
            if self._conn is not None and not self._conn.is_closed():
                return self._conn
            now = time.monotonic()
            if now < self._next_attempt:
                raise HostUnreachable(self.host, f"backing off {self._next_attempt - now:.0f}s")
            try:
                self._conn = await self._connect()
            except (OSError, asyncssh.Error) as e:
                self._next_attempt = now + self._backoff
                self._backoff = min(self._backoff * 2, RECONNECT_MAX_S)
                raise HostUnreachable(self.host, str(e)) from e
            self._backoff = RECONNECT_MIN_S
            log.info("connected to %s (%s)", self.host, self.address)
            return self._conn

    async def _connect(self) -> asyncssh.SSHClientConnection:
        """Connect with trust-on-first-use host key pinning.

        First contact with a host records its key; afterwards a changed key
        is a hard failure. TOFU is a fair trade over a private tailnet.
        """
        known_hosts = _known_hosts_path()
        pinned = _host_is_pinned(known_hosts, self.address)
        conn = await asyncssh.connect(
            self.address,
            username=self._ssh.user,
            client_keys=[str(self._ssh.key_file)],
            known_hosts=str(known_hosts) if pinned else None,
            connect_timeout=self._ssh.connect_timeout,
            keepalive_interval=self._ssh.keepalive,
            keepalive_count_max=3,
        )
        if not pinned:
            _pin_host_key(known_hosts, self.address, conn)
        return conn

    async def run(self, cmd: list[str], *, timeout: float = 30) -> Result:
        conn = await self._connection()
        start = time.monotonic()
        async with self._channels:
            try:
                completed = await asyncio.wait_for(conn.run(shlex.join(cmd), check=False), timeout)
            except (OSError, asyncssh.Error) as e:
                raise HostUnreachable(self.host, str(e)) from e
        return Result(
            exit_code=completed.exit_status or 0,
            stdout=_text(completed.stdout),
            stderr=_text(completed.stderr),
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    async def stream(self, cmd: list[str], *, timeout: float = 900) -> AsyncIterator[str]:
        conn = await self._connection()
        deadline = time.monotonic() + timeout
        async with self._channels:
            try:
                process = await conn.create_process(shlex.join(cmd), stderr=asyncssh.STDOUT)
            except (OSError, asyncssh.Error) as e:
                raise HostUnreachable(self.host, str(e)) from e
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"stream timed out after {timeout}s")
                    line = await asyncio.wait_for(process.stdout.readline(), remaining)
                    if not line:
                        break
                    yield line.rstrip("\n")
            finally:
                process.terminate()
                await process.wait_closed()

    async def close(self) -> None:
        if self._conn is not None and not self._conn.is_closed():
            self._conn.close()
            await self._conn.wait_closed()


def _text(data: object) -> str:
    if isinstance(data, bytes):
        return data.decode(errors="replace")
    return str(data or "")


def _known_hosts_path() -> Path:
    path = Path.home() / ".config" / "atlas" / "known_hosts"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return path


def _host_is_pinned(known_hosts: Path, address: str) -> bool:
    return any(
        line.split(None, 1)[0].strip("[]").split("]:")[0] == address
        for line in known_hosts.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )


def _pin_host_key(known_hosts: Path, address: str, conn: asyncssh.SSHClientConnection) -> None:
    key = conn.get_server_host_key()
    if key is None:
        return
    entry = f"{address} {key.get_algorithm()} {key.export_public_key().decode().split()[1]}\n"
    with known_hosts.open("a") as f:
        f.write(entry)
    log.info("pinned host key for %s", address)
