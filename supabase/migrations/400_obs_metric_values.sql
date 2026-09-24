-- 400 — OBSERVATORY METRICS ([[#121]] phase 3 pilot, 2026-09-24)
--
-- Owner: "we just collect data but we don't have anything that runs on it and tries to
-- understand it". Layer 1 of the observatory: deterministic metrics on a schedule, one
-- value per (metric, scope, day). A metric surfaces only when it CHANGES against its own
-- 28-day baseline (robust z >= 3, workers/jobs/observatory_metrics.py) — levels never
-- alert, so the page does not fill with noise.
CREATE TABLE IF NOT EXISTS obs_metric_values (
    metric_key  text        NOT NULL,     -- clv_coverage | price_fidelity | bot_clv_7d
    scope       text        NOT NULL,     -- ledger=… | book=…|market=… | bot=…
    ts          date        NOT NULL,
    value       numeric,
    n           integer,
    meta        jsonb,
    PRIMARY KEY (metric_key, scope, ts)
);
