-- Feature importance per league (P3.5)
-- Stores correlation between match signals and outcomes, computed weekly.
-- Used to show which signals drive results in each league.

CREATE TABLE IF NOT EXISTS feature_importance (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    league_id       uuid REFERENCES leagues (id) ON DELETE CASCADE,
    league_name     text,               -- denormalised for fast reads
    signal_name     text NOT NULL,      -- e.g. 'elo_diff', 'odds_drift_home'
    market          text NOT NULL,      -- '1x2_home', '1x2_draw', 'over25', etc.
    correlation     double precision,   -- Pearson r (signal → binary outcome)
    abs_correlation double precision,   -- |r|, used for ranking
    sample_count    integer NOT NULL,
    fitted_at       timestamptz NOT NULL DEFAULT now()
);

-- Fast lookup: top signals per league + market
CREATE INDEX idx_feature_importance_league_market
    ON feature_importance (league_id, market, abs_correlation DESC);

-- Fast lookup: most recent fit per (league, signal, market)
CREATE INDEX idx_feature_importance_league_signal
    ON feature_importance (league_id, signal_name, market, fitted_at DESC);

COMMENT ON TABLE feature_importance IS
    'Weekly signal-outcome correlations per league. Computed by scripts/compute_feature_importance.py.';
