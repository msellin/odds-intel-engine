Parent: [[#141]] 1X2-MODEL-REBUILD-2026-09-24 (PRIORITY_QUEUE.md)

# 1X2 model rebuild — tasks

- [x] Read-only audit (weekly eval, versions, coverage, features) — 2026-09-24
- [x] Claim #141, plan + pre-registration written before training
- [ ] Data cache: matches + Pinnacle pre-KO 1X2 triple + shots (read-only)
- [ ] Walk-forward ratings: Elo, pi, dynamic Poisson FT, dynamic Poisson HT, form, rest, league rates
- [ ] Same-day-leak guard verified
- [ ] PROD reference predictions (v20260830 raw head, zero-fill) on the Q1 window
- [ ] Tune hyper-parameters on validation slice only
- [ ] Q1 run (vs PROD) — record in plan RESULTS
- [ ] Q2 run (α vs Pinnacle, Holm m=6) — record in plan RESULTS
- [ ] Coverage gate sensitivity (N = 4/8/15) + per-tier
- [ ] History-depth analysis → decide on backfill
- [ ] Iterate on new pre-registered arms if needed
- [x] Fix weekly retrain holdout (train candidate with --cutoff) — WEEKLY-EVAL-NO-HOLDOUT 2026-09-24
- [ ] Productionise the winner (owner confirms before served model changes)
- [ ] Smoke test(s), docs (MODEL_WHITEPAPER, MODEL_ANALYSIS, WORKFLOWS, ROADMAP), close #141
