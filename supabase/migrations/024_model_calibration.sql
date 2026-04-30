-- Platt scaling calibration parameters.
-- Stores sigmoid α/β per market, fitted weekly from settled predictions.
-- The betting pipeline reads the latest row per market to post-hoc calibrate
-- model probabilities before computing edge and Kelly stake.

CREATE TABLE IF NOT EXISTS model_calibration (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    market      text NOT NULL,              -- e.g. '1x2_home', '1x2_draw', '1x2_away'
    platt_a     double precision NOT NULL,   -- sigmoid slope (α)
    platt_b     double precision NOT NULL,   -- sigmoid intercept (β)
    ece_before  double precision,            -- Expected Calibration Error before Platt
    ece_after   double precision,            -- ECE after Platt (should be lower)
    sample_count integer NOT NULL,           -- number of predictions used for fit
    fitted_at   timestamptz NOT NULL DEFAULT now()
);

-- Fast lookup: latest calibration per market
CREATE INDEX idx_model_calibration_market_fitted
    ON model_calibration (market, fitted_at DESC);

COMMENT ON TABLE model_calibration IS
    'Weekly Platt scaling parameters per market. Pipeline reads latest row per market. Fitted by scripts/fit_platt.py.';
