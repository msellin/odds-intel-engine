-- LINESHOP-RETIREMENT-REASON-CORRECTED (2026-09-14) — the four line-shop bots
-- stay retired, but the reason on record is wrong in both of its halves and
-- would mislead the next person to read it. This corrects the record only; no
-- bot changes state.
--
-- WHAT IS RECORDED TODAY (all four, retired 2026-09-08):
--   "line-shop signal loses out-of-sample (BOT-2D-AUDIT held-out);
--    model-edge is the path (owner-approved)"
--   and for bot_coolbet_value_v1 additionally:
--   "loses OOS (S52) ... it was -17% on O/U every month"
--
-- HALF ONE IS FALSE: the line-shop signal does NOT lose out-of-sample.
-- Measured 2026-09-14 on placeable books, restricted to ERA 1 (before
-- 2026-09-03 09:02 UTC) so the OU-CALIBRATOR-DOMAIN-MISMATCH window cannot be
-- what is being measured:
--
--   bot                    n     CLV       t      ROI
--   bot_sweep_ou25_v1     171   +5.20%   +9.1    -6.8%
--   bot_pin_1x2_home_v1   236   +4.34%   +7.7   +10.8%
--   bot_coolbet_value_v1  325   +4.29%   +8.3   +10.3%
--   bot_sweep_ou35_v1     137   +4.08%   +5.7   +10.4%
--
-- All four beat the closing line with t = +5.7 to +9.1. Three show double-digit
-- positive ROI (not significant on its own -- every ROI t is below 1.3 -- but
-- certainly not the negative the record asserts).
--
-- bot_coolbet_value_v1's specific charge does not survive either. Its O/U legs
-- split cleanly by era:
--
--   segment              era1 n=  ROI      era2 n=  ROI
--   over_under_25          56  +3.1%         42  -27.6%
--   over_under_35          52 +15.9%         31  -12.0%
--   1x2                   217 +10.9%        146   -7.4%
--
-- The "-17% on O/U" is an era-2 shape. NOTE the honest caveat: this bot was
-- retired on 2026-09-08, only five days into era 2, so the original figure was
-- computed largely on era-1 data and cannot be blamed entirely on the
-- calibrator -- it was most likely measured on all books or on the idealized
-- price basis rather than the executable, placeable-book basis used here. Either
-- way the conclusion it records is not what placeable-book era-1 data shows.
--
-- HALF TWO HAS SINCE BEEN FALSIFIED BY EVENTS: "model-edge is the path". The
-- model-edge bots these four were retired IN FAVOUR OF have themselves now
-- failed. Migration 336 (2026-09-14) retired bot_coolbet_trigger_1x2_v1,
-- bot_unibet_trigger_1x2_v1 and bot_trigger_1x2_model_v1 at CLV -8.4% to -9.2%
-- with no fold-robust configuration, and bot_coolbet_ou_model_v1 -- the
-- real-money model-edge bot named as the path -- was toggled off on 2026-09-13.
--
-- SO WHY DO THEY STAY RETIRED? Because the real successor is the SHARP anchor,
-- not the model anchor, and it beats them 2-3x on the same metric:
--
--   bot_coolbet_trigger_sharp_1x2_v1   n=65  CLV +12.09%  t=+8.8
--   bot_unibet_trigger_sharp_1x2_v1    n=63  CLV +10.40%  t=+4.8
--   bot_coolbet_trigger_sharp_ou_v1    n=18  CLV  +6.93%  t=+6.1
--
-- against +4.1% to +5.2% for the line-shop four. Un-retiring would add a weaker
-- duplicate of a strategy already running, and the sharp bots' data is FORWARD
-- data gathered after these four were selected out, which satisfies the BETA
-- bar's criterion (5) by construction where theirs cannot.
--
-- The useful inheritance is the EDGE >= 13% FLOOR, which lifts the line-shop
-- CLV roughly 3x (to +15.3% / +15.2% / +8.0% on era-1 data) and which all four
-- landed on independently -- not the bots themselves.

UPDATE bots
   SET retired_reason = 'SHADOW-BOT-CONSOLIDATION 2026-09-08, REASON CORRECTED 2026-09-14 (migration 337). The original reason — "line-shop signal loses out-of-sample; model-edge is the path" — is wrong in both halves. On placeable books, era-1 data only, this bot beats the close (see migration 337 header for its CLV/t/ROI). And the model-edge strategy it was retired in favour of has itself since been retired (migration 336) or toggled off. It stays retired because the SHARP anchor is the real successor and scores 2-3x better on CLV, not because line-shop loses. The transferable finding is the edge>=13% floor.',
       updated_at     = NOW()
 WHERE name IN ('bot_sweep_ou25_v1',
                'bot_sweep_ou35_v1',
                'bot_coolbet_value_v1',
                'bot_pin_1x2_home_v1')
   AND retired_at IS NOT NULL;
