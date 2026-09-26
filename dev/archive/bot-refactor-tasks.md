Parent row: PRIORITY_QUEUE.md #162 BOT-REFACTOR-CLEANUP-2026-09-25

# #162 bot refactor — task checklist

Gates (from the bot session "Top priority task", 2026-09-25): Phase 2 only after #159 + #161 ✅
(it messages us); Phase 3 only after #157 + #155 ✅. Until then do NOT edit: daily_pipeline_v2.py
BOTS_CONFIG, bot_registry.py, SYSTEM_MAP bot rows, publish_picks_forward_test.py, settlement.py
dashboard_cache, migrations touching bots/simulated_bets/picks_forward_test views, web performance/*,
bot-aggregates, engine-data. (admin-attention.ts: one agreed #139 edit, then #155 owns it.)

## Phase 1 — read-only
- [x] Audit A producers → dev/archive/bot-refactor-audit/A-producers.md
- [x] Audit B publishing/visibility → B-publishing.md
- [x] Audit C settlement/scoring → C-scoring.md
- [x] Audit D surfaces + real money → D-surfaces-money.md
- [x] Synthesised plan → dev/archive/bot-refactor-plan.md (merge/delete/rename/centralise, risks, §3 rules per step)
- [x] Independent review of the plan (2 agents: correctness + money safety) — revised
- [x] Commit phase-1 docs (own files only)

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
- [x] W5.1 + W6.5 views (migration 437) for #155 — 2 reviewers (SQL + policy)
- [x] W1.1/W1.2 autovoid = quarantine + bankroll + re-judge (2 of 90 fail) — 2 reviewers
- [x] W4.1 real_bets labels (migration 440) — 2 reviewers
- [x] W1.5 shadow_bets autovoid — owner OK (a); 196 legs voided
- [x] W4.2 cap across books + router run lock; W4.3a flat stakes
- [x] W6.1 Pick queue on bot_performance (web 1c06110)
- [x] W7.1 retired bots write nothing (c7fcbfeb, 0fb91b64)
- [x] W4.3 one placement floor (placement_floor.pick_clears) — 2 money reviews
- [x] W4.5 one real_bets writer + forward_test_pick_id (mig 448)
- [x] steps per plan, each: smoke test, 1–2 review agents, deploy check

## Phase 3 — after #157 + #155 (both ✅)
- [x] re-read handover + git log
- [x] rule-version tag on picks (owner b) — mig 453, 5b9962d5
- [x] W1.4 abandoned no-score = void (fb2baec5); partial-score case → #170
- [x] W1.3 one shadow settlement path (672db77c, agent)
- [x] W8.9 dashboard_cache prune (0265580d, agent)
- [x] W8.4 + W8.8 ladder staleness + VIP_BOTS==DB (1ac136f4 / web 38c5a05, agent)
- [x] W7.7 + W7.8 + W8.7 VIP #1 exact B2 rule + one per match across runs (bb02266b)
- [x] W5.6 status words from the status field (bd2bd721 / web fe23743)
- [x] W7.5 funnel reader + O/U sharp source (dab87a95 / web 42631a7; bf8bb2fa hardening)
- [x] W5.2 closed (done by #155/#164, verified) · W6.6 closed (no-op) · W6.7 closed (owner 15A) · W8.5 closed (14B)
- [x] W3.3 + W2.2b + W8.6 → #154 (model changes, measured)
- [x] W4.6 delete retired money code (bf7e5e50, web 4a80c6a)
- [x] W4.2 structural: exposure + final floor inside assert_may_place (843f1073)
- [x] W4.7 COOLBET_OWN_BETTING rewrite (5dc9c573)
- [x] pre-lock money review → 2 fixes (53780afd) · W4 CLOSED: contract 1 (f21893e9, mig 459) — nothing armed/resumed/switched on
- [ ] W5.5 Telegram controls audited (agent running)
- [x] W5.3 one audited sender (7fe9e8ef, mig 457)
- [x] W5.4 + W6.3 arm registry + anchor_clv (bc48a156, mig 454)
- [x] W7.2 dead pipeline code (201f3b1f)
- [x] W7.4 + W6.8 (web a3776cb, 940eebff)
- [ ] W7.6 one sharp engine (agent running)
- [ ] W6.2 public /picks CLV headline — WAITS FOR OWNER OK
- [x] mig 458 shadow_bets_unique.rule_version (3c89a29a) · health_alerts 2 broken checks fixed (f089c899)
- [ ] W8.1 tell owner: VIP channel has no recipients (TELEGRAM_VIP_CHAT_ID unset)
