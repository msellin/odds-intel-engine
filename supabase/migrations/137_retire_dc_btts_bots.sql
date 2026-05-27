-- Retire bot_dc_value (DC market, no model-native edge) and both BTTS bots
-- (model systematically miscalibrated on BTTS outcomes).
--
-- bot_dc_value:
--   DC is a derived market — probabilities computed from 1x2 model outputs,
--   not a market the XGBoost model was trained on directly. Edge signal is
--   therefore unreliable: the model doesn't "know" DC, it just combines probs.
--   -16.7% ROI on 55 v2 bets (v20260524_market). Edge threshold was 3-5%,
--   which data shows is firmly in negative-ROI territory across all markets.
--
-- bot_btts_all / bot_btts_conservative:
--   Model predicts 62.1% BTTS hit rate; actual is 46.5% — a 15.6pp gap.
--   Breakeven at avg odds 2.055 requires 48.7%; we land at 46.5%.
--   The edge signal is fake: it's computed against an overestimated prob,
--   not the market's closing line. v2 model BTTS bets have negative CLV
--   (-1.81%) confirming the market prices BTTS better than our model.
--   Paused until BTTS-specific calibration is added (target: June 8 retrain).

UPDATE bots
SET
    retired_at    = NOW(),
    is_active     = false,
    retired_reason = CASE name
        WHEN 'bot_dc_value' THEN
            'Derived market — DC is computed from 1x2 outputs, not a directly trained market. No model-native edge. -16.7% ROI on 55 v2 bets; edge threshold 3-5% is negative-ROI territory.'
        WHEN 'bot_btts_all' THEN
            'Model miscalibrated on BTTS: predicted 62.1% hit rate vs actual 46.5% (15.6pp gap). Edge signal unreliable — computed against overestimated prob. v2 CLV negative (-1.81%). Paused until BTTS-specific calibration added (June 8 retrain).'
        WHEN 'bot_btts_conservative' THEN
            'Model miscalibrated on BTTS: predicted 62.1% hit rate vs actual 46.5% (15.6pp gap). Edge signal unreliable — computed against overestimated prob. v2 CLV negative (-1.81%). Paused until BTTS-specific calibration added (June 8 retrain).'
    END
WHERE name IN ('bot_dc_value', 'bot_btts_all', 'bot_btts_conservative');
