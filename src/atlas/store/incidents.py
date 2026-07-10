"""Incident persistence and the shared timeline."""

from __future__ import annotations

import json
import time
from typing import Any

from atlas.store.db import Database


class IncidentStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def open_incident(
        self, rule_id: str, entity_key: str, severity: str, title: str, detail: dict | None = None
    ) -> int:
        incident_id = await self._db.execute(
            """
            INSERT INTO incidents (rule_id, entity_key, severity, status, title, opened_at, detail)
            VALUES (?, ?, ?, 'open', ?, ?, ?)
            """,
            (rule_id, entity_key, severity, title, int(time.time()), json.dumps(detail or {})),
        )
        await self.add_event(incident_id, "opened", title)
        return incident_id

    async def escalate(self, incident_id: int, severity: str, title: str) -> None:
        await self._db.execute(
            "UPDATE incidents SET severity = ?, title = ? WHERE id = ?",
            (severity, title, incident_id),
        )
        await self.add_event(incident_id, "escalated", title)

    async def resolve(self, incident_id: int, note: str = "") -> None:
        await self._db.execute(
            "UPDATE incidents SET status = 'resolved', resolved_at = ? WHERE id = ?",
            (int(time.time()), incident_id),
        )
        await self.add_event(incident_id, "resolved", note or "condition cleared")

    async def acknowledge(self, incident_id: int) -> None:
        await self._db.execute(
            "UPDATE incidents SET status = 'acked' WHERE id = ? AND status = 'open'",
            (incident_id,),
        )
        await self.add_event(incident_id, "acked", "acknowledged")

    async def get(self, incident_id: int) -> dict | None:
        row = await self._db.fetch_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        return dict(row) if row else None

    async def find_open(self, rule_id: str, entity_key: str) -> dict | None:
        row = await self._db.fetch_one(
            """
            SELECT * FROM incidents
            WHERE rule_id = ? AND entity_key = ? AND status != 'resolved'
            """,
            (rule_id, entity_key),
        )
        return dict(row) if row else None

    async def open_incidents(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM incidents WHERE status != 'resolved' ORDER BY opened_at DESC"
        )
        return [dict(row) for row in rows]

    async def recent_incidents(self, since_s: int) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM incidents WHERE opened_at >= ? ORDER BY opened_at DESC",
            (int(time.time()) - since_s,),
        )
        return [dict(row) for row in rows]

    async def add_event(
        self, incident_id: int | None, kind: str, body: str, ts: int | None = None
    ) -> None:
        await self._db.execute(
            "INSERT INTO incident_events (incident_id, ts, kind, body) VALUES (?, ?, ?, ?)",
            (incident_id, ts or int(time.time()), kind, body),
        )

    async def timeline(self, since_s: int, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT e.*, i.entity_key, i.severity FROM incident_events e
            LEFT JOIN incidents i ON i.id = e.incident_id
            WHERE e.ts >= ? ORDER BY e.ts DESC LIMIT ?
            """,
            (int(time.time()) - since_s, limit),
        )
        return [dict(row) for row in rows]
