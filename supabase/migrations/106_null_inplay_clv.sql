-- PERF-INPLAY-CLV-NULL (2026-05-17): null out CLV on all historical inplay bets.
--
-- settlement.py:1373 already enforces this rule for new inplay bets:
--   if is_inplay:
--       closing_odds = None
--       clv_pinnacle = None
--
-- with the comment: "CLV is meaningless for inplay bets — live odds reflect
-- game state (goals, cards) not market efficiency, so closing_odds is just
-- whatever snapshot happened to be last captured, producing arbitrarily large
-- or small CLV with no signal value."
--
-- But that skip was added partway through the inplay bots' run. Older inplay
-- bets settled before the skip and got non-null CLV values computed against
-- the pre-match closing odds — meaningless for a bet placed at minute 47.
--
-- Inconsistency surfaced on /performance: some inplay strategies showed CLV,
-- others didn't, depending on when they settled relative to the skip. Cleanest
-- fix is to retroactively null the historical CLVs so the rule is enforced
-- everywhere. Per-bet pnl/result/stake untouched — only CLV is meaningless,
-- and only CLV gets nulled.

UPDATE simulated_bets sb
SET clv = NULL,
    clv_pinnacle = NULL
FROM bots b
WHERE sb.bot_id = b.id
  AND b.name LIKE 'inplay%'
  AND (sb.clv IS NOT NULL OR sb.clv_pinnacle IS NOT NULL);
