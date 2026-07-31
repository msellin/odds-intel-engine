# Rigorous OOS eval: v20260719 vs v20260621 — OU-override head-to-head

Companion to `rigorous-eval-v20260719-vs-v20260712.md`. That eval flagged that
v20260719 closes 2.5pp of the OU gap vs Pinnacle-close (v20260712 sits at
+12.2%, v20260719 at +9.7%). This eval answers: does v20260719 beat the
dedicated OU override `v20260621` too? If yes, we can retire the override.

## Setup

- **Candidate**: `v20260719` (main-style bundle, trained 2026-07-19)
- **Production**: `v20260621` (dedicated OU-trained bundle, live as
  `MODEL_VERSION_OU` since 2026-06-21)
- **Eval window**: 2026-07-20 → 2026-07-31 — strictly OOS for both
- **n = 1,328 settled matches**
- **Bootstrap**: 1,000 resamples

```bash
python3 scripts/rigorous_eval.py \
    --candidate v20260719 --candidate-cutoff 2026-07-19 \
    --production v20260621 --production-cutoff 2026-06-21 \
    --eval-end 2026-07-31 --bootstrap-n 1000
```

Full log: `dev/active/rigorous-eval-v20260719-vs-v20260621-ou.log`

## Direct comparison

| Market | Δ log-loss | Δ % | p | Verdict |
|---|---|---|---|---|
| 1x2_home | -0.0163 | **-2.6%** | 0.999 | ✅ **BETTER** |
| 1x2_draw | -0.0235 | **-4.2%** | 1.000 | ✅ **BETTER** |
| 1x2_away | -0.0054 | -1.0% | 0.895 | tie (better, not sig) |
| **over25** | **-0.0166** | **-2.3%** | **0.997** | ✅ **BETTER** |
| **under25** | **-0.0166** | **-2.3%** | **0.997** | ✅ **BETTER** |
| btts_yes | +0.0004 | +0.1% | 0.413 | tie |
| btts_no | +0.0004 | +0.1% | 0.413 | tie |
| ah_home_-0.5 | -0.0034 | -0.6% | 0.832 | tie (better, not sig) |
| ah_home_+0.5 | -0.0093 | -1.7% | 0.995 | ✅ **BETTER** |
| ah_home_-1.5 | -0.0106 | -2.0% | 0.997 | ✅ **BETTER** |
| ah_home_+1.5 | -0.0055 | -1.4% | 0.939 | tie (better, not sig) |

**Score: 6 BETTER / 0 WORSE / 5 TIE.** v20260719 dominates across the board.

## OU specifically — the reason we ran this

| Metric | v20260719 | v20260621 | Delta |
|---|---|---|---|
| OU log-loss | 0.7173 | 0.7339 | **v20260719 -2.3% (p=0.997)** ✅ |
| OU gap vs Pinnacle-close | +9.5% (p=0.000) | +9.3% (p=0.000) | v20260621 marginally closer (0.2pp) |

Two subtly different comparisons:

- **Direct paired log-loss**: v20260719 is lower (better calibrated to actual
  outcomes) than v20260621 by 2.3% on the exact same matches. p=0.997.
- **Gap vs Pinnacle**: v20260621 is 0.2pp closer to Pinnacle's numbers. But
  Pinnacle isn't ground truth (it's an excellent proxy) — actual match
  outcomes are ground truth, and v20260719 lines up with those better.

The direct paired result is the load-bearing metric. **v20260719 is the
sharper OU model.**

## 1X2 side effect

v20260719 also beats v20260621 on 1X2 by a wide margin (-2.6% home, -4.2%
draw, both p ≥ 0.999). This isn't surprising — v20260621 is a 6-week-old
bundle trained on ~4,800 fewer rows. But it means the OU override was quietly
degrading our 1X2 predictions in the rare cases where it was consulted (via
whatever fallback chain the inference layer runs). Retiring the override is a
strict improvement on both markets.

## Per-tier breakdown (n ≥ 30)

| Tier | n | Notable |
|---|---|---|
| 1 | 1,243 | Consistent with overall: 1X2 home/draw significantly better, OU better, AH -0.5/-1.5/+1.5 all better, BTTS tie. **6 BETTER / 0 WORSE / 5 TIE**. |
| 2 | 62 | Small sample. Only 1x2_home significant (better, -7.1% p=0.971). No significant regressions. |

## Recommendation

**Retire the `v20260621` OU override. Switch to `v20260719` as the OU model.**

Concrete action:
- Change `MODEL_VERSION_OU` env var on the VPS scheduler from `v20260621` →
  `v20260719`
- Update `MODEL_WHITEPAPER.md` §3.1b to reflect the new OU override version +
  the evidence
- Update `PRIORITY_QUEUE.md` — close `OU-OVERRIDE-VALIDATION`, note the flip

**Do NOT touch main.** v20260712 remains main. This eval only touches the OU
override slot.

## Why not promote v20260719 to main instead?

Because the earlier eval (`rigorous-eval-v20260719-vs-v20260712.md`) showed
v20260719 regressing on 1X2_draw by 1.8% at p=0.012 vs v20260712. Draw is our
highest-volume 1X2 outcome and 1X2 is our highest-volume market bucket, so
that regression outweighs the OU gain when v20260719 is used as main. But
when v20260719 is used *only* for OU (via the override), the 1X2 regression
doesn't apply — we get pure OU upside.

## Rollback conditions

Watch these on the next 2 weeks of OU data:
- If OU log-loss vs Pinnacle drifts up beyond +11%, revert to v20260621.
- If OU CLV in `bot_ou_all` or `bot_over_selective` degrades ≥ 5% month-over-
  month, revert.
- If a `v20260726` bundle was trained, evaluate it against v20260719 on OU
  before locking in v20260719 for the medium term.

## Combined verdict from both post-vacation evals

- **Main**: `v20260712` (unchanged, confirmed correct in 20-day re-eval).
- **OU override**: `v20260719` (new — replaces `v20260621` on evidence above).
- **Meta**: `v_20260706_bets_xgb` (unchanged, blocked by META-EVAL-PIPELINE-
  BROKEN).
- **Shadow**: `v20260705` (unchanged).
