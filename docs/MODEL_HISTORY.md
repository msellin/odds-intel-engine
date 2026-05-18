# Model History

> **For "what model is currently in production"**: don't read this doc — call
> `workers.api_clients.supabase_client._active_model_version()` (reads the
> `MODEL_VERSION` env var, defaults to v14). Scripts should ALWAYS use that
> function; never hardcode a model version name.
>
> This doc captures the *history* of what each model version had so we can
> reason about backtest comparisons, calibration choices, and bot config
> decisions years from now.

---

## Current production: `v14`

Set via Railway env `MODEL_VERSION=v14`. Active since approximately 2026-05-13.

| Property | Value |
|---|---|
| Type | Poisson + XGBoost ensemble |
| Markets | 1X2, OU 1.5/2.5/3.5, BTTS, Asian Handicap (via Poisson scoring grid), Double Chance (derived from 1X2), Draw No Bet (derived from 1X2) |
| Calibration | Per-tier Platt scaling (1X2: 1-feature; OU 2.5: 2-feature with log-odds) |
| Dixon-Coles | Per-tier ρ correlation parameter |
| Filter stack | Pinnacle veto, sharp_consensus gate, accessible-bookmaker filter |
| Storage | Supabase Storage bundle via ML-BUNDLE-STORAGE |

---

## Version history

### v14 (current — switched 2026-05-13)
- Latest XGBoost retrain with expanded features
- Refined per-tier Dixon-Coles ρ from `scripts/fit_league_rho.py`
- Platt calibration: 1X2 1-feature standard, OU 2.5 2-feature `[shrunk_prob, log(odds)]` deployed 2026-05-12
- ~1,820 settled predictions per market available for Platt fit as of 2026-05-18 (post-Platt ECE = 0.0003 on 1x2_draw)

### v12_post0e (previous production)
- Switched 2026-05-10 from v9a_202425
- Pinnacle-free training (removed Pinnacle from input features to break circularity with the Pinnacle veto signal)
- Post-Stage-0e quality cleanup
- Beat v9 by ~50% on every 1X2 log_loss in offline_eval

### v9 / v9a_202425
- Original XGBoost trained on 2024-25 season data
- Used until v12_post0e took over
- Still referenced by `targets_v9.csv` for training data

### Earlier (v8 etc.)
- Pre-v9 baselines, no longer in use
- Historical artifacts in `data/processed/targets_v8.csv`

---

## Reference assets (not model versions, but related)

### `model_version = 'poisson_backfill'`

Created 2026-05-18 by `scripts/predict_historical_matches.py` for
**BACKTEST-HISTORICAL-PREDICT-FORWARD**. Pure Poisson + Dixon-Coles
predictions on 12,503 historical matches (137K rows). NOT a production
model — purely for backtest data expansion.

Backfilled predictions have `created_at = match.date - 1 day` so the
backtester's `created_at < m.date` lookahead guard accepts them.

**Don't mix with v14 (or other production-model) predictions for Platt
fitting** — the raw probability distributions differ between
Poisson-only and Poisson+XGBoost blend, so a single Platt fit would
average two distinct calibration curves and degrade both.

### `targets_poisson_history.csv` / `targets_global.csv`

CSV-based historical match data used by the Poisson model during the
live pipeline (Tier A / Tier B fallback). Not a model version — they're
training data fed into `compute_prediction()`.

---

## Process notes

### How to switch model versions in production

1. Train a new bundle (`scripts/train_xgboost.py` or similar)
2. Upload via `ML-BUNDLE-STORAGE` flow → registers in `model_versions` table
3. Validate via `scripts/offline_eval.py vA vB` — confirms new model beats current on log_loss + ROI metrics
4. Update Railway env: `MODEL_VERSION=v15`
5. Redeploy — next cold-start auto-pulls the new bundle from Storage
6. Add an entry above documenting what changed

### How scripts should reference the active model

```python
from workers.api_clients.supabase_client import _active_model_version
current = _active_model_version()  # respects MODEL_VERSION env var
```

Never hardcode a model name in a script — when production gets promoted to
v15 the script silently drifts. Reading the function ensures scripts always
target the same version the live pipeline is writing.

### How calibration alphas relate to model version

`model_calibration` rows store the Platt α/β per (market, tier, model_version).
When `apply_platt()` reads a prediction, it looks up the alphas matching the
prediction's `model_version`. If you switch production from v14 to v15:

- v15 predictions get fitted with v15's calibration
- v14 predictions retain v14's calibration (until purged)
- Mixing model versions in Platt fitting input is BAD (see poisson_backfill
  caveat above)
