"""Telegram alerts — critical incidents only by default, deduped and
rate-limited. ``min_severity = "warning"`` opts into more.

Everything else waits for the morning brief, which is sent here too (the
daily one at 07:00, the weekly on Sunday at 08:00, each with a switch in
atlas.toml). Alert fatigue is a design bug, not a user preference.

Alerts and briefs go out through the bot's own client, so they are escaped
and split the same way as the menu's cards, and an alert carries buttons
into the menu: Acknowledge on an incident that is still open.
"""

from __future__ import annotations

import json
import logging
import time

from atlas.bus import Bus, IncidentEvent
from atlas.config import TelegramSection
from atlas.notify.bot.format import esc, open_button
from atlas.notify.bot.transport import BotAPI
from atlas.store.db import Database

log = logging.getLogger(__name__)

MIN_SECONDS_BETWEEN_ALERTS = 60
DEDUPE_WINDOW_S = 3600

_SEVERITY_RANK = {"warning": 1, "critical": 2}
_OPEN_GLYPH = {"warning": "⚠️", "critical": "🔴"}


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
        threshold = _SEVERITY_RANK[self._config.min_severity]
        if _SEVERITY_RANK.get(event.severity, 0) < threshold:
            return
        if event.kind == "opened":
            text = f"{_OPEN_GLYPH.get(event.severity, '🔴')} ATLAS: {event.title}"
        elif event.kind == "resolved":
            text = f"✅ ATLAS resolved: {event.title}"
        elif event.kind == "escalated":
            text = f"🔴 ATLAS escalated: {event.title}"
        elif event.kind == "reminder":
            text = f"🔴 ATLAS still open: {event.title}"
        else:
            return

        key = f"{event.kind}:{event.entity}:{event.title}"
        now = time.time()
        if now - self._recent.get(key, 0) < DEDUPE_WINDOW_S:
            return
        # Opens and reminders are never rate-limited away: a reminder is already
        # daily, and one dropped here would not come back for another 24h.
        if now - self._last_sent < MIN_SECONDS_BETWEEN_ALERTS and event.kind not in (
            "opened",
            "reminder",
        ):
            return
        self._recent[key] = now
        self._last_sent = now
        # An incident that is still open can be acknowledged from the alert.
        await self._send(text, event.incident_id, ack=event.kind != "resolved")

    def _api(self) -> BotAPI | None:
        token = self._config.resolve_token()
        return BotAPI(token) if token and self._config.chat_id else None

    async def _send(self, text: str, incident_id: int | None, *, ack: bool = False) -> None:
        api = self._api()
        delivered, error = False, "telegram is not configured"
        if api is not None:
            keyboard = None
            if self._config.bot_commands:
                # handled by the bot's poller (see notify/bot)
                # open_button: the answer arrives below, the alert stays as sent
                keyboard = [
                    [open_button("✓ Acknowledge", "ack", incident_id)]
                    if ack and incident_id
                    else [],
                    [open_button("🚨 Incidents", "incidents"), open_button("📊 Status", "status")],
                ]
                keyboard = [row for row in keyboard if row]
            result = await api.send_message(str(self._config.chat_id), esc(text), keyboard)
            delivered, error = result.ok, result.error
        await self._db.execute(
            "INSERT INTO alerts (ts, channel, incident_id, payload, delivered, error)"
            " VALUES (?, 'telegram', ?, ?, ?, ?)",
            (int(time.time()), incident_id, json.dumps({"text": text}), int(delivered), error),
        )

    async def send_brief(self, kind: str, body: str) -> bool:
        """Send the morning or weekly brief. ``kind`` is "daily" or "weekly"."""
        wanted = self._config.weekly_brief if kind == "weekly" else self._config.daily_brief
        api = self._api()
        if not (self._config.enabled and wanted and api is not None):
            return False
        keyboard = None
        if self._config.bot_commands:
            keyboard = [
                [open_button("📊 Status", "status"), open_button("🚨 Incidents", "incidents")],
                [open_button("« Menu", "help")],
            ]
        result = await api.send_message(str(self._config.chat_id), esc(body), keyboard)
        if not result.ok:
            log.warning("telegram %s brief was not delivered: %s", kind, result.error)
        return result.ok
