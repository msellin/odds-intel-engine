# Rigorous eval — v20260802 vs current production

Generated 2026-08-03 (Monday, one day after v20260802 was retrained on
Sunday 2026-08-02 03:15 UTC).

## Constraint on this eval

v20260802's training cutoff = **2026-08-01** — i.e. it saw everything up to
and including Saturday. The rigorous_eval framework enforces strictly-OOS
evaluation, so the eval window is **max(cand_cutoff, prod_cutoff) + 1 day
→ today**, which right now is only **2026-08-02 → 2026-08-03** — one to two
days of settled matches (n=486).

n=486 is enough to detect large log-loss swings (5-10%) at p<0.05, but not
enough to judge subtler differences. **A follow-up eval in ~2 weeks (once
n≥3000 OOS accumulates) is required before any promotion decision.**

## Verdict — v20260802 is WORSE than current production on 1x2

### vs v20260719 (current OU override) — n=486 paired

| market | Δ log-loss | Δ % | p-value | verdict |
|---|---|---|---|---|
| **1x2_home** | +0.0298 | **+5.4%** | 0.000 | ✗ WORSE |
| **1x2_draw** | +0.0307 | **+5.7%** | 0.000 | ✗ WORSE |
| 1x2_away | +0.0072 | +1.5% | 0.134 | → worse (not sig) |
| over25 / under25 | −0.0095 | −1.3% | 0.850 | → better (not sig) |
| btts_yes / _no | +0.0063 | +0.9% | 0.154 | → worse (not sig) |
| ah_home_-0.5 | −0.0095 | −1.7% | 0.974 | ✓ BETTER |
| ah_home_+1.5 | −0.0082 | −2.4% | 0.958 | ✓ BETTER |

### vs v20260712 (current MAIN) — n=486 paired

| market | Δ log-loss | Δ % | p-value | verdict |
|---|---|---|---|---|
| **1x2_home** | +0.0303 | **+5.5%** | 0.000 | ✗ WORSE |
| **1x2_draw** | +0.0574 | **+11.2%** | 0.000 | ✗ WORSE |
| 1x2_away | +0.0139 | +2.8% | 0.056 | → worse (borderline) |
| over25 / under25 | −0.0092 | −1.3% | 0.804 | → better (not sig) |
| **btts_yes / _no** | +0.0112 | **+1.6%** | 0.016 | ✗ WORSE |
| ah_home_+1.5 | −0.0095 | −2.7% | 0.982 | ✓ BETTER |

### v20260802 vs Pinnacle-close baseline

| market | Δ log-loss | Δ % | p-value | verdict |
|---|---|---|---|---|
| 1x2_home | −0.0076 | −1.3% | 0.662 | → better (not sig) |
| **1x2_draw** | +0.0734 | **+14.7%** | 0.000 | ✗ WORSE than Pinnacle |
| 1x2_away | −0.0581 | −10.9% | 1.000 | ✓ BETTER than Pinnacle |
| over25 / under25 | +0.0635 | +9.5% | 0.008 | ✗ WORSE than Pinnacle |

**v20260712 also loses to Pinnacle on 1x2_draw and OU** — same pattern —
so this isn't a v20260802-specific regression, it's a general
"model doesn't beat sharp Pinnacle line on draws + OU" issue that's
been true for weeks.

### Per-tier breakdown (v20260802 vs v20260712)

| tier | market | n | Δ % | verdict |
|---|---|---|---|---|
| 1 | 1x2_home | 396 | +5.2% | ✗ WORSE |
| 1 | 1x2_draw | 396 | +12.2% | ✗ WORSE |
| 1 | 1x2_away | 396 | +4.1% | ✗ WORSE |
| 1 | over25/under25 | 396 | −2.3% | → better (not sig) |

## Recommendation

- **Do NOT promote v20260802 to main or OU-override at this time.**
- Keep production at:
  - `MODEL_VERSION=v20260712` (main)
  - `MODEL_VERSION_OU=v20260719` (OU override)
- **Re-run rigorous_eval on 2026-08-17** (2 weeks of OOS data
  accumulated). If v20260802 still shows sig-worse on 1x2, drop it
  from the retrain rotation and investigate the Aug 1 retrain
  pipeline (did something change in the training data or feature
  set that hurt 1x2?).
- Meanwhile, the fact that ALL our models fail to beat Pinnacle on
  1x2_draw and OU is worth separate investigation — the draw
  probability is genuinely hard for anyone, but OU 2.5 not beating
  Pinnacle after ~1 year of iterations suggests a feature-set gap
  rather than a training-window issue.

## Raw eval output

Reproducible via:
```
python3 scripts/rigorous_eval.py --candidate v20260802 --candidate-cutoff 2026-08-01 \
  --production v20260712 --production-cutoff 2026-07-11 \
  --eval-end 2026-08-03 --bootstrap-n 500
```
