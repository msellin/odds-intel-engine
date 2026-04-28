-- =============================================================================
-- OddsIntel — Migration 010: Multi-Signal Architecture
-- =============================================================================
-- S1:    Add `source` column to predictions (poisson/af/xgboost/ensemble)
-- S2:    New `match_signals` append-only signal store
-- B-ML1: Add pseudo_clv columns to matches (all fixtures, not just bet matches)
-- B-ML2: New `match_feature_vectors` wide ML training table
-- =============================================================================


-- ─── S1: Source column on predictions ────────────────────────────────────────

ALTER TABLE predictions
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'ensemble';

-- Update any existing rows to ensemble (they were all ensemble predictions)
UPDATE predictions SET source = 'ensemble' WHERE source IS NULL OR source = '';

-- New unique constraint: one row per (match, market, source)
-- Drop old constraint if exists (there was no unique on this before)
ALTER TABLE predictions
    DROP CONSTRAINT IF EXISTS uq_prediction_match_market;

ALTER TABLE predictions
    DROP CONSTRAINT IF EXISTS uq_prediction_match_market_source;

ALTER TABLE predictions
    ADD CONSTRAINT uq_prediction_match_market_source
    UNIQUE (match_id, market, source);

CREATE INDEX IF NOT EXISTS idx_predictions_source ON predictions (source);


-- ─── S2: match_signals table ──────────────────────────────────────────────────
-- Append-only store. Same signal can have many rows (captured at different times).
-- ML training uses value closest to kickoff.

CREATE TABLE IF NOT EXISTS match_signals (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    match_id        uuid        NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    signal_name     text        NOT NULL,   -- e.g. 'odds_drift', 'news_impact_score'
    signal_value    numeric,                -- numeric representation
    signal_text     text,                   -- optional raw text/JSON
    signal_group    text,                   -- 'market' | 'quality' | 'information' | 'context'
    captured_at     timestamptz NOT NULL DEFAULT now(),
    data_source     text,                   -- 'af', 'kambi', 'gemini', 'derived', etc.
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_match_signals_match_id
    ON match_signals (match_id);

CREATE INDEX IF NOT EXISTS idx_match_signals_signal_name
    ON match_signals (signal_name);

CREATE INDEX IF NOT EXISTS idx_match_signals_captured_at
    ON match_signals (captured_at);

-- Index for the "latest value before kickoff" ML training query
CREATE INDEX IF NOT EXISTS idx_match_signals_match_signal_time
    ON match_signals (match_id, signal_name, captured_at DESC);


-- ─── B-ML1: Pseudo-CLV columns on matches ────────────────────────────────────
-- (1/opening_odds) / (1/closing_odds) - 1 for each 1x2 selection.
-- Computed in settlement pipeline for ALL finished matches, not just bet matches.
-- Positive = opening odds were better than closing (we had edge at open).

ALTER TABLE matches
    ADD COLUMN IF NOT EXISTS pseudo_clv_home  numeric(8,5),
    ADD COLUMN IF NOT EXISTS pseudo_clv_draw  numeric(8,5),
    ADD COLUMN IF NOT EXISTS pseudo_clv_away  numeric(8,5);

CREATE INDEX IF NOT EXISTS idx_matches_pseudo_clv
    ON matches (pseudo_clv_home)
    WHERE pseudo_clv_home IS NOT NULL;


-- ─── B-ML2: match_feature_vectors wide ML training table ─────────────────────
-- One row per finished match. Rebuilt nightly by settlement pipeline ETL.
-- This is the actual ML training table — not the EAV match_signals directly.
-- Signals: value closest to kickoff (or at bet time where applicable).

CREATE TABLE IF NOT EXISTS match_feature_vectors (
    match_id                uuid        PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    match_date              date        NOT NULL,
    league_tier             integer,
    data_tier               text,       -- 'A' | 'B' | 'C' | 'D'

    -- ── Group 1: Model signals ───────────────────────────────────────────────
    ensemble_prob_home      numeric(5,4),
    ensemble_prob_draw      numeric(5,4),
    ensemble_prob_away      numeric(5,4),
    poisson_prob_home       numeric(5,4),
    xgboost_prob_home       numeric(5,4),
    af_pred_prob_home       numeric(5,4),
    model_disagreement      numeric(5,4),   -- abs(poisson - xgboost)

    -- ── Group 2: Market signals ──────────────────────────────────────────────
    opening_implied_home    numeric(5,4),
    opening_implied_draw    numeric(5,4),
    opening_implied_away    numeric(5,4),
    odds_drift_home         numeric(8,5),   -- implied prob change since open
    steam_move              boolean,        -- >3% move in <2h

    -- ── Group 3: Team quality signals ───────────────────────────────────────
    elo_home                numeric(7,2),
    elo_away                numeric(7,2),
    elo_diff                numeric(7,2),   -- home - away
    form_ppg_home           numeric(5,3),
    form_ppg_away           numeric(5,3),
    form_momentum_home      numeric(5,3),   -- recent trend vs 10-match avg
    form_momentum_away      numeric(5,3),

    -- ── Group 4: Information signals (populated as match_signals arrives) ───
    news_impact_score       numeric(4,3),   -- -1.0 to +1.0
    injury_severity_home    numeric(4,3),
    injury_severity_away    numeric(4,3),
    lineup_confirmed        boolean,

    -- ── Outcome labels ───────────────────────────────────────────────────────
    match_outcome           text,           -- 'home' | 'draw' | 'away'
    total_goals             integer,
    over_25                 boolean,
    pseudo_clv_home         numeric(8,5),   -- target: did opening odds have edge?
    pseudo_clv_draw         numeric(8,5),
    pseudo_clv_away         numeric(8,5),

    built_at                timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mfv_match_date
    ON match_feature_vectors (match_date);

CREATE INDEX IF NOT EXISTS idx_mfv_league_tier
    ON match_feature_vectors (league_tier);

CREATE INDEX IF NOT EXISTS idx_mfv_outcome
    ON match_feature_vectors (match_outcome)
    WHERE match_outcome IS NOT NULL;
