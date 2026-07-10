# Atlas

**An AI-native operations platform for self-hosted infrastructure.**

Grafana + Datadog + PagerDuty + Claude — rebuilt for one developer, one Hetzner bill, and an e-ink tablet.

Atlas is a terminal operations centre that runs 24/7 in tmux on one of your own servers. It discovers your fleet automatically — hosts, containers, sites, vhosts, certs, crons, backups — watches it, explains problems with Claude, pushes deploys behind a typed confirmation, and pages you on Telegram only when something is genuinely on fire.

```
┌─────────────────────────── ATLAS ───────────────────────────┐
│  Fleet Health   Hosts        Apps         Open Incidents    │
│     97/100       3/3          14               0            │
│                                                             │
│  ● web-1        load 0.42   mem 61%   disk 48%  ▁▂▂▃▂▂▁     │
│  ● web-2        load 0.11   mem 37%   disk 71%  ▁▁▂▁▁▁▁     │
│  ▲ sites-1      cert shopfront.io expires in 9 days         │
└─────────────────────────────────────────────────────────────┘
```

## Why

Traditional observability stacks assume a team, a budget, and a wall of monitors. A solo founder running real products on a few VPSes needs something else entirely:

- **One process, one SQLite file, zero agents.** Atlas is agentless — it observes over SSH (via your tailnet) and local subprocess. Nothing to install on the machines it watches.
- **AI that interprets, not decorates.** Atlas doesn't say "disk 84%". It says *"disk grew 14 GB this week, almost entirely Docker build cache from your Next.js deploys — `docker builder prune` recovers ~18 GB"* — with a hard daily API budget enforced in code.
- **Designed for e-ink.** Atlas runs beautifully in any terminal, but it's built to live on a low-power e-ink tablet as an always-on physical ops cockpit: change-driven rendering, coalesced updates, zero animation, glyphs instead of colour. MacBook for development, e-ink for operations, phone for emergencies.
- **Read-only by default.** Exactly one module can mutate your servers — the deploy orchestrator — and it sits behind a typed confirmation, an allowlist, and an audit trail. Atlas never needs your cloud credentials for anything but read-only billing queries.

## Quickstart

```bash
git clone https://github.com/dcblack87/Atlas && cd Atlas
uv sync

# see it immediately — fixture fleet, no SSH, no secrets:
uv run atlas run --demo

# then wire up your own fleet:
cp atlas.example.toml atlas.toml && $EDITOR atlas.toml
uv run atlas check
uv run atlas run
```

For an always-on installation, run it in tmux on a server: `tmux new -s atlas scripts/atlas-tmux.sh` — then attach from anything that can SSH, including an e-ink tablet.

## Status

Early and moving fast. Milestones:

- [x] **M0** — skeleton: config, TUI shell, display profiles (standard / eink / glance)
- [ ] **M1** — fleet visibility: SSH/local collectors, discovery, live dashboard
- [ ] **M2** — incidents: rules engine, health scores, Telegram alerts, demo mode
- [ ] **M3** — deploys: audited push-button deploys with post-deploy verification
- [ ] **M4** — AI: budget-capped insights, incident explanation, grounded chat
- [ ] **M5** — intelligence: baselines & anomaly detection, deploy drift, cost dashboard, briefs

## Design

See [docs/architecture.md](docs/architecture.md). The short version:

```
Transport (local subprocess | pooled SSH)
   → Collectors (async loops, per-collector cadence)
      → SQLite (inventory · metrics · incidents · audit)
         → Decision engine (rules → incidents · health · forecasts)
            → Textual TUI · Telegram · Claude
```

Strict layering: collectors never touch the UI, the UI never runs commands, and everything meets at the store. The AI layer reads the same SQLite everything else does — SQL is the retrieval engine; there is no vector database because there doesn't need to be one.

## License

MIT
