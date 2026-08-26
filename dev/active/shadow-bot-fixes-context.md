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

## Round 2 findings (2026-08-26, later)

6. Bookmaker sharpness: my first ranking used raw Brier per book and put
   Pinnacle 14th of 15. That was INVALID — each book prices a different slate,
   so Brier partly measures how easy its games are. Rewritten as a paired
   comparison on shared matches: **every one of the 15 books is worse than or
   equal to Pinnacle.** Closest (indistinguishable): 10Bet +0.000026 (t=0.26),
   Marathonbet +0.000032, 1xBet +0.000048, Superbet +0.000089. Significantly
   worse: Bet365 (t=2.23), SBO (1.96), 888Sport (3.12), Unibet (3.62),
   Coolbet (3.84, +0.003095 — 5x the next worst).
   Consistent with the consensus result: every individual book is worse than
   Pinnacle, yet an AVERAGE of them slightly beats it. Classic wisdom-of-crowds;
   averaging cancels idiosyncratic error.

7. OU "edge" root cause found and it is one book. Auditing 187 settled OU
   line-shop picks against Pinnacle's full ladder:
       Unibet 82 picks / 100% match stated line
       10Bet  43 / 91%
       Coolbet 34 / 65%   <- 11 of 12 drifts land on OU 3.5
       Betano 14 / 100%, Marathonbet 10 / 100%, 888Sport 4 / 100%
   On picks labelled over_under_25/over, Coolbet averaged 1.96 while Pinnacle's
   2.5 averaged 1.60 and its 3.5 averaged 2.44 — Coolbet's "over 2.5" prices
   like a 3.0. Coolbet is the operator's real placement venue.
   COOLBET-OU-LINE-MISLABEL-GUARD-2026-08-22 misses this because a uniformly
   shifted single line stays internally monotone.
   Fix: `_ou_line_is_consistent()` in daily_pipeline_v2 — bookmaker-agnostic,
   tests the price not the source.

## Still open
- The Coolbet INGESTION bug itself (why its OU line labels are shifted) is not
  fixed — only the downstream consumption is guarded. Needs a Coolbet-side
  investigation; filed separately.
- Long-horizon Shin-vs-proportional replay was still running at hand-off.

## Shipped (all pushed, engine deploy + migrations green)
| commit | what |
|---|---|
| engine `7ea8623` | CLV anchor, Shin de-vig, t-stat gate, migration 283, 8 analysis scripts |
| web `9feffba` | t-stat gate + Pinnacle-anchored CLV on /admin/shadow-bots |
| engine `27832ba` | OU line-integrity guard + discretion report |
| web `bd2eeb1` | discipline-check panel |
| engine `208cf84` | MODEL_WHITEPAPER §9.1 / §Anchor / §10c.2b / §10c.2c, ROADMAP, SIGNALS |
| engine `7164c7d` | fix PER-BOT-SWEEP-CONFIG (pinned the de-vig formula Shin replaced) |
| engine `3a8f79d` | queue: COOLBET-OU-LINE-SHIFT + CONSENSUS-ANCHOR-BOT |
| engine `2f76eb6` | revert two lines a bulk sed corrupted (GROWTH-* tests) |

## CI accounting
Baseline before this work (6508834): 661 passed / 89 failed.
After: 664 passed / 89 failed — +3 new engine tests, no net new failures.
The 89 are the standing CI-SMOKE-GATE-DEAD-2026-08-24 breakage (DATABASE_URL
repo secret still points at localhost:5433). Needs the operator.

## Lesson worth keeping
Two self-inflicted regressions in this session, both from bulk sed over a
30k-line test file. CI caught both only because the failure COUNT was compared
against a known baseline — the run was already red, so "still red" proved
nothing. Comparing failing test NAMES against a baseline commit is the only
thing that worked. That is a direct consequence of CI-SMOKE-GATE-DEAD: a
permanently-red gate cannot signal a regression.
