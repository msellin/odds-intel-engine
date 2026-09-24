Parent row: **#139 UNIFIED-BOT-MODEL-EPIC** in PRIORITY_QUEUE.md.

# Unified bot model — tasks

Design + contract: `docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md`. Invariants: `dev/active/unified-bot-model-genesis/`.
Rule: every step ships with a smoke test and 1–2 independent review agents (2 for anything that writes/migrates data).

- [x] Step 1 — audits #137 (`docs/BOTS_AUDIT_2026_09_24.md`) + #138 (`docs/SHADOW_BOTS_AND_CONTROLS_AUDIT_2026_09_24.md`), verified
- [x] Phase 0 — genesis write-ups (ledgers, bots-and-controls, predictions)
- [x] 5a — reader/writer inventory
- [x] Phase 1 — migration 410 views + bot_config, export job, /admin/bots rebuild (3 review rounds)
- [ ] Phase 1 — verify on live data after deploy (410 applied, first 03:40 export, page renders)
- [ ] Owner decisions (i)–(v) listed on the #139 row
- [ ] Phase 5 schema rewritten from the invariants; 2 reviewers
- [ ] Phase 2 — shadow-bots page becomes "today's picks" on bot_ledger
- [ ] Phase 3 — capability switches (keep the deliberate real-money layers separate — genesis bots-and-controls §recommendation)
- [ ] Phase 4 — clean-up per owner decisions
- [ ] Phase 5b–5e — picks table, backfill + reconciliation, dual-write, readers, compatibility views
