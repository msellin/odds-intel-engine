# SHADOW-BOT-FIXES-2026-08-26 — context

## Status
Tasks 1 (CLV), 2 (Shin de-vig), 3 (promotion gate) shipped. 4 (discretion),
5 (OU audit), 6 (view align — folded into migration 283) in progress.

## Key files touched
- `workers/model/devig.py` — NEW. Shin + proportional de-vig.
- `workers/jobs/settlement.py` — `get_closing_odds(bookmaker=...)`,
  `get_devigged_pinnacle_close_prob()`, `_market_complement_selections()`,
  shadow settlement writes `clv_pinnacle` + `closing_bookmaker`.
- `workers/jobs/daily_pipeline_v2.py` — both line-shop sites use Shin.
- `supabase/migrations/283_shadow_clv_pinnacle.sql` — applied 2026-08-26.
- `../odds-intel-web/.../admin/shadow-bots/page.tsx` — t-stat gate + pin CLV.

## New analysis scripts (all re-runnable)
- `scripts/clv_variant_backtest.py` — which CLV definition predicts ROI
- `scripts/devig_calibration_backtest.py` — Shin vs proportional calibration
- `scripts/promotion_gate_simulation.py` — Monte-Carlo of the graduation gate
- `scripts/lineshop_replay.py` — point-in-time replay of any bot config
- `scripts/anchor_comparison_backtest.py` — Pinnacle vs consensus anchors
- `scripts/bookmaker_sharpness_rank.py` — per-book calibration ranking
- `scripts/discretion_bleed_report.py` — placed vs untouched, day-clustered
- `scripts/backfill_shadow_clv_pinnacle.py` — ran 2026-08-26, 20,760 rows

## Findings that changed a decision
1. My initial claim "the CLV column measures nothing" was WRONG. Any-book CLV
   does carry signal (rho +0.059). It is the weakest of the variants and its
   LEVEL is meaningless, but it is not noise. Corrected before shipping.
2. Raw vs de-vigged Pinnacle CLV rank almost identically (+0.0780 vs +0.0784).
   The de-vig fixes the zero point, not the ordering.
3. "Shin only for 3-way" was WRONG — Shin also wins on lopsided 2-way (OU 3.5).
   Applied everywhere.
4. The discretion bleed is NOT statistically established. Pooled t looked like
   -1.65 but bets on one day are correlated; clustered by day t=-2.15 on df=3
   against a critical value of 3.18. Direction consistent (3 of 4 days), not proven.
5. Pinnacle is NOT required. A leave-one-out de-vigged consensus of the other
   books matches it (Brier -0.000036 vs Pinnacle on 51,732 identical rows).

## Next steps
- Read the two running replays (Shin vs proportional; retired-bot counterfactual).
- Task 5 OU audit; task 4 UI surface.
