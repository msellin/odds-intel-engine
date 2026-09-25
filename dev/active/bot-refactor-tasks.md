Parent row: PRIORITY_QUEUE.md #162 BOT-REFACTOR-CLEANUP-2026-09-25

# #162 bot refactor — task checklist

Gates (from the bot session "Top priority task", 2026-09-25): Phase 2 only after #159 + #161 ✅
(it messages us); Phase 3 only after #157 + #155 ✅. Until then do NOT edit: daily_pipeline_v2.py
BOTS_CONFIG, bot_registry.py, SYSTEM_MAP bot rows, publish_picks_forward_test.py, settlement.py
dashboard_cache, migrations touching bots/simulated_bets/picks_forward_test views, web performance/*,
bot-aggregates, engine-data. (admin-attention.ts: one agreed #139 edit, then #155 owns it.)

## Phase 1 — read-only
- [x] Audit A producers → dev/active/bot-refactor-audit/A-producers.md
- [x] Audit B publishing/visibility → B-publishing.md
- [x] Audit C settlement/scoring → C-scoring.md
- [x] Audit D surfaces + real money → D-surfaces-money.md
- [x] Synthesised plan → dev/active/bot-refactor-plan.md (merge/delete/rename/centralise, risks, §3 rules per step)
- [x] Independent review of the plan (2 agents: correctness + money safety) — revised
- [ ] Commit phase-1 docs (own files only)

## Phase 2 — after #159 + #161 (bot code, NOT /performance or status wiring)
- [x] re-read handover + git log; re-verify plan deltas (handover unchanged since 000046e3)
- [x] W0.1 baseline snapshot
- [x] W0.2 money-gate contract lock (mig 436) — 2 reviewers, live rolled-back dry runs (all 7 cases)
- [x] W0.3 pre-kickoff alert honours the kill switch
- [x] W0.4 unibet_placer run-level gate; router pauses on an unrecorded Unibet bet
- [x] W0.2 web half: ladder layer 8 + route 409 (web 232e135)
- [x] W4.4 pick_generator real-money supply by bot name (+ is_active/retired filter) — 57b63f75, bot_config re-exported
- [x] W8.3 sharp anchor age cap (pick_generator + pick_triggers, shared helper)
- [x] W3.1/W3.2 one fair-price rule + parity pins
- [x] W6.4 real_bets scored on the sharp-anchor close (engine)
- [x] W7.3 (part) dead telegram_bot.py deleted
- [ ] steps per plan, each: smoke test, 1–2 review agents, deploy check

## Phase 3 — after #157 + #155
- [ ] re-read handover + git log
- [ ] remaining steps per plan
