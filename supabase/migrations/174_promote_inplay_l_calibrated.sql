-- INPLAY-L-PROMOTE 2026-06-03
--
-- Promote bot_inplay_l (Goal Contagion) from current maturity to 'calibrated'
-- so it joins the placer's calibrated cohort eligible for real-money
-- placement when COOLBET_RECORD_ALLOWED_MATURITY=calibrated is active.
--
-- Source: INPLAY-CALIBRATION-IJL audit 2026-06-03 (PRIORITY_QUEUE entry).
-- Settled stats since 2026-05-09 launch:
--   n=31 (won/lost only)
--   ECE 4.96%   — just under the 5% real-money gate
--   hit rate 80.6%
--   ROI +25.8% on €155 staked / €+40.03 P&L
--   All four per-bucket calibration gaps NEGATIVE — model is slightly
--   under-predicting wins (conservative direction, safe to promote).
--
-- Companion bots NOT promoted in this migration:
--   bot_inplay_i (Favourite Stall): ECE 24.61% on n=11 — DO NOT promote.
--                                   Follow-up INPLAY-I-RECALIBRATE filed.
--   bot_inplay_j (Goal Debt O1.5):  n=0 settled — silent failure.
--                                   Follow-up INPLAY-J-SILENT-FAILURE filed.

UPDATE bots
SET maturity_label = 'calibrated'
WHERE name = 'inplay_l'
  AND is_active = true;

-- Verification view (commented; uncomment to spot-check after deploy):
-- SELECT name, maturity_label, is_active, retired_at
-- FROM bots
-- WHERE name LIKE 'inplay_%'
-- ORDER BY name;
