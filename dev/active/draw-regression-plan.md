# 1x2 Draw regression — remediation plan

Filed 2026-08-16. Context in `dev/active/rigorous-eval-v20260816.md`:
model over-predicts draws by ~17pp (37% avg pred vs 20% actual), and
this bias has persisted across 4 consecutive weekly retrains
(v20260712 → v20260802 → v20260809 → v20260816). No amount of new
training data has closed the gap.

## Shipped in this batch (2026-08-16)

**1. Post-hoc DRAW_CAL_FACTOR** (`workers/model/xgboost_ensemble.py`)

Multiplicative shrink applied to draw_prob at inference; home + away
renormalized to fill `1 - new_draw`. Env-flag toggleable, default 1.0
(no change) for safe rollout.

Live rollout plan:
- Deploy code to VPS + restart scheduler
- Set `DRAW_CAL_FACTOR=0.75` on VPS `.env` — moderate shrink (37% → ~28%)
- Watch 3 days: rigorous_eval on the 1x2_draw log-loss delta vs current-prod
  should improve by ~1-2pp with no regression elsewhere
- If good, drop to 0.60 (37% → ~22%, matching observed)
- If bad, back to 1.0

**Rollback**: `sed -i '/^DRAW_CAL_FACTOR=/d' /opt/odds-intel-engine/.env`
+ `systemctl restart oddsintel-scheduler`. No retrain needed.

**2. Retrain healthcheck cadence + dedup**

- Cron bumped Mon+Tue → Mon-Sat 09:00 UTC (in `workers/scheduler.py`)
- `ALERT_DEDUP_HOURS` default 48 → 24 (in `workers/jobs/retrain_healthcheck.py`)
- Dedup-suppressed branches now log-warn so operator sees "healthcheck
  ran, saw problem, couldn't send alert" in scheduler journal

Combined effect on the Jul 26 skip scenario:
- Mon Jul 27 age=8.25d → healthy (unchanged)
- Tue Jul 28 age=9.25d → stale, alert #1 sent
- Wed Jul 29 age=10.25d → stale, within 24h → dedup-log-warn
- **Thu Jul 30 age=11.25d → stale, past 24h → alert #2 sent**
- Fri Jul 31 age=12.25d → stale, within 24h → dedup-log-warn
- Sat Aug 1 age=13.25d → stale, past 24h → alert #3 sent
- Sun Aug 2 03:00 UTC — v20260802 lands → recovery message

3 alerts + 2 dedup-log-warns instead of 1 possibly-eaten alert.

## Next iterations (ordered by ROI/effort)

### Phase 2 — Temporal weighting in training loss (~half day)

Retrain with `sample_weight` decayed by match-date age. Recent matches
count more; older matches count less. Teaches the model to track the
current market rate instead of the historical mean.

Implementation sketch:
```python
# in workers/model/train.py::train_all()
from datetime import date
today = date.today()
if targets_df is not None and "match_date" in targets_df.columns:
    ages_days = (today - targets_df["match_date"].dt.date).dt.days.values
    halflife = int(os.getenv("SAMPLE_WEIGHT_HALFLIFE_DAYS", "0") or 0)
    if halflife > 0:
        # exponential decay: age 0 → weight 1.0; age = halflife → 0.5
        sample_weight = np.power(0.5, ages_days / halflife)
    else:
        sample_weight = None
```

Then pass `sample_weight[train_idx]` and `sample_weight[val_idx]` into
each `.fit()` call in train.py (there are 4: result_1x2, over_under,
home_goals, away_goals).

Rollout plan:
- Ship as CLI flag + env var, default disabled (halflife=0)
- Enable on a candidate retrain: `--recency-halflife-days 180`
- Compare candidate bundle vs baseline v20260712 via rigorous_eval
- Promote only if 1x2_draw ≥ 1pp better without regressing elsewhere

Expected impact: moderate. Helps track shift but doesn't fix baseline
over-prediction bias. Best paired with post-hoc calibration (Phase 1),
not replacement.

### Phase 3 — Season-context feature (1 day)

Add "days since season start" or "matchweek fraction" to MFV. Lets the
model condition on the low-draw early-season phase separately from
mid-season steady-state.

Blockers:
- Requires `leagues.season_start_date` populated for every league
  (currently sparse). Backfill via API-Football `/leagues` endpoint
  season data.
- MFV migration: add `season_days` column.
- Backfill script: recompute for all historical matches.
- Retrain from scratch with the new feature.

Expected impact: unclear. If the model is already implicitly learning
this via other features (form_home, elo_home), adding it explicitly
won't help much. Worth trying only if Phase 1+2 don't close the gap.

### Phase 4 — Draw-specialist head (1-2 days)

Separate binary "draw vs not-draw" XGBoost classifier with its own
calibration, then blend into the 1x2 output:

```
P_draw_final = P_draw_specialist(x)  # binary, calibrated
P_home_final = P_home_main(x) / (1 - P_draw_main(x)) * (1 - P_draw_final)
P_away_final = P_away_main(x) / (1 - P_draw_main(x)) * (1 - P_draw_final)
```

Blend via a config knob so we can A/B against the main head.

Blockers:
- Ensemble surgery in `xgboost_ensemble.py`
- New training path in `train.py`
- New CV/eval routine

Expected impact: highest of the four phases if the bias is genuinely
architectural. Also the biggest effort. Only worth doing if Phases 1-3
don't close the gap AND you're willing to invest ~2 days.

## Success criteria

We're done when:
- rigorous_eval shows the next weekly bundle within ±1% log-loss of
  v20260712 on 1x2_draw (currently +3.2%)
- OR observed vs predicted draw rate delta is within ±3pp on rolling
  30d (currently 17pp)

Whichever comes first. Track weekly.
