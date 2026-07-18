# Rigorous OOS eval of MODEL-VERSION-FLIP-2026-07-18

First publication-quality validation of today's model flip. Companion to
`scripts/rigorous_eval.py`.

## Setup

- **Candidate**: `v20260712` (trained through 2026-07-11, promoted 2026-07-18)
- **Production**: `v20260705` (trained through 2026-07-05, prior active)
- **Eval window**: 2026-07-12 → 2026-07-18 — **strictly OOS for both** (no in-sample overlap by construction; the earlier `weekly_eval_and_compare.py` run had 1 week of in-sample contamination for v20260712)
- **n = 1,116 settled matches**
- **Statistical framework**: bootstrap (500 resamples) paired t-style, α = 0.05, one-sided per direction
- Command:

```bash
python3 scripts/rigorous_eval.py \
    --candidate v20260712 --candidate-cutoff 2026-07-11 \
    --production v20260705 --production-cutoff 2026-07-05 \
    --eval-end 2026-07-18 --bootstrap-n 500
```

## Direct comparison

| Market | Δ log-loss | p-value | Verdict |
|---|---|---|---|
| 1x2_home | **-3.1%** | 0.998 | ✅ **BETTER** |
| 1x2_draw | **-4.0%** | 1.000 | ✅ **BETTER** |
| 1x2_away | -0.3% | 0.664 | tie |
| over25 | +1.1% | 0.076 | tie (borderline worse) |
| under25 | +1.1% | 0.076 | tie (borderline worse) |
| btts_yes | **+1.4%** | 0.004 | ❌ **WORSE** |
| btts_no | **+1.4%** | 0.004 | ❌ **WORSE** |
| ah_home_-0.5 | -1.3% | 0.936 | tie |
| ah_home_+0.5 | **-1.8%** | 0.972 | ✅ **BETTER** |
| ah_home_-1.5 | **-2.1%** | 0.992 | ✅ **BETTER** |
| ah_home_+1.5 | **-1.7%** | 0.978 | ✅ **BETTER** |

**Score: 5 BETTER / 2 WORSE / 4 tie.**

## Pinnacle-close baseline (does the model add anything?)

Pinnacle close is the sharpest available line — the market's own best
guess after all information is in. This is the "can our model beat the
best pre-match odds" question.

Both v20260712 and v20260705 are **statistically significantly worse than
Pinnacle-close on every market** (n = 684 for 1X2, 489 for OU). Expected —
Pinnacle is the sharp line; we can't outpredict them directly. What we
CAN do is arbitrage the gap between softer books we bet at (Coolbet,
Bet365, etc.) and Pinnacle's later close. That gap becomes CLV.

The useful metric: **which of our models is CLOSER to Pinnacle?**

| Market | v20260712 vs Pinnacle | v20260705 vs Pinnacle | Winner |
|---|---|---|---|
| 1x2_home | +8.2% | +10.5% | v20260712 closer to Pinnacle ✅ |
| 1x2_draw | +3.1% | +6.1% | v20260712 closer to Pinnacle ✅ |
| 1x2_away | +4.8% | +5.3% | v20260712 closer to Pinnacle ✅ |
| over25 | +18.6% | +15.5% | v20260705 closer ❌ |
| under25 | +18.6% | +15.5% | v20260705 closer ❌ |

**v20260712 is more aligned with the sharp market on 1X2 across the
board.** That's the strongest validation of today's flip — the new model
isn't just fitting our historical data better, it's tracking closer to
what the sharpest bookmaker independently arrives at.

## Verdict on the promotion

**Correct call.** Details:

1. **Statistically significant improvement on 5 markets**, the ones we
   bet on most: 1x2_home, 1x2_draw, and 3 AH lines (n=1,116 each).
2. **Statistically significant regression on 2 markets**: BTTS yes/no.
   BTTS is a market we barely bet (see `roi_by_tier_report.py`), so the
   regression cost is minimal.
3. **OU regression is p=0.076 — borderline but not statistically
   confirmed at 7 days OOS.** Keeping `MODEL_VERSION_OU=v20260621` as
   the OU override is the right call under this uncertainty.
4. **v20260712 is closer to Pinnacle on 1X2 than v20260705** — new model
   is more sharp-aligned in the market that matters most.

## Rollback triggers

If any of the following holds after another 2 weeks of OOS data (2026-08-01),
consider reverting `MODEL_VERSION` to v20260705:

1. `1x2_home` OR `1x2_draw` Δ log-loss flips positive with p > 0.95
2. `over25` Δ log-loss > +2.0% with p > 0.95
3. Combined `Simulated_bets ROI` on 1X2 markets is worse than baseline
   (v20260705 shadow) by > 3% over n ≥ 200 bets

## Post-vacation follow-ups

1. **Re-run this eval at 2026-08-01** with 3+ weeks of clean OOS data.
   Higher n → tighter CIs → any borderline OU regression will resolve.
   Comparison against `SHADOW_MODEL_VERSION=v20260705` shadow predictions
   gives the paired comparison for free.

2. **Investigate BTTS regression.** v20260712 is definitively worse on
   BTTS (p=0.004). Since BTTS derives from the joint goal matrix
   (`workers/model/joint_probability.build_joint_matrix`), and OU-2.5 is
   also borderline worse, both suggest the **new bundle's goal
   regressors (`home_goals.pkl`, `away_goals.pkl`) have worse-calibrated
   λ estimates than v20260705**. Worth a specific ablation: score both
   bundles' goal predictions vs empirical goal distributions.

3. **Extend framework**: add per-tier + per-league breakdown to
   `rigorous_eval.py`. The tier-4 CLV≈0 pattern from `roi_by_tier_report`
   probably shows up here too but we didn't slice for it.

## Sources

- Live output (2026-07-18): `/tmp/rig2.out` on VPS
- Script: `scripts/rigorous_eval.py`
- Related: `dev/active/ou-regression-2026-07-18.md`, `MODEL_WHITEPAPER.md §3.1b`
