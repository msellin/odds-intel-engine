-- 422 — backfill shadow_bets.clv_pinnacle_live (2026-09-24, found by #140's slice analysis).
--
-- Why: workers/jobs/settlement.py's _PENDING_SHADOW_BETS_SQL never selected sb.odds_at_pick_live,
-- so the shadow settle path computed clv_pinnacle_live from bet.get("odds_at_pick_live") = None and
-- wrote NULL on every shadow pick it settled (the same defect class 04fff33f fixed on the
-- simulated_bets side). clv_pinnacle_live is the CLV at the price actually on offer — the column
-- the CLV gate reads (migration 300) — so every gate/analysis on it saw no data for these rows.
-- The code fix selects the column (same commit). This repairs the history exactly:
--
--   clv_pinnacle      = odds_at_pick      * p_devig - 1   (stored)
--   clv_pinnacle_live = odds_at_pick_live * p_devig - 1   (missing)
--   => clv_pinnacle_live = odds_at_pick_live * (clv_pinnacle + 1) / odds_at_pick - 1
--
-- Same de-vigged Pinnacle close, same rounding (4 dp) as the settler. Only rows where the live
-- price and the Pinnacle CLV exist and the live CLV is missing; never overwrites a value.
UPDATE public.shadow_bets
   SET clv_pinnacle_live = round((odds_at_pick_live * (clv_pinnacle + 1) / odds_at_pick - 1)::numeric, 4)
 WHERE result IN ('won', 'lost')
   AND clv_pinnacle IS NOT NULL
   AND odds_at_pick_live IS NOT NULL AND odds_at_pick_live > 1
   AND odds_at_pick > 1
   AND clv_pinnacle_live IS NULL;
