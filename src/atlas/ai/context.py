"""RAG-lite context assembly: SQL is the retrieval engine.

Atlas's corpus is a few megabytes of *structured* rows, perfectly indexed by
entity and time. Chat questions are entity/time-scoped by nature, so
retrieval is keyword→entity matching plus scoped SQL — no vector database,
no embedding pipeline, better precision.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from atlas.store.db import Database
from atlas.store.incidents import IncidentStore
from atlas.store.inventory import Inventory
from atlas.store.metrics import Metrics

# ~12K tokens of context, heuristically (chars / 3.5)
MAX_CONTEXT_CHARS = 42_000

SYSTEM_ROLE = """\
You are Atlas, an AI operations engineer embedded in a terminal operations \
centre that a solo founder runs on his own infrastructure. You watch a small \
fleet of Linux servers over SSH: Docker containers, nginx, PostgreSQL, \
certificates, backups, and deploys executed by on-server deploy scripts.

Interpret, don't recite: explain what happened, why, whether it matters, \
what happens if ignored, and the exact command that fixes it. Keep answers \
tight and terminal-friendly (short lines, no markdown tables). When you \
recommend a command, put it on its own line. If the data provided doesn't \
support a confident answer, say what's missing instead of guessing.\
"""

KEY_METRICS = (
    "disk.used_pct",
    "mem.used_pct",
    "load.1m",
    "http.response_ms",
    "container.restarts",
)


class ContextBuilder:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._inventory = Inventory(db)
        self._metrics = Metrics(db)
        self._incidents = IncidentStore(db)

    # ── entity matching ──────────────────────────────────────────────

    async def match_entities(self, question: str) -> list[str]:
        """Map free text to entity keys by name/alias overlap."""
        words = {w.strip(".,?!\"'").lower() for w in question.split() if len(w) > 2}
        matched: list[str] = []
        for entity in await self._inventory.entities():
            name = entity["key"].split(":", 1)[-1].lower()
            parts = set(name.replace("/", " ").replace("-", " ").split())
            haystack = (
                parts
                | {name}
                | {str(v).lower() for v in entity["attrs"].values() if isinstance(v, str)}
            )
            if words & haystack:
                matched.append(entity["key"])
        return matched

    # ── blocks ───────────────────────────────────────────────────────

    async def inventory_block(self) -> str:
        """The semi-stable fleet summary — part of the cacheable prefix."""
        lines = ["FLEET INVENTORY"]
        for host in await self._inventory.entities(kind="host"):
            lines.append(f"\nhost {host['key'].removeprefix('host:')}")
            for app in await self._inventory.entities(kind="app", parent=host["key"]):
                facts = await self._inventory.facts_for(app["key"])
                sha = str(facts.get("git.sha", ""))[:7]
                lines.append(
                    f"  app {app['key'].removeprefix('app:')} "
                    f"({app['attrs'].get('kind', '?')}, sha {sha or '?'})"
                )
                for site in await self._inventory.entities(kind="site", parent=app["key"]):
                    lines.append(f"    site {site['key'].split('/')[-1]}")
            containers = await self._inventory.entities(kind="container", parent=host["key"])
            if containers:
                names = ", ".join(c["key"].split("/")[-1] for c in containers)
                lines.append(f"  containers: {names}")
        return "\n".join(lines)

    async def entity_block(self, entity_keys: list[str], *, window_s: int = 24 * 3600) -> str:
        """Volatile context for specific entities (or fleet-wide if empty)."""
        sections: list[str] = []

        incidents = await self._incidents.open_incidents()
        recent = await self._incidents.recent_incidents(since_s=7 * 86400)
        if entity_keys:
            incidents = [i for i in incidents if i["entity_key"] in entity_keys]
            recent = [r for r in recent if r["entity_key"] in entity_keys]
        if incidents:
            sections.append(
                "OPEN INCIDENTS\n" + "\n".join(f"[{i['severity']}] {i['title']}" for i in incidents)
            )
        resolved = [r for r in recent if r["status"] == "resolved"][:10]
        if resolved:
            sections.append("RESOLVED (7d)\n" + "\n".join(f"{r['title']}" for r in resolved))

        keys = entity_keys or [e["key"] for e in await self._inventory.entities(kind="host")]
        for key in keys[:8]:
            snap = await self._metrics.latest_snapshot(key)
            if not snap:
                continue
            lines = [f"METRICS {key} (now)"]
            lines.extend(f"  {m} = {v:g}" for m, v in sorted(snap.items()))
            for metric in KEY_METRICS:
                hourly = await self._metrics.hourly(key, metric, since_s=window_s)
                if hourly:
                    trend = " ".join(f"{h['avg']:.0f}" for h in hourly[-12:])
                    lines.append(f"  {metric} hourly-avg: {trend}")
            facts = await self._inventory.facts_for(key)
            if facts:
                lines.append(
                    "  facts: "
                    + ", ".join(f"{k}={_short_json(v)}" for k, v in sorted(facts.items()))
                )
            sections.append("\n".join(lines))

        deploys = await self._db.fetch_all(
            "SELECT * FROM deployments ORDER BY started_at DESC LIMIT 5"
        )
        if deploys:
            lines = ["RECENT DEPLOYS"]
            for d in deploys:
                when = datetime.fromtimestamp(d["started_at"]).strftime("%m-%d %H:%M")
                lines.append(
                    f"  {when} {d['app']}: {(d['git_sha_before'] or '?')[:7]} → "
                    f"{(d['git_sha_after'] or '?')[:7]} verify={d['verify_status']}"
                )
            sections.append("\n".join(lines))

        events = await self._incidents.timeline(since_s=window_s, limit=30)
        if events:
            lines = ["TIMELINE (24h)"]
            lines.extend(
                f"  {datetime.fromtimestamp(e['ts']):%H:%M} {e['kind']} {e['body']}" for e in events
            )
            sections.append("\n".join(lines))

        text = "\n\n".join(sections)
        if len(text) > MAX_CONTEXT_CHARS:
            text = text[:MAX_CONTEXT_CHARS] + "\n… [context truncated]"
        return text or "No data collected yet."

    async def incident_block(self, incident_id: int) -> str:
        row = await self._db.fetch_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        if row is None:
            return f"incident {incident_id} not found"
        opened = datetime.fromtimestamp(row["opened_at"]).strftime("%Y-%m-%d %H:%M")
        return (
            f"INCIDENT #{row['id']}\n"
            f"rule: {row['rule_id']}\nentity: {row['entity_key']}\n"
            f"severity: {row['severity']}  status: {row['status']}\n"
            f"opened: {opened}\ntitle: {row['title']}\n"
            f"detail: {row['detail']}"
        )

    async def system_blocks(self) -> list[str]:
        """The cacheable prefix: role + fleet inventory, stable across calls."""
        return [SYSTEM_ROLE, await self.inventory_block()]


def _short_json(value: object, cap: int = 80) -> str:
    text = json.dumps(value) if not isinstance(value, str) else value
    return text if len(text) <= cap else text[: cap - 1] + "…"


def freshness_note() -> str:
    return f"(data as of {datetime.now():%Y-%m-%d %H:%M}, unix {int(time.time())})"
