Parent: PRIORITY_QUEUE.md #191 OWN-BOT-LINEUP-AND-ADMIN-BOTS-CLEANUP-2026-09-26

# #191 tasks

- [x] (a) Audit every bot NOT on /performance → `dev/active/own-bot-lineup-audit.md` (agents: audit-1 triggers/generators/OWN, audit-2 model-paper/in-play/instruments/unknown)
- [x] (a2) Brainstorms → `dev/active/own-bot-lineup-brainstorm-{strategy,instruments,page}.md`
- [x] (a3) Synthesis → `own-bot-lineup-synthesis.md` — [ ] owner approves line by line
- [ ] (b) OWN line-up rules, pre-registered (research-first for MODEL variants)
- [ ] (c) Build missing OWN bots on the shared engine; fold/retire the rest (migrations + registry + SYSTEM_MAP)
- [ ] (d) /admin/bots → three blocks (PICKS · OWN · Instruments & history, the last only if it earns its place)
- [ ] (e) More Estonian books (#101: Paf, Ninja, Optibet)
- [x] "Settings unknown": bot_config export now also runs at every scheduler start (2e8c3561)
- [x] Append-only log of NEW+ / ou_comb_v1 predictions (rows are overwritten every 30 min) — prerequisite for MODEL backtests — done 2026-09-26: `model_prediction_history` (migration 479), history starts that day
- [ ] Exchange close as the independent judge for OWN bots
- [ ] Record lineup first-seen time (lineups_fetched_at is overwritten)
- [ ] Fix "placeable" for sharp bots whose placement floor is an empty band
- [ ] bot_consensus_d_v1 frozen admin record (moot if retired)
