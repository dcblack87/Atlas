-- Atlas schema v1. Applied migrations are tracked via PRAGMA user_version.

-- ── Inventory ────────────────────────────────────────────────────────
CREATE TABLE entities (
    id         INTEGER PRIMARY KEY,
    kind       TEXT    NOT NULL,           -- host | app | site | container | vhost | cron | cert | db
    key        TEXT    NOT NULL,           -- "host:web-1", "container:web-1/redis", "site:sitefarm/acme"
    parent_key TEXT,
    first_seen INTEGER NOT NULL,           -- unix seconds
    last_seen  INTEGER NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    attrs      TEXT    NOT NULL DEFAULT '{}',
    UNIQUE (kind, key)
);
CREATE INDEX idx_entities_key ON entities (key);

-- Current non-numeric state: cert dates, versions, git shas, cron last-run.
CREATE TABLE facts (
    entity_key TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    value      TEXT    NOT NULL,           -- JSON scalar or object
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (entity_key, name)
);

-- ── Metrics time-series ──────────────────────────────────────────────
CREATE TABLE metrics_raw (                 -- full resolution, ~48h retention
    ts         INTEGER NOT NULL,
    entity_key TEXT    NOT NULL,
    metric     TEXT    NOT NULL,
    value      REAL    NOT NULL
);
CREATE INDEX idx_metrics_raw ON metrics_raw (entity_key, metric, ts);
CREATE INDEX idx_metrics_raw_ts ON metrics_raw (ts);

CREATE TABLE metrics_hourly (              -- ~90 day retention
    ts_hour    INTEGER NOT NULL,
    entity_key TEXT    NOT NULL,
    metric     TEXT    NOT NULL,
    min        REAL, max REAL, avg REAL, last REAL,
    n          INTEGER NOT NULL,
    PRIMARY KEY (entity_key, metric, ts_hour)
);

CREATE TABLE metrics_daily (               -- kept indefinitely
    ts_day     INTEGER NOT NULL,
    entity_key TEXT    NOT NULL,
    metric     TEXT    NOT NULL,
    min        REAL, max REAL, avg REAL, last REAL,
    n          INTEGER NOT NULL,
    PRIMARY KEY (entity_key, metric, ts_day)
);

-- ── Incidents ────────────────────────────────────────────────────────
CREATE TABLE incidents (
    id          INTEGER PRIMARY KEY,
    rule_id     TEXT    NOT NULL,
    entity_key  TEXT    NOT NULL,
    severity    TEXT    NOT NULL,          -- warning | critical
    status      TEXT    NOT NULL,          -- open | acked | resolved
    title       TEXT    NOT NULL,
    opened_at   INTEGER NOT NULL,
    resolved_at INTEGER,
    detail      TEXT    NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX idx_incident_open ON incidents (rule_id, entity_key)
    WHERE status != 'resolved';

-- The timeline: incident transitions, deploys, discovery diffs, AI insights.
CREATE TABLE incident_events (
    id          INTEGER PRIMARY KEY,
    incident_id INTEGER REFERENCES incidents (id),
    ts          INTEGER NOT NULL,
    kind        TEXT    NOT NULL,          -- opened|escalated|resolved|acked|deploy|entity_added|entity_removed|note|ai_insight
    body        TEXT    NOT NULL
);
CREATE INDEX idx_events_ts ON incident_events (ts);

-- ── Deploys (audit) ──────────────────────────────────────────────────
CREATE TABLE deployments (
    id               INTEGER PRIMARY KEY,
    app              TEXT    NOT NULL,
    host             TEXT    NOT NULL,
    started_at       INTEGER NOT NULL,
    finished_at      INTEGER,
    command          TEXT    NOT NULL,
    git_sha_before   TEXT,
    git_sha_after    TEXT,
    exit_code        INTEGER,
    verify_status    TEXT,                 -- passed | failed | skipped
    output           TEXT,                 -- capped, head+tail truncated
    confirmed_phrase TEXT    NOT NULL
);

-- ── Alerts & AI ──────────────────────────────────────────────────────
CREATE TABLE alerts (
    id          INTEGER PRIMARY KEY,
    ts          INTEGER NOT NULL,
    channel     TEXT    NOT NULL,
    incident_id INTEGER,
    payload     TEXT    NOT NULL,
    delivered   INTEGER NOT NULL,
    error       TEXT
);

CREATE TABLE ai_analyses (
    id                INTEGER PRIMARY KEY,
    ts                INTEGER NOT NULL,
    kind              TEXT    NOT NULL,    -- insight | chat | brief | incident_explain | entity_explain
    incident_id       INTEGER,
    entity_key        TEXT,
    model             TEXT,
    input_tokens      INTEGER,
    output_tokens     INTEGER,
    cache_read_tokens INTEGER,
    cost_usd          REAL,
    prompt_digest     TEXT,
    response          TEXT
);
CREATE INDEX idx_ai_digest ON ai_analyses (prompt_digest);

CREATE TABLE ai_spend (
    day           TEXT PRIMARY KEY,        -- YYYY-MM-DD (UTC)
    cost_usd      REAL    NOT NULL DEFAULT 0,
    auto_cost_usd REAL    NOT NULL DEFAULT 0,
    calls         INTEGER NOT NULL DEFAULT 0
);
