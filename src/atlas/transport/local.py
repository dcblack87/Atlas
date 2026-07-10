"""Local execution — used for the host Atlas itself runs on.

Deliberately subprocess, not ssh-to-localhost: self-monitoring must survive a
broken sshd, and Atlas shouldn't spam its own auth log.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from atlas.transport.base import Result


class LocalTransport:
    def __init__(self, host: str) -> None:
        self.host = host

    async def run(self, cmd: list[str], *, timeout: float = 30) -> Result:
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return Result(
            exit_code=proc.returncode or 0,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    async def stream(self, cmd: list[str], *, timeout: float = 900) -> AsyncIterator[str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"stream timed out after {timeout}s")
                line = await asyncio.wait_for(proc.stdout.readline(), remaining)
                if not line:
                    break
                yield line.decode(errors="replace").rstrip("\n")
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
