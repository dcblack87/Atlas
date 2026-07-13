"""The incident manager: findings in, incident lifecycle out.

Three sources feed it:
  1. metric rules   — hysteresis judged here against recent samples
  2. fact rules     — judged whenever facts refresh
  3. collector findings — pre-judged; auto-resolved when no longer asserted

Deduped on (rule_id, entity): one open incident per condition, escalation
when severity rises, auto-resolve when the condition clears.
"""

from __future__ import annotations

import json
import logging
import time

from atlas.bus import Bus, FindingsEvent, IncidentEvent, InventoryChanged, SamplesEvent
from atlas.engine.rules import FACT_RULES, METRIC_RULES
from atlas.model import Finding, Severity
from atlas.store.db import Database
from atlas.store.incidents import IncidentStore
from atlas.store.metrics import Metrics

log = logging.getLogger(__name__)

# A collector finding that hasn't been re-asserted for this many seconds is
# considered cleared (collector intervals are <= 600s).
ASSERTION_TTL_S = 1800
SWEEP_INTERVAL_S = 60


class IncidentManager:
    def __init__(self, db: Database, bus: Bus) -> None:
        self._store = IncidentStore(db)
        self._metrics = Metrics(db)
        self._db = db
        self._bus = bus
        self._last_asserted: dict[tuple[str, str], float] = {}
        self._suppressed_entities: dict[str, float] = {}  # entity prefix -> until ts

    @property
    def store(self) -> IncidentStore:
        return self._store

    def attach(self) -> None:
        self._bus.subscribe(SamplesEvent, self._on_samples)
        self._bus.subscribe(FindingsEvent, self._on_findings)
        self._bus.subscribe(InventoryChanged, self._on_inventory)

    def suppress(self, entity_prefix: str, seconds: float) -> None:
        """Silence incidents for an entity subtree (deploy windows)."""
        self._suppressed_entities[entity_prefix] = time.time() + seconds

    def _is_suppressed(self, entity: str) -> bool:
        now = time.time()
        return any(
            entity.startswith(prefix) and until > now
            for prefix, until in self._suppressed_entities.items()
        )

    # ── sources ──────────────────────────────────────────────────────

    async def _on_samples(self, event: SamplesEvent) -> None:
        # A host reporting up again clears its (transport-level) host_down —
        # host_down is a collector finding, not a metric rule, so resolve it
        # here the moment connectivity returns rather than waiting on the sweep.
        # Likewise a good probe clears health_down (the pre-2026-07 rule id
        # for HTTP liveness; http_down owns it now) so old open incidents
        # resolve on the first good probe instead of leaking.
        for sample in event.samples:
            if sample.metric == "host.up" and sample.value >= 1:
                await self._clear("host_down", sample.entity)
                self._last_asserted.pop(("host_down", sample.entity), None)
            elif sample.metric == "http.up" and sample.value >= 1:
                await self._clear("health_down", sample.entity)
                self._last_asserted.pop(("health_down", sample.entity), None)

        touched = {(s.entity, s.metric) for s in event.samples}
        for rule in METRIC_RULES:
            for entity, metric in touched:
                if metric != rule.metric:
                    continue
                values = await self._metrics.last_n(
                    entity, metric, max(rule.for_samples, rule.clear_samples)
                )
                severity = rule.judge(values)
                if severity is not None:
                    await self._raise(rule.finding(entity, severity, values[-1]))
                elif rule.cleared(values):
                    await self._clear(rule.id, entity)

    async def _on_findings(self, event: FindingsEvent) -> None:
        for finding in event.findings:
            self._last_asserted[(finding.rule_id, finding.entity)] = time.time()
            await self._raise(finding)

    async def _on_inventory(self, event: InventoryChanged) -> None:
        for key in event.added:
            await self._store.add_event(None, "entity_added", key)
        for key in event.removed:
            await self._store.add_event(None, "entity_removed", key)

    async def evaluate_facts(self) -> None:
        """Judge fact rules against current facts (called by the sweep)."""
        for rule in FACT_RULES:
            rows = await self._db.fetch_all(
                "SELECT entity_key, value FROM facts WHERE name = ?", (rule.fact,)
            )
            for row in rows:
                try:
                    value = float(json.loads(row["value"]))
                except (TypeError, ValueError):
                    continue
                severity = rule.judge(value)
                if severity is not None:
                    await self._raise(rule.finding(row["entity_key"], severity, value))
                else:
                    await self._clear(rule.id, row["entity_key"])
            # A fact that disappeared entirely (forecast receded, source
            # gone) must also clear: the loop above only sees surviving rows.
            present = {row["entity_key"] for row in rows}
            stale = await self._db.fetch_all(
                "SELECT DISTINCT entity_key FROM incidents"
                " WHERE rule_id = ? AND status != 'resolved'",
                (rule.id,),
            )
            for row in stale:
                if row["entity_key"] not in present:
                    await self._clear(rule.id, row["entity_key"])

    # ── lifecycle ────────────────────────────────────────────────────

    async def raise_finding(self, finding: Finding, *, ignore_suppression: bool = False) -> None:
        """Public entry for pre-judged findings (deploy verification failures)."""
        await self._raise(finding, ignore_suppression=ignore_suppression)

    async def _raise(self, finding: Finding, *, ignore_suppression: bool = False) -> None:
        if finding.severity == Severity.INFO:
            return
        if not ignore_suppression and self._is_suppressed(finding.entity):
            return
        existing = await self._store.find_open(finding.rule_id, finding.entity)
        if existing is None:
            incident_id = await self._store.open_incident(
                finding.rule_id, finding.entity, finding.severity, finding.title, finding.detail
            )
            await self._bus.publish(
                IncidentEvent(
                    incident_id, "opened", finding.severity, finding.title, finding.entity
                )
            )
        elif existing["severity"] == Severity.WARNING and finding.severity == Severity.CRITICAL:
            await self._store.escalate(existing["id"], finding.severity, finding.title)
            await self._bus.publish(
                IncidentEvent(
                    existing["id"], "escalated", finding.severity, finding.title, finding.entity
                )
            )

    async def _clear(self, rule_id: str, entity: str) -> None:
        existing = await self._store.find_open(rule_id, entity)
        if existing is not None:
            await self._store.resolve(existing["id"])
            await self._bus.publish(
                IncidentEvent(
                    existing["id"], "resolved", existing["severity"], existing["title"], entity
                )
            )

    # ── sweep ────────────────────────────────────────────────────────

    async def sweep(self) -> None:
        """Periodic housekeeping: fact rules + stale collector assertions."""
        await self.evaluate_facts()
        now = time.time()
        collector_rules = {(f[0], f[1]) for f in self._last_asserted}
        for rule_id, entity in collector_rules:
            if now - self._last_asserted[(rule_id, entity)] > ASSERTION_TTL_S:
                await self._clear(rule_id, entity)
                del self._last_asserted[(rule_id, entity)]
