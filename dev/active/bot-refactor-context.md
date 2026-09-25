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
- ~17:25: landed 4f402bbf (W0 + mig 436), web 232e135, 57b63f75 + web a46ce32 (W4.4, bot_config re-exported
  on the VPS: source_bots), b488cb9d (W8.3 anchor age 7 h / 2 h-in-12 h shared helper; W3.1/W3.2 fair_prob +
  FAIR-PRICE-ONE-RULE), bf110d6b (CONTROL-FN-REFUSES follows the lock — CI found it on 4f402bbf).
  Live verified after each: contract 0, paused, not armed, 0/11 ON.
- Remaining phase-2 steps mostly need the OWNER's OK (W1.1, W2.1, W2.2, W4.1, W4.2, W6.1) or wait on #164/#165/
  #155 files. Doing the no-OK free ones next: W6.4 (real_bets into leg_clv_sharp), W7.3 (dead telegram_bot.py).
- ~17:55: e543f59f (W6.4 real_bets in leg_clv_sharp — 143 legs scored on the VPS: 139 fresh Pinnacle close,
  mean clv_sharp −0.9% (125, cons ok) / −4.8% (14); W7.3 telegram_bot.py deleted). CI red on 4f402bbf /
  57b63f75 = CONTROL-FN-REFUSES only, fixed by bf110d6b (awaiting its run).
- No-OK, free steps are now exhausted. Everything left needs the owner (list in plan §5) or #164/#165/#155/
  #157 to close. Next when unblocked: W2.1 (OK), W4.2 (OK, money), W6.1 (OK), W1.1 (OK); after #164: W5.x.
- ~19:00: migration 437 live (c8e5f10d) — views for #155. Owner answers via the bot session: Q7 sent =
  /picks AND public Telegram (sent_public drives both; no view change); bot_ou35_model_v1 RETIRED by them
  (b954c603, mig 438); pending_exposed RLS gap → #155. #164 in progress (its files locked).
- Owner 2026-09-25 (via bot session): FLAT STAKES EVERYWHERE (handover rule 9) — W8.2 re-bases pnl/bankroll on a flat unit; W4 sizes real money flat; compute_stake→flat is #155's after #164.
- W8.2 stake/pnl/bankroll restatement MOVED to #155 (owner). Don't write simulated_bets.stake/pnl/bankroll_after.
- LESSON (twice now: 05a088a9, f542bfbf): NEVER build the staged smoke_test.py from the working tree — other
  sessions' uncommitted tests ride along. Build from `git show HEAD:scripts/smoke_test.py` + insert only my own
  test text (and my own pin edits by exact replacement).
- ~21:30: landed 3be5404d (W1.1/1.2 autovoid; run once on VPS: 2 voided, v10 bankroll 1302.49 consistent),
  f542bfbf+bed9a549 (W4.1 mig 440 real_bets labels 464/31/354; reverted #165 hunks I swept), c070e737 (W2.1
  paper writers DO NOTHING), 2663c1e0 + web 45ba206 (W4.2 cap all books + router lock; W4.3a flat real money).
  Migrations 441-443 reserved for #155 → mine from 444. W8.2 pnl restatement moved to #155.
- ~16:40 phase 3 (GO received): HOTFIX dff516db (second bare % in pick_generator SQL comment); batch A =
  W1.5 shadow autovoid, W6.1 Pick queue on bot_performance (+ "can't judge yet"), headline _EXEC_PNL = stored pnl
  (+9.6% → +12.2%, = /performance), in-play markers loaded by settlement + results_check (#157 leftover).
  Owner answered a-d all ⭐. New rules: one commit per task per repo, ops/verify/<task>.yml, no git reset/stash.
