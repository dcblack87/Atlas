"""Inventory: entities and facts, with diffing.

Discovery is the source of truth. Each sync upserts what a collector saw and
returns what appeared/disappeared so the timeline can record drift — a new
tenant site showing up is an event worth remembering.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from atlas.model import Entity
from atlas.store.db import Database


@dataclass(slots=True)
class InventoryDiff:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


class Inventory:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def sync(
        self, parents: list[str], kinds: list[str], entities: list[Entity]
    ) -> InventoryDiff:
        """Reconcile entities against what a collector just saw.

        The diff is bounded to ``kinds`` under ``parents``: syncing host A's
        containers must not deactivate host B's containers, and a docker sync
        must not deactivate discovery's app/site entities.
        """
        if not parents or not kinds:
            return InventoryDiff()
        now = int(time.time())
        seen_keys = {e.key for e in entities}
        placeholders_p = ",".join("?" * len(parents))
        placeholders_k = ",".join("?" * len(kinds))
        existing = {
            row["key"]: row["active"]
            for row in await self._db.fetch_all(
                f"SELECT key, active FROM entities "
                f"WHERE parent_key IN ({placeholders_p}) AND kind IN ({placeholders_k})",
                (*parents, *kinds),
            )
        }

        diff = InventoryDiff()
        for entity in entities:
            if not existing.get(entity.key):  # brand new, or was inactive
                diff.added.append(entity.key)
            await self._db.execute(
                """
                INSERT INTO entities (kind, key, parent_key, first_seen, last_seen, active, attrs)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT (kind, key) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    active = 1,
                    parent_key = excluded.parent_key,
                    attrs = excluded.attrs
                """,
                (entity.kind, entity.key, entity.parent, now, now, json.dumps(entity.attrs)),
            )

        vanished = [k for k, active in existing.items() if active and k not in seen_keys]
        for key in vanished:
            await self._db.execute("UPDATE entities SET active = 0 WHERE key = ?", (key,))
            diff.removed.append(key)
        return diff

    async def upsert(self, entity: Entity) -> None:
        """Upsert a single entity without diffing (e.g. the host itself)."""
        now = int(time.time())
        await self._db.execute(
            """
            INSERT INTO entities (kind, key, parent_key, first_seen, last_seen, active, attrs)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT (kind, key) DO UPDATE SET
                last_seen = excluded.last_seen, active = 1, attrs = excluded.attrs
            """,
            (entity.kind, entity.key, entity.parent, now, now, json.dumps(entity.attrs)),
        )

    async def set_fact(self, entity_key: str, name: str, value: object) -> None:
        await self._db.execute(
            """
            INSERT INTO facts (entity_key, name, value, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT (entity_key, name) DO UPDATE SET
                value = excluded.value, updated_at = excluded.updated_at
            """,
            (entity_key, name, json.dumps(value), int(time.time())),
        )

    async def get_fact(self, entity_key: str, name: str) -> object | None:
        value = await self._db.fetch_value(
            "SELECT value FROM facts WHERE entity_key = ? AND name = ?", (entity_key, name)
        )
        return json.loads(value) if value is not None else None

    async def facts_for(self, entity_key: str) -> dict[str, object]:
        rows = await self._db.fetch_all(
            "SELECT name, value FROM facts WHERE entity_key = ?", (entity_key,)
        )
        return {row["name"]: json.loads(row["value"]) for row in rows}

    async def entities(
        self, *, kind: str | None = None, parent: str | None = None, active_only: bool = True
    ) -> list[dict]:
        sql = "SELECT kind, key, parent_key, first_seen, last_seen, active, attrs FROM entities"
        clauses, params = [], []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if parent:
            clauses.append("parent_key = ?")
            params.append(parent)
        if active_only:
            clauses.append("active = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY key"
        rows = await self._db.fetch_all(sql, tuple(params))
        return [
            {
                "kind": row["kind"],
                "key": row["key"],
                "parent": row["parent_key"],
                "attrs": json.loads(row["attrs"]),
                "active": bool(row["active"]),
            }
            for row in rows
        ]
