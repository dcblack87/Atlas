"""Morning and weekly briefs.

The skeleton is deterministic — built from the store, always available. The
AI narrative sits on top when budget allows; when it doesn't, the skeleton
IS the brief. Stored in ai_analyses (kind=brief) so the Reports screen can
replay history.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from atlas.ai.client import AIClient, AIDisabled, BudgetExhausted
from atlas.ai.context import ContextBuilder
from atlas.engine.health import health_scores
from atlas.store.db import Database
from atlas.store.incidents import IncidentStore

log = logging.getLogger(__name__)


async def build_skeleton(db: Database, *, window_s: int) -> str:
    incidents = IncidentStore(db)
    scores = await health_scores(incidents)
    open_incidents = await incidents.open_incidents()
    recent = await incidents.recent_incidents(since_s=window_s)
    resolved = [r for r in recent if r["status"] == "resolved"]
    deploys = await db.fetch_all(
        "SELECT * FROM deployments WHERE started_at >= ? ORDER BY started_at",
        (int(time.time()) - window_s,),
    )

    lines = [
        f"fleet health {scores['fleet']}/100",
        f"open incidents: {len(open_incidents)}"
        + (
            f" ({sum(1 for i in open_incidents if i['severity'] == 'critical')} critical)"
            if open_incidents
            else ""
        ),
    ]
    for incident in open_incidents[:5]:
        lines.append(f"  ▲ {incident['title']}")
    if resolved:
        lines.append(f"resolved: {len(resolved)}")
        lines.extend(f"  ✓ {r['title']}" for r in resolved[:5])
    if deploys:
        lines.append(f"deploys: {len(deploys)}")
        for d in deploys:
            lines.append(
                f"  {d['app']} {(d['git_sha_before'] or '?')[:7]} → "
                f"{(d['git_sha_after'] or '?')[:7]} verify={d['verify_status']}"
            )
    forecasts = await db.fetch_all(
        "SELECT entity_key, value FROM facts WHERE name = 'forecast.disk_full_days'"
    )
    for row in forecasts:
        lines.append(f"forecast: {row['entity_key']} disk full in ~{row['value']} days")
    spend = await db.fetch_one("SELECT * FROM ai_spend ORDER BY day DESC LIMIT 1")
    if spend:
        lines.append(f"claude spend {spend['day']}: ${spend['cost_usd']:.2f}")
    return "\n".join(lines)


async def generate_brief(
    db: Database, ai: AIClient | None, context: ContextBuilder | None, *, weekly: bool = False
) -> str:
    window_s = 7 * 86400 if weekly else 86400
    kind = "weekly_brief" if weekly else "brief"
    skeleton = await build_skeleton(db, window_s=window_s)

    narrative = ""
    if ai is not None and context is not None:
        try:
            narrative = await ai.complete(
                kind,
                await context.system_blocks(),
                f"STATUS SKELETON\n{skeleton}\n\n"
                f"Write a {'weekly' if weekly else 'morning'} brief for the operator: "
                "5-10 short lines, most important first, concrete next actions with "
                "commands where useful. No preamble.",
                max_tokens=800,
            )
        except (BudgetExhausted, AIDisabled) as e:
            log.info("brief narrative skipped: %s", e)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = "WEEKLY BRIEF" if weekly else "MORNING BRIEF"
    body = (
        f"{title} — {stamp}\n\n{narrative}\n\n— data —\n{skeleton}"
        if narrative
        else (f"{title} — {stamp}\n\n{skeleton}")
    )
    if ai is None:  # still archive skeleton-only briefs for the Reports screen
        await db.execute(
            "INSERT INTO ai_analyses (ts, kind, cost_usd, response) VALUES (?, ?, 0, ?)",
            (int(time.time()), kind, body),
        )
    else:
        await db.execute(
            "INSERT INTO ai_analyses (ts, kind, cost_usd, response) VALUES (?, ?, 0, ?)",
            (int(time.time()), f"{kind}_rendered", body),
        )
    return body


def due_brief(now: float, last_daily: float, last_weekly: float) -> str | None:
    """Which brief is due? Daily at 07:00 local, weekly Sunday 08:00."""
    dt = datetime.fromtimestamp(now)
    if dt.weekday() == 6 and dt.hour >= 8 and now - last_weekly > 6 * 86400:
        return "weekly"
    if dt.hour >= 7 and now - last_daily > 20 * 3600:
        return "daily"
    return None
