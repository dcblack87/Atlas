"""Bot commands — every card reads the same store the TUI screens read.

Registry pattern: ``COMMANDS`` maps a slash-command name to a description
(for Telegram's command menu) and a handler. Callback buttons carry
``cmd:<name>`` and route straight back through the same registry.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from atlas.notify.bot.format import (
    BotResponse,
    ago,
    button,
    card,
    esc,
    menu_button,
    truncate,
    when,
)

if TYPE_CHECKING:
    from atlas.runtime import Runtime

Handler = Callable[["Runtime"], Coroutine[Any, Any, BotResponse]]

GLYPH = {"ok": "✅", "warn": "⚠️", "crit": "❌", "unknown": "·"}


@dataclass(frozen=True, slots=True)
class Command:
    description: str
    handler: Handler


async def process(rt: Runtime, text: str) -> BotResponse | None:
    """Dispatch a message or ``cmd:<key>`` callback. None = not for us."""
    text = text.strip()
    if text.startswith("cmd:"):
        text = "/" + text[4:]
    if not text.startswith("/"):
        return None
    name = text.split()[0][1:].lower().split("@")[0]
    if name == "start":
        name = "help"
    entry = COMMANDS.get(name)
    if entry is None:
        return BotResponse(
            f"Unknown command: <code>/{esc(name)}</code>. Try /help.",
            [[menu_button()]],
        )
    try:
        return await entry.handler(rt)
    except Exception as e:  # a broken card must never kill the poller
        return BotResponse(f"❌ Command failed: {esc(str(e))}", [[menu_button()]])


def bot_command_list() -> list[dict]:
    """For setMyCommands — descriptions double as the autocomplete docs."""
    return [
        {"command": name, "description": cmd.description[:256]}
        for name, cmd in COMMANDS.items()
        if name != "help"
    ] + [{"command": "help", "description": COMMANDS["help"].description[:256]}]


# ── handlers ─────────────────────────────────────────────────────────────


async def handle_help(rt: Runtime) -> BotResponse:
    return BotResponse(
        "🧭 <b>Atlas Ops</b>\n\nTap a button or type a command.",
        [
            [button("📊 Status", "status"), button("📰 Digest", "digest")],
            [
                button("🚨 Incidents", "incidents"),
                button("⏰ Crons", "crons"),
                button("💾 Backups", "backups"),
            ],
            [
                button("🖥 Hosts", "hosts"),
                button("📦 Apps", "apps"),
                button("🚀 Deploys", "deploys"),
            ],
        ],
    )


async def handle_status(rt: Runtime) -> BotResponse:
    from atlas.engine.health import health_scores

    scores = await health_scores(rt.incidents.store)
    fleet = scores.get("fleet", 100)
    hosts = await rt.inventory.entities(kind="host")
    hosts_up = 0
    for host in hosts:
        snap = await rt.metrics.latest_snapshot(host["key"])
        if snap.get("host.up", 1.0) >= 1:
            hosts_up += 1
    apps = await rt.inventory.entities(kind="app")
    open_incidents = await rt.incidents.store.open_incidents()
    pending = await _pending_deploys(rt)

    health_glyph = "✅" if fleet >= 90 else "⚠️" if fleet >= 60 else "❌"
    lines = [
        f"{health_glyph} Health: <b>{fleet}/100</b>",
        f"🖥 Hosts up: <b>{hosts_up}/{len(hosts)}</b>",
        f"📦 Apps: <b>{len(apps)}</b>",
        f"🚨 Open incidents: <b>{len(open_incidents)}</b>",
        f"🚀 Pending deploys: <b>{len(pending)}</b>"
        + (f" ({', '.join(esc(a) for a, _ in pending[:3])})" if pending else ""),
    ]
    return BotResponse(
        card("📊", "Fleet status", lines),
        [[button("🚨 Incidents", "incidents"), button("🖥 Hosts", "hosts"), menu_button()]],
    )


async def handle_incidents(rt: Runtime) -> BotResponse:
    incidents = await rt.incidents.store.open_incidents()
    if not incidents:
        return BotResponse(
            card("🟢", "No open incidents", ["Fleet is quiet."]),
            [[button("📊 Status", "status"), menu_button()]],
        )
    lines = []
    for incident in incidents[:10]:
        glyph = "❌" if incident["severity"] == "critical" else "⚠️"
        lines.append(
            f"{glyph} {esc(truncate(incident['title'], 90))}\n"
            f"    <code>{esc(incident['entity_key'])}</code> · {ago(incident['opened_at'])}"
        )
    if len(incidents) > 10:
        lines.append(f"<i>… and {len(incidents) - 10} more</i>")
    return BotResponse(
        card("🚨", f"Open incidents ({len(incidents)})", lines),
        [[button("📊 Status", "status"), menu_button()]],
    )


async def handle_backups(rt: Runtime) -> BotResponse:
    apps = await rt.inventory.entities(kind="app")
    lines = []
    stale = 0
    for app in sorted(apps, key=lambda a: a["key"]):
        name = app["key"].removeprefix("app:")
        facts = await rt.inventory.facts_for(app["key"])
        age = facts.get("backup.age_hours")
        ts = facts.get("backup.last_ts")
        if not isinstance(age, int | float):
            lines.append(f"❌ {esc(name)}: <b>no backups found</b>")
            stale += 1
            continue
        glyph = "✅" if age < 30 else "⚠️" if age < 54 else "❌"
        if age >= 30:
            stale += 1
        date = f" · <code>{when(ts)}</code>" if isinstance(ts, int | float) else ""
        lines.append(f"{glyph} {esc(name)}: <b>{age:.1f}h ago</b>{date}")
    footer = "All fresh." if not stale else f"{stale} need attention."
    return BotResponse(
        card("💾", "Backups", [*lines, "", f"<i>{footer}</i>"]),
        [[button("📦 Apps", "apps"), menu_button()]],
    )


async def handle_crons(rt: Runtime) -> BotResponse:
    jobs = await rt.inventory.entities(kind="cron")
    if not jobs:
        return BotResponse(
            card("⏰", "Cron jobs", ["No cron jobs discovered yet."]),
            [[menu_button()]],
        )
    rows = []
    for job in jobs:
        facts = await rt.inventory.facts_for(job["key"])
        host = job["key"].removeprefix("cron:").partition("/")[0]
        name = str(job["attrs"].get("name", job["key"]))
        status = _cron_status(facts)
        last_run = facts.get("cron.last_run_ts")
        rows.append((status, host, name, last_run))

    weight = {"failed": 0, "late": 1, "unknown": 2, "ok": 3}
    rows.sort(key=lambda r: (weight[r[0]], r[1], r[2]))
    glyphs = {"failed": "❌", "late": "⚠️", "ok": "✅", "unknown": "·"}
    lines = []
    for status, host, name, last_run in rows[:12]:
        run = ago(last_run) if isinstance(last_run, int | float) else "never seen"
        lines.append(f"{glyphs[status]} {esc(truncate(name, 34))} · {esc(host)} · {run}")
    if len(rows) > 12:
        lines.append(f"<i>… and {len(rows) - 12} more</i>")
    failed = sum(1 for r in rows if r[0] == "failed")
    late = sum(1 for r in rows if r[0] == "late")
    summary = "All healthy." if not failed and not late else f"{failed} failing, {late} late."
    return BotResponse(
        card("⏰", f"Cron jobs ({len(rows)})", [*lines, "", f"<i>{summary}</i>"]),
        [[button("🚨 Incidents", "incidents"), menu_button()]],
    )


async def handle_hosts(rt: Runtime) -> BotResponse:
    hosts = await rt.inventory.entities(kind="host")
    lines = []
    for host in sorted(hosts, key=lambda h: h["key"]):
        name = host["key"].removeprefix("host:")
        snap = await rt.metrics.latest_snapshot(host["key"])
        if snap.get("host.up", 1.0) < 1:
            lines.append(f"🔴 <b>{esc(name)}</b> — DOWN")
            continue
        load = snap.get("load.1m")
        mem = snap.get("mem.used_pct")
        disk = snap.get("disk.used_pct")
        worst = max(_pct_level(mem, 90, 97), _pct_level(disk, 80, 90))
        glyph = ["🟢", "⚠️", "❌"][worst]
        lines.append(
            f"{glyph} <b>{esc(name)}</b> — load {_num(load)} · "
            f"mem {_num(mem)}% · disk {_num(disk)}%"
        )
    return BotResponse(
        card("🖥", "Hosts", lines),
        [[button("📊 Status", "status"), menu_button()]],
    )


async def handle_apps(rt: Runtime) -> BotResponse:
    apps = await rt.inventory.entities(kind="app")
    lines = []
    for app in sorted(apps, key=lambda a: a["key"]):
        name = app["key"].removeprefix("app:")
        snap = await rt.metrics.latest_snapshot(app["key"])
        up = snap.get("http.up")
        ms = snap.get("http.response_ms")
        glyph = "🟢" if up is None or up >= 1 else "🔴"
        latency = f" · {ms:.0f}ms" if ms is not None else ""
        sites = await rt.inventory.entities(kind="site", parent=app["key"])
        site_note = f" · {len(sites)} sites" if sites else ""
        lines.append(f"{glyph} <b>{esc(name)}</b>{latency}{site_note}")
    return BotResponse(
        card("📦", "Apps", lines),
        [[button("💾 Backups", "backups"), button("🚀 Deploys", "deploys"), menu_button()]],
    )


async def handle_deploys(rt: Runtime) -> BotResponse:
    pending = await _pending_deploys(rt)
    if not pending:
        return BotResponse(
            card("🚀", "Deploys", ["Everything is in sync with origin/main."]),
            [[button("📦 Apps", "apps"), menu_button()]],
        )
    lines = [f"▲ <b>{esc(name)}</b> — {int(behind)} commits behind" for name, behind in pending]
    lines.append("")
    lines.append("<i>Deploy from the TUI (key 4) — deploys stay audited.</i>")
    return BotResponse(
        card("🚀", f"Pending deploys ({len(pending)})", lines),
        [[button("📦 Apps", "apps"), menu_button()]],
    )


async def handle_digest(rt: Runtime) -> BotResponse:
    recent = await rt.incidents.store.recent_incidents(since_s=86400)
    opened = len(recent)
    resolved = sum(1 for i in recent if i.get("status") == "resolved")
    still_open = await rt.incidents.store.open_incidents()

    apps = await rt.inventory.entities(kind="app")
    backups_stale = 0
    for app in apps:
        age = await rt.inventory.get_fact(app["key"], "backup.age_hours")
        if not isinstance(age, int | float) or age >= 30:
            backups_stale += 1

    jobs = await rt.inventory.entities(kind="cron")
    crons_bad = 0
    for job in jobs:
        facts = await rt.inventory.facts_for(job["key"])
        if _cron_status(facts) in ("failed", "late"):
            crons_bad += 1

    lines = [
        f"🚨 Incidents (24h): <b>{opened}</b> opened · <b>{resolved}</b> resolved"
        f" · <b>{len(still_open)}</b> still open",
        f"💾 Backups: <b>{len(apps) - backups_stale}/{len(apps)}</b> fresh",
        f"⏰ Crons: <b>{len(jobs) - crons_bad}/{len(jobs)}</b> healthy",
    ]
    return BotResponse(
        card("📰", "Daily digest", lines),
        [
            [
                button("🚨 Incidents", "incidents"),
                button("💾 Backups", "backups"),
                button("⏰ Crons", "crons"),
            ],
            [menu_button()],
        ],
    )


# ── helpers ──────────────────────────────────────────────────────────────


async def _pending_deploys(rt: Runtime) -> list[tuple[str, float]]:
    rows = await rt.db.fetch_all(
        "SELECT entity_key, value FROM facts WHERE name = 'drift.commits_behind'"
    )
    pending = []
    for row in rows:
        try:
            behind = float(json.loads(row["value"]))
        except (TypeError, ValueError):
            continue
        if behind > 0:
            pending.append((row["entity_key"].removeprefix("app:"), behind))
    return sorted(pending, key=lambda p: -p[1])


def _cron_status(facts: dict) -> str:
    if facts.get("cron.last_status") == "failed":
        return "failed"
    ratio = facts.get("cron.overdue_ratio")
    if isinstance(ratio, int | float):
        return "late" if ratio >= 2 else "ok"
    return "ok" if facts.get("cron.last_status") == "ok" else "unknown"


def _pct_level(value: float | None, warn: float, crit: float) -> int:
    if value is None:
        return 0
    return 2 if value >= crit else 1 if value >= warn else 0


def _num(value: float | None) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") if value is not None else "?"


COMMANDS: dict[str, Command] = {
    "status": Command("Fleet overview: health, hosts, incidents, deploys", handle_status),
    "digest": Command("24h summary: incidents, backups, crons", handle_digest),
    "incidents": Command("Open incidents", handle_incidents),
    "crons": Command("Cron jobs across the fleet", handle_crons),
    "backups": Command("Backup freshness per app", handle_backups),
    "hosts": Command("Host load, memory, disk", handle_hosts),
    "apps": Command("App health and latency", handle_apps),
    "deploys": Command("Apps with commits waiting to deploy", handle_deploys),
    "help": Command("The menu", handle_help),
}
