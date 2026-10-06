"""Deploy orchestrator: the gate, the stream, the audit, verification."""

import asyncio
import shutil
from pathlib import Path

import pytest

from atlas.bus import Bus
from atlas.config import Config
from atlas.deploy.orchestrator import DeployError, DeployOrchestrator, _host_locked
from atlas.engine.incidents import IncidentManager
from atlas.store.db import Database
from atlas.transport.base import CommandFailed, Result
from atlas.transport.local import LocalTransport

SHA_A = "a" * 40
SHA_B = "b" * 40


class FakeTransport:
    """Scripted transport: canned run() responses, canned stream() lines."""

    def __init__(self, host: str = "web-1") -> None:
        self.host = host
        self.commands: list[str] = []
        self.stream_lines = ["pulling…", "building…", "restarting…", "done"]
        self.stream_exit_code = 0
        self.stream_error: Exception | None = None
        self.run_responses: dict[str, str] = {}

    async def run(self, cmd, *, timeout: float = 30) -> Result:
        text = " ".join(cmd)
        self.commands.append(text)
        for needle, response in self.run_responses.items():
            if needle in text:
                return Result(0, response, "", 5)
        return Result(0, "", "", 5)

    async def stream(self, cmd, *, timeout: float = 900):
        self.commands.append(" ".join(cmd))
        for line in self.stream_lines:
            yield line
        if self.stream_error is not None:
            raise self.stream_error
        if self.stream_exit_code != 0:
            raise CommandFailed(self.host, self.stream_exit_code)


def make_config(tmp_path: Path, remediations: list[str] | None = None) -> Config:
    return Config.model_validate(
        {
            "atlas": {"db_path": str(tmp_path / "t.db")},
            "hosts": [{"name": "web-1", "address": "local", "apps": ["shopfront"]}],
            "apps": {
                "shopfront": {
                    "kind": "single-container",
                    "path": "/opt/shopfront",
                    "container": "shopfront",
                    "liveness_url": "http://127.0.0.1:3000/",
                }
            },
            "deploy": {"remediations": remediations or ["docker restart {container}"]},
        }
    )


@pytest.fixture
async def env(tmp_path: Path, monkeypatch):
    # verification should be instant in tests
    monkeypatch.setattr("atlas.deploy.verify.GRACE_S", 0)
    monkeypatch.setattr("atlas.deploy.verify.POLL_TIMEOUT_S", 0)
    config = make_config(tmp_path)
    db = Database(config.atlas.db_path)
    await db.open()
    bus = Bus()
    incidents = IncidentManager(db, bus)
    incidents.attach()
    transport = FakeTransport()
    transport.run_responses = {
        "rev-parse HEAD": SHA_A,
        "ls-remote": SHA_B,  # the real command pipes through `cut -f1`
        "docker inspect": "running",
        "curl": "200",
    }
    orchestrator = DeployOrchestrator(config, db, bus, lambda host: transport, incidents)
    yield orchestrator, transport, db, incidents
    await db.close()


async def test_preflight(env) -> None:
    orchestrator, _transport, _db, _ = env
    pf = await orchestrator.preflight("shopfront")
    assert pf.deployed_sha == SHA_A
    assert pf.remote_sha == SHA_B
    assert pf.up_to_date is False
    assert pf.command == "./scripts/deploy.sh update"


async def test_wrong_phrase_refused(env) -> None:
    orchestrator, *_ = env
    with pytest.raises(DeployError, match="confirmation phrase"):
        async for _ in orchestrator.deploy("shopfront", "shopfrnt"):
            pass


async def test_deploy_streams_verifies_and_audits(env) -> None:
    orchestrator, _transport, db, _ = env
    lines = [line async for line in orchestrator.deploy("shopfront", "shopfront")]
    assert "pulling…" in lines
    assert any("VERIFICATION PASSED" in line for line in lines)

    row = await db.fetch_one("SELECT * FROM deployments")
    assert row is not None
    assert row["app"] == "shopfront"
    assert row["confirmed_phrase"] == "shopfront"
    assert row["exit_code"] == 0
    assert row["verify_status"] == "passed"
    assert "pulling…" in row["output"]
    # the timeline recorded the deploy
    event = await db.fetch_one("SELECT * FROM incident_events WHERE kind='deploy'")
    assert event is not None


async def test_failed_verification_opens_incident(env) -> None:
    orchestrator, transport, _db, incidents = env
    transport.run_responses["curl"] = "502"
    lines = [line async for line in orchestrator.deploy("shopfront", "shopfront")]
    assert any("VERIFICATION FAILED" in line for line in lines)
    open_incidents = await incidents.store.open_incidents()
    assert len(open_incidents) == 1
    assert open_incidents[0]["rule_id"] == "deploy_verification_failed"


async def test_nonzero_exit_is_recorded_even_when_verification_passes(env) -> None:
    """A deploy script that dies early leaves the old containers serving, so
    verification passes. The audit row must still say the command failed."""
    orchestrator, transport, db, incidents = env
    transport.stream_exit_code = 2
    lines = [line async for line in orchestrator.deploy("shopfront", "shopfront")]
    assert "✖ deploy command exited 2" in lines
    assert any("VERIFICATION PASSED" in line for line in lines)
    assert any("previous version may still be serving" in line for line in lines)

    row = await db.fetch_one("SELECT * FROM deployments")
    assert row is not None
    assert row["exit_code"] == 2
    assert row["verify_status"] == "passed"
    assert "✖ deploy command exited 2" in row["output"]
    event = await db.fetch_one("SELECT * FROM incident_events WHERE kind='deploy'")
    assert event is not None
    assert "deploy command exited 2" in event["body"]
    # Recorded and shown, not paged: verification is still what opens incidents.
    assert await incidents.store.open_incidents() == []


async def test_stored_output_is_scrubbed_but_the_live_stream_is_not(env) -> None:
    """Deploy scripts print the crontab they install, tokens and all."""
    orchestrator, transport, db, _ = env
    token = "0" * 64
    transport.stream_lines = [f"installed: curl -H 'Authorization: Bearer {token}' http://x/"]
    lines = [line async for line in orchestrator.deploy("shopfront", "shopfront")]
    assert any(token in line for line in lines)

    row = await db.fetch_one("SELECT * FROM deployments")
    assert row is not None
    assert token not in row["output"]
    assert "Bearer [redacted]" in row["output"]


async def test_timeout_is_recorded_as_124(env) -> None:
    orchestrator, transport, db, _ = env
    transport.stream_error = TimeoutError("stream timed out")
    lines = [line async for line in orchestrator.deploy("shopfront", "shopfront")]
    assert any("timed out" in line for line in lines)
    row = await db.fetch_one("SELECT * FROM deployments")
    assert row is not None
    assert row["exit_code"] == 124
    event = await db.fetch_one("SELECT * FROM incident_events WHERE kind='deploy'")
    assert event is not None
    assert "deploy command timed out" in event["body"]


async def test_deploy_runs_under_the_host_lock(env) -> None:
    orchestrator, transport, *_ = env
    async for _ in orchestrator.deploy("shopfront", "shopfront"):
        pass
    deploy_cmd = next(c for c in transport.commands if "deploy.sh" in c)
    assert "flock -n" in deploy_cmd
    assert deploy_cmd.endswith("cd /opt/shopfront && ./scripts/deploy.sh update")


async def test_held_host_lock_is_reported_as_such(env) -> None:
    orchestrator, transport, db, _ = env
    transport.stream_lines = []
    transport.stream_exit_code = 75
    lines = [line async for line in orchestrator.deploy("shopfront", "shopfront")]
    assert any("another mutation holds the lock on web-1" in line for line in lines)
    row = await db.fetch_one("SELECT * FROM deployments")
    assert row is not None
    assert row["exit_code"] == 75


async def test_host_lock_wrapper_runs_the_command(tmp_path: Path) -> None:
    """Through a real shell: with flock where it exists, without where it doesn't."""
    argv = _host_locked("echo one && echo two", login=False, lock_dir=str(tmp_path))
    lines = [line async for line in LocalTransport("web-1").stream(argv)]
    assert lines == ["one", "two"]


async def test_host_lock_wrapper_passes_the_exit_code_through(tmp_path: Path) -> None:
    argv = _host_locked("exit 3", login=False, lock_dir=str(tmp_path))
    with pytest.raises(CommandFailed) as raised:
        async for _ in LocalTransport("web-1").stream(argv):
            pass
    assert raised.value.exit_code == 3


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock is Linux-only (util-linux)")
async def test_second_mutation_on_a_host_is_refused_while_the_first_runs(tmp_path: Path) -> None:
    transport = LocalTransport("web-1")
    first = _host_locked("echo started; sleep 2", login=False, lock_dir=str(tmp_path))
    second = _host_locked("echo must-not-run", login=False, lock_dir=str(tmp_path))

    running = transport.stream(first)
    assert await anext(running) == "started"  # the first now holds the lock
    seen: list[str] = []
    with pytest.raises(CommandFailed) as raised:
        async for line in transport.stream(second):
            seen.append(line)
    assert raised.value.exit_code == 75
    assert seen == []

    async for _ in running:  # the first finishes normally and releases it
        pass
    await asyncio.sleep(0)
    after = [line async for line in transport.stream(second)]
    assert after == ["must-not-run"]


async def test_rollback_checks_out_sha(env) -> None:
    orchestrator, transport, *_ = env
    async for _ in orchestrator.deploy("shopfront", "shopfront", checkout_sha=SHA_A):
        pass
    deploy_cmd = next(c for c in transport.commands if "deploy.sh" in c)
    assert f"git checkout {SHA_A}" in deploy_cmd


async def test_rollback_rejects_non_sha(env) -> None:
    orchestrator, *_ = env
    with pytest.raises(DeployError, match="not a git sha"):
        async for _ in orchestrator.deploy("shopfront", "shopfront", checkout_sha="main; rm -rf /"):
            pass


async def test_remediation_allowlist(env) -> None:
    orchestrator, transport, *_ = env
    # not in allowlist
    with pytest.raises(DeployError, match="allowlist"):
        async for _ in orchestrator.remediate("web-1", "rm -rf {path}", {}, "web-1"):
            pass
    # in allowlist, wrong phrase
    with pytest.raises(DeployError, match="confirmation phrase"):
        async for _ in orchestrator.remediate(
            "web-1", "docker restart {container}", {"container": "shopfront"}, "web-2"
        ):
            pass
    # happy path
    lines = []
    async for line in orchestrator.remediate(
        "web-1", "docker restart {container}", {"container": "shopfront"}, "web-1"
    ):
        lines.append(line)
    assert any("docker restart shopfront" in c for c in transport.commands)


async def test_remediation_records_nonzero_exit(env) -> None:
    orchestrator, transport, db, _ = env
    transport.stream_exit_code = 1
    lines = [
        line
        async for line in orchestrator.remediate(
            "web-1", "docker restart {container}", {"container": "shopfront"}, "web-1"
        )
    ]
    assert "✖ remediation exited 1" in lines
    row = await db.fetch_one("SELECT * FROM deployments")
    assert row is not None
    assert row["exit_code"] == 1


async def test_remediation_rejects_hostile_params(env) -> None:
    orchestrator, _transport, *_ = env
    with pytest.raises(DeployError, match="missing remediation parameter"):
        async for _ in orchestrator.remediate(
            "web-1", "docker restart {container}", {"container": "x; rm -rf /"}, "web-1"
        ):
            pass
