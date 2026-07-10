"""Deploy orchestrator: the gate, the stream, the audit, verification."""

from pathlib import Path

import pytest

from atlas.bus import Bus
from atlas.config import Config
from atlas.deploy.orchestrator import DeployError, DeployOrchestrator
from atlas.engine.incidents import IncidentManager
from atlas.store.db import Database
from atlas.transport.base import Result

SHA_A = "a" * 40
SHA_B = "b" * 40


class FakeTransport:
    """Scripted transport: canned run() responses, canned stream() lines."""

    def __init__(self, host: str = "web-1") -> None:
        self.host = host
        self.commands: list[str] = []
        self.stream_lines = ["pulling…", "building…", "restarting…", "done"]
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


async def test_remediation_rejects_hostile_params(env) -> None:
    orchestrator, transport, *_ = env
    with pytest.raises(DeployError, match="missing remediation parameter"):
        async for _ in orchestrator.remediate(
            "web-1", "docker restart {container}", {"container": "x; rm -rf /"}, "web-1"
        ):
            pass
