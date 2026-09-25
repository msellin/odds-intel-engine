Parent row: PRIORITY_QUEUE.md #162

# #162 bot refactor — context (update as you go; read on "continue")

- Brief: owner-forwarded 2026-09-25 (in #162 row). Policy = dev/active/bots-session-handover-2026-09-25.md §3.
- Owner away some hours from ~11:40 UTC 2026-09-25: "don't stop until it's done; test and verify every step".
- Sync partner: session "Top priority task" (uds:/tmp/cc-socks/96511.sock) owns #155 #157 #159 #161; it
  messages when phases may start. It okayed our small #139 admin-attention.ts edit — send it the hash.
- Prior inputs: docs/UNIFIED_BOT_MODEL_5A_INVENTORY.md, docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md,
  dev/active/unified-bot-model-genesis/, dev/active/unified-bot-model-phase5-{schema-draft,decisions}.md,
  dev/active/admin-models-page-brief.md, dev/active/unified-bot-model-HANDOVER.md §7 (real_bets question).
- Parallel: #139 dashboard round 6 (fixer G on Jobs/Feeds) — finish + deploy + ping owner.

## State
- 2026-09-25 ~11:40: row #162 filed/claimed (ea51c30b); 4 audit agents launched (A–D).
- ~12:40: #139 round 6 pushed (web 9003e5d, engine 944e68fc); peer sent the hash. Audits A + B done and
  committed. Key plan inputs so far: VIP leak (vip_exclude re-derives; forward test no VIP check);
  one-per-match not across runs; VIP #1 live rule ≠ backtest (8 inherited gates, Kelly stakes); 4 de-vigs;
  paper writers overwrite price but keep pick time; generator `predictions` source unfiltered; real-money
  supply keyed on status label 'calibrated'; 33 retired bots still evaluated 48×/day; VIP senders keep no
  sent record; anon can read EXPERIMENTAL pending picks; settlement never voids postponed shadow picks (219).
- Waiting: audits C (scoring) + D (surfaces/money); peer message for phase 2.
- Shared-checkout lesson: commit via a TEMP GIT_INDEX_FILE (read-tree HEAD + own files) and afterwards
  `git reset -q -- <own paths>` only — a bare `git reset` dropped the web session's staged deletion once
  (restored with `git rm --cached`).
- Peer (bot session) took ownership 2026-09-25: VIP-PICKS-LEAK-TO-PUBLIC (P0) and
  SHADOW-PICKS-POSTPONED-NEVER-VOIDED — LEAVE BOTH OUT of the refactor plan (reference only).
  433_one_bot_performance.sql = #159's in-progress migration — don't touch. Still phase 1.
- ~13:10: plan written + reviewed by 2 agents (correctness: 13 claims confirmed; money: 3 HIGH in W4 —
  23 "unknown" rows are REAL bets; spent_today rewrite would loosen caps; floor move would loosen 3 ways)
  and revised (lock table §0, W0 safety rails incl. DB guard money_gate_ready, W8 dropped findings).
  #161 ✅; #159 still in progress → Phase 2 not open. #163/#164/#165 exist (164/165 = the two findings).
- NEXT: when the bot session says #159 ✅ → Phase 2 starts with W0.1 baseline snapshot, W0.2 guard (OK).
- ~16:30: Phase 2 started on free files (bot session "waiting"; told it; no objection). W0.1–W0.4 done:
  mig 436 money_gate_contract (int) + trigger; placement_gate refuses unless DB contract >=1 AND == code
  coolbet_state.GATE_CONTRACT (=0 now; the W4-closing commit raises both to 1). Reviewers: bypass (none;
  fixed contract/lock_timeout/INSERT/thread stubs) + ops (web ladder layer, readiness blocker, docs).
  Dry runs on prod in BEGIN…ROLLBACK caught a PL/pgSQL CASE-in-IF syntax error before commit.
  NEXT: push engine → migrate applies 436 → web: FLEET_COLS money_gate_contract, ladder layer 8, route 409.
