# Rigorous eval — v20260809 + retrain-drift review

Generated 2026-08-16 after the PG outage recovery. Compares the two new
weekly-retrain bundles since 2026-07-19 (v20260802 and v20260809) against
both current-production versions.

## Bundle inventory (last ~5 weeks)

| Version | Trained | Train cutoff | Rows | Status |
|---|---|---|---|---|
| v20260712 | 2026-07-12 | 2026-07-11 | 63,910 | **PROD MAIN** |
| v20260719 | 2026-07-19 | 2026-07-19 | 65,499 | **PROD OU-override** |
| v20260726 | — | — | — | Sunday retrain **skipped** (didn't run) |
| v20260802 | 2026-08-02 | 2026-08-01 | 67,850 | Reviewed 08-03: WORSE — not promoted |
| v20260809 | 2026-08-09 | 2026-08-08 | 70,366 | **This eval** |
| v20260816 | — | — | — | Today's Sunday retrain **skipped** (scheduler was down during 03:00 UTC fire) |

## Head-to-head — v20260809 vs current PROD MAIN v20260712

Eval window: 2026-08-09 → 2026-08-16 (strictly-OOS both), n=1,470 paired.

| market | Δ log-loss | Δ % | p | verdict |
|---|---|---|---|---|
| 1x2_home | +0.0061 | +1.0% | 0.060 | → worse (not sig) |
| **1x2_draw** | **+0.0246** | **+4.4%** | 0.000 | ✗ WORSE |
| 1x2_away | +0.0014 | +0.2% | 0.388 | tie |
| over25 / under25 | +0.0055 | +0.7% | 0.154 | → worse (not sig) |
| btts_yes / _no | −0.0030 | −0.4% | 0.804 | → better (not sig) |
| ah_home_-0.5 | +0.0010 | +0.2% | 0.400 | tie |
| ah_home_+0.5 | +0.0039 | +0.6% | 0.130 | → worse (not sig) |
| ah_home_-1.5 | +0.0018 | +0.3% | 0.330 | tie |
| ah_home_+1.5 | −0.0027 | −0.6% | 0.818 | → better (not sig) |

**Verdict: do NOT promote v20260809 to main.** Same 1x2_draw regression
pattern as v20260802.

## v20260809 vs current PROD OU-override v20260719

n=1,470 paired.

| market | Δ log-loss | Δ % | p | verdict |
|---|---|---|---|---|
| 1x2_home | −0.0016 | −0.3% | 0.638 | tie |
| **1x2_draw** | +0.0094 | +1.7% | 0.016 | ✗ WORSE |
| 1x2_away | −0.0064 | −1.1% | 0.848 | → better (not sig) |
| **over25 / under25** | +0.0121 | +1.7% | 0.014 | ✗ WORSE |
| btts_yes / _no | −0.0016 | −0.2% | 0.724 | tie |
| ah_* | ~0 | ~0 | ~0.5 | tie |

**Verdict: do NOT promote v20260809 to OU-override either.** v20260719
still wins on OU 2.5.

## v20260809 vs v20260802 (week-over-week improvement check)

Comparing consecutive weekly retrains, n=1,470 paired.

| market | Δ log-loss | Δ % | p | verdict |
|---|---|---|---|---|
| 1x2_home | −0.0197 | −3.0% | 1.000 | ✓ BETTER |
| 1x2_draw | −0.0247 | −4.1% | 1.000 | ✓ BETTER |
| 1x2_away | −0.0082 | −1.4% | 0.968 | ✓ BETTER |
| over25 / under25 | +0.0030 | +0.4% | 0.320 | tie |
| btts_yes / _no | −0.0007 | −0.1% | 0.588 | tie |
| ah_* | −0.0065 to −0.0099 | −1.0% to −2.1% | <0.05 | ✓ BETTER on all 4 |

**Read:** v20260809 recovered from v20260802's regression — but it recovered
back to *worse than the July bundles*, not better than them. The Aug 2
retrain just introduced a bigger break that Aug 9 partly healed.

## Retrain-drift trend

Three consecutive weekly retrains have failed to beat the July production
versions:

- 2026-07-26 — **retrain skipped** (didn't produce a bundle at all)
- 2026-08-02 — WORSE on 1x2 (per eval 2026-08-03)
- 2026-08-09 — WORSE on 1x2_draw (this eval)
- 2026-08-16 — **retrain skipped** (scheduler was down during 03:00 UTC fire — Aug 12→15 hang + PG OOM)

**This is a concerning trend.** Reasons to investigate:
1. **Data drift**: European season restart (Aug 15-23) is bringing new-season rosters, promoted/relegated teams, and updated tactics that the July training data hasn't seen. Reasonable — but if the model is *worse* on OOS despite MORE training rows, the added data may be adding noise or distribution shift, not signal.
2. **Sunday retrain reliability**: 2 of the last 4 Sunday retrains DIDN'T PRODUCE A BUNDLE. Whatever is failing silently on those Sundays should be surfaced (a `retrain_healthcheck` cron exists — is it alerting?).
3. **1x2_draw specifically**: every regression is on draws. This is the market our model has always been weak on (loses to Pinnacle by ~7% per prior evals). The July models are the *least bad* at draws so far. Whatever new features or data are being added post-July are making draws worse, not better.

## Recommendation

- **Keep production at**: MAIN v20260712, OU-override v20260719, meta v_20260706_bets_xgb. Unchanged since 2026-07-31.
- **Do not run any promotion decisions** on v20260802 or v20260809.
- **Investigate why** the Sunday retrain didn't fire on 2026-07-26 (skipped Sunday between the last two July bundles) — same pattern as 2026-08-16 which we know was scheduler-outage.
- **Re-eval v20260816 as soon as it exists** — today's retrain didn't run. Force-run it manually now that scheduler is back up, then eval next Saturday.
- **Diagnose 1x2_draw regression trend** — separately, since it's now 3 consecutive bundles worse on draws. Prime suspects: (a) new mid-August European fixtures added to training with pre-season / friendly noise; (b) some feature engineered post-July that hurts draws.

## Reproducible commands

```
python3 scripts/rigorous_eval.py --candidate v20260809 --candidate-cutoff 2026-08-08 \
  --production v20260712 --production-cutoff 2026-07-11 --eval-end 2026-08-16 \
  --bootstrap-n 500

python3 scripts/rigorous_eval.py --candidate v20260809 --candidate-cutoff 2026-08-08 \
  --production v20260719 --production-cutoff 2026-07-19 --eval-end 2026-08-16 \
  --bootstrap-n 500

python3 scripts/rigorous_eval.py --candidate v20260809 --candidate-cutoff 2026-08-08 \
  --production v20260802 --production-cutoff 2026-08-01 --eval-end 2026-08-16 \
  --bootstrap-n 500
```
