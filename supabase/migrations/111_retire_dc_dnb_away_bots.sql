-- BOTS-RETIRE-DC-DNB (2026-05-19): retire two DC bots and bot_dnb_away_value.
--
-- Reason: 1-year backtest (25K bets / 22 bots) shows these three bots are
-- negative-EV and no edge threshold rescues them:
--
--   bot_dc_value      — 2707 bets, -2.8% ROI baseline. Best threshold (8%) only
--                       reaches -2.4%. DC market prices are too efficient for a
--                       model that derives DC probs by summing 1x2 components.
--
--   bot_dc_strong_fav — 1451 bets, -3.4% ROI flat across all thresholds (1–15%).
--                       Tightening the edge gate has zero effect — the loss is
--                       structural, not a threshold calibration issue.
--
--   bot_dnb_away_value — 1037 bets (excluding voids), -7.9% ROI. Best threshold
--                        (9%) reaches -7.1%. Away DNB thesis doesn't work —
--                        the model's away-team Poisson edge doesn't survive
--                        the DNB pricing formula.
--
-- bot_dnb_home_value is kept — marginal +1.4% ROI at 11% threshold,
-- directionally positive; will revisit at Batch 2 (~2026-06-15).
--
-- Historical bets remain in simulated_bets for audit.
-- Re-enable trigger: 30+ settled live bets at ≥3% real ROI.

UPDATE bots
SET
    is_active     = false,
    retired_at    = now(),
    retired_reason = CASE name
        WHEN 'bot_dc_value' THEN
            '1-year backtest (2707 bets): -2.8% ROI, no threshold rescues it (best 8% = -2.4%). DC market too efficient for summed-1x2-prob model.'
        WHEN 'bot_dc_strong_fav' THEN
            '1-year backtest (1451 bets): flat -3.4% ROI at every threshold 1–15%. Loss is structural, not a calibration issue.'
        WHEN 'bot_dnb_away_value' THEN
            '1-year backtest (1037 settled bets): -7.9% ROI, best threshold (9%) only reaches -7.1%. Away DNB thesis does not work.'
    END
WHERE name IN ('bot_dc_value', 'bot_dc_strong_fav', 'bot_dnb_away_value')
  AND retired_at IS NULL;
