# GLOBAL-PLATT-OVERCONFIDENCE deep-dive — findings 2026-05-25

> Follow-up to LONGSHOT-GEO-AUDIT. Answer: it's the ODDS bucket, not country/tier/time-of-season.

## Cohort

485 settled 1X2 bets in the 30-50% calibrated_prob bin, last 90 days. Global gap: predicted 39.5%, actual 29.3%, **-10.2pp**.

## H1 — ODDS bucket (WINS)

| Odds bin | n | Predicted | Actual | Gap |
|---|---|---|---|---|
| 2.50-3.00 | 120 | 44.1% | 34.2% | **-10pp** |
| 3.00-3.50 | 119 | 41.4% | 43.7% | **+2.3pp** ✓ |
| 3.50-4.00 | 85 | 39.3% | 27.1% | -12pp |
| **4.00+** | **155** | **34.2%** | **14.2%** | **-20pp** |

**Spread: 22.3pp.** The 30-50% predicted-prob overconfidence is **concentrated in longshot odds**. Crucially:
- The 3.00-3.50 bin is well-calibrated — that's where the current `CAL-ALPHA-ODDS` step (-0.20 shrinkage at odds > 3.0) lands. ✓
- Below the trigger (2.50-3.00) it's worse than the trigger
- Far above the trigger (4.00+) the single -0.20 step isn't enough

## H2 — League tier (LOSES)

Only T0 (cup matches) and T1 (top leagues) had ≥20 bets. T0 gap -13.3pp vs T1 -10.7pp. **Spread: 2.6pp.** Not a driver.

## H3 — Season progress (CAN'T TEST)

Only 15 of 485 bets had `season_progress` populated — that signal only landed today. Re-test in 30 days.

## Action shipped

`CAL-ALPHA-ODDS-V2` — graduated longshot shrinkage, env-gated OFF:

| Odds | Current (-0.20 step) | V2 (graduated) |
|---|---|---|
| < 2.5 | no boost | no boost |
| 2.5-3.0 | no boost | **alpha -0.10** (new modest pull) |
| 3.0-3.5 | alpha -0.20 | alpha -0.20 (same) |
| 3.5-4.0 | alpha -0.20 | **alpha -0.25** |
| 4.0+ | alpha -0.20 | **alpha -0.35** (catches the -20pp longshot bucket) |

Activate post-Phase-3.5 via `CAL_ALPHA_ODDS_V2_ENABLED=true`. Stacks with `STAGE2_CALIBRATOR=isotonic`. Default OFF preserves current behaviour.

## Expected interaction with isotonic

Isotonic alone closes ~70% of the calibration gap. CAL-ALPHA-ODDS-V2 attacks the source upstream (Stage 1 shrinkage) — should compound additively, getting the 30-50% bin within 2-4pp of perfect calibration.

## 2026-06-08 deploy update

Add to env-flip checklist:
```
CAL_ALPHA_ODDS_V2_ENABLED=true
```

Plus the existing recommendation:
```
MODEL_VERSION=v_20260525_depth8
STAGE2_CALIBRATOR=isotonic
```

depth8 + isotonic + CAL-ALPHA-ODDS-V2 = the strongest possible deploy stack today.

## Validation follow-up

After 2 weeks of post-deploy data (2026-06-22), re-run platt_overconfidence_deepdive.py. Target: spread on H1 (odds bucket) drops below 6pp. If it doesn't, escalate to per-(odds × tier) isotonic models.
