# Model Comparison — v_20260525_signals vs v20260524_market

> Generated 2026-05-25 after MFV-V3-PIVOT-EXTEND + candidate train

## Held-out window
- 2,522 settled matches between 2026-05-20 and 2026-05-24
- **Leakage caveat:** both models trained on MFV that includes this slice. Numbers are upper bounds. The relative comparison between two models trained on the SAME data is still informative — the candidate is fitting better.

## What changed in v_20260525_signals vs v20260524_market

Added 10 new features to the trainer feature list:
- `form_momentum_home/away` (last-3 ppg − last-10 ppg)
- `injury_severity_score_home/away` (SEVERE×3 + MODERATE×1.5 + MINOR×0.5)
- `league_draw_rate_ytd` (per-league season-to-date draw rate)
- `season_progress` (per-match [0..1] in season window)
- `line_velocity` (Pinnacle home implied-prob slope T-12h..T-2h)
- `xg_overperf_home/away` (rolling 10-match goals − xG)
- `league_clv_efficiency` (60d mean pseudo_clv per league)
- `team_avg_player_rating_home/away` (AF player ratings, sparse coverage)

## Results

| Market | v_20260525_signals | v20260524_market | Δ log_loss | hit_rate Δ |
|---|---|---|---|---|
| 1x2_home | **0.4517** | 0.4841 | **-6.7%** | +3.3pp |
| 1x2_draw | **0.4367** | 0.4736 | **-7.8%** | +1.8pp |
| 1x2_away | **0.4103** | 0.4389 | **-6.5%** | +2.1pp |
| over_25 | 0.6462 | **0.6451** | +0.2% (tied) | -0.8pp |
| btts_yes | **0.6682** | 0.6884 | **-2.9%** | +2.7pp |

ECE (calibration):
| Market | v_20260525_signals | v20260524_market |
|---|---|---|
| 1x2_home | 0.0731 | 0.0743 (≈tied) |
| 1x2_draw | 0.0693 | **0.0511** (worse) |
| 1x2_away | 0.0463 | 0.0458 (≈tied) |
| over_25 | 0.0710 | **0.0465** (worse) |
| btts_yes | **0.0451** | 0.0734 (better) |

## Verdict

**Candidate beats production on 4 of 5 markets** with material log-loss improvements on the three 1X2 outcomes (-6.5% to -7.8%) and BTTS (-2.9%). OU 2.5 is statistically tied. Calibration regresses on draw and over_25 — likely because the new features over-fit those markets at the bin boundaries.

## Recommendation

**DO NOT deploy mid-Phase-3.5** (env stays `MODEL_VERSION=v20260524_market` until 2026-06-07).

**Deploy decision queued for 2026-06-08:**
1. Run weekly retrain produces v_20260608 (clean 14-day window post-signal-shipping)
2. Run `offline_eval v_20260608 v_20260525_signals v20260524_market`
3. Deploy whichever wins on the most markets, with preference for the one with better calibration on the still-weak markets (draw + over_25)

Alternative: if v_20260608 also loses on draw calibration, consider keeping v20260524_market for the OU + draw heads and v_20260525_signals (or v_20260608) for the 1X2 home/away heads via per-market `MODEL_VERSION_*` env vars — already supported by inference.

## Validation follow-up (filed in PRIORITY_QUEUE as LEAGUE-DRAW-YTD-VALIDATE)

After deploy, measure per-market log-loss on the NEXT held-out window (2026-06-08 to 2026-06-15) — that's truly out-of-sample and tells us whether the signal lift was real or leakage.
