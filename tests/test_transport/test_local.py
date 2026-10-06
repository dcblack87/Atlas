"""LocalTransport: streamed commands report how they ended."""

import pytest

from atlas.transport.base import CommandFailed
from atlas.transport.local import LocalTransport


async def test_stream_yields_lines_and_returns_on_success() -> None:
    transport = LocalTransport("web-1")
    lines = [line async for line in transport.stream(["sh", "-c", "echo one; echo two"])]
    assert lines == ["one", "two"]


async def test_stream_raises_after_the_output_on_nonzero_exit() -> None:
    transport = LocalTransport("web-1")
    lines: list[str] = []
    with pytest.raises(CommandFailed) as raised:
        async for line in transport.stream(["sh", "-c", "echo hi; exit 3"]):
            lines.append(line)
    assert lines == ["hi"]  # the output arrives before the verdict
    assert raised.value.exit_code == 3


async def test_stream_times_out() -> None:
    transport = LocalTransport("web-1")
    with pytest.raises(TimeoutError):
        async for _ in transport.stream(["sh", "-c", "sleep 5"], timeout=0.2):
            pass
