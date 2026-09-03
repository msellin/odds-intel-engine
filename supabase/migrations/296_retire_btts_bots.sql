-- BTTS-RETIRED-2026-09-03
--
-- Retire every BTTS bot across every cohort and stop pricing the market.
--
-- THE EVIDENCE
--
--   shadow BTTS      n=427   ROI -12.76%   t=-2.87   (p<0.01, live prices)
--   production BTTS  n=276   ROI  -2.36%   t=-0.38
--   every individual BTTS bot negative: -3.8% to -19.6%
--
-- Recalibration did not rescue it, and that is the part worth recording.
-- ENSEMBLE-RECALIBRATION took BTTS from ECE 0.047 to 0.009 through the
-- production apply_platt path -- the best-calibrated market we have. Re-scoring
-- every settled BTTS pick under the new coefficients cut volume to 33% and made
-- the survivors WORSE (-20.73% vs -12.76%), because shrinking probabilities
-- toward the base rate leaves only the longest-priced picks clearing an edge
-- floor. Honest probabilities revealed the absence of edge rather than
-- creating one.
--
-- It also cannot be validated even in principle: 0 of 3,076,350 BTTS snapshots
-- carry Pinnacle, because API-Football's Pinnacle feed has 8 bet types and
-- Both Teams To Score is not one of them. clv_pinnacle is permanently NULL for
-- BTTS, so the CLV-anchored promotion gate can never fire for a BTTS bot --
-- there is no sharp anchor to price against.
--
-- Operator decision 2026-09-03: retire, and spend the effort on OU instead.
--
-- NOT deleted: historical BTTS bets stay. They are the evidence for this
-- decision and the answer to "how do you know your bots are not curve-fit?"
-- (OUT-OF-BETA-CUTOFF: never delete historical bets).
--
-- Code side, same commit: bot_sweep_btts_yes_v1 removed from the sweep pass,
-- bot_btts_all / bot_btts_v2 gated is_active=False in BOT_CONFIGS, and
-- coolbet_placer._MIN_EDGE_BY_MARKET["btts"] set to None so _min_edge_for
-- returns infinity and no BTTS bet can ever be placed.

UPDATE bots
   SET is_active      = false,
       retired_at     = COALESCE(retired_at, NOW()),
       maturity_label = 'retired'
 WHERE name IN ('bot_btts_all', 'bot_btts_v2', 'bot_sweep_btts_yes_v1')
   AND (is_active = true OR retired_at IS NULL);

-- Belt and braces: any other bot whose name marks it as BTTS-dedicated.
UPDATE bots
   SET is_active      = false,
       retired_at     = COALESCE(retired_at, NOW()),
       maturity_label = 'retired'
 WHERE name ILIKE '%btts%'
   AND (is_active = true OR retired_at IS NULL);
