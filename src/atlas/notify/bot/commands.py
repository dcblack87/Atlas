"""Bot commands — every card reads the same store the TUI screens read.

Registry pattern: ``COMMANDS`` maps a name to a description (for Telegram's
command menu) and a handler. Buttons carry ``cmd:<name>[:<arg>…]`` and route
straight back through the same registry, so a typed command and a tap cannot
drift apart. A command with ``listed=False`` is reached only from a button.

Everything here reads, with one exception: acknowledging an incident, which
the TUI also does with one key. It asks first (``ack``) and then does it
(``ack_go``). Deploys stay in the TUI, where they are audited.
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
    footer,
    menu_button,
    page_note,
    pager,
    paginate,
    truncate,
    when,
)

if TYPE_CHECKING:
    from atlas.runtime import Runtime

Handler = Callable[["Runtime", list[str]], Coroutine[Any, Any, BotResponse]]

GLYPH = {"ok": "✅", "warn": "⚠️", "crit": "❌", "unknown": "·"}

INCIDENTS_PER_PAGE = 8
ROWS_PER_PAGE = 12


@dataclass(frozen=True, slots=True)
class Command:
    description: str
    handler: Handler
    listed: bool = True  # False: a button's target, not a typed command


async def process(rt: Runtime, text: str) -> BotResponse | None:
    """Dispatch a message or a ``cmd:<key>[:<arg>…]`` callback. None = not for us."""
    text = text.strip()
    if text.startswith("cmd:"):
        name, *args = text[4:].split(":")
    elif text.startswith("/"):
        first, *args = text.split()
        name = first[1:].lower().split("@")[0]
    else:
        return None
    if name in ("start", "menu"):
        name = "help"
    entry = COMMANDS.get(name)
    if entry is None:
        return BotResponse(
            f"Unknown command: <code>/{esc(name)}</code>. Try /help.",
            [[menu_button()]],
        )
    try:
        return await entry.handler(rt, args)
    except Exception as e:  # a broken card must never kill the poller
        return BotResponse(f"❌ Command failed: {esc(str(e))}", [[menu_button()]])


def bot_command_list() -> list[dict]:
    """For setMyCommands — descriptions double as the autocomplete docs."""
    return [
        {"command": name, "description": cmd.description[:256]}
        for name, cmd in COMMANDS.items()
        if cmd.listed and name != "help"
    ] + [{"command": "help", "description": COMMANDS["help"].description[:256]}]


# ── handlers ─────────────────────────────────────────────────────────────


async def handle_help(rt: Runtime, args: list[str]) -> BotResponse:
    """The menu, with the headline on it: nothing to tap to learn the fleet is fine."""
    from atlas.engine.health import health_scores

    scores = await health_scores(rt.incidents.store)
    fleet = scores.get("fleet", 100)
    open_incidents = await rt.incidents.store.open_incidents()
    critical = sum(1 for i in open_incidents if i["severity"] == "critical")
    pending = await _pending_deploys(rt)
    glyph = "✅" if fleet >= 90 else "⚠️" if fleet >= 60 else "❌"
    lines = [
        f"{glyph} Health <b>{fleet}/100</b>",
        (
            f"🚨 <b>{len(open_incidents)}</b> open incidents"
            + (f" · <b>{critical}</b> critical" if critical else "")
            if open_incidents
            else "🟢 No open incidents"
        ),
    ]
    if pending:
        lines.append(f"🚀 <b>{len(pending)}</b> apps with commits waiting")
    return BotResponse(
        card("🧭", "Atlas Ops", lines),
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
            [button("↻ Refresh", "help")],
        ],
    )


async def handle_status(rt: Runtime, args: list[str]) -> BotResponse:
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
        [[button("🚨 Incidents", "incidents"), button("🖥 Hosts", "hosts")], footer("status")],
    )


async def handle_incidents(rt: Runtime, args: list[str]) -> BotResponse:
    incidents = await rt.incidents.store.open_incidents()
    if not incidents:
        return BotResponse(
            card("🟢", "No open incidents", ["Fleet is quiet."]),
            [[button("📊 Status", "status")], footer("incidents")],
        )
    shown, page, pages = paginate(incidents, args, INCIDENTS_PER_PAGE)
    first = page * INCIDENTS_PER_PAGE
    lines = []
    for number, incident in enumerate(shown, start=first + 1):
        glyph = "❌" if incident["severity"] == "critical" else "⚠️"
        acked = " · acked" if incident["status"] == "acked" else ""
        lines.append(
            f"{number}. {glyph} {esc(truncate(incident['title'], 90))}\n"
            f"    <code>{esc(incident['entity_key'])}</code> · {ago(incident['opened_at'])}{acked}"
        )
    lines.append("")
    lines.append("<i>Tap a number to open that incident.</i>")
    lines.extend(page_note(page, pages))
    numbers = [
        button(str(number), "incident", incident["id"])
        for number, incident in enumerate(shown, start=first + 1)
    ]
    return BotResponse(
        card("🚨", f"Open incidents ({len(incidents)})", lines),
        [
            numbers[:4],
            numbers[4:],
            pager("incidents", page, pages),
            [button("📊 Status", "status")],
            footer("incidents", page),
        ],
    )


def _incident_id(args: list[str]) -> int | None:
    return int(args[0]) if args and args[0].isdigit() else None


async def _incident_card(rt: Runtime, incident: dict, toast: str | None = None) -> BotResponse:
    glyph = "❌" if incident["severity"] == "critical" else "⚠️"
    status = {"open": "open", "acked": "acknowledged", "resolved": "resolved"}.get(
        incident["status"], incident["status"]
    )
    events = await rt.db.fetch_all(
        "SELECT ts, kind, body FROM incident_events WHERE incident_id = ? ORDER BY ts DESC LIMIT 5",
        (incident["id"],),
    )
    lines = [
        f"<code>{esc(incident['entity_key'])}</code>",
        f"{incident['severity']} · {status} · opened {ago(incident['opened_at'])}",
    ]
    if events:
        lines.append("")
        lines.extend(
            f"· {esc(event['kind'])} {ago(event['ts'])}: {esc(truncate(event['body'], 80))}"
            for event in events
        )
    keyboard = [
        [button("✓ Acknowledge", "ack", incident["id"])] if incident["status"] == "open" else [],
        [button("« Incidents", "incidents")],
        footer("incident", incident["id"]),
    ]
    return BotResponse(card(glyph, truncate(incident["title"], 200), lines), keyboard, toast=toast)


def _no_incident() -> BotResponse:
    return BotResponse(
        card("🚨", "Incident", ["That incident is not on file any more."]),
        [[button("« Incidents", "incidents"), menu_button()]],
    )


async def handle_incident(rt: Runtime, args: list[str]) -> BotResponse:
    incident_id = _incident_id(args)
    incident = await rt.incidents.store.get(incident_id) if incident_id is not None else None
    return await _incident_card(rt, incident) if incident else _no_incident()


async def handle_ack(rt: Runtime, args: list[str]) -> BotResponse:
    """The question before anything changes."""
    incident_id = _incident_id(args)
    incident = await rt.incidents.store.get(incident_id) if incident_id is not None else None
    if incident is None:
        return _no_incident()
    if incident["status"] != "open":
        return await _incident_card(rt, incident, toast=f"Already {incident['status']}.")
    return BotResponse(
        card(
            "✓",
            "Acknowledge this incident?",
            [
                esc(truncate(incident["title"], 200)),
                "",
                "The daily reminder stops. It stays on the list until the condition clears.",
            ],
        ),
        [
            [
                button("Yes, acknowledge", "ack_go", incident["id"]),
                button("Cancel", "incident", incident["id"]),
            ],
            [menu_button()],
        ],
    )


async def handle_ack_go(rt: Runtime, args: list[str]) -> BotResponse:
    incident_id = _incident_id(args)
    incident = await rt.incidents.store.get(incident_id) if incident_id is not None else None
    if incident is None:
        return _no_incident()
    if incident["status"] != "open":  # a second tap, or it cleared meanwhile
        return await _incident_card(rt, incident, toast=f"Already {incident['status']}.")
    await rt.incidents.store.acknowledge(incident["id"])
    fresh = await rt.incidents.store.get(incident["id"])
    return await _incident_card(rt, fresh or incident, toast="Acknowledged. Reminders stop.")


async def handle_backups(rt: Runtime, args: list[str]) -> BotResponse:
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
    summary = "All fresh." if not stale else f"{stale} need attention."
    shown, page, pages = paginate(lines, args, ROWS_PER_PAGE)
    return BotResponse(
        card("💾", "Backups", [*shown, "", f"<i>{summary}</i>", *page_note(page, pages)]),
        [pager("backups", page, pages), [button("📦 Apps", "apps")], footer("backups", page)],
    )


async def handle_crons(rt: Runtime, args: list[str]) -> BotResponse:
    jobs = await rt.inventory.entities(kind="cron")
    if not jobs:
        return BotResponse(
            card("⏰", "Cron jobs", ["No cron jobs discovered yet."]),
            [footer("crons")],
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
    shown, page, pages = paginate(rows, args, ROWS_PER_PAGE)
    lines = []
    for status, host, name, last_run in shown:
        run = ago(last_run) if isinstance(last_run, int | float) else "never seen"
        lines.append(f"{glyphs[status]} {esc(truncate(name, 34))} · {esc(host)} · {run}")
    failed = sum(1 for r in rows if r[0] == "failed")
    late = sum(1 for r in rows if r[0] == "late")
    summary = "All healthy." if not failed and not late else f"{failed} failing, {late} late."
    return BotResponse(
        card(
            "⏰",
            f"Cron jobs ({len(rows)})",
            [*lines, "", f"<i>{summary}</i>", *page_note(page, pages)],
        ),
        [
            pager("crons", page, pages),
            [button("🚨 Incidents", "incidents")],
            footer("crons", page),
        ],
    )


async def handle_hosts(rt: Runtime, args: list[str]) -> BotResponse:
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
    shown, page, pages = paginate(lines, args, ROWS_PER_PAGE)
    return BotResponse(
        card("🖥", f"Hosts ({len(lines)})", [*shown, *page_note(page, pages)]),
        [pager("hosts", page, pages), [button("📊 Status", "status")], footer("hosts", page)],
    )


async def handle_apps(rt: Runtime, args: list[str]) -> BotResponse:
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
    shown, page, pages = paginate(lines, args, ROWS_PER_PAGE)
    return BotResponse(
        card("📦", f"Apps ({len(lines)})", [*shown, *page_note(page, pages)]),
        [
            pager("apps", page, pages),
            [button("💾 Backups", "backups"), button("🚀 Deploys", "deploys")],
            footer("apps", page),
        ],
    )


async def handle_deploys(rt: Runtime, args: list[str]) -> BotResponse:
    pending = await _pending_deploys(rt)
    if not pending:
        return BotResponse(
            card("🚀", "Deploys", ["Everything is in sync with origin/main."]),
            [[button("📦 Apps", "apps")], footer("deploys")],
        )
    lines = [f"▲ <b>{esc(name)}</b> — {int(behind)} commits behind" for name, behind in pending]
    lines.append("")
    lines.append("<i>Deploy from the TUI (key 4) — deploys stay audited.</i>")
    return BotResponse(
        card("🚀", f"Pending deploys ({len(pending)})", lines),
        [[button("📦 Apps", "apps")], footer("deploys")],
    )


async def handle_digest(rt: Runtime, args: list[str]) -> BotResponse:
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
            footer("digest"),
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
    # Reached from a button, never typed.
    "incident": Command("One incident", handle_incident, listed=False),
    "ack": Command("Acknowledge an incident?", handle_ack, listed=False),
    "ack_go": Command("Acknowledge an incident", handle_ack_go, listed=False),
}
