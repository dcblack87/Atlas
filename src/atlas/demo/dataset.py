"""The demo fleet: a plausible three-host setup, no SSH, no secrets.

``atlas run --demo`` seeds this into a throwaway database so a first-time
visitor sees the product in thirty seconds. Also the source of README
screenshots — never real infrastructure.
"""

from __future__ import annotations

import math
import random
import time

from atlas.model import Entity, EntityKind, Sample
from atlas.store.db import Database
from atlas.store.incidents import IncidentStore
from atlas.store.inventory import Inventory
from atlas.store.metrics import Metrics

HOSTS = ("web-1", "web-2", "sites-1")

CONTAINERS = {
    "web-1": [
        ("exampleapp-backend", "running", "healthy", 0),
        ("exampleapp-frontend", "running", "healthy", 0),
        ("exampleapp-postgres", "running", "healthy", 0),
        ("exampleapp-redis", "running", "healthy", 0),
        ("exampleapp-worker", "running", "healthy", 2),
    ],
    "web-2": [("shopfront", "running", "", 0)],
    "sites-1": [
        ("sitefarm-postgres", "running", "healthy", 0),
        ("sitefarm-acmedetailing", "running", "", 0),
        ("sitefarm-plumberspro", "running", "", 0),
        ("sitefarm-roofersnearme", "running", "", 1),
    ],
}

SITES = ["acmedetailing", "plumberspro", "roofersnearme"]

BASELINES = {  # (load, mem%, disk%)
    "web-1": (0.42, 61, 48),
    "web-2": (0.11, 37, 71),
    "sites-1": (0.25, 52, 44),
}

TOTALS = {  # (mem GB, disk GB) — feeds headroom / site-capacity estimates
    "web-1": (8, 80),
    "web-2": (4, 40),
    "sites-1": (8, 80),
}

GB = 1024**3


async def seed_demo(db: Database) -> None:
    rng = random.Random(20260710)  # deterministic — stable screenshots
    inventory = Inventory(db)
    metrics = Metrics(db)
    incidents = IncidentStore(db)
    now = int(time.time())

    apps = {"web-1": "exampleapp", "web-2": "shopfront", "sites-1": "sitefarm"}
    for host in HOSTS:
        host_key = f"host:{host}"
        await inventory.upsert(
            Entity(EntityKind.HOST, host_key, attrs={"address": f"100.64.0.{HOSTS.index(host)}"})
        )
        app = apps[host]
        await inventory.upsert(
            Entity(EntityKind.APP, f"app:{app}", parent=host_key, attrs={"kind": "demo"})
        )
        await inventory.set_fact(f"app:{app}", "git.sha", "d3adb33f" + "0" * 32)
        await inventory.set_fact(f"app:{app}", "git.branch", "main")
        backup_age = rng.uniform(2, 8)
        await inventory.set_fact(f"app:{app}", "backup.age_hours", backup_age)
        await inventory.set_fact(f"app:{app}", "backup.last_ts", int(now - backup_age * 3600))
        for name, state, health, restarts in CONTAINERS[host]:
            key = f"container:{host}/{name}"
            await inventory.upsert(
                Entity(
                    EntityKind.CONTAINER,
                    key,
                    parent=host_key,
                    attrs={
                        "image": f"{name}:latest",
                        "state": state,
                        "health": health,
                        "status": "Up 2 days",
                    },
                )
            )
            await metrics.write(
                [
                    Sample("container.up", 1.0, key),
                    Sample("container.restarts", float(restarts), key),
                    Sample("container.cpu_pct", round(rng.uniform(0.1, 4.0), 2), key),
                    Sample("container.mem_pct", round(rng.uniform(0.5, 12.0), 1), key),
                    Sample("container.mem_bytes", rng.uniform(180, 420) * 1024**2, key),
                ]
            )
        mem_gb, disk_gb = TOTALS[host]
        _, _, disk_pct = BASELINES[host]
        await metrics.write(
            [
                Sample("mem.total_bytes", float(mem_gb * GB), host_key),
                Sample("disk.total_bytes", float(disk_gb * GB), host_key),
                Sample("disk.used_bytes", disk_gb * GB * disk_pct / 100, host_key),
            ]
        )

    for site in SITES:
        await inventory.upsert(
            Entity(
                EntityKind.SITE,
                f"site:sitefarm/{site}",
                parent="app:sitefarm",
                attrs={"port": 5001 + SITES.index(site), "container": f"sitefarm-{site}"},
            )
        )
        await metrics.write(
            [
                Sample("http.up", 1.0, f"site:sitefarm/{site}"),
                Sample("http.response_ms", round(rng.uniform(40, 240), 1), f"site:sitefarm/{site}"),
            ]
        )
    for app_entity in ("app:exampleapp", "app:shopfront"):
        await metrics.write(
            [
                Sample("http.up", 1.0, app_entity),
                Sample("http.response_ms", round(rng.uniform(60, 180), 1), app_entity),
            ]
        )

    # 24h of host history at 5-minute resolution, with a gentle daily curve
    samples: list[tuple[int, list[Sample]]] = []
    for host in HOSTS:
        load_base, mem_base, disk_base = BASELINES[host]
        key = f"host:{host}"
        for i in range(288):
            ts = now - (288 - i) * 300
            wave = math.sin(i / 288 * 2 * math.pi)
            batch = [
                Sample(
                    "load.1m", max(0.02, load_base + wave * 0.2 + rng.uniform(-0.05, 0.05)), key
                ),
                Sample("cpu.load_per_core", max(0.01, (load_base + wave * 0.2) / 3), key),
                Sample("mem.used_pct", min(99, mem_base + wave * 5 + rng.uniform(-1, 1)), key),
                Sample("disk.used_pct", disk_base + i * 0.002, key),
                Sample("docker.running", float(len(CONTAINERS[host])), key),
                Sample("host.up", 1.0, key),
            ]
            samples.append((ts, batch))
    for ts, batch in samples:
        await metrics.write(batch, ts=ts)

    # the demo story: one warning brewing, one resolved overnight incident
    await inventory.set_fact("cert:sites-1/shopfront.io.pem", "cert.days_remaining", 9.0)
    cert_incident = await incidents.open_incident(
        "cert_expiry",
        "cert:sites-1/shopfront.io.pem",
        "warning",
        "certificate sites-1/shopfront.io.pem expires in 9 days",
    )
    _ = cert_incident

    resolved = await incidents.open_incident(
        "http_down", "site:sitefarm/plumberspro", "critical", "site plumberspro is not responding"
    )
    await incidents.resolve(resolved, "recovered after container restart")
    await incidents.add_event(None, "deploy", "deployed exampleapp 8f31a2c → d3adb33 ✓")

    # a week of AI spend well under a $1.50/day budget
    for i in range(7):
        day = time.strftime("%Y-%m-%d", time.gmtime(now - i * 86400))
        await db.execute(
            "INSERT OR REPLACE INTO ai_spend (day, cost_usd, auto_cost_usd, calls) "
            "VALUES (?, ?, ?, ?)",
            (
                day,
                round(rng.uniform(0.04, 0.55), 3),
                round(rng.uniform(0.01, 0.2), 3),
                rng.randint(2, 14),
            ),
        )

    # cron jobs: mostly healthy, one failing backup so screen 9 tells a story
    crons = [
        # (host, slug, name, schedule, source, last_run_offset_s, interval, status)
        (
            "web-1",
            "backup-postgres",
            "Database backup",
            "0 3 * * *",
            "crontab",
            6 * 3600,
            86400,
            "ok",
        ),
        ("web-1", "renew-metrics", "Renew metrics", "*/15 * * * *", "crontab", 480, 900, "ok"),
        (
            "web-1",
            "report-weekly",
            "Weekly report",
            "30 6 * * 1",
            "crontab",
            2 * 86400,
            604800,
            "ok",
        ),
        (
            "web-2",
            "backup-shopfront",
            "Shopfront backup",
            "0 2 * * *",
            "crontab",
            58 * 3600,
            86400,
            "failed",
        ),
        ("web-2", "renew-sitemap", "Renew sitemap", "0 * * * *", "cron.d", 1800, 3600, "ok"),
        ("sites-1", "celery-sync-listings", "sync listings", "celery", "celery", 240, 86400, "ok"),
        (
            "sites-1",
            "celery-purge-sessions",
            "purge sessions",
            "celery",
            "celery",
            7200,
            86400,
            "ok",
        ),
    ]
    for host, slug, cname, schedule, source, offset, interval, status in crons:
        key = f"cron:{host}/{slug}"
        await inventory.upsert(
            Entity(
                EntityKind.CRON,
                key,
                parent=f"host:{host}",
                attrs={"name": cname, "schedule": schedule, "source": source},
            )
        )
        await inventory.set_fact(key, "cron.schedule", schedule)
        await inventory.set_fact(key, "cron.source", source)
        await inventory.set_fact(key, "cron.last_run_ts", now - offset)
        await inventory.set_fact(key, "cron.expected_interval_s", float(interval))
        await inventory.set_fact(key, "cron.overdue_ratio", round(offset / interval, 2))
        await inventory.set_fact(key, "cron.last_status", status)
    await incidents.open_incident(
        "cron_failed",
        "cron:web-2/backup-shopfront",
        "warning",
        "cron job Shopfront backup on web-2 is failing",
    )

    # M5 texture: costs, drift, security posture, a forecast
    for host, cost in (("web-1", 16.18), ("web-2", 8.51), ("sites-1", 8.51)):
        await inventory.set_fact(f"host:{host}", "cost.monthly_eur", cost)
    await inventory.set_fact("project:demo", "cost.monthly_eur", 33.20)
    await inventory.set_fact("app:shopfront", "drift.commits_behind", 3)
    await inventory.set_fact("app:shopfront", "github.ci_status", "success")
    await inventory.set_fact("app:shopfront", "github.open_prs", 2)
    await inventory.set_fact("host:web-2", "forecast.disk_full_days", 43.5)
    for host in HOSTS:
        await inventory.set_fact(f"host:{host}", "security.pending_updates", rng.randint(0, 14))
        await inventory.set_fact(f"host:{host}", "security.reboot_required", host == "sites-1")
        await inventory.set_fact(f"host:{host}", "security.public_ports", [22, 80, 443])
        await metrics.write(
            [Sample("security.failed_auth_1h", float(rng.randint(0, 40)), f"host:{host}")]
        )
