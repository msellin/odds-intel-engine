-- RETIRE-DC-SPECIALIST 2026-06-01
--
-- Paper ROI -7.53% on n=58 settled bets since 2026-05-24, avg CLV +3.68%.
-- The bot has three profiles after OU-DC-CONSOLIDATION (migration 152):
--   "X2 Value"   — Brazil Serie B + China Super League (backtest +20.1% / +13.7%)
--   "1X Israel"  — Israel Liga Leumit (backtest +13.3%)
--   "DC Global"  — absorbed bot_dc_value, all leagues, all DC selections
--
-- All 62 production bets since consolidation have strategy_profile=NULL, so we
-- cannot attribute the loss to a single profile from the data alone. But the
-- underlying mechanism — DC is a derived market computed from 1x2 outputs, no
-- model-native head — is the same one that retired bot_dc_value on 2026-05-28
-- (migration 137). The v20260524_market model has no dedicated DC training
-- target and no Platt calibration for DC ("dc was insufficient samples"); the
-- "edge" the bot computes is a derived-prob artefact, not a true signal.
--
-- The X2 Value and 1X Israel league-whitelist profiles backtest positively but
-- those backtests share the same probabilistic foundation. They should be
-- revived as standalone bots after the June 8 retrain ships a DC-specific
-- model or Platt fit, not before.
--
-- Re-activation trigger:
--   (a) v20260608+ ships a DC-specific calibration / training target, OR
--   (b) shadow_bets accumulates 30+ X2 Value or 1X Israel bets at >5% real ROI

UPDATE bots
SET
    is_active     = false,
    retired_at    = NOW(),
    retired_reason = 'RETIRE-DC-SPECIALIST 2026-06-01: -7.53% ROI on 58 settled bets since 2026-05-24 (+3.68% CLV — strong line agreement, weak outcome conversion). DC is a derived market (computed from 1x2 outputs); the model has no dedicated DC training target on v20260524_market. Edge signal is a derived-prob artefact, same root cause that retired bot_dc_value on 2026-05-28. Three profiles ("X2 Value" Brazil B + China SL, "1X Israel" Liga Leumit, "DC Global") all paused. Revive as standalone bots if June 8 retrain ships DC-specific calibration OR shadow_bets shows the whitelist profiles holding ROI > 5% on n≥30.'
WHERE name = 'bot_dc_specialist';
