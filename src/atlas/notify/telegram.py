"""Telegram alerts — critical incidents only, deduped and rate-limited.

Everything else waits for the morning brief. Alert fatigue is a design bug,
not a user preference.
"""

from __future__ import annotations

import json
import logging
import time

import httpx

from atlas.bus import Bus, IncidentEvent
from atlas.config import TelegramSection
from atlas.store.db import Database

log = logging.getLogger(__name__)

MIN_SECONDS_BETWEEN_ALERTS = 60
DEDUPE_WINDOW_S = 3600


class TelegramNotifier:
    def __init__(self, config: TelegramSection, db: Database, bus: Bus) -> None:
        self._config = config
        self._db = db
        self._bus = bus
        self._last_sent = 0.0
        self._recent: dict[str, float] = {}  # dedupe key -> ts

    def attach(self) -> None:
        if self._config.enabled and self._config.resolve_token():
            self._bus.subscribe(IncidentEvent, self._on_incident)
        else:
            log.info("telegram alerts disabled")

    async def _on_incident(self, event: IncidentEvent) -> None:
        if event.kind == "opened" and event.severity == "critical":
            text = f"🔴 ATLAS: {event.title}"
        elif event.kind == "resolved" and event.severity == "critical":
            text = f"✅ ATLAS resolved: {event.title}"
        else:
            return

        key = f"{event.kind}:{event.entity}:{event.title}"
        now = time.time()
        if now - self._recent.get(key, 0) < DEDUPE_WINDOW_S:
            return
        if now - self._last_sent < MIN_SECONDS_BETWEEN_ALERTS and event.kind != "opened":
            return
        self._recent[key] = now
        self._last_sent = now
        await self._send(text, event.incident_id)

    async def _send(self, text: str, incident_id: int | None) -> None:
        token = self._config.resolve_token()
        delivered, error = False, None
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": self._config.chat_id, "text": text},
                )
                delivered = response.status_code == 200
                if not delivered:
                    error = f"HTTP {response.status_code}"
        except httpx.HTTPError as e:
            error = str(e)
            log.warning("telegram send failed: %s", e)
        await self._db.execute(
            "INSERT INTO alerts (ts, channel, incident_id, payload, delivered, error)"
            " VALUES (?, 'telegram', ?, ?, ?, ?)",
            (int(time.time()), incident_id, json.dumps({"text": text}), int(delivered), error),
        )
