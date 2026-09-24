Parent: [[#141]] 1X2-MODEL-REBUILD-2026-09-24 (PRIORITY_QUEUE.md)

# 1X2 model rebuild — tasks

- [x] Read-only audit (weekly eval, versions, coverage, features) — 2026-09-24
- [x] Claim #141, plan + pre-registration written before training
- [x] Data cache: matches + Pinnacle pre-KO 1X2 triple + shots (read-only)
- [x] Walk-forward ratings: Elo, pi, dynamic Poisson FT, dynamic Poisson HT, form, rest, league rates
- [x] Same-day-leak guard verified
- [x] PROD reference predictions (v20260830 raw head, zero-fill) on the Q1 window
- [x] Tune hyper-parameters on validation slice only
- [x] Q1 run (vs PROD) — record in plan RESULTS
- [x] Q2 run (α vs Pinnacle, Holm m=6) — record in plan RESULTS
- [x] Per-tier + gated/ungated buckets (gate sensitivity superseded: with history warm-up ungated ≈ gated, 1.0091 vs 1.0070)
- [x] History-depth analysis → decide on backfill
- [x] Iterate on new pre-registered arms if needed
- [x] Fix weekly retrain holdout (train candidate with --cutoff) — WEEKLY-EVAL-NO-HOLDOUT 2026-09-24
- [x] Productionise in SHADOW: migration 412, ratings_1x2.py, rating_1x2_shadow job, smoke tests
- [ ] Apply migration 412 (CI) + load history `fetch_1x2_history_cache.py --to-db` + first shadow run verified
- [ ] Forward check once ≥ 2,000 settled shadow rows (≈ 1 week): rating vs served vs Pinnacle
- [ ] OWNER DECISION: replace the Poisson/XGB legs of the served 1X2 blend with the rating model
- [ ] Schedule in-season refresh of rating_history_results (weekly re-fetch of current seasons)
- [ ] Smoke test(s), docs (MODEL_WHITEPAPER, MODEL_ANALYSIS, WORKFLOWS, ROADMAP), close #141
