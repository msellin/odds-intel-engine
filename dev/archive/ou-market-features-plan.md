# OU-MARKET-FEATURES — Plan

**Filed:** 2026-05-10
**Started:** 2026-05-10
**Owner:** Margus + Claude
**Effort estimate:** 4-6h

## Why this exists

Audit while triaging today's OU-PIN-REQUIRED fix (commit `9d4166e`) revealed that v10/v11/v12/v13 model bundles were **not** affected by the OU bookmaker bug (the bug is placement-side, the model uses 1X2 odds only). But the audit also surfaced a real feature gap: all three model heads (1X2, OU 2.5, BTTS) train on the same feature set, which contains **only 1X2 market signals**. OU and BTTS heads have no view of their own markets' odds — despite OU 0.5/1.5/2.5/3.5/4.5 and BTTS yes/no being stored in `odds_snapshots` for every match.

Current FEATURE_COLS market features (from `workers/model/train.py:147-149` + `:459`):
- `opening_implied_home/draw/away` — 1X2 only
- `bookmaker_disagreement` — 1X2 only (`market = '1x2' AND selection = 'home'`)
- v11+: `pinnacle_implied_home/draw/away` — 1X2 only (`WHERE bookmaker = 'Pinnacle' AND market = '1x2'`)

Adding Pinnacle-anchored OU 2.5 and BTTS implied probabilities should lift the OU/BTTS heads the same way Pinnacle 1X2 lifted v13's 1X2 head.

## Hard precondition: odds-quality audit FIRST

The same OU data path has produced wrong odds **10× already** per user reports (`feedback_odds_quality_recurring` memory). Most recent fixes:
- `9d4166e` (today, 2026-05-10) — OU-PIN-REQUIRED placement guard requires Pinnacle reference.
- `dd78fea` (today, earlier) — OU-PINNACLE-CAP cleanup voided historical bets at >2× Pinnacle prices.
- Multiple earlier ODDS-QUALITY-CLEANUP / blacklist additions in `workers/utils/odds_quality.py`.

**Default assumption:** the data is dirty until proven otherwise. If audit surfaces problems, fix at source (`odds_snapshots` ingestion / `workers/utils/odds_quality.py` blacklist) BEFORE training. Bad features train worse models than no features.

## Phases

### Phase A — Odds-quality audit (BLOCKING)

Run pre-train audit queries:

**A.1 — Pinnacle 1X2 outliers**
```sql
SELECT match_id, selection, odds, timestamp
FROM odds_snapshots
WHERE bookmaker = 'Pinnacle' AND market = '1x2'
  AND (odds < 1.05 OR odds > 30)
ORDER BY timestamp DESC LIMIT 50;
```
Expected: empty or only legitimate edge cases (huge favourites in cup ties etc).

**A.2 — Pinnacle OU 2.5 sanity**
- Outliers: `bookmaker = 'Pinnacle' AND market = 'over_under_25' AND (odds < 1.05 OR odds > 10)`.
- Cross-check overround: for each `(match_id, timestamp)` with both 'over' and 'under' Pinnacle rows, `1/odds_over + 1/odds_under` should sit in [1.02, 1.10]. Anything outside is suspect (mislabel or stale row).

**A.3 — Pinnacle BTTS sanity**
- Same overround check on `market = 'btts'` (yes + no).

**A.4 — Mislabel sweep**
- Verify there are no rows where `bookmaker = 'Pinnacle'` but odds shape doesn't match Pinnacle's typical pattern (e.g., extreme overround, missing both sides). This is the failure mode behind the user's recurring complaints — non-Pinnacle book mislabelled.

**A.5 — Coverage measurement**
- Count finished matches with at least one pre-KO Pinnacle row in 1X2, OU 2.5, OU 1.5, BTTS, on the v12/v13 training window (2024+).
- Decide what coverage threshold is workable. v13 had 5% Pinnacle 1X2 coverage and still won OU/BTTS markets. OU 2.5 should be ~85% per today's commit message — much stronger.

**Decision gate:**
- If A.1–A.4 surface bad rows → file source-side fix, halt feature work.
- If only A.5 shows thin coverage → proceed; `_missing` indicators (Stage 2a pattern) will handle gaps.

### Phase B — Feature build

Add to MFV:
- `pinnacle_implied_over25` = 1 / (latest pre-KO Pinnacle OU 2.5 OVER odds).
- `pinnacle_implied_under25` = 1 / (latest pre-KO Pinnacle OU 2.5 UNDER odds).
- `pinnacle_implied_btts_yes` = 1 / (latest pre-KO Pinnacle BTTS YES odds).
- `pinnacle_implied_btts_no` = 1 / (latest pre-KO Pinnacle BTTS NO odds).
- `ou25_bookmaker_disagreement` = max - min implied_over25 across distinct bookmakers (mirrors `compute_bookmaker_disagreement` for 1X2).

Migration adds the columns to `match_feature_vectors`. Builders updated in `supabase_client.py` (both nightly `build_match_feature_vectors` and pre-KO `build_match_feature_vectors_live`). Add all five to `INFORMATIVE_MISSING_COLS` so Stage 2a generates `_missing` indicators.

### Phase C — Train v14

In `train.py`:
- New `OU_MARKET_FEATURE_COLS = ["pinnacle_implied_over25", "pinnacle_implied_under25", "pinnacle_implied_btts_yes", "pinnacle_implied_btts_no", "ou25_bookmaker_disagreement"]`.
- `--include-ou-market` CLI flag (mirrors `--include-pinnacle`).
- `_load_ou_market_features()` mirrors `_load_pinnacle_features()` — joined into MFV at training load time so we don't need to wait for nightly MFV rebuild.

Train v14 = v12 features (post-0e MFV refresh) + Pinnacle 1X2 + new OU/BTTS market features. Auto-uploads to Supabase Storage via existing ML-BUNDLE-STORAGE flow.

### Phase D — Offline eval

`scripts/offline_eval.py v9 v11_pinnacle v12 v13 v14` on the same held-out MFV slice. Compare log_loss across markets; v14 should beat v11_pinnacle on OU 2.5 and BTTS, match it on 1X2 (since 1X2 features are unchanged from v12+Pinnacle).

If v14 wins, **don't auto-promote** — operator runs the Railway env var swap. Document in PRIORITY_QUEUE.

## Out of scope

- Closing-line snapshots / odds drift on OU/BTTS (next step after v14 lands; same pattern as `odds_drift_home`).
- BTTS bookmaker disagreement — leave for v15. We're avoiding shotgunning new features; do one round, measure lift, decide what to add next.
- OU 1.5 / 3.5 features — only OU 2.5 because the OU model head predicts that line specifically.

## Risks

- **Odds quality.** Mitigated by Phase A blocking gate.
- **Train/serve skew.** New features must be present in MFV at *both* training time AND inference time. If the live builder doesn't compute them, v14 falls back on imputation in production. Smoke test must guard the live builder explicitly.
- **Coverage cliff in small leagues.** Pinnacle doesn't price OU 2.5 in Belarus Premier etc. (today's audit said 85% coverage on OU 2.5; the missing 15% is exactly the small-league bots that bot_ou15_defensive was placing into). `_missing` indicator handles it.
