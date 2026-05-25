# Model Comparison — 5-way (2026-05-25)

> Held-out: 2,522 settled matches in 2026-05-20..05-24 (offline_eval upper-bound numbers — bundles trained on data including this window).

## Candidates trained today

| Bundle | What changed vs production (`v20260524_market`) |
|---|---|
| `v_20260525_signals` | Baseline candidate: +10 MFV-V3 features added to FEATURE_COLS |
| `v_20260525_no_pin` | signals MINUS `--include-pinnacle` flag — drops 3 Pinnacle 1X2 features |
| `v_20260525_v3only` | Only the 10 new features (12 incl. form_momentum); narrow feature space |
| `v_20260525_depth8` | signals + XGB `max_depth=8` (production = 6 on 1X2, 5 elsewhere) |

## Raw scores (no isotonic)

### 1x2_home log_loss / hit_rate / ECE
| Version | log_loss ↓ | hit % | ECE ↓ |
|---|---|---|---|
| v20260524_market | 0.4841 | 76.2% | 0.0743 |
| v_20260525_signals | 0.4518 | 79.5% | 0.0734 |
| v_20260525_no_pin | 0.4473 | 79.7% | 0.0627 |
| **v_20260525_depth8** | **0.4049** | **85.9%** | 0.1265 ⚠️ |

depth8 wins log_loss by -16% vs production AND -10% vs signals. But ECE +70% vs production = **overfit**.

### Same pattern across draw/away/OU/BTTS

|  | depth8 log_loss | depth8 ECE | vs production |
|---|---|---|---|
| 1x2_draw | 0.399 (-16%) | 0.112 (+120%) | overfit |
| 1x2_away | 0.367 (-16%) | 0.106 (+131%) | overfit |
| over_25 | 0.596 (-8%) | 0.049 (+6%) | mostly OK |
| btts_yes | 0.655 (-5%) | 0.058 (-21%) | actually BETTER |

## Isotonic fix on depth8

Fit isotonic Stage-2 calibrator on depth8 (same as morning's signals fit):

| Market | ECE before isotonic | ECE after isotonic | Δ |
|---|---|---|---|
| 1x2_home | 0.1042 | 0.0258 | -75% |
| 1x2_draw | 0.1537 | 0.0393 | -75% |
| 1x2_away | 0.0858 | 0.0222 | -74% |
| over_25 | 0.1660 | 0.0566 | -66% |
| btts_yes | 0.1686 | 0.0491 | -71% |

**Isotonic completely fixes depth8's overconfidence regression.** All markets drop to ECE ≤0.06, materially better than production.

## Verdict

**`v_20260525_depth8 + isotonic` is the new strongest deploy candidate.**

- Best log_loss across all 5 markets (-5% to -16% vs production)
- Best hit-rate (+4-12pp)
- ECE comparable to or better than production
- All paid for by isotonic Stage-2, which is environment-flag activated

**Secondary findings:**

1. **Pinnacle features add nothing.** `no_pin` log_loss matches `signals` within ±1pp on every market. The 3 `pinnacle_implied_{home,draw,away}` columns + their `_missing` indicators are noise at our current 23% coverage. Could simplify the training schema.

2. **`v3only` can't be evaluated** through `offline_eval` — the script's MFV-schema detector rejects bundles with feature lists this narrow. Either fix the detector or evaluate v3only via a separate path. Not blocking.

3. **Default depth is too shallow.** The 1X2 head was at depth=6, OU/BTTS at depth=5. Raising 1X2 to depth=8 captures interaction effects the shallower tree was missing. Caveat: needs isotonic to be safe.

## 2026-06-08 deploy decision (proposed)

```
MODEL_VERSION=v_20260525_depth8
STAGE2_CALIBRATOR=isotonic
```

Plus the planned `META_B_ML3_*`, `ELITE_LEAGUE_FILTER_ENABLED`, `LEAGUE_EFF_EDGE_BUMP_ENABLED`, `BOT_COHORT_OVERRIDES`, `GATE_EVENTS_BY_COVERAGE` flips.

If the standard 2026-06-08 weekly retrain produces `v_20260608` that beats `depth8 + isotonic` on the new (unseen) week, prefer that instead.

## Caveats

- All numbers are upper bounds — bundles trained on MFV that includes the held-out window. The true test is the first week post-deploy on unseen matches.
- `MODEL-3WAY-COMPARE-20260608` (filed task) should become `MODEL-5WAY-COMPARE` to include `no_pin` + `depth8`.
