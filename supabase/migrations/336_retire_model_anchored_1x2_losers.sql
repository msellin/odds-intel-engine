-- SHADOW-BOT-VERDICTS (2026-09-14) — retire the three MODEL-anchored 1x2
-- trigger bots. Owner-authorized ("ok lets retire the ones you think we
-- should"), held one day pending the O/U calibrator investigation, now released
-- for the 1x2 half only.
--
-- WHY THESE THREE AND NOT THE SEVEN ORIGINALLY PROPOSED
-- -----------------------------------------------------
-- The 2026-09-13 verdict sweep named seven model-anchored losers. Four of them
-- are O/U and are NOT retired here: the O/U Platt calibrator was fitted on one
-- quantity and applied to another between 2026-09-03 09:02 and 2026-09-13 21:00
-- UTC (OU-CALIBRATOR-DOMAIN-MISMATCH, migration 335), which manufactured both
-- the edge and the volume on every O/U pick in that window.
--
-- The handoff on that fix noted the pre-09-03 O/U history is still usable, since
-- era 1 (no curve) and era 3 (no curve again, post-335) share a calibration
-- regime. That is true in general and IRRELEVANT for these bots -- measured:
--
--   bot                        era1  era2(bad)  era3
--   bot_coolbet_trigger_ou_v1     0        494     0
--   bot_unibet_trigger_ou_v1      0        267     0
--   bot_ou35_model_v1             0        190     0
--   bot_trigger_ou_model_v1       0        149     0
--   bot_coolbet_ou_model_v1       0         33     0
--
-- Every one of them was born inside the poisoned window. Excise era 2 and there
-- is no evidence left at all -- not weak evidence, none. So they stay active and
-- generating paper picks until era 3 accumulates volume, and the decision is
-- re-taken then. Retiring a bot you cannot measure is not a cleanup, it is a
-- coin flip with a paper trail.
--
-- THE 1x2 THREE ARE A DIFFERENT CASE. 1x2 is not part of that incident:
-- model_calibration 1x2_home held a=1.6081, b=-0.8604 continuously from
-- 2026-08-30 through 2026-09-13 with no step change on 09-03. The other
-- confound in the same window, the ACCESSIBLE_BOOKMAKERS change of 09-04..09-06,
-- also does not apply: all three have 100% of their CLV-bearing picks AFTER
-- 09-06, so their entire measured life sits in one book regime.
--
-- THE EVIDENCE (placeable books only -- Unibet-Kambi excluded, it disagrees with
-- the real site on 91% of quotes; Pinnacle excluded, unbettable here and the
-- reference CLV is measured against, so circular). Re-run 2026-09-14:
--
--   bot                          n    CLV      t      ROI   fold-robust +ve cfg
--   bot_coolbet_trigger_1x2_v1  277  -9.16%  -14.5  -24.2%  NONE
--   bot_unibet_trigger_1x2_v1   309  -8.68%   -7.1   -9.1%  NONE
--   bot_trigger_1x2_model_v1    381  -8.44%  -12.2  -15.7%  NONE
--
-- Searched edge floors (5/8/10/13/15%), odds floors (2.2/2.8/3.2) and each
-- selection in isolation. Not one candidate is CLV-positive in all three
-- walk-forward folds. "No configuration works" is the decisive finding -- it is
-- a stronger statement than "it loses", because it says tuning the gates is
-- wasted effort. bot_trigger_1x2_model_v1 additionally clears this repo's
-- n >= 334 threshold for a trusted CLV read, at t = -12.2.
--
-- This is consistent with the independent finding that the model's disagreement
-- with the de-vigged market has AUC 0.344 on 1x2 -- anti-predictive -- and that
-- `edge` IS that disagreement. Whether model-anchored betting has a future at
-- all is a separate, open, owner-level question (OU-CALIBRATOR-REFIT-ON-SHRUNK
-- and the AUC finding); this migration only stops three bots that have answered
-- it in the negative on their own data.
--
-- SAFE: all three are paper-only -- they write shadow_bets, have zero rows in
-- simulated_bets and real_bets, and no coolbet_placer_bots toggle. No published
-- /performance figure moves and no money changes. Their settled picks are KEPT
-- as the evidence record for the anchor split (model-anchored loses to the
-- close; sharp-anchored beats it).

UPDATE bots
   SET is_active      = FALSE,
       retired_at     = NOW(),
       retired_reason = 'SHADOW-BOT-VERDICTS 2026-09-14: model-anchored 1x2, CLV negative on placeable books (t=-7.1..-14.5) and NO fold-robust positive configuration over edge floors, odds floors or single selections. Not part of the O/U calibrator incident (1x2 curve continuous since 08-30). Paper-only, history kept.',
       updated_at     = NOW()
 WHERE name IN ('bot_coolbet_trigger_1x2_v1',
                'bot_unibet_trigger_1x2_v1',
                'bot_trigger_1x2_model_v1')
   AND retired_at IS NULL;

-- Belt-and-braces: none of these should hold a real-money toggle. Idempotent.
DELETE FROM coolbet_placer_bots
 WHERE bot_name IN ('bot_coolbet_trigger_1x2_v1',
                    'bot_unibet_trigger_1x2_v1',
                    'bot_trigger_1x2_model_v1');
