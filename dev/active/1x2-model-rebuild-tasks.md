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
- [x] Apply migration 412 (CI) + load history (258,222 rows) + first shadow run verified on the VPS (414 written, 360 gated) — after fixing the pandas 3.0.4 segfault (6e6f36ad)
- [ ] Forward check once ≥ 2,000 settled shadow rows (≈ 1 week): `python3 scripts/ab_1x2_rating_arms.py --forward`
- [ ] OWNER DECISION: replace the Poisson/XGB legs of the served 1X2 blend with the rating model
- [x] ~~Schedule in-season refresh~~ DROPPED: the table already holds current seasons up to 2026-09-24, and those leagues are tracked in `matches` from here on, so new results arrive through the normal pipeline
- [x] Smoke tests (WEEKLY-EVAL-NO-HOLDOUT, RATING-1X2-LEAK-GUARD, RATING-1X2-SHADOW-ISOLATED) + docs (MODEL_WHITEPAPER §4.4, MODEL_ANALYSIS, WORKFLOWS, DATA_SOURCES, ROADMAP, RELIABILITY_LEDGER #26)
- [ ] Close #141 after the forward check + owner decision; archive these dev docs
