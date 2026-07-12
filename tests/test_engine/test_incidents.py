"""Incident lifecycle: open, dedupe, escalate, resolve, suppress."""

from pathlib import Path

import pytest

from atlas.bus import Bus, FindingsEvent, IncidentEvent, SamplesEvent
from atlas.engine.health import health_scores
from atlas.engine.incidents import IncidentManager
from atlas.model import Finding, Sample, Severity
from atlas.store.db import Database
from atlas.store.metrics import Metrics


@pytest.fixture
async def env(tmp_path: Path):
    db = Database(tmp_path / "t.db")
    await db.open()
    bus = Bus()
    manager = IncidentManager(db, bus)
    manager.attach()
    events: list[IncidentEvent] = []
    bus.subscribe(IncidentEvent, events.append)
    yield db, bus, manager, events
    await db.close()


def finding(severity: Severity = Severity.CRITICAL) -> Finding:
    return Finding("container_restarting", "container:a/web", severity, "web is restarting")


async def test_finding_opens_once(env) -> None:
    _db, bus, manager, events = env
    await bus.publish(FindingsEvent("a", "docker", [finding()]))
    await bus.publish(FindingsEvent("a", "docker", [finding()]))
    open_incidents = await manager.store.open_incidents()
    assert len(open_incidents) == 1
    assert [e.kind for e in events] == ["opened"]


async def test_warning_escalates_to_critical(env) -> None:
    _db, bus, manager, events = env
    await bus.publish(FindingsEvent("a", "docker", [finding(Severity.WARNING)]))
    await bus.publish(FindingsEvent("a", "docker", [finding(Severity.CRITICAL)]))
    open_incidents = await manager.store.open_incidents()
    assert len(open_incidents) == 1
    assert open_incidents[0]["severity"] == "critical"
    assert [e.kind for e in events] == ["opened", "escalated"]


async def test_metric_rule_pipeline(env) -> None:
    """Samples above threshold open an incident; recovery resolves it."""
    db, bus, manager, events = env
    metrics = Metrics(db)

    async def push(value: float) -> None:
        samples = [Sample("disk.used_pct", value, "host:a")]
        await metrics.write(samples)
        await bus.publish(SamplesEvent("a", "system", samples))

    for value in [95, 95, 95]:
        await push(value)
    open_incidents = await manager.store.open_incidents()
    assert len(open_incidents) == 1
    assert open_incidents[0]["rule_id"] == "disk_high"
    assert open_incidents[0]["severity"] == "critical"

    for value in [50, 50, 50]:
        await push(value)
    assert await manager.store.open_incidents() == []
    assert [e.kind for e in events] == ["opened", "resolved"]


async def test_suppression_blocks_new_incidents(env) -> None:
    _db, bus, manager, _events = env
    manager.suppress("app:shopfront", seconds=60)
    await bus.publish(
        FindingsEvent(
            "a", "http", [Finding("health_down", "app:shopfront", Severity.CRITICAL, "down")]
        )
    )
    assert await manager.store.open_incidents() == []


async def test_health_scores(env) -> None:
    _db, bus, manager, _events = env
    assert (await health_scores(manager.store))["fleet"] == 100
    await bus.publish(FindingsEvent("a", "docker", [finding()]))
    scores = await health_scores(manager.store)
    assert scores["container:a/web"] == 60
    assert scores["fleet"] < 100


async def test_http_blip_does_not_open_incident(env) -> None:
    """One failed probe (curl blip under load) must not page anyone."""
    db, bus, manager, events = env
    metrics = Metrics(db)
    entity = "site:directorylab/mobiledetailing"

    async def probe(value: float) -> None:
        samples = [Sample("http.up", value, entity)]
        await metrics.write(samples)
        await bus.publish(SamplesEvent("directorylab-1", "http_health", samples))

    await probe(1.0)
    await probe(0.0)  # the blip
    await probe(1.0)
    assert await manager.store.open_incidents() == []
    assert events == []


async def test_http_down_opens_after_two_probes_and_resolves_fast(env) -> None:
    db, bus, manager, events = env
    metrics = Metrics(db)
    entity = "site:directorylab/mobiledetailing"

    async def probe(value: float) -> None:
        samples = [Sample("http.up", value, entity)]
        await metrics.write(samples)
        await bus.publish(SamplesEvent("directorylab-1", "http_health", samples))

    await probe(0.0)
    await probe(0.0)
    open_incidents = await manager.store.open_incidents()
    assert len(open_incidents) == 1
    assert open_incidents[0]["rule_id"] == "http_down"
    assert open_incidents[0]["severity"] == "critical"

    await probe(1.0)  # first good probe resolves, not a 30-minute sweep
    assert await manager.store.open_incidents() == []
    assert [e.kind for e in events] == ["opened", "resolved"]


async def test_legacy_health_down_clears_on_good_probe(env) -> None:
    """Incidents opened under the old health_down rule id resolve the moment
    a good http.up sample arrives (covers open incidents across an upgrade)."""
    db, bus, manager, _events = env
    metrics = Metrics(db)
    entity = "site:directorylab/mobiledetailing"
    await bus.publish(
        FindingsEvent(
            "directorylab-1",
            "http_health",
            [Finding("health_down", entity, Severity.CRITICAL, "not answering (HTTP 000)")],
        )
    )
    assert len(await manager.store.open_incidents()) == 1

    samples = [Sample("http.up", 1.0, entity)]
    await metrics.write(samples)
    await bus.publish(SamplesEvent("directorylab-1", "http_health", samples))
    assert await manager.store.open_incidents() == []


async def test_host_down_recovers_on_host_up(env) -> None:
    """host.up=1 must clear a host_down incident and un-stick the dashboard."""
    db, bus, manager, events = env
    metrics = Metrics(db)

    # host goes down
    down = [Sample("host.up", 0.0, "host:a")]
    await metrics.write(down)
    await bus.publish(
        FindingsEvent(
            "a",
            "transport",
            [Finding("host_down", "host:a", Severity.CRITICAL, "a unreachable")],
        )
    )
    assert len(await manager.store.open_incidents()) == 1

    # host recovers
    up = [Sample("host.up", 1.0, "host:a")]
    await metrics.write(up)
    await bus.publish(SamplesEvent("a", "transport", up))
    assert await manager.store.open_incidents() == []
    assert "resolved" in [e.kind for e in events]
