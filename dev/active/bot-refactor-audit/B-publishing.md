Parent row: [[#162]] BOT-REFACTOR-CLEANUP, phase 1 (read-only audit). Area **B: publishing, distribution & visibility**.

# B — Who sees or receives a pick (current truth, 2026-09-25)

Auditor scope: every path that decides whether a pick is **sent** (public Telegram channel, VIP DMs,
private VIP channel), **shown** (/picks, /performance, /api/v1/*), or **readable** (RLS, anon grants,
views), plus every visibility/status field and the candidate-funnel / pick audit trail. Measured against
owner policy §3 of `dev/active/bots-session-handover-2026-09-25.md` (#155):

> EXPERIMENTAL = admin-only, nothing sent · TESTING = on /performance, picks SENT, counted in own record,
> NOT in headline · BETA / CALIBRATED = sent + headline · ⭐ VIP is a CHANNEL on top of any status
> (Pro/Elite + private channel live; public settled-only) · anything sent is counted · public bots never
> take a VIP-held pick.

Builds on `dev/active/unified-bot-model-genesis/bots-and-controls.md` §2.2–2.7 (maturity_label's six
jobs, show_on_picks, publishing_paused) and `docs/UNIFIED_BOT_MODEL_5A_INVENTORY.md`. Those were written
2026-09-24, before `vip` (420), `hide_pending` (421/424), `show_on_performance` (427), the O/U VIP sender
(423/424), `vip_exclude` (#152), migration 432 (send TESTING/BETA model picks) and the uncommitted 433/434.
Everything below was re-verified against the code and a read-only DB query on 2026-09-25.

**No PII:** `profiles.telegram_chat_id` has zero rows. No customer data was read.

---

## 0. Headline findings (read these first)

1. **No field states a bot's distribution.** Distribution is re-derived in at least **9 places** from 6
   columns (`maturity_label`, `show_on_picks`, `show_on_performance`, `vip`, `hide_pending`,
   `retired_at`/`is_active`), a Python constant (`VIP_BOTS`), a publisher constant (`PUBLISHED_ARMS`),
   and hard-coded arm/grade→bot maps in 5 SQL views and 3 code files. Nothing derives any of it from
   status. (§3)
2. **VIP picks leak to the public, measured.** Since the VIP bots went live (2026-09-24) **4 VIP-held
   picks were published publicly AFTER the VIP bot already held them.** Two came from
   `bot_v10_1x2_newplus_v1` (it has `vip_exclude`, and it still took them). Two came from the forward test,
   which has no VIP check at all. Four more overlaps were published publicly *before* the VIP bot took the
   same pick. That means the paid channel sold a pick that was already free. (§4.1)
3. **`vip_exclude` is a copy of the VIP rules, not a check against VIP picks.** It re-derives
   "would the VIP bot want this now?" with the VIP rule constants hard-coded
   (`daily_pipeline_v2.py:3995-4012`). It never asks "does a VIP bot hold a pending pick on this
   selection?" So once the price moves, a pick the VIP bot took earlier passes. (§2 D1)
4. **The VIP channel currently reaches nobody.** `TELEGRAM_VIP_CHAT_ID` is unset on the VPS (grep count 0),
   and 0 profiles have a `telegram_chat_id`, so every DM loop sends to 0 users. The sends also leave no
   audit row, so "anything sent is counted" cannot be proven for VIP. (§4.2)
5. **Status disagrees with distribution for 7 active bots** (table §4.3). The two VIP bots are
   `experimental` (policy wants "VIP · TESTING"). `bot_consensus_d_v1` is `testing` but never sent.
   `bot_v10_1x2_newplus_v1` (TESTING) and `bot_high_roi_global_v2` (BETA) reach /picks but **not
   Telegram**: the public channel sends `calibrated` only, which is the Ludogorets split, live again for
   2 bots.
6. **`/pausepicks` does not pause the VIP senders.** The two VIP send paths (`daily_pipeline_v2`,
   `ou_sharp_outlier`) never read `publishing_paused`. The Telegram `/pausepicks` and `/resumepicks`
   commands write the flag directly and leave no `control_changes` row. (§4.4)
7. **An EXPERIMENTAL bot's pending picks are readable by the public anon key.** The `simulated_bets` RLS
   hides pending rows only when a bot has `vip OR hide_pending` set. Today that exposes
   `bot_rating_1x2_v1` (experimental, 2 pending), and it would expose any new experimental bot. `hide_pending`
   exists only to patch this, one bot at a time. (§4.5)
8. **The headline in `dashboard_cache` counts TESTING and RETIRED bots.** The filter
   `maturity_label != 'experimental'` (`settlement.py:3325-3535`) breaks the "TESTING not in headline"
   rule. The web headline set `HEADLINE_MATURITY_LABELS` excludes testing, so the two headlines disagree.
   `settlement.py` is being edited under #159; I only flag this. (§4.6)
9. **The admin "why is this bot on channel X" explainer is a separate, already-stale re-implementation.**
   `channel-reasons.ts` says `bot_v10_1x2_newplus_v1` is "Not on /performance" (it is listed, via
   `show_on_performance`). It also says a VIP bot is "not on /picks because VIP", which nothing enforces:
   `admin_set_control` would let `show_on_picks` be switched ON for a VIP or experimental bot, and the view
   would then publish its pending picks. (§4.7)
10. **Dead:** `fetchUpcomingPicks`, `PUBLIC_MATURITY_LABELS` and `SIGNED_IN_MATURITY_LABELS` in
    `upcoming-picks.ts`; `workers/notify/telegram_bot.py` (no importer); `_clv_for_footer` (a DB read per
    run whose result is never used); `dropVipUnsettled` (no caller in the working tree); `candidate_funnel`
    (write-only, no reader anywhere). §5 has the full list.

---

## 1. Inventory

### 1.1 Senders: every path that puts a pick in front of a person

| # | Path | file:line | Audience | Which bots | Gate that decides | Reads `publishing_paused`? | Records the send? |
|---|---|---|---|---|---|---|---|
| S1 | Model signaler → public channel | `workers/automation/coolbet_signaler.py:80-209` (query), `212-225` `is_public_eligible`, `569-582` send; called from `workers/jobs/betting_pipeline.py:77-132` | @oddsintelpicks (public) | any `simulated_bets` bot **except `VIP_BOTS`** (`:153-154`) whose (match,market,selection) group contains a **`calibrated`** bot (`:141-143` `bool_or`), market in `_PUBLIC_MARKETS` (`:295`), model edge floor (`clears_edge_floor`) | ✅ `betting_pipeline.py:105-122` (returns) | `simulated_bets.signaled_at` on **every** row of the group (`_mark_signaled :414-436`), incl. non-calibrated and VIP-twin rows. No message id for the public post. |
| S2 | Model signaler → operator prompt | `coolbet_signaler.py:522-540` | owner's chat | same candidates | env `OPERATOR_PICK_ALERTS` (off on VPS) | ✅ (same caller) | `signal_message_id` |
| S3 | Forward-test publisher (scheduled) | `workers/scheduler.py:2694-2900`, registered `:4101-4104` (`5,35`) | public channel | arms `live` → `bot_sharp_1x2_v1` / `bot_sharp_ou_v1`; `consensus_anchor` → `bot_consensus_b/c_v1` (grade D claimed, never sent `:2850`) | `PUBLISHED_ARMS` (`scripts/publish_picks_forward_test.py:241`), `select()` `:649-694`, `daily_room()` `:613-646` | ✅ `:2751`: still records, skips the send (#139) | `picks_forward_test.telegram_message_id` (`attach_message_id`) |
| S4 | Forward-test publisher (manual `--send`) | `scripts/publish_picks_forward_test.py:1170-1239` `main()` | public channel | **live arm only** (+ junk control recorded) | same constants | ✅ `:1204-1211` (**refuses outright**, unlike S3) | same |
| S5 | Model-pipeline **VIP** send (1X2) | `workers/jobs/daily_pipeline_v2.py:4376-4378` (tags), `4535-4556` (send) | Pro/Elite DMs (`send_telegram_to_users`) + private channel (`send_telegram_vip`) | `VIP_BOTS` ∩ bots in this pipeline run = `bot_combined_1x2_ev5_v1` | `bot_name in VIP_BOTS` | ❌ **no** | ❌ **nothing** (in-memory dedup key only) |
| S6 | O/U sharp-outlier **VIP** send | `workers/jobs/ou_sharp_outlier.py:128-146` `_send_vip_pick`, triggered `:206-208`; job `scheduler.py:1178-1183`, cron `:3684` (`14,44`) | Pro/Elite DMs + private channel | `bot_ou_sharp_early_v1` | `p["bot"] in VIP_BOTS` | ❌ **no** | ❌ **nothing** |
| S7 | In-play DM to users | `workers/jobs/inplay_bot.py:425-440` | owner + **all Pro/Elite** | in-play strategies | env `INPLAY_STRATEGIES_ENABLED` (default false, `live_poller.py:565`) | ❌ | `bet_telegram_alerts` (owner msg only) |
| S8 | Pipeline per-pick owner alert | `daily_pipeline_v2.py:4492-4533` | owner chat | every `simulated_bets` bot in the run | `operator_pick_alerts_enabled()` (off) | ❌ (operator-only) | `bet_telegram_alerts` |
| S9 | Shadow-picks summaries | `daily_pipeline_v2.py:4900,5161,5472,5711,5919` → `telegram.notify_shadow_picks :485-590` | owner chat | shadow bots | per-call flag | ❌ (operator-only) | – |
| S10 | Test script | `scripts/test_telegram_public_channel.py:51` | public channel | – | manual | ❌ | – |

Senders in the web repo: `src/app/api/telegram/webhook/route.ts` (operator command replies only) and
`src/lib/telegram-operator.ts`. Neither sends picks.

### 1.2 Display surfaces: where a pick or a record is shown

| # | Surface | file:line | Reads | Which bots it shows |
|---|---|---|---|---|
| W1 | `/picks` | `odds-intel-web/src/app/picks/page.tsx:380-387` → `src/lib/forward-test-picks.ts:255-277` `fetchPublicPicks`, `:318` `fetchBoard` | view `picks_public_all` (anon), `picks_board_public` | forward-test arms `live`/`consensus_anchor` (grade D only if it was sent), plus model bots with `show_on_picks AND retired_at IS NULL`. **Pending rows included.** |
| W2 | `/api/v1/upcoming` | `src/app/api/v1/upcoming/route.ts:69-174` → `fetchPublicPicks` | `picks_public_all` | same as W1 |
| W3 | `/performance` rows | `src/app/(app)/performance/page.tsx:222-225` (sim bots), `:242-262` `PUBLISHED_ARM_BOTS` (ledger bots) — **being edited (#159)** | `bots`, `bot_performance` (433, uncommitted), `picks_forward_test_bot_record` | `isPublicBot` (calibrated/beta) **OR** `isVipBot` **OR** `show_on_performance`, not retired, not `LEDGER_BACKED_BOTS`, plus the 5 hard-coded forward-test bots once they have published ≥1 pick |
| W4 | `/performance` detail legs | `src/app/api/performance/bot-legs/route.ts:35,49` (new, untracked, #159) | `getBotLegs` | same OR-rule + `LEDGER_BACKED_BOTS`; `settledOnly = isVipBot OR hidePending` |
| W5 | `/performance` history | `performance/page.tsx:158-167` → `src/lib/bot-performance.ts:174-206` (untracked, #159) | per-leg view | public cohort; `hidePendingBots = vip OR hide_pending` |
| W6 | `/api/v1/track-record` | `src/app/api/v1/track-record/route.ts:45-48,131,162` | `simulated_bets` via anon | `HEADLINE_MATURITY_LABELS` = calibrated, beta, **active** (`engine-data.ts:1491`) |
| W7 | Hero / headline | `engine-data.ts:1491-1526` + `dashboard_cache` (written by `settlement.py:3310-3540`) | – | web: calibrated/beta/active · engine: `!= 'experimental'` (**disagree**, §4.6) |
| W8 | `/performance` client | `src/components/performance-client.tsx:55` | – | `!retiredAt && !isLiveBot && isPublicBot` (yet another subset) |
| W9 | Admin channel explainer | `src/app/(app)/admin/bots/channel-reasons.ts` (whole file) | `bot_config`, `bots` | re-states rules S1/S3/W1/W3 in TS (§4.7) |
| W10 | Admin /picks↔Telegram mismatch badge | `src/app/(app)/admin/bots/bot-board-model.ts:434-436` | `bot_config.telegram` vs `show_on_picks` | all sim bots |
| W11 | Admin controls | `src/app/api/admin/bots/controls/route.ts:43-133` → SQL `admin_set_control` (mig 413) | – | writes `show_on_picks`, `maturity_label`, `publishing_paused`, `retire` |

### 1.3 Readability: RLS, grants, views

| Object | State (DB, 2026-09-25) | Note |
|---|---|---|
| anon `SELECT` grants | `bots, dashboard_cache, leagues, match_page_views, matches, picks_board_public, picks_forward_test_public, picks_forward_test_summary, picks_forward_test_summary_by_market, picks_public_all, simulated_bets, teams` + `rpc get_coverage_counts` | matches migration 404's allow-list |
| `simulated_bets` policy "Public read" | `result IS DISTINCT FROM 'pending' OR NOT EXISTS (bot.vip OR bot.hide_pending)` (mig 421) | exposes pending picks of **every other bot**, experimental ones included (§4.5) |
| `bots` policies | two identical `USING (true)` policies (`public_read`, `Public read`) | duplicate, harmless |
| `shadow_bets` | policy `shadow_bets_anon_read USING (true)` but **no anon grant** | dead policy; the grant revocation (404) is what protects it |
| Views | all 11 relevant views have empty `reloptions` (owner rights, not `security_invoker`) | they **bypass** the `simulated_bets` RLS, so VIP protection inside a view must be explicit, and today it is not (§4.7) |

### 1.4 Field → reader / writer map (visibility and status fields)

| Field | Writers | Readers (engine) | Readers (SQL) | Readers (web) |
|---|---|---|---|---|
| `bots.maturity_label` (CHECK: experimental/beta/calibrated/testing/retired; **default `'active'` violates the CHECK**) | migrations; `admin_set_control('maturity_label')`; trigger `bots_set_retired_label` | signaler `:141` (Telegram = calibrated); `settlement.py:3325,3350,3376,3415,3492,3535`; `pick_generator.py:405`; `coolbet_placer.py:311,584,726,2685`; `coolbet_prekickoff_alert.py:48-155`; `coolbet_daily_summary.py:129`; `coolbet_daemon_healthcheck.py:150`; `export_bot_config.py:172`; scripts `_our_stats.py:67`, `backtest_day_ahead_picks.py:89-91`, `coolbet_market_mix_audit.py:149` | `bot_scoreboard` | `bot-aggregates.ts:332` PUBLIC {calibrated,beta}; `engine-data.ts:1491` HEADLINE {calibrated,beta,active}; `upcoming-picks.ts:186-187` (dead); `channel-reasons.ts:44` PERF_LABELS; `track-record/route.ts:48`; `performance-client.tsx:55` |
| `bots.show_on_picks` | migrations (356, 372, 402, 427, **432**); `admin_set_control('show_on_picks')` | `export_bot_config.py:188` (`published`) | **`picks_public_all`** (the only thing that gates on it); `bot_capabilities` (`COALESCE(c.published, b.show_on_picks)`) | `controls-context.tsx:122`, `bot-controls-cell.tsx:84,123`, `bot-sheet.tsx:191`, `channel-reasons.ts`, `admin-overview.ts:290`, `bot-board.ts:399` |
| `bots.show_on_performance` (427) | **migration only** (no control, no audit) | – | – | `engine-data.ts:394,415`; `performance/page.tsx:224`; `bot-legs/route.ts:35`. **Not** in `channel-reasons.ts`, `bot-board.ts:399` or `export_bot_config` |
| `bots.vip` (420) | **migration only** | – (engine uses the constant `VIP_BOTS` instead; smoke VIP-BOT pins the two together) | RLS policy | `engine-data.ts:412`, `performance/page.tsx:107,160,224`, `bot-legs/route.ts:35,49`, `channel-reasons.ts`, `control-specs.tsx:36`, `bot-board.ts:399` |
| `VIP_BOTS` (`bot_registry.py:325`) | code | signaler `:53,154`; `daily_pipeline_v2.py:4376-4378`; `ou_sharp_outlier.py:27,207` | – | – |
| `bots.hide_pending` (421, 424) | **migration only** | – | RLS policy | `engine-data.ts:413` (`hide_pending OR vip`), `performance/page.tsx:160`, `bot-legs/route.ts:49`, `bot-performance.ts:180,206` |
| BOTS_CONFIG `vip_exclude` | code (`daily_pipeline_v2.py:180`, only `bot_v10_1x2_newplus_v1`) | `daily_pipeline_v2.py:3997,4003` | – | – |
| `bots.retired_at` / `is_active` | migrations; `admin_set_control('retire'/'unretire')`; trigger | most loaders | `picks_public_all` (`retired_at IS NULL`) | `performance/page.tsx:223`, `channel-reasons.ts:65`, W8 |
| `PUBLISHED_ARMS` (`publish_picks_forward_test.py:241`) | code | publisher `:576-646,696-706`; `export_bot_config.py:474-539` | hard-coded as `ARRAY['live','consensus_anchor']` in `picks_public_all`, `picks_forward_test_public`, `_summary`, `_summary_by_market`, `_record_leg`, `_arm_rule`, `_anchor_clv`, `clv_sharp_legs`, `bot_ledger` (9 views) | `performance/page.tsx:242-250` `PUBLISHED_ARM_BOTS`; `bot-aggregates.ts:344-354` `LEDGER_BACKED_BOTS` |
| `coolbet_session_state.publishing_paused` (353) | Telegram webhook `/pausepicks` `/resumepicks` (**direct UPDATE, unaudited**, `webhook/route.ts:276-310`); `admin_set_control` (audited); `coolbet_state.set_publishing_paused :546-553` | `betting_pipeline.py:111`; `scheduler.py:2751`; `publish_picks_forward_test.py:1207` | trigger `coolbet_session_state_start_guard` (keeps the reason on a re-pause; does **not** guard resume) | ~12 admin files; `shadow-bots/queries.ts:449` defaults unreadable → **paused** while the engine fails **open** |
| `simulated_bets.signaled_at` / `signal_message_id` | signaler `_mark_signaled`, `:529-535` | signaler (dedup), `health_alerts.check_signal_silence` | – | webhook `sigplaced:`/`sigskip:` |
| `picks_forward_test.telegram_message_id` | publisher `attach_message_id :964` | – | `picks_public_all` / `_public` (grade-D filter) | – |
| `candidate_funnel` (384) | `daily_pipeline_v2.py:2793`, `scheduler.py:2869-2875` (publisher) | **none** | – | **none** |

---

## 2. Duplication: the same decision computed in several places

| ID | Decision | Copies (file:line) | Agree? |
|---|---|---|---|
| **D1** | **"Is this pick VIP-held?"** | (a) the VIP rules themselves: `daily_pipeline_v2.py` BOTS_CONFIG `bot_combined_1x2_ev5_v1` (EV ≥ 5%, Pinnacle required, 1.30–6.00, one per match) and `ou_sharp_outlier.py:33-38` (EV 5–15%, ≥ 12 h, latest power-de-vigged Pinnacle); (b) **`vip_exclude` re-derivation** `daily_pipeline_v2.py:3995-4012`: EV ≥ 0.05 on the *current* combined p (no Pinnacle or odds-band check); O/U: 0.05 ≤ EV ≤ 0.15 and ≥ 12 h on the *served* `ou_model_by_match` p, which is **not** the outlier job's p | **No.** (b) never looks at the VIP bot's actual pending picks, so a price move after the VIP pick lets the public bot through (measured, §4.1). The O/U copy uses a different probability source. Forward-test publisher: **no copy at all**. |
| **D2** | **"Is this pick sent to the public channel?"** (model arm) | signaler `is_public_eligible :212-225` (group has calibrated); `export_bot_config.py:172` (`live AND calibrated`); `channel-reasons.ts:84-86` (prose); SYSTEM_MAP §459; `bot_registry` one-liners | Code copies agree today. `channel-reasons` and the docs restate it by hand. |
| **D3** | **"Is this pick on /picks?"** | `picks_public_all` WHERE; `export_bot_config.py:188,506-539`; `bot_capabilities.publish`; `channel-reasons.ts:96-112`; admin `picksUnavailable` | View vs `channel-reasons`: **no**. The explainer says VIP is never on /picks, but the view has no VIP filter. |
| **D4** | **"Is this bot listed on /performance?"** | `performance/page.tsx:222-225`; `bot-legs/route.ts:35`; `channel-reasons.ts:114-129` (`PERF_LABELS` + VIP + ledger); `performance-client.tsx:55` (calibrated/beta only); SYSTEM_MAP §460 ("calibrated or beta only") | **No.** `channel-reasons`, `performance-client` and SYSTEM_MAP miss `show_on_performance`. |
| **D5** | **"Which labels count as public / headline?"** | `bot-aggregates.ts:332` {calibrated, beta}; `engine-data.ts:1491` {calibrated, beta, active}; `upcoming-picks.ts:186` {calibrated} (dead); `upcoming-picks.ts:187` {calibrated, beta, active} (dead); `channel-reasons.ts:44` {calibrated, beta}; `settlement.py:3325…` `!= 'experimental'`; `track-record/route.ts:48` alias of HEADLINE; `scripts/_our_stats.py:67` {calibrated, beta, active} | **No**: 4 different sets. `'active'` is illegal under the CHECK. Settlement counts testing **and retired** bots. |
| **D6** | **"Hide this bot's pending picks"** | RLS (`vip OR hide_pending`, mig 421); `engine-data.ts:413` (`hide_pending OR vip`); `performance/page.tsx:160`; `bot-legs/route.ts:49`; `bot-performance.ts:206`; `dropVipUnsettled` (`bot-aggregates.ts:401`, now uncalled) | Agree (all `vip OR hide_pending`). Five copies of one rule. |
| **D7** | **arm / grade / market → bot name** | SQL CASE in `picks_public_all`, `clv_sharp_legs`, `bot_ledger`, and 433's `bot_performance` (`433:229-232`); `funnel_rows` `publish_picks_forward_test.py:745-750`; `export_bot_config.py:506-539`; web `PUBLISHED_ARM_BOTS` (`performance/page.tsx:242-250`); `LEDGER_BACKED_BOTS` (`bot-aggregates.ts:344`) | **Latent disagreement.** For a NULL grade the SQL maps `consensus_anchor` → **B** and `funnel_rows` maps it → **C**. No NULL-grade consensus rows exist today (B 6, C 47, D 37), so the numbers do not differ yet. |
| **D8** | **Published-arm allow-list** | `PUBLISHED_ARMS` (Python) + `ARRAY['live','consensus_anchor']` hard-coded in 9 views (§1.4) + web `PUBLISHED_ARM_BOTS` | Agree today. Adding or removing an arm means editing all 11. 434 relies on this ("every view filters an explicit allow-list"). |
| **D9** | **Grade D "recorded, not sent"** | `scheduler.py:2850` (`grade == 'D'` skip); `funnel_rows :735`; views `NOT (grade='D' AND telegram_message_id IS NULL)` (`picks_public_all` and `picks_forward_test_public` spell it differently); `channel-reasons.ts:46-48` (`isGradeD` via a gate *string* `"recorded, not sent"`); `export_bot_config` `sent` | Agree. The admin copy keys on a display string. |
| **D10** | **Status words in customer-facing TEXT** | Telegram render `publish_picks_forward_test.py:214-217` (hard-codes "beta" / "testing" per grade); `/picks` `page.tsx:101-113`; `forward-test-picks.ts:207`; registry one-liners (`bot_registry.py` consensus_b "**BETA**", c "**TESTING**"); SYSTEM_MAP rows | Agree today. A status change leaves the channel text and /picks text wrong until someone edits the code. |
| **D11** | **Forward-test send loop** | `scheduler.py:2769-2860` (live + consensus + twins, records while paused) vs `publish_picks_forward_test.main() :1170-1239` (live only, header, refuses when paused) | Deliberately different, documented at `scheduler.py:2769`. Still two send loops to one channel. |
| **D12** | **Pause semantics** | engine `is_publishing_paused` fails **open** (`coolbet_state.py:542`); web `shadow-bots/queries.ts:449` defaults **paused**; `controls-context.tsx:114` → null | Display can disagree with behaviour when the read fails. |

---

## 3. Why there is no single "distribution" value today

For any bot, "who receives its picks" is the combination of:

```
Telegram public  = (sim bot) group-has-calibrated AND bot ∉ VIP_BOTS AND market ∈ _PUBLIC_MARKETS AND NOT paused
                 | (forward test) arm ∈ PUBLISHED_ARMS AND grade ≠ D AND NOT paused
VIP DM + channel = bot ∈ VIP_BOTS                                     (pause ignored, nothing recorded)
/picks           = (sim bot) show_on_picks AND not retired            (VIP / status NOT checked)
                 | (forward test) arm ∈ view allow-list AND NOT (grade D unsent)
/performance     = (calibrated|beta) OR vip OR show_on_performance, not retired
                 | forward-test hard-coded list, once published > 0
pending readable = NOT (vip OR hide_pending)                          (status NOT checked)
headline (engine)= maturity_label ≠ experimental                      (includes testing + retired)
headline (web)   = maturity_label ∈ {calibrated, beta, active}
```

`maturity_label` is only one input among several, and three of the outcomes (VIP, /picks, pending
readability) ignore it entirely. That is the structural cause of every drift in §4.

---

## 4. Drift from policy §3

### 4.1 VIP-held picks reaching the public (rule 6), measured

Query: every VIP-bot pick since 2026-09-24 joined to public surfaces on (match, market, selection),
public item before kickoff.

| VIP bot | Public source | Public time vs VIP time | Count |
|---|---|---|---|
| `bot_combined_1x2_ev5_v1` | `bot_v10_1x2_newplus_v1` on /picks (`vip_exclude` bot) | **after** (draw 02:35→09:06; away 05:35→11:35) | 2 |
| `bot_combined_1x2_ev5_v1` | `bot_v10_1x2_newplus_v1` on /picks | before (09:06 vs VIP 10:36) | 2 |
| `bot_combined_1x2_ev8_v1` (hide_pending twin) | `bot_v10_1x2_newplus_v1` | – | 1 |
| `bot_ou_sharp_early_v1` | forward test `consensus_anchor` grade C, **Telegram + /picks** | **after** (21:14→02:05 next day; 10:14→11:05) | 2 |
| `bot_ou_sharp_early_v1` | forward test `live`, Telegram + /picks | before (05:05 vs 06:44) | 1 |
| `bot_combined_1x2_ev5_v1` | forward test `consensus_anchor` C | ~same run (07:35:01 vs 07:35:30) | 1 |
| `bot_ou_sharp_2anchor_v1` (hide_pending twin) | forward test live 2 / consensus 3 | – | 5 |

No overlaps from `bot_v10_1x2` or `bot_high_roi_global_v2`. They have no `vip_exclude` and could leak the
same way. The public signaler excludes only VIP-bot *rows*, so a calibrated bot holding the same selection
would still post it.

### 4.2 VIP sends: not counted as sent, not deliverable, not pausable

- Delivery: `TELEGRAM_VIP_CHAT_ID` is unset on the VPS, so `send_telegram_vip` returns None
  (`telegram.py:195-197`). There are 0 Pro/Elite profiles with Telegram. The VIP product currently reaches
  no one. The `simulated_bets` row still exists, so the record is kept, but nothing proves a send.
- No send record: S5 and S6 write no `signaled_at`, message id or audit row. Dedup is in-process
  (`_LAST_SENT`), so a scheduler restart can double-DM. Rule 2 cannot be verified for this channel.
- S5 only runs on non-shadow pipeline runs (`daily_pipeline_v2.py:4471` returns early for shadow cohorts).
  The funnel shows `pipeline_shadow` "accepted" rows for the EV5 bot (8 in 7 d). I did not verify whether
  any of those write a `simulated_bets` row, and if they do, it was never sent. In any case the VIP send is
  coupled to the pipeline's control flow, not to the pick.

### 4.3 Bots whose fields disagree with their status (active bots, DB 2026-09-25)

| Bot | `maturity_label` | Really sent to | /picks | /performance | Disagreement with §3 |
|---|---|---|---|---|---|
| `bot_combined_1x2_ev5_v1` ⭐ | experimental | VIP DMs + VIP channel (by code; 0 delivered) | no | yes (VIP) | EXPERIMENTAL means nothing sent. Policy wants **"VIP · TESTING"**. |
| `bot_ou_sharp_early_v1` ⭐ | experimental | same | no | yes (VIP) | same |
| `bot_consensus_d_v1` | **testing** | nothing since 09-23 (21 unsent D; 16 sent before the re-tier) | only its 16 old sent rows | yes (ledger list) | TESTING but not sent. Handover §2: owner decided **D → EXPERIMENTAL**, not yet applied. |
| `bot_v10_1x2_newplus_v1` | testing | **/picks only**. Telegram = calibrated only | yes (432) | yes (`show_on_performance`) | TESTING should be "sent". Telegram and /picks disagree. `show_on_performance` is a second switch that just repeats the status. |
| `bot_high_roi_global_v2` | beta | **/picks only** (5 signaled rows in 30 d came from groups a calibrated bot also held) | yes (432) | yes | BETA should be "sent + headline". Its own picks never go to Telegram. |
| `bot_consensus_c_v1` | testing | Telegram + /picks | yes (by arm) | yes | `show_on_picks = false` (the flag has no effect for forward-test bots, 372). No behavioural drift, but the field lies. |
| `bot_consensus_b_v1` | beta | Telegram + /picks | yes | yes | `show_on_picks = false`, same lie as above |
| `bot_sharp_1x2_v1`, `bot_sharp_ou_v1` | testing | Telegram + /picks | yes | yes | `show_on_picks = true` has no effect. OK. |
| `bot_combined_1x2_ev8_v1`, `bot_ou_sharp_2anchor_v1` | experimental | nothing | no | no | Consistent. `hide_pending` is needed **only** because the RLS is not status-based (§4.5). |
| `bot_rating_1x2_v1` | experimental | nothing | no | no | Pending picks readable by anon (§4.5) |
| Retired `bot_v10_ou`, `bot_sharp_forward_test_v1` | retired | – | – | – | `show_on_picks = true` left set. |
| Retired `bot_high_roi_global_v2_newplus_v1` | retired | – | – | – | `show_on_performance = true` left set. |

Twin arms `bot_sharp_aligned_v1` / `bot_consensus_pinconf_v1` are in the working-tree registry but **not
in the DB** (migration 434 is uncommitted, #161).

### 4.4 Places where a second setting can drift from status

1. `show_on_picks`: can be ON for an experimental bot or a VIP bot. `admin_set_control` checks only
   active, fresh `bot_config`, sim ledger and a non-forward-test family (`413:522-549`). 432 set it by
   migration, and `control_changes` has **0 rows for show_on_picks**, so the flip is unaudited.
2. `show_on_performance`: migration-only and unaudited. It exists solely to express "TESTING is listed".
3. `vip` vs `VIP_BOTS`: two sources, held together by a smoke test only.
4. `hide_pending`: must be remembered for every twin that shares picks with a VIP bot. 421 and 424
   each fixed a "back door" after it was found.
5. `vip_exclude`: a per-bot config key that has to be added to every public bot. Only 1 of 3 public sim
   bots has it.
6. `PUBLISHED_ARMS` vs the 9 view allow-lists vs web `PUBLISHED_ARM_BOTS`.
7. Grade → status: the text in the Telegram render and on /picks hard-codes "beta"/"testing".
8. `retired_at` does not clear `show_on_picks` / `show_on_performance`, which is harmless only because
   every reader also filters `retired_at`.
9. `publishing_paused`: the VIP senders ignore it. The Telegram `/pausepicks` and `/resumepicks` bypass
   `admin_set_control`, so they are unaudited, and resume-publishing has no start guard (only
   placement-resume has one).

### 4.5 EXPERIMENTAL pending picks are publicly readable

`simulated_bets` "Public read" hides pending only for `vip OR hide_pending`, and anon holds `SELECT`
(404), with the anon key shipped in the browser bundle. Pending rows readable today: `bot_rating_1x2_v1`
(experimental) 2, `bot_v10_1x2_newplus_v1` (testing, fine) 11, `bot_v10_1x2` 1. A new experimental bot is
readable by default. Policy: EXPERIMENTAL is admin-only. The fix is a status-based policy: pending is
visible only when the bot is sent publicly (status ∈ testing/beta/calibrated AND NOT vip). That makes
`hide_pending` redundant.

### 4.6 Headline cohort

`dashboard_cache` ROI/CLV (`settlement.py:3325-3535`) filters `maturity_label != 'experimental'`, so it
**includes testing and retired bots**, against the rule "TESTING not in headline". It also uses the legacy
`simulated_bets.clv` (handover §4 says this is not the shared CLV). The web headline
(`engine-data.ts:1491`) uses {calibrated, beta, active}. **#159 is rewriting this right now**
(`settlement.py` is modified in the working tree). This is recorded for #159, and I propose nothing here.

### 4.7 Real-money-capable and customer paths missing a gate

- `picks_public_all` (owner rights, bypasses RLS) has **no VIP or status filter** on its model arm.
  `show_on_picks` alone decides. One admin toggle would publish a VIP bot's live picks.
- The forward-test publisher (S3/S4) has no VIP-held check (§4.1).
- S7 (in-play DMs, dormant behind an env flag) would DM **non-VIP** picks to every Pro/Elite user, which
  contradicts #148 "only VIP picks to pro users". It is one env var away from happening.
- No real-money path is in area B. The placer gates are area-D territory. One cross-area note: `signaled_at`
  is set on every bot's row in a group, and the placer/pick-queue may read it. I did not verify that.

### 4.8 Numbers shown from a non-shared computation

- The Telegram CLV footer `clv_footer_line()` / `get_elite_30d_clv()` (`telegram.py:41-100`) reads
  `dashboard_cache.elite_value_bets_30d`: all active bots, legacy `clv`. It is used only by S7 (dormant).
  `daily_pipeline_v2.py:4490` computes it on every run and never uses it.
- Grade status words in the send text (D10) are not numbers, but they are claims that do not come from the
  status field.

---

## 5. Dead, retired or unused (with evidence)

| Item | Evidence | Action |
|---|---|---|
| `fetchUpcomingPicks`, `PUBLIC_MATURITY_LABELS`, `SIGNED_IN_MATURITY_LABELS` (`odds-intel-web/src/lib/upcoming-picks.ts:186-300`) | grep finds no importer. The file header says it is off the read path, and that `PUBLIC_MATURITY_LABELS` is "still read by the ledger endpoint", which is false: `track-record` aliases `engine-data`'s HEADLINE | delete (keep `breakEvenOdds`, `placementTriggerOdds` and `fetchUserPickMarkStates`, which are live) |
| `workers/notify/telegram_bot.py` (`start_listener`) | no importer anywhere (the only hits are its own docstring) | delete |
| `_clv_for_footer = get_elite_30d_clv()` (`daily_pipeline_v2.py:4490`) | variable never read | delete the line (BOTS_CONFIG file is claimed by another session, see §7) |
| `dropVipUnsettled` (`bot-aggregates.ts:401`) | its only caller (`performance/page.tsx`) was removed in the #159 working tree | delete after #159 lands |
| `candidate_funnel` table | writers only (`daily_pipeline_v2.py:2793`, `scheduler.py:2870`). No SELECT in engine, web or scripts. 90-day retention. `ou_sharp_outlier` writes nothing to it, so the VIP O/U bot has no funnel | keep it (it is the audit trail #082 designed), but give it a reader (admin bot sheet "why not picked") and add the O/U sharp job as a source |
| funnel rows for retired bots under `pipeline_shadow` (`bot_aggressive`, `bot_high_alignment` ×2,289 in 7 d, …) | retired bots still evaluated in shadow passes (owner-approved "retired keep writing", bots-and-controls §2.4) | owner decision, not area B. Noted only |
| funnel rows for `bot_sharp_forward_test_v1` (retired 402) | from before the 402 split; the mapping now uses the market bots | none, ages out |
| `shadow_bets_anon_read` policy | no anon grant (404) | drop the policy for clarity |
| duplicate `bots` policies `public_read` / `Public read` | identical `USING (true)` | drop one |
| `bots.maturity_label` DEFAULT `'active'` | `'active'` violates the CHECK, so any INSERT that omits the label fails | default `'experimental'` (matches policy: new = admin-only) |
| `'active'` in `HEADLINE_MATURITY_LABELS`, `_our_stats.py:67`, `backtest_day_ahead_picks.py:91` | illegal value, matches nothing | remove |
| `show_on_picks = true` on retired `bot_v10_ou`, `bot_sharp_forward_test_v1`; `show_on_performance = true` on retired `bot_high_roi_global_v2_newplus_v1` | DB | clear, or remove the columns entirely (§6 R1) |
| `bot-aggregates.ts:324` comment "show_on_picks … which nothing reads yet" | stale since 361 | fix with R1 |
| SYSTEM_MAP §459/§460 ("/performance = calibrated or beta only") and `channel-reasons.ts` header | stale since 420/427 | fix with R1 |

---

## 6. Refactor candidates

Each lists the owner rule it serves (§3 numbering from the handover), the risk, and dependencies.
**"Twin + owner OK"** marks anything that changes what a live bot publishes.

### R1 — One distribution function derived from status + VIP (core of area B)
Create a single SQL function or view, `bot_distribution(name)` → `{admin, performance, picks, telegram_public,
vip_channel, headline, pending_public}`, computed **only** from `maturity_label` (renamed `status` later),
`vip`, `retired_at`, and the forward-test arm/grade. Every reader in §1 (S1, S3, S5, S6, the RLS policy,
`picks_public_all`, W3–W9, `export_bot_config`, the settlement headline) reads it. Then retire
`show_on_picks`, `show_on_performance` and `hide_pending` (compatibility columns for one release, then
drop). Python and TS read the view; the Python `VIP_BOTS` constant becomes a DB read or a generated file.
- Serves rules 1, 2 and 5 (one shared computation, deprecate the second sources).
- Risk: **high blast radius.** It changes what /picks, Telegram and /performance show for
  `bot_high_roi_global_v2` and `bot_v10_1x2_newplus_v1` (both would start going to Telegram as BETA and
  TESTING) and for D (off /performance, or kept as EXPERIMENTAL "recorded"). **Needs an owner OK** on what
  "sent" means for a sim-ledger TESTING bot: Telegram, /picks, or both. That is open today, because 432 said
  "sending = /picks".
- Depends on #155 (status rollout), #159 (`bot_performance`, the headline) and #161 (arms). Do it after
  those rows close.

### R2 — Replace `vip_exclude` with "a VIP bot holds a pending pick on this selection"
One predicate, `vip_held(match, market, selection)`, is true when a `vip` bot has a pending
`simulated_bets` row on the selection. Apply it in the pipeline (all public sim bots, not only
`bot_v10_1x2_newplus_v1`), in the forward-test `select()`, in the signaler candidate query and in
`picks_public_all`. It also needs a reverse rule for picks already public before the VIP bot fires: the
VIP bot should skip a selection that is already public, otherwise the paid channel sells free picks. That
last part is an owner call.
- Serves rule 6 (VIP split) and rule 5.
- Risk: changes the **pre-registered forward test's** selection (the live arm). A VIP-held skip in the live
  arm is a rule change → **twin + owner OK + pre-registration amendment**. For `bot_v10_1x2` /
  `bot_high_roi_global_v2` this is a live-bot rule change → **twin + owner OK**. `bot_v10_1x2_newplus_v1`
  already intends this behaviour, so it is a bug fix for it.
- Files: `daily_pipeline_v2.py` BOTS_CONFIG and gate (claimed by another session), `publish_picks_forward_test.py`
  (claimed, #161).

### R3 — Route every pick send through one audited sender
`send_pick(channel, bot, pick_ref, text)` does four things: checks `publishing_paused`, checks the channel is
allowed for the bot's distribution (R1), writes a `pick_sends` row (channel, pick FK, message id or "no
recipients", ts), and dedups in the DB rather than in memory. It replaces S1, S3, S4, S5 and S6
(and S7 if that is ever revived).
- Serves rule 2 (anything sent is counted, provably) and fixes the §4.2 and §4.4-9 gaps.
- Risk: medium. Every send path changes at once; a bug silences the public channel (RELIABILITY_LEDGER
  "second code path"). Ship with parity smoke tests per sender. It also collapses D11 (two forward-test send
  loops) into one.
- Depends on R1 for the allow check (the pause and audit parts can land first).

### R4 — Status-based RLS for pending picks
Policy: pending rows are visible to anon only if `bot_distribution.pending_public`, i.e. the bot is
TESTING+ and not VIP. Drop `hide_pending`. Also add an explicit VIP/status filter inside `picks_public_all`,
because owner-rights views bypass RLS (or make the views `security_invoker`).
- Serves rule 1 (EXPERIMENTAL admin-only) and rule 6.
- Risk: low to medium. The web already reads with the service role for admin, so check that no *public*
  surface relies on anon reading experimental pending rows. `LoggedInHistorySection` and `track-record`
  read settled rows.

### R5 — One arm registry for the forward test
A table `forward_test_arms(arm, rule_version, published bool, bot_name, grade, market)` becomes the only map.
The 9 views join it instead of hard-coding `ARRAY[...]` and CASE. `PUBLISHED_ARMS`, `ARM_RULE_VERSION`,
`funnel_rows`' map, `export_bot_config`'s map, the web `PUBLISHED_ARM_BOTS` and `LEDGER_BACKED_BOTS` all read
it. That fixes the D7 NULL-grade B/C mismatch.
- Serves rule 5.
- Risk: medium. 9 views are recreated. The pre-registration pins `rule_version` strings, and those must not
  change (the smoke test PICKS-FORWARD-TEST-RULE-LOCKED).
- Depends on #161 (twin arms add two arms right now) and #159 (`bot_performance` has its own CASE). After
  both close.

### R6 — Status words from the status field, not code
The Telegram render (`publish_picks_forward_test.py:214-217`), `/picks` grade chips (`page.tsx:101-113`),
`forward-test-picks.ts:207` and registry one-liners read the bot's status. Serves rules 1 and 5. Low risk,
text only. The publisher file is claimed (#161).

### R7 — Make `channel-reasons.ts` render R1's output instead of re-deriving it
Once R1 exists, the explainer prints the reason string the function returns. Until then the minimum fix is
to add `show_on_performance` and correct the VIP-/picks claim. Serves rule 5. Low risk (admin-only).

### R8 — Audit the Telegram pause commands
Have `/pausepicks` and `/resumepicks` call `admin_set_control('publishing_paused', …, source='telegram')`
instead of a raw UPDATE, and make the web default on an unreadable pause match the engine (or show "unknown").
Serves rule 5 (audited controls). Low risk. The `admin_set_control` resume check for source `web` needs a
`telegram` equivalent.

### R9 — Apply the decided status changes (data only; decisions already made)
`bot_consensus_d_v1` → experimental (owner, #155). The VIP bots → testing with VIP ("VIP · TESTING", #155).
Clear `show_on_*` on retired rows. Change the `maturity_label` default to experimental. This belongs to
#155's rollout. List it there, not as a new task.
- Risk: VIP bots as TESTING puts them in the /performance TESTING group, and the settlement headline
  (§4.6) would then count them until #159 fixes that cohort. **Sequence R9 after the #159 headline fix.**

### R10 — Deletions (§5)
`fetchUpcomingPicks` and the two label sets, `telegram_bot.py`, `_clv_for_footer`, `dropVipUnsettled`,
the dead policies and the `'active'` label. Zero behavioural risk. Serves the "one shared computation" rule
by removing copies that could be re-imported.

### R11 — Give `candidate_funnel` a reader and full coverage
Add `ou_sharp_outlier` as a source (with a `drop_vip_held` / `already_public` step once R2 exists) and show
"why not picked" in the admin bot sheet. Serves auditability. Low risk.

---

## 7. Files other sessions are editing right now (do not touch before their rows close)

Working-tree evidence (`git status` in both repos, 2026-09-25):

| File | Row | My candidates that depend on it |
|---|---|---|
| `workers/jobs/daily_pipeline_v2.py` BOTS_CONFIG | #152/#155 (bots) | R2 (the `vip_exclude` gate at `:3995-4012`), R3 (S5 send at `:4535`), R10 (`_clv_for_footer :4490`) |
| `workers/registry/bot_registry.py` (modified) | #161 | R1/R3 (`VIP_BOTS`), R6 (one-liners) |
| `docs/SYSTEM_MAP.md` bot rows (modified) | #161/#155 | R1 (§459/§460 stale), R7 |
| `scripts/publish_picks_forward_test.py` (modified, +153 lines) | #161 | R2 (forward-test VIP check), R3 (D11), R5, R6 |
| `workers/jobs/settlement.py` dashboard_cache (modified) | #159 | §4.6 headline, R9 sequencing |
| `scripts/export_bot_config.py` (modified) | #161 | R1, R5 |
| `supabase/migrations/433_one_bot_performance.sql`, `434_forward_test_twin_arms.sql` (untracked) | #159, #161 | R5 (a new CASE copy in 433 `:229-232`) |
| web `src/app/(app)/performance/page.tsx`, `performance-client.tsx`, `performance-leaderboard.tsx`, `bot-aggregates.ts`, `engine-data.ts`, `admin-attention.ts`, new `bot-performance.ts`, new `api/performance/bot-legs/route.ts` | #159/#157/#155 | R1 (W3–W8), R4, R10 (`dropVipUnsettled`, `HEADLINE_MATURITY_LABELS` 'active') |

Not claimed (free to change once the refactor starts): `coolbet_signaler.py`, `betting_pipeline.py`,
`ou_sharp_outlier.py`, `workers/notify/telegram.py`, `scheduler.py` publisher job (it is modified, so
check its owner first), `picks_public_all` and the other public views, the web `/picks`,
`forward-test-picks.ts`, `upcoming-picks.ts`, `api/v1/upcoming`, `channel-reasons.ts`, the Telegram webhook.

---

## 8. Measurements used (all read-only SELECTs, 2026-09-25)

- `bots` visibility columns for 30 rows (active plus retired rows with any flag set): table in §4.3.
- `picks_forward_test` by arm, grade, sent: live 99 sent; consensus B 6/C 47 sent; D 16 sent (before the
  re-tier) + 21 unsent; junk 617 unsent.
- `simulated_bets.signaled_at` in 30 d by bot: `bot_v10_ou` (retired) 92, `bot_v10_1x2` 50,
  `bot_high_roi_global_v2` 5, retired others 22. Signaled rows are marked group-wide, so the counts show
  group membership, not authorship.
- VIP overlap query: §4.1.
- `candidate_funnel` 7 d by source/bot/step: `drop_vip_held` 13 (pipeline) + 12 (shadow) for
  `bot_v10_1x2_newplus_v1`. There is no funnel for `ou_sharp_*`.
- `control_changes`: only the five migration seed rows. No show_on_picks, maturity or publishing flips have
  ever gone through the audited path.
- VPS env (counts only, no values): `TELEGRAM_VIP_CHAT_ID` 0, `TELEGRAM_PUBLIC_CHANNEL` **2 lines**
  (duplicate key in `/opt/odds-intel-engine/.env`; which one wins depends on the loader, so check it),
  `OPERATOR_PICK_ALERTS=true` 0.
- `coolbet_session_state`: `publishing_paused = false`, `placement_paused = true`.
