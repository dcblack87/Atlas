"""Deployment audit trail."""

from __future__ import annotations

import time

from atlas.store.db import Database

OUTPUT_CAP_BYTES = 1_000_000
HEAD_LINES = 200


class DeploymentStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def start(
        self, app: str, host: str, command: str, sha_before: str | None, confirmed_phrase: str
    ) -> int:
        return await self._db.execute(
            """
            INSERT INTO deployments (app, host, started_at, command, git_sha_before,
                                     confirmed_phrase)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (app, host, int(time.time()), command, sha_before, confirmed_phrase),
        )

    async def finish(
        self,
        deployment_id: int,
        *,
        exit_code: int | None,
        sha_after: str | None,
        output: str,
        verify_status: str,
    ) -> None:
        await self._db.execute(
            """
            UPDATE deployments
            SET finished_at = ?, exit_code = ?, git_sha_after = ?, output = ?, verify_status = ?
            WHERE id = ?
            """,
            (
                int(time.time()),
                exit_code,
                sha_after,
                cap_output(output),
                verify_status,
                deployment_id,
            ),
        )

    async def close_abandoned(self, older_than_s: float) -> int:
        """Close deploys that never finished: Atlas quit or crashed mid-deploy.

        Without this they sit in the history as running forever, with no outcome.
        Only rows older than the deploy timeout are touched, so nothing still
        running in this process is closed under it.
        """
        now = int(time.time())
        rows = await self._db.fetch_all(
            "SELECT id FROM deployments WHERE finished_at IS NULL AND started_at < ?",
            (now - int(older_than_s),),
        )
        for row in rows:
            await self._db.execute(
                """
                UPDATE deployments
                SET finished_at = ?, verify_status = 'abandoned',
                    output = COALESCE(output || char(10), '') || ?
                WHERE id = ?
                """,
                (
                    now,
                    "✖ Atlas stopped before this deploy finished; its outcome was never recorded",
                    row["id"],
                ),
            )
        return len(rows)

    async def recent(self, limit: int = 20) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT * FROM deployments ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in rows]

    async def last_for_app(self, app: str) -> dict | None:
        row = await self._db.fetch_one(
            "SELECT * FROM deployments WHERE app = ? ORDER BY started_at DESC LIMIT 1", (app,)
        )
        return dict(row) if row else None


def cap_output(output: str) -> str:
    """Keep the first HEAD_LINES lines and as much tail as fits the cap."""
    if len(output) <= OUTPUT_CAP_BYTES:
        return output
    lines = output.splitlines()
    head = "\n".join(lines[:HEAD_LINES])
    remaining = OUTPUT_CAP_BYTES - len(head) - 64
    tail = output[-max(remaining, 0) :]
    return f"{head}\n… [output truncated] …\n{tail}"
