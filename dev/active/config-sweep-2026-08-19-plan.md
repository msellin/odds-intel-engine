# CONFIG-SWEEP-2026-08-19 — Parameter Grid Sweep

## Goal
Systematically discover profitable bot configurations by walk-forward
backtesting a theory-driven parameter grid over historical data
(2026-05-01 → today-7d). Report configs that are consistently positive
across all three test windows. Deploy top winners as shadow bots.

## Why this way, not the naive way

Grid search against 4 months of data is a classic overfitting trap. To
avoid producing garbage "winners" that don't repeat live:

1. **Theory-driven grid, not brute force** — ~2000 combos, not 20,000.
   Every axis has justification (edge threshold matches known EV theory,
   odds range matches known market segments, tier matches known-edge
   leagues).
2. **Walk-forward validation** — three non-overlapping test windows. A
   config that's positive in only one window is variance; positive in
   all three is more likely real.
3. **CLV first, ROI second** — CLV is low-variance; ROI is noisy over
   4 months. Sort by aggregate CLV, filter by ROI for tie-breaking.
4. **Minimum sample per window** — require n ≥ 30 per config per window
   (else too small to distinguish from noise).
5. **Shadow deploy only** — top winners write to shadow_bets for 4-6
   weeks BEFORE they touch simulated_bets. Same discipline as
   bot_no_pin_shadow_v1.

## Parameter grid (~2000 configs)

| Axis | Levels | Justification |
|------|--------|---------------|
| `market` | 7: 1x2_home, 1x2_draw, 1x2_away, ou25_over, ou25_under, btts_yes, btts_no | Core markets with sharp Pinnacle coverage. AH excluded (line-specific logic). OU 0.5/1.5/3.5/4.5 excluded (rarely-picked). |
| `edge_threshold` | 4: 0.03, 0.05, 0.07, 0.10 | Below 3% is noise; 5-7% is where existing bots operate; 10% is Kelly-friendly gate. |
| `odds_min` | 3: 1.30, 1.50, 2.00 | Short-fav, mid, coin-flip. Below 1.30 → juice too heavy. |
| `odds_max` | 3: 2.50, 3.50, 5.00 | Cap prevents fake-edge tail (see Gremio U20 4.50 fantasy prices). |
| `min_prob` | 3: 0.25, 0.35, 0.45 | Model confidence floor. Below 0.25 = long-tail bets. |
| `tier_filter` | 5: {1}, {1,2}, {1,2,3}, {1,2,3,4}, {2,3} | Match model's known-edge leagues. {2,3} is "middle tier only" test. |
| `require_pinnacle` | 2: True, False | Cross-validate whether Pinnacle-required actually gains edge. |

Total: 7 × 4 × 3 × 3 × 3 × 5 × 2 = **7,560 configs**.

*Larger than initial estimate — but each config eval is a pandas filter
(millisecond). Full sweep should complete in 5-10 minutes.*

## Walk-forward windows

- **W1**: 2026-05-01 → 2026-06-15 (6.5 weeks)
- **W2**: 2026-06-16 → 2026-07-31 (6.5 weeks)
- **W3**: 2026-08-01 → 2026-08-12 (12 days — smaller, but the most
  recent)

Each config gets evaluated independently in all three windows. No
"training" — the model itself is fixed; we're only selecting configs
that use its predictions.

## Result acceptance criteria

Top winners must satisfy ALL:

1. `n_settled ≥ 30` in each of W1, W2, W3
2. `roi ≥ 0` in each of W1, W2, W3 (positive in all three)
3. `clv ≥ 0` aggregate across windows
4. `roi_aggregate ≥ 5%` across windows (economic threshold)

Sort survivors by aggregate CLV. Manual review of top 5-10 to check
for market/tier/threshold patterns that suggest real edge.

## Deployment (Phase C)

Top 2-3 surviving configs → shadow bots via a migration analogous to
271_bot_no_pin_shadow.sql. Same pattern:
- Write to shadow_bets only
- `maturity_label='experimental'`
- 4-6 weeks observation
- Then evaluate for paper-beta promotion

## Files

- `scripts/config_sweep.py` — sweep engine (Phase B)
- `dev/active/config-sweep-2026-08-19-results.csv` — full result table
- `dev/active/config-sweep-2026-08-19-report.md` — human-readable
  summary of top configs + recommended shadow deployments

## Risk register

- **Look-ahead**: closing_odds and calibrated_prob are post-hoc.
  Sweep uses `odds_at_pick` (accessible-book best) and raw
  `model_probability` from `ensemble` source — both known at pick time.
- **Multi-comparison inflation**: 7,560 configs × 5% significance = 378
  false positives expected. Cross-window consistency (W1 AND W2 AND W3
  positive) is the primary defense. Aggregate CLV as tie-breaker.
- **Fantasy-price leakage**: Some historical odds_at_pick are unreachable
  (Gremio U20-style). Sweep should filter out rows where
  odds_at_pick/closing_odds ≥ 1.65 (same threshold as CLV-AUTOVOID).

## Estimated effort

- Phase A: plan (this doc) — 30 min ✓
- Phase B: sweep engine — 4-6h
- Phase C: run + interpret + shadow-deploy top winners — 2-3h
- Total: ~7-10h
