# #084 HALF-TIME LAYER — the BASELINE, measured before any change

**Captured 2026-09-23, on the owner's instruction: *"measure the starting point
and then after it finishes we can see if and how much it helped"*.**

Everything below is the state of the world BEFORE a single half-time feature
exists. Re-run the identical commands after the work and compare only against
this file — not against memory, and not against a number quoted in a commit
message.

---

## 1. The α baseline — the two numbers that decide whether this helped

Produced by `scripts/residual_test.py` and `scripts/residual_test_ou.py`, bundle
`v20260914_clean_cut0820`, cutoff 2026-08-20, **REALISTIC arm (the one that
decides)**:

| | market log-loss | market AUC | model log-loss | model AUC | **α** | residual AUC |
|---|---|---|---|---|---|---|
| **1x2** | **0.6195** | **0.7041** | 0.6471 | 0.6608 | **0.0000** | 0.4303 |
| **O/U 2.5** | **0.6737** | **0.6038** | 0.6789 | 0.5825 | **0.0000** | 0.4382 |

Shin-de-vig robustness arm: 1x2 market 0.6186 / residual 0.4138; O/U market
0.6738 / residual 0.4317. Both FAIL on both arms.

**Read the residual AUC, not just α.** Both are **below 0.5** — where our model
disagrees with the market, the market is right more often than chance. That is a
harder starting point than α = 0 alone suggests, and any claim of improvement
must move this number toward 0.5 as well.

**What "it helped" would mean, pre-committed:**
* **Real:** α > 0.02 AND blend log-loss < market log-loss on the realistic arm.
* **Directional but not a pass:** residual AUC rises toward 0.50 while α stays 0.
* **No effect:** α stays 0.0000 and residual AUC does not move. ← the expected outcome

## 2. The data baseline — what exists before the work

| asset | count | % of 175,450 finished |
|---|---|---|
| `ht_score_home/away` | **172,439** | **98.28%** |
| `h2_score_home/away` | 172,436 | 98.28% |
| Half-time-derived FEATURES in `match_feature_vectors` | **0** | — |
| `h2_score_*` SELECTs anywhere in the codebase | **0** | — |
| 1H markets in `predictions` | **0** | — |

## 3. The 1H market baseline — what we would be pricing into

`1x2_1h` odds are stored on ~9,226 matches (thin, because
`prune_odds_snapshots` is aggressive) against an OUTCOME side 141,764 matches
deep. **So the model trains on plenty and the backtest window is short** — state
that up front rather than discovering it at review.

Pinnacle-priced 1H markets, last 90 days: `team_total_1h_home_05` 6,863 matches ·
`team_total_1h_away_05` 6,607 · `1x2_1h` 3,439 · `corners_1h_ou_45` 3,025.

## 4. Confounds that will contaminate a naive before/after

⚠️ **Do not compare a new model against the OLD model.** Two things move
underneath this measurement while the work is in progress:

1. **The #078/#081/#088 backfills are running right now.** Lineups went
   11,359 → 25,000+, referee 98,211 → ~100,000, assists 0 → climbing, and the
   `match_stats` shot-location columns go 0 → ~54,000. Feature FILL RATES will
   change under us, and a feature's `_missing` indicator changes meaning when its
   fill changes (gotcha §39 in shape).
2. **A retrain is required for any of this to matter** — the production bundle
   was fitted on the sparse version.

**Therefore the only valid comparison is new-model-vs-MARKET, using the market
columns in the table above as the fixed reference.** The market numbers
(0.6195 / 0.7041 and 0.6737 / 0.6038) are the anchor; our own old model is not.

## 5. Reproduce exactly

```bash
python3 scripts/residual_test.py
python3 scripts/residual_test_ou.py --line 25
python3 scripts/data_coverage_report.py --days 180
```
