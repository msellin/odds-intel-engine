-- ============================================================================
-- HELD — NOT A MIGRATION. Deliberately parked outside supabase/migrations/ so
-- the auto-apply workflow cannot run it. Updated 2026-09-14.
--
-- The 1x2 half of this decision SHIPPED as migration
-- 336_retire_model_anchored_1x2_losers.sql. What remains held is the four
-- MODEL-anchored O/U bots, and the reason is now stronger than when this file
-- was first parked.
--
-- WHY STILL HELD
-- --------------
-- The O/U Platt calibrator was fitted on `predictions.model_probability` and
-- applied to `shrunk` between 2026-09-03 09:02 and 2026-09-13 21:00 UTC
-- (OU-CALIBRATOR-DOMAIN-MISMATCH; rows removed by migration 335, preserved in
-- model_calibration_ou_domain_mismatch_backup). Inside that window the curve
-- manufactured both the edge and the volume: `under25` had range
-- [0.3028, 0.6663] and a fixed point at 0.4713, so `edge = cal_prob - 1/odds`
-- degenerated into "how far is this price from ~0.45", which the longest price
-- on the board always maximises.
--
-- Era 1 (before the fit, no curve) and era 3 (after migration 335, no curve
-- again) share a calibration regime, so in GENERAL pre-09-03 O/U history is
-- usable and there is no need to wait for fresh volume.
--
-- That does not help these four. Measured 2026-09-14, settled picks per era:
--
--   bot                        era1  era2(bad)  era3
--   bot_coolbet_trigger_ou_v1     0        494     0
--   bot_unibet_trigger_ou_v1      0        267     0
--   bot_ou35_model_v1             0        190     0
--   bot_trigger_ou_model_v1       0        149     0
--
-- Every one was born inside the poisoned window. Excise era 2 and there is no
-- evidence left at all — not weak evidence, none. Era 3 is empty because the
-- fix landed ~20h ago and bets have not settled yet.
--
-- For contrast, bots that CAN be judged on O/U today because they predate the
-- fit: bot_ou25_global (era1=216, era2=17), bot_sweep_ou25_v1 (246/239),
-- bot_sweep_ou35_v1 (199/205), bot_aggressive (190/0), bot_v10_all (126/99).
--
-- NOT IN THIS FILE, and deliberately:
--   bot_coolbet_ou_model_v1 — same era problem (era1=0, era2=33, era3=0), but it
--     is real money and already has ui_place_enabled=false as of 2026-09-13. It
--     keeps generating PAPER picks so it can be re-measured in era 3. Retiring
--     it would destroy the only path back to an answer.
--   bot_coolbet_trigger_sharp_ou_v1 — SHARP-anchored, never touches cal_prob,
--     so it was never affected. Not a loser and not under decision.
--
-- TO PROCEED
-- ----------
-- Wait for era-3 volume (expect a ~90% drop in O/U pick count — that is the fix
-- working, not a regression; the held-out backtest put the no-curve arm at 48
-- picks against 488). Then re-measure these four on era 3 alone, decide on CLV
-- rather than ROI (~334 settled for CLV against ~9,300 for ROI), and move
-- whatever still fails into a numbered migration.
--
-- Sequencing note: OU-CALIBRATOR-REFIT-ON-SHRUNK may land first and would make
-- era 3 a third distinct regime. If it does, restart the era-3 clock rather
-- than pooling across the refit.
-- ============================================================================

UPDATE bots
   SET is_active      = FALSE,
       retired_at     = NOW(),
       retired_reason = 'SHADOW-BOT-VERDICTS: model-anchored O/U, no fold-robust configuration. RE-MEASURE ON ERA 3 BEFORE APPLYING.',
       updated_at     = NOW()
 WHERE name IN ('bot_coolbet_trigger_ou_v1',
                'bot_unibet_trigger_ou_v1',
                'bot_ou35_model_v1',
                'bot_trigger_ou_model_v1')
   AND retired_at IS NULL;
