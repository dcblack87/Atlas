"""Incident lifecycle: open, dedupe, escalate, resolve, suppress."""

import json
from pathlib import Path

import pytest

from atlas.bus import Bus, FindingsEvent, IncidentEvent, SamplesEvent
from atlas.engine.health import health_scores
from atlas.engine.incidents import IncidentManager
from atlas.model import Finding, Sample, Severity
from atlas.store.db import Database
from atlas.store.inventory import Inventory
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


async def test_open_incident_tracks_the_current_value(env) -> None:
    """An open incident quotes now, not whatever opened it.

    Reproduces the bookingmachine case: opened at 30.3h, escalated at 54.2h,
    and the stored detail was still saying 30.3 two days later while the
    title said 54.2 and the fact itself had reached 58.1.
    """
    db, _bus, manager, events = env
    inventory = Inventory(db)

    for age in (30.3, 54.2, 58.1):
        await inventory.set_fact("app:bm", "backup.age_hours", age)
        await manager.evaluate_facts()

    incident = (await manager.store.open_incidents())[0]
    assert incident["severity"] == "critical"
    assert "58.1h" in incident["title"]
    assert json.loads(incident["detail"])["value"] == 58.1

    # The two refreshes are silent: a condition that persists is re-judged
    # every sweep, so only the real transitions may notify.
    assert [e.kind for e in events] == ["opened", "escalated"]
    assert [e["kind"] for e in await manager.store.timeline(3600)] == ["escalated", "opened"]


async def test_escalation_carries_detail(env) -> None:
    _db, bus, manager, _events = env
    for severity, streak in ((Severity.WARNING, 1), (Severity.CRITICAL, 3)):
        await bus.publish(
            FindingsEvent(
                "a",
                "cron",
                [
                    Finding(
                        "cron_failed",
                        "cron:a/backup",
                        severity,
                        f"cron job backup on a is failing ({streak})",
                        detail={"streak": streak},
                    )
                ],
            )
        )
    incident = (await manager.store.open_incidents())[0]
    assert json.loads(incident["detail"])["streak"] == 3


async def test_open_critical_is_reminded_daily(env) -> None:
    """A critical left open gets re-announced once per quiet day, never per sweep."""
    db, bus, manager, events = env
    await bus.publish(FindingsEvent("a", "docker", [finding()]))
    await manager._remind_open()
    assert [e.kind for e in events] == ["opened"]  # just announced — nothing owed

    # Age the incident and its opening notice past the reminder interval.
    await db.execute("UPDATE incidents SET opened_at = opened_at - 3 * 86400")
    await db.execute("UPDATE incident_events SET ts = ts - 3 * 86400")
    await manager._remind_open()
    assert [e.kind for e in events] == ["opened", "reminder"]
    assert events[-1].title == "web is restarting (open 3d)"

    await manager._remind_open()  # the reminder itself resets the clock
    assert [e.kind for e in events] == ["opened", "reminder"]


async def test_acked_and_warning_incidents_are_not_reminded(env) -> None:
    db, bus, manager, events = env
    await bus.publish(FindingsEvent("a", "docker", [finding(Severity.WARNING)]))
    critical = Finding("host_down", "host:b", Severity.CRITICAL, "b is down")
    await bus.publish(FindingsEvent("b", "ssh", [critical]))
    acked = await manager.store.find_open("host_down", "host:b")
    await manager.store.acknowledge(acked["id"])
    await db.execute("UPDATE incident_events SET ts = ts - 3 * 86400")
    await manager._remind_open()
    assert "reminder" not in [e.kind for e in events]
