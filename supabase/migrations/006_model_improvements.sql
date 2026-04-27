-- =============================================================================
-- OddsIntel — Migration 006: Model Improvement Columns
-- =============================================================================
-- Adds columns to simulated_bets for:
--   - Calibrated probability (P1: market shrinkage)
--   - Odds movement tracking (P2: drift/velocity)
--   - Alignment scoring (P3: 7-dimension filter)
--   - Kelly fraction (P4: stake sizing)
--   - CLV tracking (opening odds)
--   - News impact and lineup confirmation
-- =============================================================================

-- P1: Calibrated probability after market shrinkage
ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS calibrated_prob numeric(5,4);

-- P2: Odds movement
ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS odds_at_open numeric(10,4),
    ADD COLUMN IF NOT EXISTS odds_drift numeric(8,6);

-- P3: Alignment scoring
ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS dimension_scores jsonb,
    ADD COLUMN IF NOT EXISTS alignment_count smallint,
    ADD COLUMN IF NOT EXISTS alignment_total smallint,
    ADD COLUMN IF NOT EXISTS alignment_class text;

-- P4: Kelly fraction
ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS kelly_fraction numeric(8,6);

-- Meta-dimensions (confidence modifiers)
ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS model_disagreement numeric(5,4),
    ADD COLUMN IF NOT EXISTS news_impact_score numeric(5,4),
    ADD COLUMN IF NOT EXISTS lineup_confirmed boolean default false;

-- Index for analyzing ROI by alignment class
CREATE INDEX IF NOT EXISTS idx_simulated_bets_alignment_class
    ON simulated_bets (alignment_class)
    WHERE alignment_class IS NOT NULL;

-- Index for calibration analysis
CREATE INDEX IF NOT EXISTS idx_simulated_bets_calibrated_prob
    ON simulated_bets (calibrated_prob)
    WHERE calibrated_prob IS NOT NULL;
