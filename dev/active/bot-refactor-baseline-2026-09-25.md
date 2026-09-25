Parent row: PRIORITY_QUEUE.md #162 — W0.1 baseline (diff after every Phase 2/3 step; an unexplained move stops the sequence)

# Baseline snapshot — 2026-09-25 ~15:35 UTC, after #159 (d9d15344 / e1967cdc / fbefce28)

Query: `scratchpad/baseline.sql` (read-only), re-run it verbatim after each step.

## bot_performance (THE per-bot source since #159)
| bot | settled | pending | roi_public | roi_own | roi_staked | clv_n | clv_public | clv_own | pnl_units_public |
|---|---|---|---|---|---|---|---|---|---|
| bot_v10_1x2 | 397 | 1 | +10.79% | +8.06% | +8.19% | 170 | +5.63% | +3.03% | +42.83 |
| bot_high_roi_global_v2 | 52 | 0 | +18.79% | +17.29% | +22.86% | 23 | +2.27% | −0.09% | +9.77 |
| bot_combined_1x2_ev5_v1 (VIP #1) | 2 | 28 | −3.00% | −3.00% | −53.76% | 1 | +13.44% | +13.44% | −0.06 |
| bot_ou_sharp_early_v1 (VIP #2) | 0 | 11 | — | — | — | 0 | — | — | 0.00 |
| bot_sharp_1x2_v1 (forward test) | 64 | 1 | +6.97% | +6.97% | +6.97% | 64 | +2.38% | +2.38% | +4.46 |

## bot_scoreboard (admin) — same 5 bots
roi_unit = roi_own, roi_public identical to bot_performance; clv_anchor = clv_own; statuses: v10 calibrated,
high_roi beta, EV5 experimental, O/U EARLY experimental, sharp_1x2 testing. bot_sharp_1x2_v1 clv_mc −2.73%
(own-book close — the #156 trap).

## Pick queue source `shadow_bot_scoreboard`
Columns: bot_id, bot_name, settled_n, settled_won, settled_pnl_eur, settled_roi, clv_n, clv_mc_mean, clv_mc_sd,
decision_fresh_n, decision_age_known_n — still its own computation (W6.1).

## Money
* real_bets last 30 d (placed_real IS NOT FALSE): 155 bets, €1,550.69 staked, P&L −€111.89
* placed_real IS NULL: 849 rows, €4,925.19 (W4.1 three-state back-fill target)
* today: 0 bets, €0.00
* controls: placement_paused = TRUE, real_money_armed = FALSE, publishing_paused = FALSE, daemons_paused = FALSE
* coolbet_placer_bots: 0 of 11 ui_place_enabled
