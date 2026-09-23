-- 388 — FEED STATUS for /admin/feeds ([[#107]] FEEDS-DASHBOARD, 2026-09-23)
--
-- Owner: "for every site we scrape — when was the last successful scrape, what is
-- the interval, what is its status … I want to discover problems much faster and
-- act on them via web."
--
-- Written every 5 min by workers/jobs/feed_health.py from the registry in
-- workers/registry/feed_registry.py. Computed engine-side because two inputs are
-- invisible to the web app: systemd/docker state on the VPS, and an aggregate over
-- odds_snapshots (the largest table) that must not run per page view.
--
-- Health is judged on OUTPUT (newest row written), not on the job's own verdict:
-- the query that first read this data found Coolbet silent for 4 h with every run
-- "completed" (#108).

CREATE TABLE IF NOT EXISTS feed_status (
    feed_id           text        PRIMARY KEY,
    label             text        NOT NULL,
    book              text,
    category          text        NOT NULL,         -- book | af | infra
    kind              text,                         -- pre-match | live | results | close | fixtures | service
    schedule          text,
    interval_min      integer,
    stale_after_min   integer,
    health_basis      text        NOT NULL,         -- data | runs | service
    status            text        NOT NULL,         -- ok | warn | fail | unknown
    status_reason     text,
    last_run_at       timestamptz,
    last_run_status   text,
    last_run_seconds  numeric,
    last_success_at   timestamptz,
    last_error        text,
    runs_24h          integer,
    failures_24h      integer,
    fail_streak       integer,
    last_data_at      timestamptz,
    rows_1h           bigint,
    rows_24h          bigint,
    service_state     jsonb,                        -- {"unit": "active", ...}
    runbook           text,
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Per book: how much it actually collected today, against yesterday.
CREATE TABLE IF NOT EXISTS feed_book_stats (
    book                 text        PRIMARY KEY,
    fixtures_today       integer,    -- our DB fixtures kicking off today (UTC)
    priced_today         integer,    -- of those, priced at this book at least once
    fixtures_yesterday   integer,
    priced_yesterday     integer,
    rows_today           bigint,     -- odds_snapshots rows written today
    market_families      integer,    -- distinct markets written in the last 24 h
    last_row_at          timestamptz,
    updated_at           timestamptz NOT NULL DEFAULT now()
);
