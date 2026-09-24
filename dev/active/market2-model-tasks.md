Parent: [[#149]] SECOND-MARKET-MODEL-2026-09-24

# #149 tasks
- [x] Three research tracks (data audit, literature, consensus-outlier scan) — market = O/U totals
- [x] Plan + research-before-train + round O1 pre-registration (`market2-model-plan.md`)
- [x] O1 model: `scripts/ab_ou_combined.py` (consensus + Pinnacle + rating per line) — select/confirm
- [x] O1 bot backtest vs controls (`bot_sharp_ou_v1`, `bot_sweep_ou25/35`, null)
- [x] Production (as O3 T3 + T2, not the O1 model): workers/jobs/ou_sharp_outlier.py, migration 423, registry, SYSTEM_MAP, WORKFLOWS — `workers/model/combined_ou.py`, shadow rows, EV5/EV8 bots, migration, registry, SYSTEM_MAP
- [x] Iteration 2 (O2, sharp-anchored; found the missing-close artefact) — new layer (candidates: shots/xG sum rating, longer decay for totals, Shin de-vig, per-book weights, alt-line derivation from Pinnacle main line)
- [x] Iteration 3 (O3: early / two-anchor / fresh-anchor filters) — new layer (web research between iterations)
- [ ] Close-out: docs (MODEL_WHITEPAPER, SYSTEM_MAP, WORKFLOWS, ROADMAP), handover summary for owner
