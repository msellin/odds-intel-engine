-- BET-TIMING-MONITOR: shadow_bets table for cohort-timing analysis.
--
-- Mirrors simulated_bets minus bankroll fields. Populated by
-- run_morning(shadow_mode=True), which runs ALL bots regardless of their
-- assigned cohort at every refresh window (06/11/15 UTC). The result is a
-- factorial dataset: each (bot, match, market, selection) gets evaluated
-- at all 3 timing windows so we can compare ROI per-bot per-cohort without
-- the strategy confound that breaks the current cohort A/B.
--
-- Settled by the existing settlement pipeline (parallel pass over this table
-- after the simulated_bets pass).

CREATE TABLE IF NOT EXISTS shadow_bets (
    id                    uuid primary key default gen_random_uuid(),
    shadow_run_id         uuid not null,
    shadow_cohort         text not null
        CHECK (shadow_cohort IN ('morning', 'midday', 'pre_ko')),

    bot_id                uuid not null references bots (id) on delete cascade,
    match_id              uuid not null references matches (id) on delete cascade,
    market                text not null,
    selection             text not null,

    odds_at_pick          numeric(10, 4) not null,
    pick_time             timestamptz not null default now(),

    -- Fixed nominal stake (10.0) for ROI comparison. Not a real wager —
    -- shadow_bets never touches bot bankrolls. Stored only so the existing
    -- settle_bet_result() can compute pnl = stake*(odds-1) on win.
    stake                 numeric(12, 2) not null default 10.00,

    model_probability     numeric(6, 4) not null,
    calibrated_prob       numeric(6, 4),
    edge_percent          numeric(6, 4) not null,

    recommended_bookmaker text,
    kelly_fraction        numeric(6, 4),
    timing_cohort         text,                                    -- the bot's *assigned* cohort
    model_version         text,

    -- Settled by run_settlement
    closing_odds          numeric(10, 4),
    clv                   numeric(6, 4),
    result                bet_result not null default 'pending',
    pnl                   numeric(12, 4),

    created_at            timestamptz not null default now(),

    CONSTRAINT chk_shadow_odds_positive    CHECK (odds_at_pick > 0),
    CONSTRAINT chk_shadow_probability      CHECK (model_probability >= 0 AND model_probability <= 1),
    CONSTRAINT chk_shadow_stake_positive   CHECK (stake > 0),

    -- Dedup guard: a single shadow run produces at most one row per
    -- (bot, match, market, selection). Without this we'd accumulate dupes
    -- on retries.
    CONSTRAINT uq_shadow_bet_per_run UNIQUE (shadow_run_id, bot_id, match_id, market, selection)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_shadow_bets_bot_match_mkt
    ON shadow_bets (bot_id, match_id, market, selection);

CREATE INDEX IF NOT EXISTS idx_shadow_bets_cohort_picktime
    ON shadow_bets (shadow_cohort, pick_time);

CREATE INDEX IF NOT EXISTS idx_shadow_bets_run
    ON shadow_bets (shadow_run_id);

-- Partial index for settlement lookups (pending bets only)
CREATE INDEX IF NOT EXISTS idx_shadow_bets_pending
    ON shadow_bets (match_id)
    WHERE result = 'pending';

-- RLS: read-only for analytics; no public mutation
ALTER TABLE shadow_bets ENABLE ROW LEVEL SECURITY;

CREATE POLICY shadow_bets_anon_read ON shadow_bets
    FOR SELECT
    USING (true);

COMMENT ON TABLE shadow_bets IS
    'Shadow placements of all bots at all 3 timing windows (morning/midday/pre_ko). '
    'Never affects real bankroll; produced by run_morning(shadow_mode=True). '
    'Used to break the cohort×strategy confound in the current cohort A/B. '
    'See dev/active/bet-timing-monitor-plan.md.';

COMMENT ON COLUMN shadow_bets.shadow_cohort IS
    'Which timing window this shadow was generated AT — NOT the bot''s assigned cohort.';

COMMENT ON COLUMN shadow_bets.timing_cohort IS
    'The bot''s assigned cohort (BOT_TIMING_COHORTS). Constant per bot. '
    'Compare to shadow_cohort to isolate timing-vs-strategy effects.';

COMMENT ON COLUMN shadow_bets.shadow_run_id IS
    'Groups all shadow bets generated in the same run_morning(shadow_mode=True) invocation.';


-- ops_snapshots daily-health counters for shadow runs.
-- shadow_runs_today: how many of the 3 expected runs (morning/midday/pre_ko)
--                    actually wrote rows today. 3/3 = healthy.
-- shadow_bets_today: total shadow rows written today across all 3 runs.
ALTER TABLE ops_snapshots
    ADD COLUMN IF NOT EXISTS shadow_runs_today  smallint DEFAULT 0,
    ADD COLUMN IF NOT EXISTS shadow_bets_today  integer  DEFAULT 0;

COMMENT ON COLUMN ops_snapshots.shadow_runs_today IS
    'Count of distinct shadow_cohort values that produced shadow_bets today (0-3). '
    'Should equal 3 by 16:00 UTC on a healthy day. Anything <3 = a shadow run failed.';
COMMENT ON COLUMN ops_snapshots.shadow_bets_today IS
    'Total rows written to shadow_bets today across all 3 runs.';
