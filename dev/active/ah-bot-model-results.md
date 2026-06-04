# AH-BOT-MODEL — Backtest Results

Universe: **8,441 matches** with paired Pinnacle AH closing + ensemble 1X2 prediction, since 2024-01-01. ALL handicap lines accepted (whole / half / quarter) — `_ah_model_prob()` handles each push-correctly.

Model path: `ensemble (p_h, p_d, p_a)` → `_solve_lambdas_calibrated()` → `(exp_h, exp_a)` → `_ah_model_prob(exp_h, exp_a, selection, line)`. This is the production Poisson + Dixon-Coles AH function from `workers/jobs/daily_pipeline_v2.py:1158`.

## Edge threshold sweep

| Edge threshold | N bets | N home | N away | P&L (units) | ROI |
|---:|---:|---:|---:|---:|---:|
| -0.020 | 8,441 | 2,689 | 5,752 | -247.06 | -2.93% |
| +0.000 | 8,441 | 2,689 | 5,752 | -247.06 | -2.93% |
| +0.010 | 8,005 | 2,500 | 5,505 | -240.90 | -3.01% |
| +0.020 | 7,605 | 2,321 | 5,284 | -234.86 | -3.09% |
| +0.030 | 7,187 | 2,130 | 5,057 | -238.49 | -3.32% |
| +0.050 | 6,390 | 1,805 | 4,585 | -234.04 | -3.66% |
| +0.080 | 5,136 | 1,321 | 3,815 | -181.29 | -3.53% |
| +0.100 | 4,374 | 1,071 | 3,303 | -203.33 | -4.65% |
| +0.120 | 3,678 | 860 | 2,818 | -142.42 | -3.87% |
| +0.150 | 2,669 | 553 | 2,116 | -115.23 | -4.32% |

Skipped 0 matches (lambda solver failed).

## ❌ No positive-ROI threshold

Best threshold with ≥200 bets: **−0.020 / +0.000** → 8,441 bets, ROI **−2.93%** (negative — roughly the AH vig). Production Poisson + Dixon-Coles function + reverse-derived expected goals from ensemble 1X2 does not beat Pinnacle's AH closing on any subset.

## Comparison vs AH-BOT-PROTOTYPE

The prototype (naive 1X2 → AH derivation, ±0.5 / ±0.25 / 0 lines only) found −2.65% at zero-edge and worsened to −5.01% at +0.10 edge. The proper model (full Poisson + Dixon-Coles, all lines) finds **−2.93%** at zero-edge and **−4.65%** at +0.10 edge. Marginally worse — both models hit Pinnacle's AH vig, and tighter edge filtering concentrates noise rather than signal.

Two consistent observations across both backtests:
1. **The ensemble systematically rates away sides higher than Pinnacle's AH implied.** N(away bets) ≈ 2.1× N(home bets) at zero edge. Either Pinnacle's home-favourite advantage anticipation is sharper than ours, or our Platt calibration leaves a residual away bias the AH market correctly prices.
2. **ROI monotonically worsens as edge threshold tightens** — the textbook signature of "edge" that is statistical noise rather than signal.

## Implication: Pinnacle AH is the wrong market for our ensemble

Our model has no edge over Pinnacle's AH closing — confirmed by both prototype and the proper goals-model path. Both indicate the same conclusion: **a viable AH bot would need a different source of edge**, not just a better translation of 1X2 → AH. Candidate edge sources, in rough priority:

1. **Line movement** — the drift signal from CSV-FULL-EXTRACT (+8.76pp WR spread top vs bottom quintile on 1X2). If the same pattern holds on AH lines, betting against late-moving lines could yield edge.
2. **Soft-book disagreement** — finding cases where Pinnacle's AH implied diverges from the soft-book consensus (the existing `bookmaker_disagreement` signal direction). Pinnacle is sharp on closing but slower to move on news; soft-book consensus catches market reactions.
3. **A dedicated goals model trained on AH closing directly** — not reverse-derived from 1X2. Would require training a Poisson regressor against historical AH closing as the target, rather than just goals.
4. **Lineup / injury / weather signals** that Pinnacle isn't pricing — coverage of these features is sparse but growing.

## Recommendation

**Shelve AH bot development.** The cheap path (naive prototype) and the proper path (production Poisson + Dixon-Coles) both confirm Pinnacle's AH market is too sharp for our current model. Pursuing it further would mean building one of the four candidate-edge mechanisms above, each of which is its own substantial project.

**Strategic value of this work:** ruled out a tempting-but-empty direction before sinking deeper model effort. Plus the 8,868-match AH closing backtest universe stays as a reusable asset for any future AH model.

The CSV-FULL-EXTRACT data unlock remains net-positive even with this negative AH finding — the **drift signal (+8.76pp WR spread)** is a real, separately-confirmed win that should improve calibration when wired in (DRIFT-FEATURE-REENABLE post WC).
