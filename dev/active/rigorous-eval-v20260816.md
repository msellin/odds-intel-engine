# Rigorous eval — v20260816 + draw-regression root-cause analysis

Generated 2026-08-16. Manually fired today's weekly retrain (the 03:00 UTC
scheduled fire failed because the scheduler was hung by the Aug 12→15
outage). v20260816 was trained on 71,409 rows through 2026-08-15.

## Head-to-head — v20260816 on strictly-OOS window Aug 16 (n=334)

Sample is small (only 1 day of strict OOS since the Aug 15 training cutoff);
enough to catch obvious regressions but not enough to detect subtle wins.

### vs v20260712 (current PROD MAIN)

| market | Δ log-loss | Δ % | p | verdict |
|---|---|---|---|---|
| 1x2_home | +0.0088 | +1.2% | 0.206 | tie |
| **1x2_draw** | **+0.0183** | **+3.2%** | 0.048 | ✗ WORSE (borderline) |
| 1x2_away | −0.0030 | −0.5% | 0.610 | tie |
| over25 / under25 | +0.0197 | +2.6% | 0.112 | → worse (not sig) |
| btts | ~0 | ~0 | 0.538 | tie |
| ah_home_+0.5 | **−0.0147** | **−2.2%** | 0.958 | ✓ BETTER |
| other ah lines | −1.9% to −2.2% | | not sig | → better |

### vs v20260719 (current PROD OU-override)

| market | Δ log-loss | Δ % | p | verdict |
|---|---|---|---|---|
| 1x2_draw | +0.0124 | +2.1% | 0.086 | → worse (borderline) |
| **over25 / under25** | **+0.0267** | **+3.6%** | 0.012 | ✗ WORSE |
| ah_home_-0.5 | −0.0128 | −1.8% | 0.978 | ✓ BETTER |

### vs v20260809 (last week)

| market | Δ log-loss | Δ % | p | verdict |
|---|---|---|---|---|
| **1x2_home** | +0.0134 | +1.9% | 0.048 | ✗ WORSE |
| 1x2_draw | +0.0073 | +1.2% | 0.196 | tie |
| **1x2_away** | **+0.0255** | **+4.1%** | 0.006 | ✗ WORSE |
| 4 AH lines | −2.1% to −2.5% | | <0.05 | ✓ BETTER on all |

**Verdict: do NOT promote v20260816 to either main or OU-override.**

The **4-consecutive-bundle regression pattern on 1x2_draw is now confirmed**:

| Bundle | 1x2_draw log-loss Δ vs v20260712 | Sig? |
|---|---|---|
| v20260712 | 0 (baseline) | — |
| v20260802 | +5.7% | ✗ WORSE |
| v20260809 | +4.4% | ✗ WORSE |
| v20260816 | +3.2% | ✗ WORSE (borderline) |

Each new bundle inches closer to v20260712 but never beats it. That's not
variance — it's a persistent regression pattern.

## Draw-regression root-cause analysis

The initial "friendlies pollute the training data" hypothesis proved wrong.
Empirical breakdown of the added window (Jul 12 → Aug 9, i.e. new data
between the last two retrains):

### Draw rate by segment

| Window | n | Draw rate |
|---|---|---|
| Historical pre-Jul-12 baseline | 144,773 | **25.41%** |
| Same window one year ago (Jul 12 → Aug 16, 2025) | 3,727 | 26.19% |
| **Added window** (Jul 12 → Aug 9, 2026) | 7,888 | **21.74%** |
| **OOS eval window** (Aug 9 → 16, 2026) | 3,270 | **22.75%** |

Summer 2026 has a **real ~3.5pp lower draw rate** than either historical
average or the same period in 2025. This is a season/market shift, not a
data artifact.

### Fixture-type breakdown of the added window

| Type | n | Draw rate |
|---|---|---|
| Club regular | 5,599 | 21.81% |
| **Friendly** | 1,877 | **21.79%** |
| International | 412 | 20.63% |

Friendlies show the same 21.8% draw rate as club regular season.
**Filtering friendlies changes nothing** — the low-draw signal is uniform
across fixture types. Original hypothesis wrong.

### The bigger clue — predicted rate vs actual

From the Aug 3 rigorous_eval SUMMARY_JSON:

- v20260802 `1x2_draw` **predicted mean rate**: 37.1%
- v20260802 `1x2_draw` **actual hit rate**: 20.0%

**The model over-predicts draws by ~17 percentage points**, and this
persists across all 4 recent bundles. Whether training data has 25%
draws or 22% draws barely moves the needle because the ensemble +
calibration stack is architecturally biased toward draws.

The market shift is real, but the model has been miscalibrated on
draws all along. What's happened since July isn't new — it's just that
the misprediction penalty grew when actual draws dropped further from
the model's already-inflated prior.

## What's actually worth trying

Not "filter friendlies before retrain" — that hypothesis was wrong.

**Real candidates, ranked by expected impact:**

1. **Post-hoc draw-rate calibration** (1-2 hours) — after model predicts,
   scale predicted P(draw) by a factor so the mean matches recent
   observed draw rate. Simplest and most reversible. Config knob to
   toggle on/off. Probably worth ~2pp log-loss recovery on 1x2_draw
   based on the pred_rate vs actual gap.

2. **Temporal weighting** (half day) — weight recent matches more heavily
   in training loss so the model tracks the current market rate. XGBoost
   supports `sample_weight` at fit time. Requires retrain from scratch.

3. **Season-context feature** (1 day) — add "days into current season"
   and/or "matchweek" as a feature. Lets the model condition on the
   low-draw early-season phase. Requires MFV backfill.

4. **Draw-specialist head** (1-2 days) — separate binary "draw vs not"
   head with its own calibration, blended into the 1x2 output. Bigger
   change, worth doing only if #1-3 don't move the needle.

## Sunday retrain reliability separately

Two of the last four Sundays produced no bundle:

- 2026-07-26 — no bundle, cause unknown (retrain_healthcheck presumably
  didn't alert)
- 2026-08-16 — no auto bundle, scheduler was hung by Aug 12→15 outage;
  manually fired today post-recovery

The `retrain_healthcheck` cron alerting logic is worth an audit — should
have caught the Jul 26 skip within 24h.

## Recommendation

- **Production stays**: MAIN v20260712, OU-override v20260719, meta
  v_20260706_bets_xgb. Unchanged for another week.
- **Ship #1 (post-hoc draw calibration)** as the next model iteration.
  Small change, high expected impact, fully reversible via env flag.
- **Audit `retrain_healthcheck`** to close the "silent Sunday" gap.

## Reproducible commands

```
python3 -m workers.model.train --version v20260816 \
  --include-pinnacle --include-ou-market

python3 scripts/rigorous_eval.py --candidate v20260816 --candidate-cutoff 2026-08-15 \
  --production v20260712 --production-cutoff 2026-07-11 --eval-end 2026-08-16 \
  --bootstrap-n 500

python3 scripts/rigorous_eval.py --candidate v20260816 --candidate-cutoff 2026-08-15 \
  --production v20260719 --production-cutoff 2026-07-19 --eval-end 2026-08-16 \
  --bootstrap-n 500

python3 scripts/rigorous_eval.py --candidate v20260816 --candidate-cutoff 2026-08-15 \
  --production v20260809 --production-cutoff 2026-08-08 --eval-end 2026-08-16 \
  --bootstrap-n 500
```
