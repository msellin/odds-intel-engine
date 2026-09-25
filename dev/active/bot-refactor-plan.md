Parent row: PRIORITY_QUEUE.md #162 BOT-REFACTOR-CLEANUP-2026-09-25

# Bot refactor / clean-up — plan (phase 1 output, 2026-09-25)

Written from four read-only audits — `dev/active/bot-refactor-audit/A-producers.md` (who makes a pick),
`B-publishing.md` (who sees / receives it), `C-scoring.md` (how it is settled and scored),
`D-surfaces-money.md` (pages + real-money paths) — on top of the 2026-09-24 #139 inventory, genesis and
phase-5 docs. Every claim below carries its evidence in those files (file:line + SELECTs). Policy is
`dev/active/bots-session-handover-2026-09-25.md` §3; this plan cites its rules as **§3.1–§3.8**.

**Not in this plan — owned by other rows (verified with the bot session 2026-09-25):**
VIP-PICKS-LEAK-TO-PUBLIC (one `vip_held()` reading the VIP ledger; A-R1, B-R2) · SHADOW-PICKS-POSTPONED-
NEVER-VOIDED (C-K6's shadow part) · #159 one ROI/CLV definition + `bot_performance` (migration 433) +
pick-time price writer (`pick_price.py`) + headline/dashboard_cache (C-K12) · #161 twin arms · #157
retired totals · #155 status rollout + retirement flag wiring (B-R9 data changes belong there) · #160
Coolbet price-sanity guard · #154 model inputs (proportional de-vig in NEW+ consensus).
Where a step below depends on one of these, it says so and waits.
Also owned elsewhere: **#164** VIP-PICKS-LEAK-TO-PUBLIC (its `vip_held()` is the ONLY VIP predicate — W5.2,
W5.3, W7.7, W7.8 reuse it and wait for it), **#165** SHADOW-PICKS-POSTPONED-NEVER-VOIDED, **#163** twin arms
on /admin/bots, **#152** model bots on new models (owns BOTS_CONFIG + the O/U plumbing).

> **Revised 2026-09-25 after two independent reviews** (correctness: 13 claims spot-checked against code,
> all confirmed — findings were overlap, ordering and missing OK marks; money safety: 3 HIGH findings in
> W4). Changes are marked ⟲ below.

## 0. Lock table — who owns which file, and when it is released

| File / object | Owner row | Released when | Steps that wait |
|---|---|---|---|
| `daily_pipeline_v2.py` BOTS_CONFIG + gates, O/U `ou_prob_source` / `vip_exclude` plumbing | #152, #164 | #152 ✅ and #164 ✅ | W7.1, W7.2, W7.7, W7.8 |
| `bot_registry.py`, `docs/SYSTEM_MAP.md` bot rows, `scripts/export_bot_config.py` | #159 / #155 / #157 | #159 + #155 ✅ | W4.3, W5.4 |
| `scripts/publish_picks_forward_test.py`, `picks_forward_test_checkpoint.py` | #161 ✅ / #164 | #164 ✅ | W5.3, W5.4, W6.3 |
| `workers/jobs/settlement.py` — dashboard_cache + hero block | #159 | #159 ✅ | W6.x headline, W8.9 |
| `workers/jobs/settlement.py` — void / settle functions | #165 (postponed void) | #165 ✅ (then a merge window) | W1.3, W1.4 |
| `workers/utils/pick_price.py`, `supabase_client.py` store_bet, migrations 433/434 | #159 | #159 ✅ | W2.1, W4.5, W6.x |
| web `engine-data.ts`, `bot-aggregates.ts`, `bot-performance.ts`, `performance/*`, `admin/bots/*`, `bot-board*.ts`, `api/performance/*` | #159 | #159 ✅ | W6.1, W6.2, W7.4 |
| web `admin-attention.ts` | #155 (after one #139 edit, done 9003e5d) | #155 ✅ | W6.5 |
| `coolbet_signaler.py`, `picks_public_all` | #164 | #164 ✅ | W5.2, W5.3 |
| `pick_generator.py`, placers, `placement_gate.py`, `best_price_router.py`, `unibet_placer.py`, `coolbet_prekickoff_alert.py` | free | — | W0, W4 (Phase 2) |

---

## 1. What is wrong, in one page

A "bot" today is not one object. It is **six things that must agree and often don't**:

| Part | Today | Consequence (measured) |
|---|---|---|
| **Rule** (what it picks) | `BOTS_CONFIG` dicts in `daily_pipeline_v2.py` + 5 standalone producers (ou_sharp_outlier, pick_generator, pick_triggers, ou35_model_shadow, forward-test publisher), each with its own gates, freshness and de-vig | VIP #1's live rule ≠ its backtest: 8 inherited legacy gates dropped 24 of 50 qualifying candidates in 2 days, Kelly stakes vs a flat backtest (A §3). Four de-vig methods, so CLV judges a pick with a different fair price from the one that chose it (A §2). |
| **Price recorded** | Three paper writers `DO UPDATE` the price on every re-run but keep the first pick time | 22–52% of their rows differ from the quote at pick time, +1.0–3.8% on average (A §2). |
| **Distribution** (who sees / receives it) | ≥ 9 places decide from 6 columns + 2 constants (`show_on_picks`, `show_on_performance`, `hide_pending`, `vip`, `vip_exclude`, `maturity_label`, `VIP_BOTS`, `PUBLISHED_ARMS`); the arm list is hard-coded in 9 views | 7 active bots' fields disagree with their status; anon can read an EXPERIMENTAL bot's pending picks; the VIP senders keep no record of what they sent (B §1–4). |
| **Settlement** | Main settler + 3 paper bots settling themselves; CLV-AUTOVOID and `resettle_wrongly_voided_bets` undo each other | 90 of 93 autovoided bets were re-graded (net +€591; 10 on bot_v10_1x2, +€45.7). Corners bots have no closing price on any of 944 settled picks (C §5). |
| **Score** | 5 verdict engines; one bot shows up to 6 ROIs and 5 CLVs across pages | bot_v10_1x2: ROI +13.4 / +8.1 / +5.2 / +5.0%, CLV +7.0 / +0.8 / +3.0% depending on the page (C §3). #159 fixes /performance + /admin/bots; the Pick queue and the /picks panel still use their own. |
| **Real-money path** | Two placers with different floors; per-bot floors cover 2 of 11 capable bots; the router skips account verification and counts caps on Coolbet only; 547 paper rows sit unlabelled in `real_bets` | A bot switched ON would stake a different strategy from the one being scored (breaks §3.4); money pages count €3,124 of paper stake as real (D §0). |

Dead weight on top: 33 retired pipeline bots still evaluated in 48 shadow runs a day (~52k rows / 30 d),
~1,300 lines of no-op shadow passes, InplayBot (3,269 lines, off), 1,369 trigger windows nobody reads, the
Mac daemon / Path-B placer / manual drain / daemon healthcheck, ~520 lines of uncalled web money code,
a pre-kickoff alert keyed on a heartbeat frozen since 2026-09-10 (A §4, B §5, C §7, D §5).

## 2. Target shape — one bot, one of each

```
bots (identity + status + VIP flag)              ← one row per bot, status = distribution (§3.1)
 ├─ bot_config (its RULE, exported daily)          ← the ONLY source for floors, odds range, edge unit, gates
 ├─ bot_distribution(name) view                    ← derived from status + VIP only; every reader uses it
 ├─ ledgers (simulated_bets / shadow_bets / picks_forward_test) → bot_ledger view  ← one per-leg read
 ├─ pick_sends (pick, channel, sent_at, message_id) ← every send recorded (§3.2)
 ├─ bot_performance view (#159)                    ← the ONLY per-bot ROI / CLV / n
 ├─ bot_review_flag view                           ← §3.3 retirement flag, over bot_performance
 └─ placement_gate.pick_clears()/assert_may_place()← the ONLY real-money gate, reads bot_config
real_bets (money ledger, kept separate)            ← + pick_id link, required paper/real state, one writer
```

Why keep `real_bets` separate (answers HANDOVER §7): dedupe before placing, per-match exposure, daily caps,
settlement's € grading, `coolbet_placement_attempts` / `promo_ledger` FKs and the one-per-shadow-pick unique
index all key on it (D §7). What it lacks is a `pick_id` link (fixes "forward-test bets can't be linked to a
bot"), a REQUIRED paper/real state and one writer. Dropping the /admin/real-bets *page* is independent and is
decided in step W6.

## 3. Work streams and steps

Legend — **Phase**: 2 = after #159 + #161 ✅ (bot code, not /performance or status wiring); 3 = after #157 +
#155 ✅. **OK** = needs the owner's explicit OK before merging (money, a live bot's rule, a published record).
**Twin** = changes a live bot's rule → only via a twin (§3.4). Every step: smoke test in the same commit, 1–2
independent review agents (2 for money / migrations / settlement), deploy verified (drift check + a live
read), docs rippled (SYSTEM_MAP + registry in the same commit when a bot changes).

### W0 — ⟲ Safety rails first (Phase 2, first commits; each ONLY tightens)
| Step | What | Rule | Risk | Gate |
|---|---|---|---|---|
| W0.1 | ✅ 2026-09-25 (`dev/active/bot-refactor-baseline-2026-09-25.md`) **Baseline snapshot**: for 5 fixed bots (bot_v10_1x2, bot_high_roi_global_v2, VIP #1 EV5, O/U EARLY, bot_sharp_1x2_v1) record ROI / CLV / n / P&L on every surface (bot_performance, /admin/bots, Pick queue, /picks panel, dashboard_cache) + real_bets 30-day € + spent_today; diff after every step | §3.5 | none (read-only) | none |
| W0.2 | ✅ 2026-09-25 (migration 436; contract int instead of a boolean after review) **`money_gate_ready` guard**: `coolbet_session_state.money_gate_ready` (default FALSE); `admin_set_control` + the arm route REFUSE `ui_place_enabled=true` / arming while FALSE; `assert_run_may_place` refuses unless code `GATE_CONTRACT` = DB value (so stale Mac code can't bypass); only the migration closing W4 sets it TRUE; smoke fails if TRUE while any W4 step is open | makes "W4 before any switch" code, not a promise | Only tightens; all 11 switches OFF today | **OK** (owner learns switches are locked until W4) — 2 reviewers |
| W0.3 | ✅ 2026-09-25 Pre-kickoff "PLACE MANUALLY" alert checks `placement_paused` (today it ignores the kill switch and keys on a heartbeat frozen since 09-10) — or unregister it | kill switch honoured | Only tightens | none |
| W0.4 | ✅ 2026-09-25 Router: a Unibet bet placed but not recorded sets `placement_paused=TRUE` (today it logs and carries on, so the stake escapes caps + cross-book dedupe); `unibet_placer.place_bet(execute=True)` gets its own gate call | RELIABILITY "second path" | Only tightens | 2 reviewers |

### W1 — Settlement integrity (Phase 2; no dependency on in-flight files except a merge window in settlement.py)
| Step | What | Rule | Risk | Gate |
|---|---|---|---|---|
| W1.1 | CLV-AUTOVOID writes `void_reason='quarantine: clv-autovoid — …'`; `resettle_wrongly_voided_bets` never touches a quarantine; smoke "every void carries a reason" (C-K1) | §3.5 | ⟲ Future autovoids now STICK, so bot_v10_1x2's public record changes going forward (sim ledger only — no real_bets €) | **OK** + before/after in the commit |
| W1.2 | Decide the 93 already-flipped rows (re-void / re-judge / accept) and restate (C-K1) | §3.2 | Moves bot_v10_1x2's record (10 legs, +€45.7) and retired totals | **OK** (owner Q1) |
| W1.3 | Delete the self-settlers in `team_total_paper_bot` / `first_half_1x2_paper_bot`; corners grading into the generic registry with a stats hook so corners get closes (C-K5) | §3.5 | Bots retired/experimental; nothing public moves | none |
| W1.4 | Abandoned-with-no-score = void, not 0-0, in #165's void function (C-P10). ⟲ Must cover `real_bets` too (today such a match is graded 0-0 and real bets on it settle); any new status value (e.g. 'abandoned') added to the dead-match void query or real bets stay pending forever; real bets on abandoned matches take the BOOK's own result from account reconcile | §3.2 | Touches € (0 historical real bets affected today) | **OK** (owner Q5); after #165; 2 reviewers |
Verify: dry-run counts before/after on the VPS DB (SELECT only), then run, then diff bot_performance.

### W2 — One recorded price (Phase 2; after #159's `pick_price.py` lands)
| W2.1 | The three paper writers stop overwriting price on re-run (`DO UPDATE` sets only a new `last_seen_odds`) and never write `odds_at_pick_live` — `pick_price` owns it (A-R5) | §3.5 | Shadow scoreboards shift −1…−4% ROI. ⟲ Includes the real-money-CAPABLE `bot_coolbet_*_model_v1` figures on the Pick queue, where the owner places by hand | **OK** (shown with W6.1's before/after) |
| W2.2 | Every `predictions` reader filters `source='ensemble'` + the production version (A-R6): generator, pick_triggers, ou35_model_shadow, publish_daily_picks | §3.5 | Changes picks of unified_gate / ou35 (experimental) | **OK** (experimental only) |

### W3 — One fair price (Phase 2 for the service + parity; switching live consumers is Twin)
| W3.1 | ✅ 2026-09-25 (`devig.fair_prob` + `FAIR_METHOD_BY_SHAPE`) `workers/model/devig.py` + `utils/anchor.py`: one `fair_prob(match, market, at, method, max_age)`; method per market written down with its citation (Shin 1X2, power O/U — GOTCHAS #78) | §3.5 | None until callers move | none |
| W3.2 | ✅ 2026-09-25 (smoke FAIR-PRICE-ONE-RULE; copies frozen) Parity tests: each current copy vs the service on 30 days of rows; the forward-test `_consensus_anchor` is **frozen by pre-registration** → parity-test only, never swapped | §3.5 | — | none |
| W3.3 | Move consumers one at a time: signal writer `pinnacle_implied_*`, both `power_devig` copies, `market_consensus_1x2._triples` (with #154) | §3.5 | `bot_v10_1x2`'s calibrate anchor + Pinnacle veto, NEW+ fit | **Twin + OK** per live consumer |

### W4 — One real-money gate (Phase 2; MUST land before the owner switches any bot ON)
| W4.1 | ⟲ **Three states, not two**: `placed_real` → confirmed / unverified / paper. Back-fill **paper only on the 493 proven rows** (408 post-DUPE-FIX-2 `ticket=None`, 55 in-play, 30 combo — those paths never POSTed). The 23 `auto ticket=<id>` rows are **REAL** (ticket prefix = placed_at hour, same format as the 05-20 trial) → confirmed. The 54 pre-DUPE-FIX-2 `ticket=None` rows (44 share an hour with a real ticket) and the 259 no-note rows of 05-11..05-24 (SELF-USE-VALIDATION Phase 2, when this table held manual real bets) → **unverified**, to be checked against the May Coolbet statement. `record_manual_real_bet` and the router's Unibet-uncertain path write NULL ON PURPOSE (the NULL row blocks a retry) → both writers move to 'unverified' in the SAME migration; no NOT NULL that could reject a real-money record. Dry run in `BEGIN … ROLLBACK`: counts + € per note pattern, spent_today, exposure, /admin/real-bets 30-day — only the 493 may change | honest money | Money record; placement risk nil (all 849 NULL rows are settled, past matches) | **OK**, 2 reviewers |
| W4.2 | Move exposure / canon dedupe / `spent_today` / kickoff cutoff / caps / account verify out of `scripts/place_coolbet_ui.py` into `workers/automation/placement_gate.py`; every executor calls `assert_may_place()` with a REQUIRED `held` argument; router takes the same flock + per-book verify (D-R2). ⟲ `spent_today` = placed `coolbet_placement_attempts` ∪ every book's `real_bets` with `placed_real IS NOT FALSE`, de-duplicated on `real_bet_id` (NOT "`real_bets` IS TRUE" — that would drop placed-but-unwritten attempts, manual and unverified bets and LOOSEN the cap). Add the gate calls first; delete the old copies in a LATER commit | RELIABILITY "second path" | Same behaviour for Coolbet; smoke pins move | **OK** (money path), 2 reviewers |
| W4.3 | Placers read `edge_floor / odds_min / odds_max / edge_unit` from `bot_config` via `pick_clears()` (D-R1). ⟲ `pick_clears` takes the **HIGHER of the bot's floor and today's market floor** (2.80 1x2 odds floor; router 13/10/8% per-market edge floors) unless the owner explicitly opts a bot out; converts EV floors to probability points before comparing (EV 0.08 at odds 3 ≈ 0.027 pp — reading one as the other is looser); REFUSES a pick when floor, edge unit or the 36 h config freshness is missing. Only then delete `BOT_THRESHOLDS`, router floor stacking, web `BOT_EDGE_THRESHOLDS`. Parity generator ↔ placer ↔ Pick queue on ALL 11 capable bots, not two | §3.4, §3.5 | Changes what a switched-on bot would stake; all 11 OFF | **OK**, 2 reviewers; after #159 + #155 (registry/export) |
| W4.4 | ✅ 2026-09-25 Real-money supply by bot NAME, not status label (`source_bots=("bot_v10_1x2",)` not `maturity=("calibrated",)`) (A-R7). ⟲ `pick_generator.py` is free, but this is bot code (Phase 2) — so it is a **written prerequisite on #155**: #155 must not flip labels before W4.4 lands (sent to the bot session) | §3.1 "real money is not a status" | Same set today | none |
| W4.5 | One `real_bets` writer (`store_real_bet` canonicaliser, incl. account-sync + manual RPC) + `pick_id` link (D-R8, D §7) | §3.5 | Migration on a money table | **OK**, 2 reviewers |
| W4.6 | Delete retired money code: Mac daemon, Path-B placer + CLI, manual drain, daemon healthcheck, pre-kickoff alert (or re-base on the router heartbeat + kill switch), web `getPlaceableBets` / `getRealBets` / `real-money-tier` / dead `coolbet-edge`; update smoke `PLACEMENT-GATE-ALL-EXECUTORS` (D-R7) | fewer second paths | Keep `_place_bet_api` decision explicit | none after W4.2 |
| W4.7 | `docs/COOLBET_OWN_BETTING.md` rewritten to the post-413 reality: maturity is not a money gate; placers apply the bot's own floors (D-R9) | ripple | — | with W4.3 |

### W5 — One distribution decision (Phase 3: after #155 + #159 + #164)
⟲ **Overlap with #155**: #155 already owns wiring the signaler, the forward-test publisher, the headline label
sets, `show_on_performance` and `vip` to ONE status. W5.1 is therefore offered to #155 as its
implementation (the `bot_distribution` view), not built twice — whichever row builds it, the other reads it.
W5.2 keeps only the status-based pending rule; the VIP half is #164's `vip_held()`.
| W5.1 | ✅ 2026-09-25 VIEW built (migration 437, read-only; #155 wires the readers) `bot_distribution(name)` view → `{admin, performance, picks, telegram_public, vip_channel, headline, pending_public}` from status + VIP (+ forward-test arm) only; every reader switches; `show_on_picks` / `show_on_performance` / `hide_pending` become derived, then dropped (B-R1) | §3.1, §3.5 | **High blast radius**: bot_high_roi_global_v2 + bot_v10_1x2_newplus_v1 would start going to Telegram | **OK** on what "sent" means for a sim TESTING bot (owner Q7) |
| W5.2 | RLS: anon sees pending rows only when `pending_public`; `picks_public_all` filters VIP/status or becomes `security_invoker` (B-R4) | §3.1 | Check no public surface relies on anon reading experimental pending | 2 reviewers |
| W5.3 | One audited sender `send_pick(channel, bot, pick_ref, text)`: pause check, distribution check, `pick_sends` row, DB dedupe — replaces the 5–6 send paths (B-R3, A-R10) | §3.2 | A bug silences the channel → parity smoke per sender | pause + audit parts can land in Phase 2 |
| W5.4 | Forward-test arm registry table replacing the hard-coded arrays in 9 views + 6 code maps; `rule_version` strings unchanged (B-R5, C-K4) | §3.5 | 9 views recreated; PICKS-FORWARD-TEST-RULE-LOCKED | after #161 |
| W5.5 | Telegram `/pausepicks` `/resumepicks` through `admin_set_control(source='telegram')` (B-R8) | audited controls | Low | Phase 2 |
| W5.6 | Status words from the status field (Telegram render, /picks chips, registry one-liners); `channel-reasons.ts` prints W5.1's reason (B-R6/R7) | §3.1 | Text only | after W5.1 |

### W6 — One score everywhere (Phase 2 after #159; the flag in Phase 3)
| W6.1 | Pick queue verdict chip reads `bot_performance`; retire `botVerdict` / `botTrack` / `shadow_bot_scoreboard` (C-K2, D-R5) | §3.5, 🤖 OWN | Changes which bot reads "lead" on the manual-placement queue | **OK** (owner sees it before/after) |
| W6.2 | /picks forward-test panel leads with sharp-anchor CLV, own-book mc-CLV as labelled secondary — ADD columns, pre-registration unchanged (C-K8) | §3.5, 👥 PICKS | ⟲ Changes the PUBLIC headline (sharp arm −2.6% → +2.4%) + methodology wording | **OK**; after #159 (engine-data) |
| W6.3 | One `anchor_clv()` function used by views 430/431/433 + the checkpoint script, parity on all 753 settled forward legs (C-K3) | §3.5 | Pre-registered numbers must not move | after #159 + #161 |
| W6.4 | ✅ 2026-09-25 engine side (confirmed singles; the admin view shows it with W6.8) real_bets into `leg_clv_sharp` (4th ledger); /admin real-money view shows anchor CLV (C-K9) | definition | AH needs the line | none |
| W6.5 | ✅ 2026-09-25 VIEW built (migration 437; #155 wires attention + /admin/bots) `bot_review_flag` view (n ≥ 50 settled, upper 95% CI of anchor CLV < 0) read by attention + /admin/bots; weekly_bot_review rebased on it (C-K11) | §3.3 | Basis choice | ⟲ **#155 owns the flag** — offered as its implementation, not rebuilt here; owner Q2 |
| W6.6 | Column names say their basis (`roi_flat_public`, `roi_flat_own`, `roi_staked_public`) (C-K13) | legibility | 65 admin smoke pins | ⟲ asked #159 to name its new columns by basis NOW, so this is a no-op later |
| W6.7 | Legacy CLV columns stop being written — only after the meta-model label moves (research-first rule) (C-K10) | §3.5 | **Model risk** | owner Q6 + research note |
| W6.8 | /admin/real-bets page folded into a "Real money" view on /admin/bots (€ P/L, cross-bot list, reconcile to-do, daily-limit use), page dropped | owner 2026-09-25 | Admin only | with W4.5 (pick_id) |

### W7 — Dead code and speed (Phase 2 where files are free; rest Phase 3)
| W7.1 | Stop evaluating retired BOTS_CONFIG bots in shadow cohorts (reverses a 05-20 decision); archive the 33 dicts where `export_bot_config` / registry still read them (A-R9) | cleanup, §3.3 (retired picks keep counting) | ~52k rows/30 d stop | **OK**; BOTS_CONFIG free |
| W7.2 | Delete no-op shadow passes P3/P4 (~1,300 lines), retired TRIGGER_CONFIGS, `model_*` trigger strategies + calibrator, InplayBot + live_poller hook, `prediction_snapshots` calls; move/remove ~12 smoke pins (A-R8). ⟲ NOT the `ou_prob_source` / O/U `vip_exclude` plumbing — inert until #152, which is still open. Verify: no scheduler registration or import survives (grep + `scheduler.py` job list) + drift check | cleanup | Keep `_get_bot_id_by_name` (imported) | BOTS_CONFIG free (#152, #164) |
| W7.3 | ◐ 2026-09-25 `telegram_bot.py` deleted; the rest waits (upcoming-picks.ts is #164's area, labels/default are #155's, policies are a bots migration) — Web dead code: `fetchUpcomingPicks` + label sets, `telegram_bot.py`, `_clv_for_footer`, `dropVipUnsettled` (after #159), dead RLS policies, `'active'` label + default → `'experimental'` (B-R10) | cleanup | Zero behaviour | files free |
| W7.4 | Bots page speed: server-side PostgREST over localhost, `React.cache` for board/control/superadmin reads, one superadmin check, column-pruned `bot_config` (D-R4) — ~40 calls × Cloudflare round-trip today, DB 12–185 ms | owner "Bots page slow" | Read path only | after #159 (bot-board.ts) |
| W7.5 | `candidate_funnel` gets a reader ("why not picked" on the bot sheet) and the O/U sharp job as a source (B-R11) | auditability | Low | Phase 2 |
| W7.6 | One sharp-producer engine (fold pick_generator `sharp_devig` + pick_triggers; one freshness/ceiling/book set; O/U EARLY a config of it) (A-R13) | §3.5 | 5 per-book sharp bots experimental; O/U EARLY is VIP live | **Twin + OK** for O/U EARLY |
| W7.7 | ⟲ (after #164) VIP #1 as its own module running exactly the B2 rule (flat stake, no inherited gates), as a twin; the current bot keeps running (A-R3) | pre-reg, §3.4 | VIP record restarts for the twin | **Twin + OK** |
| W7.8 | ⟲ (after #164, same code block) one_per_match across runs (skip if the bot already holds a pending row on the match) (A-R2) | §3.2 | Changes VIP #1 | **OK**; BOTS_CONFIG free |

### W8 — ⟲ Findings the first draft dropped (from the audits; each small)
| W8.1 | **Tell the owner now**: the VIP channel reaches nobody — `TELEGRAM_VIP_CHAT_ID` unset on the VPS, 0 profiles with Telegram linked; the VPS `.env` has `TELEGRAM_PUBLIC_CHANNEL` twice (values not read) | owner's call (`.env` is the owner's) | — | message only |
| W8.2 | `pnl`, `bankroll_after` and Kelly compounding still use the `odds_at_pick` high-water price (C-P4) — no row owns it; propose to #159 or take it after | §3.5 | Changes paper P&L | **OK** |
| W8.3 | ✅ 2026-09-25 (+ pick_triggers sharp strategies, one shared rule `anchor_line_too_old`, 7 h / 2 h-in-12 h) `pick_generator` `sharp_devig` has no Pinnacle age cap on a real-money-CAPABLE path → cap it (with W3.1's max_age) | money | Only tightens | Phase 2 |
| W8.4 | web `ladder.ts` misses the engine's 36 h config-staleness rule → add it (the CAN STAKE answer) | money honesty | Only tightens | after #159 frees bot-board files |
| W8.5 | Pick queue shows no simulated-ledger bots (v10, VIP #1) — decide if it should | 🤖 OWN | — | owner Q |
| W8.6 | `CONSENSUS_BOOKS` includes AF Unibet, which feeds VIP #1 → pointer to #154 | — | — | note on #154 |
| W8.7 | VIP #1's EV threshold receives pp-scale bumps (ALN-1 / mid-band) → part of W7.7's exact-rule twin | pre-reg | — | with W7.7 |
| W8.8 | Startup/smoke check `VIP_BOTS == {bots.vip}` (A-R11) | §3.1 | — | with #164 |
| W8.9 | `dashboard_cache` is 133 MB and never pruned → prune history (C-K7) | infra | — | after #159 |
| W8.10 | `#163` + `#159`'s open detail view: `/api/performance/bot-legs` accepts twin-arm names, so EXPERIMENTAL twins' picks could go public once #163 lands → the route must check distribution first (sent to the bot session) | §3.1 | — | #159/#163 own it |

## 4. Order

1. **Now (phase 1, done):** audits + this plan, reviewed; nothing edited.
2. **Phase 2 opens (#159 + #161 ✅)** — re-read the handover + git log, re-verify each step's premise (numbers
   move as rows land), then in this order:
   **W0.1 → W0.2 → W0.3 → W0.4** (rails, only tighten) → **W4.4** (before #155 flips labels) → W1.1 (OK) →
   W8.3 → W5.5 + the pause/audit half of W5.3 (VIP senders ignore `/pausepicks` — early) → W2.1 (OK), W2.2
   (OK) → W4.2 (gate calls added; old copies deleted a commit later) → W3.1, W3.2 → W6.4 → W6.1 (OK) →
   W7.3, W7.5 → W4.6, W4.7 → W4.1 (OK) → W6.2 (OK), W6.3, W8.4, W8.9 (after #159) → W1.3, W1.4 (after #165).
   **W4 must be complete before any real-money switch is turned on — enforced by W0.2, not by this sentence.**
3. **Phase 3 opens (#157 + #155 ✅; #164 for its items)** — W4.3 (OK) → W4.5 (OK) → close W4 (sets
   `money_gate_ready`) → W5.1 (with/for #155) → W5.2 → W5.3 → W5.4 → W5.6 → W6.5 (#155) → W6.6, W6.8 → W7.1,
   W7.2, W7.4 → the Twin items (W3.3, W7.6, W7.7, W7.8) one at a time, a twin and an OK each.

Each step is its own commit (code + smoke + docs), pushed only when its reviewer(s) pass, and verified live
(drift check, a SELECT that proves the new path ran, a page read) before the next step starts.

## 5. Owner decisions — ✅ ALL ANSWERED 2026-09-25 ("I trust your recommendations")

The owner approved every recommended option (the 16-question list sent 2026-09-25): 1C re-judge the 93
autovoided bets on corrected closes · 2C three-state real_bets (493 paper / 23 real / 313 unverified) ·
3C strictest-of floors + one cross-book cap (switching bots ON stays a separate owner call) · 4C Pick
queue on bot_performance · 5A autovoid keeps a reason going forward · 6B abandoned-no-score = void (real
bets take the book's result) · 7A flag on the PUBLIC basis · 8A show CLV coverage · 9A a /picks watchlist
line counts as sent · 10A paper P&L / bankroll on the pick-time price · 11A paper writers keep the first
price · 12A source-filter `predictions` readers · 13A stop shadow-evaluating the 33 retired bots ·
14B Pick queue stays own-book-placeable bots only · 15A keep the legacy CLV column for the meta-model only ·
16A build the four twins (VIP #1 exact rule, one-per-match, v10 on fair_prob, one sharp engine) — each
switch decided by the owner on twin data. Every step still shows before/after before it lands.

### (original question list, kept for the record)

1. **W1.2** — the 93 CLV-autovoided bets: re-void, re-judge against today's close, or accept? (moves
   bot_v10_1x2 by 10 legs / +€45.7)
2. **W6.5** — the retirement flag's CLV basis: at the public "available" price or at our books?
3. Show anchor-CLV coverage next to the figure ("CLV on 170 of 397 picks")?
4. Does a `/picks` watchlist line count as "sent" under §3.2?
5. **W1.4** — abandoned matches with no score: void (book practice) or 0-0?
6. **W6.7** — the meta-model label (`clv_pinnacle_devig`, no age limit): move it (a model change → research
   first) or keep writing the legacy column for it alone?
7. ✅ ANSWERED 2026-09-25 (owner, via the bot session): "sent" for TESTING-or-above = /picks AND the public Telegram channel — `bot_distribution.sent_public` drives both. **W5.1** — for a sim-ledger TESTING bot, does "sent" mean Telegram, /picks, or both? (decides whether
   bot_high_roi_global_v2 and bot_v10_1x2_newplus_v1 start going to Telegram)
8. **W4.1 / W4.3 / W4.5** — the money-record back-fill (3 states; 23 rows are real, 313 unverified to check
   against the May Coolbet statement) and the per-bot floors in the placers.
11. **W0.2** — real-money switches refused until W4 is done (only tightens; all OFF today).
12. **W8.1** — the VIP channel has no recipients (`TELEGRAM_VIP_CHAT_ID` unset); duplicate `TELEGRAM_PUBLIC_CHANNEL` in the VPS `.env`.
13. **W8.5** — should the Pick queue list the simulated-ledger bots (v10, VIP #1)?
9. **W7.1** — stop shadow-evaluating the 33 retired pipeline bots (reverses 2026-05-20)?
10. **Twin items** W3.3, W7.6, W7.7, W7.8 — each when its twin is ready.

## 6. Risks and how each is contained

* **Shared checkout** — other sessions edit the same files. Stage only own hunks via a temp
  `GIT_INDEX_FILE`; afterwards `git reset -q -- <own paths>` only; never stash; verify `git diff --cached`.
* **Silent second path** (RELIABILITY_LEDGER's recurring pattern) — every centralisation step ships a smoke
  that enumerates the callers and fails if one bypasses the shared function.
* **Pre-registered forward test** — never change its selection, its `rule_version` strings or its checkpoint
  numbers; parity tests only (PICKS-FORWARD-TEST-RULE-LOCKED).
* **Live bots** — nothing in phase 2 changes a live bot's pick rule; W3.3 / W7.6–W7.8 are twins with OKs.
* **Money** — all 11 real-money switches are OFF and placement is paused; W0.2 makes "no switch before W4"
  a DB refusal; money steps get two reviewers and a rolled-back dry run; never loosen in the same commit that
  moves a check (add the new check first, delete the old one later).
* **Mac placers run from the shared, dirty local checkout**, which the drift check does not cover — so any
  code-only guard can be bypassed by stale code; W0.2's DB-side refusal is the answer, and every money
  step's verification includes the Mac checkout's HEAD.
* **Baseline** — W0.1's snapshot is diffed after every step; an unexplained move stops the sequence.
* **Numbers moving** — every step that changes a shown figure records before/after in its commit message
  and in the row, so a moved number is explained, not discovered.
