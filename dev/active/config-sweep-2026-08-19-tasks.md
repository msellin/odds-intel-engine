# CONFIG-SWEEP-2026-08-19 — Tasks

## Phase A — Plan
- [x] Write plan doc — `dev/active/config-sweep-2026-08-19-plan.md`
- [x] Write context doc — `dev/active/config-sweep-2026-08-19-context.md`
- [x] Write tasks doc (this file)

## Phase B — Build sweep engine (`scripts/config_sweep.py`)
- [ ] Data-loading query: matches + ensemble predictions + best accessible odds + closing odds + actual result
- [ ] Load-to-pandas with derived columns: `won` per (market, selection), `edge`, `ratio_pick_to_close`
- [ ] Fantasy-price filter: drop rows where `ratio_pick_to_close ≥ 1.65` (CLV-AUTOVOID discipline)
- [ ] Parameter grid enumeration (7,560 configs)
- [ ] Per-config, per-window evaluator: filter → aggregate ROI/CLV/n
- [ ] Result writer: CSV with all (config × window) rows
- [ ] Acceptance filter: n≥30 per window AND roi≥0 per window AND aggregate CLV≥0 AND aggregate ROI≥5%
- [ ] Top-N summary printer sorted by aggregate CLV

## Phase C — Run + interpret
- [ ] Execute sweep on VPS or locally (~5-10 min)
- [ ] Generate report `dev/active/config-sweep-2026-08-19-report.md`
- [ ] Review top winners with user
- [ ] Pick top 2-3 for shadow deployment

## Phase D — Deploy winners (only if worthy configs found)
- [ ] Migration for new shadow bots
- [ ] Sweep-config → bot-config translation in daily_pipeline_v2
- [ ] Smoke test
- [ ] Ship + apply on VPS
