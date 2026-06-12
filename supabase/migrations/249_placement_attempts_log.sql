-- COOLBET-MAC-DAEMON-VISIBILITY (2026-06-12): every daemon tick records
-- per-pick outcomes so we can answer "what did the daemon try, why did
-- it skip, when did it last work" without grep'ing the launchd log.
--
-- One row per (tick, simulated_bet_id) — placed AND skipped both written.
-- Daily/weekly aggregates are simple GROUP BY queries; per-match diagnosis
-- is one SELECT. Used by a future /report Telegram command and any admin
-- dashboard that wants to surface "we keep skipping X — fix the matcher".

CREATE TABLE IF NOT EXISTS placement_attempts (
    id               BIGSERIAL PRIMARY KEY,
    attempted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    simulated_bet_id UUID NOT NULL REFERENCES simulated_bets(id) ON DELETE CASCADE,
    match_id         UUID,
    market           TEXT,
    selection        TEXT,
    outcome          TEXT NOT NULL,
    reason           TEXT,
    -- Snapshot values at attempt time so we can replay the decision
    -- later — e.g. "why did this skip when edge looked fine in
    -- simulated_bets at signal time?"
    model_odds       NUMERIC,
    live_odds        NUMERIC,
    model_edge       NUMERIC,
    live_edge        NUMERIC,
    stake            NUMERIC,
    coolbet_event_id BIGINT,
    real_bet_id      UUID REFERENCES real_bets(id) ON DELETE SET NULL,
    -- "mac_daemon" vs "railway_pipeline" vs "manual_admin" — distinguish
    -- automated daemon ticks from operator-triggered runs.
    source           TEXT NOT NULL DEFAULT 'mac_daemon'
);

CREATE INDEX IF NOT EXISTS idx_placement_attempts_attempted_at
    ON placement_attempts (attempted_at DESC);

CREATE INDEX IF NOT EXISTS idx_placement_attempts_sim_id
    ON placement_attempts (simulated_bet_id);

CREATE INDEX IF NOT EXISTS idx_placement_attempts_outcome_recent
    ON placement_attempts (outcome, attempted_at DESC);

COMMENT ON TABLE placement_attempts IS
    'Per-tick log of what the placer attempted and why it succeeded/failed. '
    'Powers diagnostic reports — "what did we try today", "why does Coolbet '
    'never match this team name", "is the daemon alive". The placer/daemon '
    'inserts a row for every outcome (placed, no_event, no_market, '
    'edge_eroded, search_blocked, guard_skip, dry_run).';

COMMENT ON COLUMN placement_attempts.outcome IS
    'placed | no_event | no_market | edge_eroded | guard_skip | '
    'search_blocked | dry_run | error';

COMMENT ON COLUMN placement_attempts.reason IS
    'Free-text detail when outcome is not "placed" — e.g. "Coolbet best '
    'fuzzy score 53 < threshold 70", "market 1x2/home unavailable", '
    '"live odds 1.22 vs model 2.95 = -58% edge".';
