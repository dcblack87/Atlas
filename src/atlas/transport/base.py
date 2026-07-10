"""The transport contract: how Atlas talks to machines.

Collectors and the deploy orchestrator receive a ``Transport`` and never care
whether the host is local or across the tailnet. Commands are argv lists, not
shell strings, except where a collector deliberately composes a `sh -c`
pipeline — in which case that string is visible at the call site, greppable
and reviewable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import NamedTuple, Protocol, runtime_checkable


class HostUnreachable(Exception):
    """The machine cannot be reached right now (network, sshd, auth)."""

    def __init__(self, host: str, reason: str) -> None:
        super().__init__(f"{host}: {reason}")
        self.host = host
        self.reason = reason


class Result(NamedTuple):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@runtime_checkable
class Transport(Protocol):
    host: str

    async def run(self, cmd: list[str], *, timeout: float = 30) -> Result:
        """Run a command to completion and capture its output."""
        ...

    def stream(self, cmd: list[str], *, timeout: float = 900) -> AsyncIterator[str]:
        """Run a command and yield merged stdout/stderr line by line."""
        ...
