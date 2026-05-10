# OU-MARKET-FEATURES — Context

> Update this file before context compresses. New sessions read it after "continue".

## Status

- **2026-05-10** — Phase A audit complete. Dev docs written. Phase B (feature build) is next — ready to start.

## Phase A Audit Results (2026-05-10)

Full output saved to `dev/active/ou-market-features-audit-2026-05-10.txt`.

| Check | Result | Decision |
|-------|--------|----------|
| A.1 — Pinnacle 1X2 outliers | 13 pairs, all real heavy mismatches (Shakhtar away, Barcelona Femení, Levadia Tallinn vs Laagri amateur) | ✅ CLEAN — not data quality issues |
| A.2 — Pinnacle OU 2.5 overround | 2,725 paired. 2,659 (97.6%) clean. 66 (2.4%) overround > 1.10 | ⚠️ Fix in training SQL: filter `1/over + 1/under < 1.10` |
| A.2b — Timestamp skew | 66 suspicious pairs are ALL same-timestamp (within 60s) | Confirmed real bad data (mislabeled lines), not snapshot skew |
| A.3 — BTTS Pinnacle | 1 paired row in 30 days | ❌ Pinnacle BTTS = 0% coverage. Drop Pinnacle BTTS from scope |
| A.4 — Label hygiene | Labels clean. No near-match "Pinnacle" aliases. BTTS has no Pinnacle rows at all | ✅ CLEAN |
| A.5 — Coverage (last 90d) | 1X2: 23.1%, OU 2.5: 21.9%, BTTS: 0.0% of 11,867 finished matches | Proceed: missing indicators handle 78% gaps |

## Revised feature scope (based on audit)

| Feature | Decision | Rationale |
|---------|----------|-----------|
| `pinnacle_implied_over25` | ✅ Add, with overround guard < 1.10 in SQL | 21.9% coverage, 97.6% of those rows clean |
| `pinnacle_implied_under25` | ✅ Add, paired with over | Same |
| `pinnacle_implied_btts_yes/no` | ❌ Dropped | Zero Pinnacle BTTS data |
| `ou25_bookmaker_disagreement` | ✅ Add | Multi-book (filtered), mirrors 1X2 disagreement |
| `market_implied_btts_yes` | ✅ Add instead | Multi-book consensus (Marathonbet 68K, 1xBet 66K, William Hill 60K rows in 30d) |

## Key files

| File | Why it matters |
|------|----------------|
| `workers/model/train.py` | FEATURE_COLS at line 125, PINNACLE_FEATURE_COLS at line 459, `_load_pinnacle_features()` at line 462 — the pattern OU/BTTS features will mirror. INFORMATIVE_MISSING_COLS at line 48 — add new feature names here for Stage 2a `_missing` indicators. |
| `workers/api_clients/supabase_client.py` | `compute_bookmaker_disagreement` at 2775 (1X2 selection='home', mirror this for OU 2.5). `build_match_feature_vectors` and live twin `build_match_feature_vectors_live` — both need the new columns wired. MFV insert payload at ~1571. |
| `supabase/migrations/` | Need a new migration to add MFV columns. Next number = current highest + 1. Last applied was 092 per recent commits. |
| `workers/model/xgboost_ensemble.py` | Inference loader — `_build_row_from_mfv` reads MFV columns at inference time, so new features automatically flow once MFV has them. |
| `workers/utils/odds_quality.py` | Existing OU blacklist + sanity filters. If Phase A surfaces issues, fix here. |
| `scripts/offline_eval.py` | Phase D harness. |
| `workers/jobs/daily_pipeline_v2.py` | `_load_today_from_db` — placement-time OU aggregation (the OU-PIN-REQUIRED guard is here at the line containing `pin_price is None`). Cross-reference for "what's clean" definitions. |

## Decisions made

- **Stick to Pinnacle for the new market features.** Same rationale as v11+ — sharpest book, cleanest signal once placement bug is fixed. Multi-book OU disagreement is the only multi-book feature added (mirrors how `bookmaker_disagreement` for 1X2 is multi-book but `pinnacle_implied_*` is single-book).
- **OU 2.5 only**, no 1.5 / 3.5. The OU model head predicts the 2.5 line specifically. Don't shotgun.
- **BTTS implied (yes + no) but no BTTS disagreement.** Single-book BTTS is the cheaper win; disagreement can wait until lift is measured.
- **Audit BLOCKS feature work**, per user's explicit request — recurring history (10× user reports) means default assumption is the data is dirty.

## Next steps (start here tomorrow)

**Phase B — Feature build. Start with:**

1. **Migration `093_mfv_ou_market_features.sql`** — add to `match_feature_vectors`:
   - `pinnacle_implied_over25  numeric(5,4)`
   - `pinnacle_implied_under25 numeric(5,4)`
   - `ou25_bookmaker_disagreement numeric(5,4)`
   - `market_implied_btts_yes  numeric(5,4)`

2. **`_load_ou_market_features()` in `workers/model/train.py`** — mirrors `_load_pinnacle_features()`:
   - Join `odds_snapshots` with `bookmaker = 'Pinnacle' AND market = 'over_under_25' AND is_live = false`
   - Pivot over/under per match_id
   - **Quality guard:** `HAVING (1.0/over_odds + 1.0/under_odds) < 1.10` — drops the 2.4% bad pairs
   - Also fetch `ou25_bookmaker_disagreement` — max-min across distinct books (filtered by `odds_quality.py` blacklist)
   - And `market_implied_btts_yes` — average 1/yes_odds across distinct bookmakers (same pattern as `compute_market_implied_strength`)

3. **`OU_MARKET_FEATURE_COLS` constant** in `train.py` and `INFORMATIVE_MISSING_COLS` additions.

4. **MFV builders in `supabase_client.py`** — `build_match_feature_vectors` and `build_match_feature_vectors_live` both need to compute and store these 4 new columns.

5. **Smoke tests** — assert new columns present in MFV builder SQL + overround guard in `_load_ou_market_features`.

6. **Train v14** — `python3 workers/model/train.py --version v14 --include-pinnacle --include-ou-market`

7. **`offline_eval.py v9 v11_pinnacle v12 v13 v14`** — compare.

## Open questions

- Does Pinnacle BTTS coverage match OU 2.5 (~85%)? Phase A.5 will measure.
- For OU 2.5 disagreement, do we filter blacklisted bookmakers via `workers/utils/odds_quality.py` first or just rely on the latest pre-KO row per bookmaker? Probably filter — otherwise blacklisted books re-enter through this feature path even though they're blocked at placement.

## Linked work

- `9d4166e` — OU-PIN-REQUIRED placement guard. Defines the production "is this Pinnacle OU row trustworthy" predicate. Audit and feature SQL must match this definition.
- `dd78fea` — OU-PINNACLE-CAP cleanup.
- ML-MODEL-COMPARISON (PRIORITY_QUEUE) — v9..v13 results in `dev/active/model-comparison-2026-05-10-final.md`. v14 numbers go in a follow-up doc.
- `dev/active/odds-quality-cleanup-plan.md` — historical record of OU quality issues.
