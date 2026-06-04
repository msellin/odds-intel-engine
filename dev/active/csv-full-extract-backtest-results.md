# CSV-FULL-EXTRACT — Backtest Results

Generated from `scripts/backtest_csv_full_extract.py`, since=2024-01-01.

## TL;DR

| Test | Question | Result | Verdict |
|---|---|---|---|
| A | Is Betfair Exchange a sharper anchor than Pinnacle? | Identical Brier/LogLoss to 4 decimal places | **No improvement — keep Pinnacle anchor** |
| B | Can a flat-stake AH strategy beat the vig? | Home -5.4%, Away +0.9% (≈ vig) | **AH market efficient; need a model edge to beat it. But we now have 8,868 matches' backtest universe.** |
| C | Does pre-kickoff Pinnacle drift predict outcome? | Monotonic, **8.76pp WR spread top-vs-bottom quintile** | **Strong signal — add `pinnacle_close_minus_open_implied` as meta-model feature** |

---

## A. Anchor swap — Pinnacle vs Betfair Exchange (1X2 closing devig'd implied)

Paired matches with both Pinnacle and Exchange closing 1X2: **7,328** (matches since 2024-01-01).

| Anchor | Brier (mean) | LogLoss (mean) | N |
|---|---|---|---|
| Pinnacle | 0.5886 | 0.9862 | 7,328 |
| Betfair Exchange | 0.5887 | 0.9862 | 7,328 |

**Interpretation:** functionally identical. The two sharpest markets in football converge on the same devig'd probabilities — the 0.0001 Brier delta is statistical noise. This **validates Pinnacle as the current shrinkage anchor** (CAL-PIN-SHRINK 2026-05-06) and indicates that paying for an Exchange feed solely to get a "sharper anchor" wouldn't move calibration. Exchange has other uses (true no-vig back/lay split, line-shopping diversity) but for the calibration anchor specifically, no swap warranted.

## B. AH market sanity — Pinnacle closing AH

- Matches with both home + away AH closing pair: **8,868** (first time we have this depth in the DB)
- Flat-stake ROI, home: **-5.43%**
- Flat-stake ROI, away: **+0.91%**

**Interpretation:** Pinnacle AH is efficient — a uniform "always bet home AH" loses to vig; "always bet away" is roughly break-even (likely sampling noise rather than a real away bias). This is the expected baseline. What's now unlocked: we have **8,868 matches with paired AH closing prices + actual outcomes** as a backtest universe for any future AH bot strategy. Previously: zero. Without that backtest universe we couldn't even calibrate an AH model.

## C. Opening→closing drift — Pinnacle home implied probability

Paired open+close 1X2 matches: **8,850** (since 2024-01-01).

| Quintile | N | Mean drift (close − open, implied home prob) | Home win rate |
|---|---|---|---|
| 1 (lowest) | 1,770 | **−4.82%** (market moved AWAY from home) | **39.94%** |
| 2 | 1,770 | −1.95% | 41.13% |
| 3 | 1,770 | −0.47% | 41.64% |
| 4 | 1,770 | +0.99% | 44.75% |
| 5 (highest) | 1,770 | **+4.02%** (market moved TOWARD home) | **48.70%** |

**Top-quintile minus bottom-quintile home win rate: 8.76 percentage points.**

**Interpretation:** clean monotonic signal. When sharp money pushes Pinnacle's home implied prob up by ~4%+ from open to close, home wins ~49% of the time. When the market moves the same magnitude away from home, home wins ~40%. The current model has `pinnacle_line_move_home` (T-12h → T-2h slope from the live feed, post-April 2026 only). This adds the same signal for historical training data going back to 2023 — and on a longer time window (open ≈ kickoff−7d → close ≈ kickoff).

## Recommended follow-up tasks

1. **DRIFT-FEATURE** — add `pinnacle_close_minus_open_implied_home/draw/away` to `match_feature_vectors`. Backfill from `odds_snapshots` where Pinnacle has both is_opening + is_closing rows for the same match (now 8,850 matches' worth). Re-train at next Sunday weekly retrain and measure feature importance + calibration delta vs control.

2. **AH-BOT-PROTOTYPE** — develop an AH bot using the existing Poisson goals model inverted to AH probabilities. Backtest against the 8,868-match universe to find break-even edge threshold. Only ship if `--edge-threshold` ≥ 0.X produces consistent ROI > 0 on out-of-sample years.

3. **Anchor decision** — _confirmed: no change._ Pinnacle stays as CAL-PIN-SHRINK anchor. Betfair Exchange rows kept in DB as second-opinion / cross-validation feed but not used as the shrinkage target.

## Dataset baseline numbers (post-ingest)

| Row type | Before CSV-FULL-EXTRACT | After CSV-FULL-EXTRACT |
|---|---|---|
| Betfair Exchange rows | 0 | 118,315 |
| Max consensus rows | 0 | 80,363 |
| Avg consensus rows | 0 | 80,363 |
| Betfred closing 1X2 | 0 | 26,913 |
| BetWin closing 1X2 | 0 | 58,458 |
| Pinnacle AH closing **with handicap_line** | 0 | 17,998 |
| Bet365 AH closing **with handicap_line** | 0 | 22,958 |
| Betfair Exchange AH closing **with handicap_line** | 0 | 17,266 |

Plus historical match stats (shots, SoT, corners, fouls, cards) and referee names backfilled from the same CSVs where AF's data was missing.
