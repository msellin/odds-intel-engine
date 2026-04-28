-- =============================================================================
-- OddsIntel — Migration 008: API-Football Predictions (T1)
-- =============================================================================
-- Stores API-Football's own predictions alongside our model.
-- Enables direct comparison: does AF agreement predict bet outcomes?
--
-- matches.af_prediction     — full raw JSONB (all AF prediction fields)
-- simulated_bets columns    — parsed probabilities + agreement flag for fast queries
-- =============================================================================

-- Store full AF prediction JSONB on each match (fetched once in morning pipeline)
ALTER TABLE matches
    ADD COLUMN IF NOT EXISTS af_prediction jsonb;

-- Parsed AF probabilities on bets (for fast ROI-by-agreement queries)
ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS af_home_prob numeric(5,4),
    ADD COLUMN IF NOT EXISTS af_draw_prob numeric(5,4),
    ADD COLUMN IF NOT EXISTS af_away_prob numeric(5,4),
    ADD COLUMN IF NOT EXISTS af_agrees boolean;

-- Index for ROI split analysis: af_agrees = true vs false
CREATE INDEX IF NOT EXISTS idx_simulated_bets_af_agrees
    ON simulated_bets (af_agrees)
    WHERE af_agrees IS NOT NULL;
