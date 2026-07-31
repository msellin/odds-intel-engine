# Rigorous OOS re-eval of MODEL-VERSION-FLIP-2026-07-18 — 20-day window

Follow-up to `rigorous-eval-2026-07-18.md`. Same candidate/production pair, but
now with 20 days of clean OOS data instead of 7. This is the re-eval the
whitepaper flagged as due ~2026-07-20 to confirm the flip.

## Setup

- **Candidate**: `v20260712` (trained through 2026-07-11, promoted 2026-07-18)
- **Production**: `v20260705` (trained through 2026-07-05, prior active)
- **Eval window**: 2026-07-12 → 2026-07-31 — strictly OOS for both
- **n = 3,171 settled matches** (vs 1,116 in the 7-day eval)
- **Bootstrap**: 1,000 resamples (vs 500 originally)
- Command:

```bash
python3 scripts/rigorous_eval.py \
    --candidate v20260712 --candidate-cutoff 2026-07-11 \
    --production v20260705 --production-cutoff 2026-07-05 \
    --eval-end 2026-07-31 --bootstrap-n 1000
```

Full log: `dev/active/rigorous-eval-2026-07-31.log`

## Direct comparison — 7-day → 20-day

| Market | 7-day Δ% (p) | 20-day Δ% (p) | Change |
|---|---|---|---|
| 1x2_home | -3.1% (0.998) | **-4.3% (1.000)** | ✅ larger effect, higher confidence |
| 1x2_draw | -4.0% (1.000) | **-4.4% (1.000)** | ✅ stable |
| 1x2_away | -0.3% (0.664) | +0.0% (0.480) | tie (unchanged) |
| over25 | +1.1% (0.076) | **-0.0% (0.523)** | 🔄 borderline WORSE → TIE |
| under25 | +1.1% (0.076) | **-0.0% (0.523)** | 🔄 borderline WORSE → TIE |
| btts_yes | +1.4% (0.004) | **+0.7% (0.010)** | ❌ still worse, effect halved |
| btts_no | +1.4% (0.004) | **+0.7% (0.010)** | ❌ still worse, effect halved |
| ah_home_-0.5 | -1.3% (0.936) | -0.2% (0.673) | near-sig → tie |
| ah_home_+0.5 | -1.8% (0.972) | -0.9% (0.954) | ✅ still better |
| ah_home_-1.5 | -2.1% (0.992) | -2.2% (1.000) | ✅ stable |
| ah_home_+1.5 | -1.7% (0.978) | -1.2% (0.982) | ✅ still better |

**Score: 5 BETTER / 2 WORSE / 4 TIE** — identical high-level conclusion.

## Key changes from the 7-day read

**1. OU regression was noise.** The 7-day eval showed OU at Δ = +1.1% p=0.076
(borderline worse), which is why we kept v20260621 as an OU override. At 3x
data, OU is Δ = 0.0% p=0.523 — a clean TIE. **The main models are OU-equivalent
under identical training**. The whitepaper's stated reason for keeping the OU
override ("borderline OU regression") no longer holds. See "OU override
recommendation" below.

**2. BTTS regression halved.** From +1.4% (p=0.004) to +0.7% (p=0.010). Still
statistically significant worse but the practical cost dropped ~50%. BTTS is a
rarely-bet market for us (`bot_btts_all` had 0 real bets pre-loosening on
2026-07-19) so the impact is minor either way.

**3. 1X2 improvement got stronger.** Home log-loss delta widened from -3.1% to
-4.3%, draw held at -4.4% with p=1.000. These are our highest-volume markets.

## Pinnacle-close baseline — sharp-alignment check

Does either model add anything over Pinnacle? Same test as before: paired
comparison against Pinnacle's closing implied probability.

**Both models are still statistically worse than Pinnacle on every market**
(expected — Pinnacle is the sharp line, we can't outpredict it directly). The
useful question is **which of our models is closer to Pinnacle**.

| Market | v20260712 gap vs Pinnacle | v20260705 gap vs Pinnacle | Winner |
|---|---|---|---|
| 1x2_home | +2.7% (p=0.044) | +5.9% (p=0.001) | v20260712 (Δ 3.2pp closer) ✅ |
| 1x2_draw | +2.3% (p=0.011) | +5.2% (p=0.000) | v20260712 (Δ 2.9pp closer) ✅ |
| 1x2_away | +0.3% (p=0.44) | +0.4% (p=0.41) | tie |
| over25 | +14.4% (p=0.000) | +13.0% (p=0.000) | v20260705 (1.4pp closer) ❌ |
| under25 | +14.4% (p=0.000) | +13.0% (p=0.000) | v20260705 (1.4pp closer) ❌ |

**v20260712 is meaningfully closer to Pinnacle on 1X2.** The gap widened vs
the 7-day read — the sharp-alignment story is stronger with more data. On OU
both models are far from Pinnacle (~13-14% worse) — this is why we keep
`v20260621` as an OU override even though the main-model regression didn't
replicate.

## Per-tier breakdown (n ≥ 30 per cell)

| Tier | n | Highlights |
|---|---|---|
| 1 | 2,758 | Consistent with overall: better on 1X2 home/draw + AH -1.5/+1.5, worse on BTTS. |
| 2 | 207 | v20260712 significantly better on 1X2 home/draw/away. BTTS worse (p=0.077). |
| 3 | 90 | Small sample. v20260712 worse on 1x2_away (+5.7%, p=0.040). Watch. |
| 4 | 116 | Better on 1X2 home/draw. BTTS +3.4% worse (p=0.090). |

**Only concern**: Tier 3 1x2_away regression at p=0.040 with n=90. Small sample
but crossed threshold. Worth a follow-up read at 6 weeks (2026-08-24) to see
if it holds — if yes, tier-3 upstream signal or bet-sizing tweak needed.

## Verdict

**Flip was correct — re-confirmed with 3x more data.**

1. **1X2 gains stronger** (-4.3% home, -4.4% draw, both p=1.000).
2. **Pinnacle-close alignment improved** by ~3pp on 1X2 — the new model tracks
   the sharp line more tightly, which is exactly what we want for CLV.
3. **OU regression was noise** — TIE at 20 days.
4. **BTTS regression persists but effect halved** — cost minor.
5. **AH gains stable** — 3 of 4 AH lines better with p ≥ 0.95.

## OU override recommendation

The whitepaper §3.1b says we keep `MODEL_VERSION_OU=v20260621` because
v20260712's OU had a borderline regression. That regression didn't replicate at
20 days. Two possible reads:

- **Keep the override.** v20260621 is dedicated OU-trained; on Pinnacle
  alignment both v20260712 and v20260705 are ~14% off, so if v20260621 beats
  either on OU-specific eval, it stays the right choice. This eval doesn't
  score v20260621 (not the candidate) so we can't rule out `v20260621` is
  still the OU champion.
- **Drop the override.** If we're bandwidth-constrained on model management,
  losing the override simplifies the stack. The "OU regression" narrative that
  justified it no longer holds.

**Recommendation**: keep the override until we run a `v20260621 vs v20260712`
head-to-head OU eval. That's a separate task, not this one. Filed as
`OU-OVERRIDE-VALIDATION` in the priority queue follow-up.

## Rollback status

None triggered. All rollback conditions from the 07-18 write-up remain unmet:

- ❌ No market with candidate significantly worse than production at p > 0.99
  (BTTS at p=0.010, effect halved)
- ❌ No 1X2 regression
- ✅ Pinnacle-close 1X2 gap narrowed further

Next re-read: **2026-08-31** (30 days more data) if the model version hasn't
flipped again by then.
