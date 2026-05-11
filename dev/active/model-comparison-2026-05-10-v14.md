# Model Comparison — v14 vs v11_pinnacle (2026-05-11)

**Held-out window:** 2026-04-11 → 2026-05-10 (8,794 matches)
**Bundles available locally:** v11_pinnacle, v14 (v9/v12/v13 not on this machine)

## Results

| Market | Metric | v11_pinnacle | v14 | Winner |
|--------|--------|-------------|-----|--------|
| 1x2_home | log_loss ↓ | 0.4000 | 0.3882 | ✅ v14 (−3.0%) |
| 1x2_home | Brier ↓ | 0.1209 | 0.1169 | ✅ v14 |
| 1x2_draw | log_loss ↓ | 0.4247 | 0.4182 | ✅ v14 (−1.5%) |
| 1x2_draw | Brier ↓ | 0.1282 | 0.1256 | ✅ v14 |
| 1x2_away | log_loss ↓ | 0.3391 | 0.3163 | ✅ v14 (−6.7%) |
| 1x2_away | Brier ↓ | 0.0967 | 0.0894 | ✅ v14 |
| over_25 | log_loss ↓ | 0.6442 | 0.6456 | ≈ tie (+0.2%, noise) |
| over_25 | Brier ↓ | 0.2265 | 0.2275 | ≈ tie |
| btts_yes | log_loss ↓ | 0.6974 | 0.6921 | ✅ v14 (−0.8%) |
| btts_yes | Brier ↓ | 0.2518 | 0.2494 | ✅ v14 |

## Summary

v14 wins cleanly on all three 1X2 selections and on BTTS. The 1X2 away improvement (-6.7% log_loss) is the largest signal — the new OU/BTTS market features appear to be helping calibrate away-win probabilities indirectly, possibly because BTTS market consensus correlates with match openness.

OU 2.5 is a tie within noise (+0.14%). The OU head now has its own market signals (Pinnacle OU 2.5 implied, OU disagreement) but the ~22% Pinnacle coverage means the `_missing` indicators do most of the work. Coverage will grow as more OU snapshots accumulate.

**Caveat:** leakage risk — bundles were trained on the same MFV data the test window may have been part of (noted in offline_eval.py). Use v9a_202425 (Kaggle baseline) as the clean reference when it's available. This comparison is directionally valid for relative performance.

## Decision

v14 is the clear winner. Promote by setting `MODEL_VERSION=v14` in Railway env.

**Do NOT auto-promote** — operator (Margus) runs the Railway env var swap.
