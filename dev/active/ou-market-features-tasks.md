# OU-MARKET-FEATURES — Tasks

## Phase A — Odds-quality audit ✅ DONE 2026-05-10

- [x] A.1 — 13 extreme-odds pairs, all legitimate heavy mismatches. CLEAN.
- [x] A.2 — 97.6% clean. 66 (2.4%) have overround > 1.10 — real bad rows (mislabeled lines). Fix: training SQL guard `< 1.10`.
- [x] A.2b — All 66 same-timestamp. Confirmed mislabeled (not snapshot skew).
- [x] A.3 — Pinnacle BTTS = 0 coverage. Dropped from scope.
- [x] A.4 — Labels clean. No near-match Pinnacle aliases.
- [x] A.5 — 1X2: 23.1%, OU 2.5: 21.9%, BTTS: 0.0% (last 90d finished matches).
- [x] Outputs saved to `dev/active/ou-market-features-audit-2026-05-10.txt`
- [x] Decision: proceed with OU 2.5 (overround guard) + multi-book BTTS + ou25 disagreement. Drop Pinnacle BTTS.

## Phase B — Feature build ✅ DONE 2026-05-11

- [x] Migration `093_mfv_ou_market_features.sql` — add 4 columns: `pinnacle_implied_over25`, `pinnacle_implied_under25`, `ou25_bookmaker_disagreement`, `market_implied_btts_yes`
- [x] `_load_ou_market_features()` in `train.py` — Pinnacle OU 2.5 with `HAVING (1/over + 1/under) < 1.10` guard + ou25 disagreement + market btts_yes (3 separate queries merged in Python to avoid timeout)
- [x] `OU_MARKET_FEATURE_COLS` constant in `train.py` + add to `INFORMATIVE_MISSING_COLS`
- [x] `--include-ou-market` CLI flag + `train_all(include_ou_market=True)` parameter
- [x] `compute_ou25_bookmaker_disagreement` + `compute_market_implied_btts_yes` helpers in `supabase_client.py`
- [x] Wire new columns into MFV builder (batch loads + `_build_feature_row_batched`) in `supabase_client.py`
- [x] Smoke test — source-guards: OU_MARKET_FEATURE_COLS, overround guard, CLI flag, INFORMATIVE_MISSING_COLS
- [x] Smoke test — MFV builder computes the 4 new columns + batch load vars present

## Phase C — Train v14

- [x] `OU_MARKET_FEATURE_COLS` constant in `train.py` ← done in Phase B
- [x] `--include-ou-market` CLI flag in `train.py` ← done in Phase B
- [x] `train_all(include_ou_market=True)` parameter wired ← done in Phase B
- [ ] Train v14 = v12 features + Pinnacle 1X2 + OU/BTTS market features
- [ ] Verify auto-upload to Supabase Storage succeeded
- [ ] Verify `model_versions` registry row created

## Phase D — Offline eval

- [ ] Run `scripts/offline_eval.py v9 v11_pinnacle v12 v13 v14`
- [ ] Save results to `dev/active/model-comparison-2026-05-10-v14.md`
- [ ] Document headline numbers in PRIORITY_QUEUE entry
- [ ] If v14 wins: mark in queue but do NOT auto-promote (env var swap is operator-side)

## Wrap-up

- [ ] Update `MODEL_WHITEPAPER.md` — feature inventory now includes OU 2.5 + BTTS market features
- [ ] Update `SIGNALS.md` — new signals captured
- [ ] Update `PRIORITY_QUEUE.md` — mark OU-MARKET-FEATURES ✅ Done with date
- [ ] Update task `dev/active/ou-market-features-context.md` — final status + outcomes
- [ ] Commit code + docs together
