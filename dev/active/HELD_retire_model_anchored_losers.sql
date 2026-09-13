-- ============================================================================
-- HELD — NOT A MIGRATION. Deliberately parked outside supabase/migrations/ so
-- the auto-apply workflow cannot run it. 2026-09-13.
--
-- WHY HELD. An independent investigation the same day found a confirmed bug in
-- the O/U Platt calibrator (model_calibration rows `under25` and `under35`,
-- both fitted 2026-09-03 10:48:19 UTC): the curve is fitted on
-- `predictions.model_probability` (raw ensemble) at
-- scripts/fit_calibration_from_predictions.py:186 but applied to `shrunk`
-- (already ~90% Pinnacle at odds > 3.0) at workers/model/improvements.py:227.
-- Verified: `under25` has range [0.3028, 0.6663] and a fixed point at 0.4713,
-- so `edge = cal_prob - 1/odds` degenerates into "how far is this price from
-- ~0.45", which the longest price on the board always maximises. The 8% O/U
-- floor became a longshot-finder.
--
-- THE DECISIVE FACT FOR THIS FILE: all seven bots named below have **100% of
-- their settled picks after that fit** — zero rows before it. There is no clean
-- pre-bug window to fall back on for any of them, so the four O/U bots cannot
-- be separated from the calibrator on the data that exists today. Retiring them
-- now would be retiring a measurement of the bug.
--
--   bot                          settled  before fit  after fit
--   bot_coolbet_trigger_ou_v1        484           0        484
--   bot_unibet_trigger_ou_v1         260           0        260
--   bot_ou35_model_v1                190           0        190
--   bot_trigger_ou_model_v1          141           0        141
--   bot_coolbet_trigger_1x2_v1       405           0        405
--   bot_unibet_trigger_1x2_v1        388           0        388
--   bot_trigger_1x2_model_v1         374           0        374
--
-- NOTE `bot_ou35_model_v1` is in scope even though the handoff describing this
-- bug named only the 2.5 line: `under35` was fitted in the SAME batch, at the
-- same second, and is compressed the same way (range [0.4787, 0.7287], fixed
-- point 0.6480).
--
-- 1x2 IS NOT PART OF THE 09-03 DISCONTINUITY (1x2_home has been a=1.6081,
-- b=-0.8604 continuously since 08-30), so the three 1x2 verdicts below stand on
-- their own evidence. They are held here only to keep one decision in one file
-- rather than splitting the owner's call across two commits. A separate,
-- CHRONIC compression exists on the 1x2 curve too (range [0.297, 0.679], fixed
-- point 0.4766) — that is a pre-existing condition rather than a step change,
-- and it means these bots measure "the model edge as currently computed", which
-- is exactly what a retirement decision is about.
--
-- TO PROCEED: after the calibrator is refit, re-measure the four O/U bots on
-- post-fix data only (expect volume to fall 90-99%; that is the fix working),
-- then move whichever bots still fail into a numbered migration.
-- ============================================================================

-- SHADOW-BOT-VERDICTS (2026-09-13) — retire the seven MODEL-anchored trigger
-- bots. Owner-authorized ("ok lets retire the ones you think we should").
--
-- WHY. Measured on PLACEABLE BOOKS ONLY (Unibet-Kambi excluded — it disagrees
-- with the real unibet.ee site on 91% of quotes and reads HIGHER on 29%,
-- KAMBI-FEED-DIVERGENCE; Pinnacle excluded — unbettable here AND the reference
-- CLV is measured against, so including it is circular):
--
--   bot                          n    CLV      t      ROI
--   bot_coolbet_trigger_1x2_v1   272  -9.1%  -14.2  -22.8%
--   bot_coolbet_trigger_ou_v1    343  -8.7%  -30.9   -3.4%
--   bot_unibet_trigger_1x2_v1    302  -8.5%   -6.8   -7.0%
--   bot_trigger_1x2_model_v1     370  -8.4%  -11.9  -14.6%
--   bot_trigger_ou_model_v1      136  -8.2%  -16.5   +3.2%
--   bot_unibet_trigger_ou_v1     205  -7.8%  -23.0   +5.5%
--   bot_ou35_model_v1            190  -7.0%  -14.6  -17.4%
--
-- The decisive fact is not that they lose — it is that NO CONFIGURATION OF THEM
-- WINS. Searched edge floors (5/8/10/13%), odds floors (2.2/2.8/3.2) and
-- dropping each selection; every candidate returned "none fold-robust" (i.e.
-- not CLV-positive in all three walk-forward folds). Tuning their gates is
-- therefore wasted effort, which is a stronger statement than "negative ROI".
-- Independently, `bot_segment_table.py` found each of them negative in 17-19 of
-- 17-19 significant segments — unanimity, not a bad slice.
--
-- Note bot_trigger_ou_model_v1 (+3.2%) and bot_unibet_trigger_ou_v1 (+5.5%)
-- show POSITIVE ROI next to deeply negative CLV. At these volumes ROI is noise
-- (~9,300 settled bets are needed for +/-2%) and CLV is not (~334). Do not let
-- the green number rescue them.
--
-- SAFE TO RETIRE: all seven are paper-only — they write shadow_bets, have zero
-- rows in simulated_bets, zero rows in real_bets and no coolbet_placer_bots
-- toggle. So no published /performance figure moves and no money changes.
-- Their settled picks are KEPT as the evidence record for the anchor split
-- (model-anchored loses to the close; sharp-anchored beats it).
--
-- NOT included here, deliberately: bot_coolbet_ou_model_v1. It is also
-- model-anchored and also has no fold-robust config (CLV -5.7%, t=-4.6), but it
-- is the bot we stake MOST (22 bets, EUR 220 in 30d) and switching it off is a
-- live-money decision that is the owner's to make, not a cleanup item.

UPDATE bots
   SET is_active      = FALSE,
       retired_at     = NOW(),
       retired_reason = 'SHADOW-BOT-VERDICTS 2026-09-13: model-anchored, CLV negative on placeable books (t=-6.8..-30.9) and NO fold-robust configuration over edge/odds floors or selection drops. Paper-only, history kept.',
       updated_at     = NOW()
 WHERE name IN (
         'bot_coolbet_trigger_1x2_v1',
         'bot_coolbet_trigger_ou_v1',
         'bot_unibet_trigger_1x2_v1',
         'bot_trigger_1x2_model_v1',
         'bot_trigger_ou_model_v1',
         'bot_unibet_trigger_ou_v1',
         'bot_ou35_model_v1')
   AND retired_at IS NULL;

-- Belt-and-braces: none of these should have a real-money toggle. Idempotent.
DELETE FROM coolbet_placer_bots
 WHERE bot_name IN (
         'bot_coolbet_trigger_1x2_v1',
         'bot_coolbet_trigger_ou_v1',
         'bot_unibet_trigger_1x2_v1',
         'bot_trigger_1x2_model_v1',
         'bot_trigger_ou_model_v1',
         'bot_unibet_trigger_ou_v1',
         'bot_ou35_model_v1');
