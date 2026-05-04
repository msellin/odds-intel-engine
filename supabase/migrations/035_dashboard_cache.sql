-- 035_dashboard_cache.sql
-- Pre-computed dashboard stats written by settlement job (21:00 UTC).
-- Frontend fetches latest row — one fast query instead of 3+ heavy joins.

CREATE TABLE IF NOT EXISTS dashboard_cache (
    id SERIAL PRIMARY KEY,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Bot performance (from simulated_bets)
    total_bets INTEGER NOT NULL DEFAULT 0,
    settled_bets INTEGER NOT NULL DEFAULT 0,
    pending_bets INTEGER NOT NULL DEFAULT 0,
    won_bets INTEGER NOT NULL DEFAULT 0,
    lost_bets INTEGER NOT NULL DEFAULT 0,
    hit_rate FLOAT,
    total_staked FLOAT NOT NULL DEFAULT 0,
    total_pnl FLOAT NOT NULL DEFAULT 0,
    roi_pct FLOAT,
    avg_clv FLOAT,

    -- Per-bot summary (JSON: [{name, settled, won, pnl, roi_pct, avg_clv, timing_cohort}])
    bot_breakdown JSONB,

    -- Market breakdown (JSON: [{market, bets, won, avg_clv}])
    market_breakdown JSONB,

    -- Model accuracy (from predictions + matches)
    model_accuracy_pct FLOAT,
    prediction_sample_size INTEGER,

    -- Data accumulation progress
    pseudo_clv_count INTEGER,
    live_snapshot_matches INTEGER,
    alignment_settled_count INTEGER
);

-- Only keep last 30 days of cache rows
CREATE INDEX IF NOT EXISTS dashboard_cache_computed_at_idx ON dashboard_cache (computed_at DESC);
