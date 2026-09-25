Parent row: PRIORITY_QUEUE.md #162 BOT-REFACTOR-CLEANUP-2026-09-25

# #162 bot refactor — task checklist

Gates (from the bot session "Top priority task", 2026-09-25): Phase 2 only after #159 + #161 ✅
(it messages us); Phase 3 only after #157 + #155 ✅. Until then do NOT edit: daily_pipeline_v2.py
BOTS_CONFIG, bot_registry.py, SYSTEM_MAP bot rows, publish_picks_forward_test.py, settlement.py
dashboard_cache, migrations touching bots/simulated_bets/picks_forward_test views, web performance/*,
bot-aggregates, engine-data. (admin-attention.ts: one agreed #139 edit, then #155 owns it.)

## Phase 1 — read-only
- [ ] Audit A producers → dev/active/bot-refactor-audit/A-producers.md
- [ ] Audit B publishing/visibility → B-publishing.md
- [ ] Audit C settlement/scoring → C-scoring.md
- [ ] Audit D surfaces + real money → D-surfaces-money.md
- [ ] Synthesised plan → dev/active/bot-refactor-plan.md (merge/delete/rename/centralise, risks, §3 rules per step)
- [ ] Independent review of the plan (2 agents: correctness + money safety)
- [ ] Commit phase-1 docs (own files only)

## Phase 2 — after #159 + #161 (bot code, NOT /performance or status wiring)
- [ ] re-read handover + git log; re-verify plan deltas
- [ ] steps per plan, each: smoke test, 1–2 review agents, deploy check

## Phase 3 — after #157 + #155
- [ ] re-read handover + git log
- [ ] remaining steps per plan
