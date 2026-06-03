-- PIN-CROSS-DRIFT (2026-06-03): shadow-mode flag on simulated_bets.
--
-- 60d backtest (scripts/pin_drift_veto_analysis.py) showed that non-1X2 bets
-- placed when Pinnacle's 1X2 line drifted significantly pre-KO lose money
-- systematically (BTTS -50%, DC -38%, OU -21%, AH -18% on the bad cohorts).
--
-- The veto is shipped in shadow mode first: the helper computes a decision,
-- the pipeline LOGS it on the bet row (this column), but the bet is still
-- placed. After ~7 days of live shadow data we query:
--
--   SELECT result, COUNT(*), SUM(pnl)
--   FROM simulated_bets
--   WHERE pin_cross_drift_shadow_flag = TRUE
--     AND result::text IN ('won', 'lost')
--   GROUP BY result;
--
-- If the shadow cohort PnL matches the backtest (≈ -$5/day, -22% ROI), flip
-- PIN_CROSS_DRIFT_VETO_ENABLED=true to activate the actual veto.

ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS pin_cross_drift_shadow_flag boolean DEFAULT FALSE;

COMMENT ON COLUMN simulated_bets.pin_cross_drift_shadow_flag IS
    'PIN-CROSS-DRIFT (2026-06-03): TRUE if this bet WOULD have been vetoed by '
    'workers.model.pin_cross_drift_veto when the env gate is enabled. '
    'Shadow column for validating the veto policy against live results before '
    'activating PIN_CROSS_DRIFT_VETO_ENABLED.';

CREATE INDEX IF NOT EXISTS idx_simulated_bets_pin_cross_drift_shadow
    ON simulated_bets (pin_cross_drift_shadow_flag)
    WHERE pin_cross_drift_shadow_flag = TRUE;
