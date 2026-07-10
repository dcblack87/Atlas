"""Database core: open, migrate, and serialise writes.

SQLite in WAL mode with exactly one writer connection guarded by a lock, and
a separate read connection — WAL makes concurrent read-while-write safe.
Migrations are numbered SQL files applied in order, gated by
``PRAGMA user_version``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA_DIR = Path(__file__).parent / "schema"
_MIGRATION_RE = re.compile(r"^(\d{3})_.+\.sql$")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._write: aiosqlite.Connection | None = None
        self._read: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def open(self) -> None:
        self._write = await self._connect()
        await self._migrate(self._write)
        self._read = await self._connect()

    async def close(self) -> None:
        for conn in (self._write, self._read):
            if conn is not None:
                await conn.close()
        self._write = self._read = None

    async def _connect(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def _migrate(self, conn: aiosqlite.Connection) -> None:
        cursor = await conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current = row[0] if row else 0
        migrations = sorted(
            (int(m.group(1)), f)
            for f in SCHEMA_DIR.glob("*.sql")
            if (m := _MIGRATION_RE.match(f.name))
        )
        for version, file in migrations:
            if version <= current:
                continue
            log.info("applying migration %03d: %s", version, file.name)
            await conn.executescript(file.read_text())
            await conn.execute(f"PRAGMA user_version = {version}")
            await conn.commit()

    # ── writes (serialised) ──────────────────────────────────────────

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Run one write; returns lastrowid."""
        assert self._write is not None, "Database not opened"
        async with self._write_lock:
            cursor = await self._write.execute(sql, params)
            await self._write.commit()
            return cursor.lastrowid or 0

    async def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        if not rows:
            return
        assert self._write is not None, "Database not opened"
        async with self._write_lock:
            await self._write.executemany(sql, rows)
            await self._write.commit()

    # ── reads ────────────────────────────────────────────────────────

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        assert self._read is not None, "Database not opened"
        cursor = await self._read.execute(sql, params)
        return list(await cursor.fetchall())

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        assert self._read is not None, "Database not opened"
        cursor = await self._read.execute(sql, params)
        return await cursor.fetchone()

    async def fetch_value(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = await self.fetch_one(sql, params)
        return row[0] if row else None
