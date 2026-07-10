# Architecture

Atlas is one Python process: an asyncio core with a Textual UI on top.

```
┌──────────────────────────── TUI (Textual screens) ────────────────────────────┐
│  reads store + subscribes to bus; issues commands (deploy, ask AI, bundle)    │
└──────────────▲──────────────────────────────▲─────────────────────────────────┘
               │ bus events                    │ queries
┌──────────────┴───────────────┐   ┌──────────┴──────────────┐
│  Decision / Insight engine   │   │       SQLite store       │
│  rules → findings → incidents│──▶│  inventory · metrics ·   │
│  health · forecast · AI hooks│   │  incidents · audit · ai  │
└──────────────▲───────────────┘   └──────────▲──────────────┘
               │ samples (bus)                 │ writes
┌──────────────┴────────────────────────────────┴──────────────┐
│                    Scheduler + Collectors                     │
│   per-(host, collector) asyncio loops, TTLs, jitter, backoff  │
└──────────────▲───────────────────────────────────────────────┘
               │ run(cmd) / stream(cmd)
┌──────────────┴───────────────────────────────┐
│   Transport: LocalTransport | SSHTransport   │
│   (asyncssh pool, 1 conn/host, N channels)   │
└──────────────────────────────────────────────┘
```

## Rules of the architecture

1. **Collectors never touch the TUI; the TUI never runs commands.** Everything meets at the store and the event bus. The TUI is killable and restartable without losing monitoring state.
2. **Read-only by default.** `atlas.deploy` is the only module allowed to construct a mutating command. This invariant is enforced by a test.
3. **Be a polite guest.** Atlas runs on a production box: per-host channel semaphores, command timeouts, bounded DB size, and Atlas monitors itself as just another app.
4. **Budget-deterministic AI.** Every Claude call passes a budget gate that reads a spend ledger *before* the call. Cost is recorded from actual API usage, never estimated.

## Layers

- **Transport** (`atlas.transport`) — a `Transport` protocol with `run()` and `stream()`. `LocalTransport` shells out via subprocess (used for the host Atlas runs on — no dependency on sshd for self-monitoring). `SSHTransport` holds one asyncssh connection per host and multiplexes channels over it, with keepalives and capped-backoff reconnect.
- **Collectors** (`atlas.collectors`) — one file per concern, registered by decorator. Each implements `discover()` (inventory), `collect()` (one composite command per run), and `analyze()` (pure function, unit-testable against recorded fixtures).
- **Store** (`atlas.store`) — SQLite in WAL mode, single writer task. Raw metrics are kept 48h, hourly rollups 90 days, daily rollups forever. Numbered SQL migrations gated by `PRAGMA user_version`.
- **Engine** (`atlas.engine`) — a declarative rule table with hysteresis, an incident lifecycle with dedupe and auto-resolve, per-entity health scores, and least-squares forecasting ("disk full in ~23 days").
- **AI** (`atlas.ai`) — context assembly straight from SQLite (RAG-lite: entity/time-scoped SQL, no vector DB), budget-gated Anthropic client with prompt caching, insight generation, chat, and Markdown context bundles for use with Claude Code.
- **Deploy** (`atlas.deploy`) — preflight → typed confirmation → streamed execution → post-deploy verification → audit row. Guided remediations run through the same gate from an allowlist of templates.
- **TUI** (`atlas.tui`) — Textual screens and widgets, tuned for e-ink: no animation, change-driven rendering, coalesced update flushes, fixed layouts that never reflow on value changes.
