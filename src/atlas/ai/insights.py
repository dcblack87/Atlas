"""Insights: incident explanation, entity explanation, auto-insights.

Auto-insights ride the bus: a critical incident opening queues one analysis,
gated by the sub-budget, a per-entity cooldown, and prompt-digest dedupe.
The result lands on the incident timeline, so it's readable later even if
nobody was watching.
"""

from __future__ import annotations

import asyncio
import logging

from atlas.ai.client import AIClient, AIDisabled, BudgetExhausted
from atlas.ai.context import ContextBuilder, freshness_note
from atlas.bus import Bus, IncidentEvent
from atlas.store.incidents import IncidentStore

log = logging.getLogger(__name__)


class InsightEngine:
    def __init__(
        self, client: AIClient, context: ContextBuilder, incidents: IncidentStore, bus: Bus
    ) -> None:
        self._client = client
        self._context = context
        self._incidents = incidents
        self._bus = bus

    def attach(self) -> None:
        self._bus.subscribe(IncidentEvent, self._on_incident)

    async def _on_incident(self, event: IncidentEvent) -> None:
        if event.kind != "opened" or event.severity != "critical":
            return
        if not self._client.check_auto_cooldown(event.entity):
            return
        # fire-and-forget: never block the incident pipeline on an API call
        asyncio.create_task(self._auto_insight(event))  # noqa: RUF006

    async def _auto_insight(self, event: IncidentEvent) -> None:
        try:
            text = await self.explain_incident(event.incident_id, auto=True)
            log.info("auto-insight for incident %d: %s", event.incident_id, text[:80])
        except (BudgetExhausted, AIDisabled) as e:
            log.info("auto-insight skipped: %s", e)
        except Exception:
            log.exception("auto-insight failed")

    async def explain_incident(self, incident_id: int, *, auto: bool = False) -> str:
        incident = await self._context.incident_block(incident_id)
        row = await self._incidents.get(incident_id)
        entity_keys = [row["entity_key"]] if row else []
        detail = await self._context.entity_block(entity_keys)
        user = (
            f"{incident}\n\n{detail}\n\n{freshness_note()}\n\n"
            "Explain this incident: what happened, the most likely cause given "
            "the surrounding data, whether it needs action now, and the exact "
            "next diagnostic or fix command."
        )
        text = await self._client.complete(
            "incident_explain",
            await self._context.system_blocks(),
            user,
            auto=auto,
            incident_id=incident_id,
        )
        await self._incidents.add_event(incident_id, "ai_insight", text)
        return text

    async def explain_entity(self, entity_key: str) -> str:
        detail = await self._context.entity_block([entity_key])
        user = (
            f"{detail}\n\n{freshness_note()}\n\n"
            f"Explain {entity_key}: its purpose in this fleet, current state, "
            "recent changes, notable risks, and one concrete recommendation."
        )
        return await self._client.complete(
            "entity_explain",
            await self._context.system_blocks(),
            user,
            entity_key=entity_key,
        )
