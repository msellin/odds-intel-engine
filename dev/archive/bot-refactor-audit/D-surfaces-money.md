Parent row: [[#162]] BOT-REFACTOR-CLEANUP (PRIORITY_QUEUE.md) — phase 1 read-only audit, area D.

# D — Surfaces & real-money paths (audit 2026-09-25, READ-ONLY)

Scope: every web surface that shows bots/picks (which READER uses which SOURCE), why /admin/bots is
slow, every path that can stake real money and the gates each one inherits, and the `real_bets`
question from `dev/active/unified-bot-model-HANDOVER.md` §7. It builds on the 5A inventory,
`docs/BOTS_AUDIT_2026_09_24.md` and `dev/active/unified-bot-model-phase5-schema-draft.md`, and
records only the current state plus what changed after them.

Method: code read at engine HEAD `ea51c30b` and web `5307c62`, **plus the uncommitted #159 working
tree** (flagged where it matters). The DB was read with read-only SELECTs
(`default_transaction_read_only=on`), and latency was measured from the VPS with curl. Nothing was
placed, executed, staged or edited.

**Live money state (DB, 2026-09-25 ~11:45 UTC):**
- `placement_paused = t`, `real_money_armed = f`.
- 11 `coolbet_placer_bots` rows, 0 ON, 1 locked (`bot_coolbet_ou_model_v1`).
- `placer_heartbeats` has 0 rows, and `manual_placement_queue` has 0 rows (ever).
- The last confirmed real stake was 2026-09-13 16:22. The last `real_bets` row is 2026-09-15.
- No placer launchd job is loaded. Loaded jobs are `cdp-watch`, `coolbet-cdp-selfheal` and
  `vps-postgres-tunnel`. The router and UI-placer `--execute` plists sit in `local/launchd/paused/`.
  The mac-daemon and router-monitor plists are in `local/launchd/retired/`.

---

## 0. Headline findings (in order of money risk)

1. **The placers' per-bot edge floor is a SECOND copy that covers 2 of the 11 capable bots.**
   - `scripts/place_coolbet_ui.py:56` `BOT_THRESHOLDS` lists only `bot_coolbet_1x2_model_v1` (0.10)
     and `bot_coolbet_ou_model_v1` (0.08). Every other bot falls back to **0.03** (`:790`, `:1133`,
     and the router at `best_price_router.py:610`).
   - The registry and `bot_config` say `bot_ou35_model_v1` = 0.08 and
     `bot_unified_gate_1x2_paper_v1` = 0.10 (odds ≥ 2.80).
   - So if the owner switches either of them ON, the UI placer stakes at 3pp: a looser gate than the
     bot's own record.
   - The handover already warned "newly enabled bots would use the placer's default 3% floor". It is
     still true.
   - Needs: read the floor from `bot_config` (§5 R1).
2. **The two real-money executors apply different gates to the same pick.**
   - The UI placer (`place_for_bot` → `stage_bet`) applies `BOT_THRESHOLDS`, and the per-market odds
     floor at `odds_at_pick`.
   - The router (`decide_book`) applies `BOT_THRESHOLDS` **plus** the selection-aware model floor
     `clears_edge_floor` (1x2 13% / home-underdog 10% / O/U 8%), because `apply_selection_floor`
     defaults to True (`best_price_router.py:131,180`).
   - `pick_generator` passes `apply_selection_floor=False` for the sharp bots
     (`pick_generator.py:259-261`), which was the [[#007]] fix. The router's `route()` does not.
   - Result for a sharp bot: the router re-creates the #007 bug on the placement side and places
     nothing, while the UI placer places at 3pp with a 2.80 1x2 odds floor that the bot never used
     (its `odds_min` is 1.01).
   - Neither executor enforces `odds_max` (`bot_trigger_1x2_sharp_tight_v1` has a cap of 2.5).
   - Both also measure edge in probability points (`cal_prob − 1/odds`,
     `coolbet_ui_placer.py:1278-1330`), while the sharp bots' `edge_percent` is multiplicative EV
     (`coolbet_placer.py` `model_edge` docstring; SYSTEM_MAP §1).
   - **So for 9 of the 11 capable bots, the set a switched-on bot would stake is not the set its
     record was measured on.** This is a policy §3 rule-4 problem: real money would silently run a
     different rule than the bot being scored.
3. **The router path lacks two gates the UI placer has** (RELIABILITY_LEDGER "second code path").
   - No account verification or reconcile. `fetch_account_holds` / `reconcile_account_to_real_bets`
     (`place_coolbet_ui.py:438,518,1107`) is called only from `place_coolbet_ui.main`. So a
     hand-placed Coolbet bet that was not logged is invisible to the router's exposure check.
   - No single-run lock (`single_run_lock`, `place_coolbet_ui.py:721`). The router's Coolbet arm
     drives the same CDP Chrome tab and could interleave with a UI-placer run.
   - Both plists are paused today, so this is latent.
4. **Daily caps do not count Unibet.** `spent_today()` (`place_coolbet_ui.py:397-419`) sums only
   Coolbet. Its two sources are `coolbet_placement_attempts` and `real_bets` with
   `bookmaker='Coolbet' AND notes LIKE 'auto ticket=%'`. The router's Unibet arm writes
   `real_bets(bookmaker='Unibet-Site', placed_real=TRUE)` (`best_price_router.py:343-358`). Within
   one pass the router counts Unibet in memory (`:665-676`), but across passes Unibet stakes never
   reach the €800 / 80 cap.
5. **547 paper rows (€3,124 of stake) sit in `real_bets` with `placed_real IS NULL`.** They break
   down as 462 `auto ticket=None…`, 55 `inplay-auto…` and 30 `auto-combo…`, placed 2026-05-20 to
   2026-09-08. By construction no bet was POSTed for any of them. Every reader counts them as money
   (`placed_real IS NOT FALSE`):
   - `/admin/real-bets` (`admin-money.ts:200-204`);
   - the Overview 30-day card (`admin-overview.ts:157-160`);
   - the per-bot "Bet made" column (`bot-board.ts:769-778`);
   - the exposure guard (`place_coolbet_ui.py:338-350`).
   This dates from before 2026-09-24. Stage 2 added `placed_real` but never back-filled these rows.
   The fix needs owner OK (money records).
6. **`/admin/bots` slowness is not the database.** Each view answers in 12–185 ms, but a cold load
   does about 40 PostgREST calls, with `loadBotBoard` and `loadControlState` run **twice**. Every
   call leaves the box and comes back through Cloudflare, and there are two remote Supabase auth
   round-trips (§1.3).
7. **One live operator send path ignores the kill switch and runs on a dead signal.**
   - `coolbet_prekickoff_alert` runs every 5 min (`scheduler.py:3767`).
   - It fires "🚨 PLACE MANUALLY — daemon down" whenever `mac_daemon_last_tick_at` is stale. That
     column has been frozen at 2026-09-10 12:22 since the daemon was retired, so the job is
     permanently in "unhealthy" mode (`prekickoff_last_run_result.healthy=false` on every run).
   - It reads neither `placement_paused` nor `OPERATOR_PICK_ALERTS`. Today it finds 0 candidates,
     so it is quiet only by coincidence.
   - It is a human-in-the-loop money prompt with its own copy of the gates
     (`coolbet_prekickoff_alert.py:116-200`).

---

## 1. Surfaces — which reader uses which source

### 1.1 Public (reader → source). #159 is mid-flight: numbers marked ★ come from the uncommitted working tree

Migration 433 (`bot_performance`) is **not applied** yet; `_schema_migrations` has 434 but not 433.
The web working tree already reads it from the untracked `src/lib/bot-performance.ts`. So until #159
lands, the committed/deployed web still runs the older per-page computations listed in migration
433's header (`engine-data` `execPnl`, `dashboard_cache.bot_breakdown`, `bot_scoreboard.roi_unit`,
`simulated_bets.clv`/`clv_pinnacle_devig`).

| Surface / number | Reader (file:line) | Source relation | Basis |
|---|---|---|---|
| /performance hero ROI all-time / 30d, "N picks" ★ | `engine-data.ts:1604` `getCalibratedHeadlineStats` → `bot-performance.ts:222` `getHeadlineFlat` | `bots` → `bot_ledger` (`source='sim'`, `in_record`) | `pnl_unit_public` (flat, odds_public); no CLV |
| hero footer "N strategies live / retired" | `performance-client.tsx:53-58` | `bots` via `getAllBotsFromDB` (`engine-data.ts:419`, 30-min cache) | label counts |
| leaderboard rows (ROI, P&L, CLV, pending) ★ | `performance/page.tsx:83-118,223-225` ← `getBotPerformance` `bot-performance.ts:51-87` | `bot_performance` (mig 433) | flat, odds_public; CLV `clv_public` (sharp anchor) |
| forward-test rows: rule label, re-checked count, own-book CLV | `engine-data.ts:2429` `getForwardTestBotRecord` | `picks_forward_test_bot_record` | `clv_margin_corrected` = the labelled secondary |
| row detail / bet list ★ | `/api/performance/bot-legs` → `bot-performance.ts:150` | `bot_ledger_display` | odds_public, `clv_anchor_public` |
| cumulative P&L chart | `getDashboardCache` `engine-data.ts:1761` | `dashboard_cache` (written by `settlement.py:3406-3438`, which another session is editing) | cohort ≠ hero: includes in-play and testing bots, no since-date |
| streak tiles, calibration table | `getPublicPerformanceExtras` `engine-data.ts:2120` | `simulated_bets` (90 d) | cohort ≠ hero |
| history teaser (anonymous) | `getRecentSettledBets` `engine-data.ts:1800` | `simulated_bets` (no cohort filter) | outcome only |
| /track-record | `track-record/page.tsx:4` | redirect to /performance | — |
| `/api/v1/track-record` meta | `route.ts:154-162,219-249` → `getHeadlineFlat` | `bot_ledger` | the same as the hero |
| `/api/v1/track-record` rows | `route.ts:116-132` | `simulated_bets` ⨝ `bots` (**no retired filter, no `in_record`, `created_at`**) | `bot_ledger.odds_public` via `getPublicPrices` |
| `/api/v1/upcoming` | `forward-test-picks.ts:255-262` | `picks_public_all` | its `clv` is **same-book** close, not the sharp anchor |
| landing ledger strip | `page.tsx:56-64` fetches `/api/v1/track-record?limit=1` | as above | public basis |
| landing "matched-window audit" and competitor table | `page.tsx:181-278` fetches `raw.githubusercontent…/ledger/comparison_*.json` | engine `scripts/_our_stats.py:54-125` over `simulated_bets` | **`odds_at_pick_live`, drops legs with none, includes retired bots** |

Computed but never displayed on /performance:
- `getModelV2Stats` (`engine-data.ts:2206`, Kelly, legacy `clv`);
- `extras.botRecentRoi` (`engine-data.ts:2093`);
- `getTrackRecordStats` outside its cold-cache fallback;
- `buildBotStats` / `buildPerformanceStats` (`bot-aggregates.ts:139,258`).

### 1.2 Admin

| Page | Reader | Sources | Scoring used |
|---|---|---|---|
| `/admin/bots` board | `bots/page.tsx:36-42` → `bot-board.ts:283-306` `loadBotBoard` (6 reads) + `:417` `loadControlState` (5 reads) | `bot_scoreboard`, `bot_config`, `bot_capabilities`, `bots`(retired), `bot_weekly`, `bot_market_stats`; `coolbet_session_state`, `coolbet_placer_bots`, `bots`, `placer_heartbeats`, `control_changes` | `bot_scoreboard`: `roi_unit` at recorded `odds_at_pick`, `clv_mc`/`clv_pin*` from `bot_ledger` (mig 410; `clv_mc` is NULL for every simulated_bets bot — `410_unified_bot_views.sql:93`). The working tree (#159) switches the board to `bot_performance` (`roi_own`, `clv_anchor_*`). |
| bot sheet → picks table | `/api/admin/bot-ledger` → `bot-board.ts:648` `loadBotPicks` | `bot_ledger_display` (page), `real_bets` by `bot_id` (`:769`), `odds_snapshots` × `SNAPSHOT_BOOKS` in parallel (`:786-806`) | "Bet made" = `real_bets` linked by `shadow_bet_id`/`simulated_bet_id`, else by (match, market, selection) (`:603-621`). The key match is not vocabulary-canonical (`o/u` vs `over_under_25`). |
| real-money ladder / CAN STAKE | `src/lib/bot-controls/ladder.ts` | the control reads above + `bot_config.placeable` | **A TS re-implementation of the engine conjunction** (`placement_gate.effective_allowlist` + `coolbet_control.can_stake`). It does not apply the engine's 36 h `bot_config` staleness rule (`placement_gate.py:113,143`). |
| `/admin/shadow-bots` (Pick queue) | `shadow-bots/queries.ts:180-230` (cached 60 s) | `bots`, `coolbet_placer_bots`, `real_bets` (today), `shadow_bets_unique` ×2, `odds_snapshots`, **`shadow_bot_scoreboard`** (mig 360, `:407`) | Its "track"/lead chip uses `shadow_bot_scoreboard.clv_mc_mean` (`picks-table.tsx:40-50`) — a **third** per-bot scoring. The per-pick PLACE verdict uses the registry floors (`ENGINE_BOT_FLOORS`, generated) and `botEdgeThreshold` (`coolbet-edge.ts:85-127`, default **0.08**) — **not** what the placer applies (default 0.03). It shows only `shadow_bets` picks, so simulated_bets bots (v10, VIP #1) never appear in the queue. |
| Pick queue "Place €X" | `components/shadow-bots/place-action.tsx` → `/api/admin/real-bet/route.ts:87` | rpc `record_manual_real_bet` (mig 407) | writer: `placed_real NULL`, exact same-day dedupe |
| `/admin/real-bets` | `admin-money.ts:190-215` `loadMoney` (paged) | `real_bets` (`placed_real IS NOT FALSE`), `promo_terms` | € ledger; `DAILY_MAX_*` constants are a **web copy** of the engine env defaults (`admin-money.ts:29-30`) |
| Overview KPI / charts / attention | `admin-overview.ts:235` `loadOverview` | **calls `loadBotBoard` + `loadControlState` again** (`:240-243`), plus `readLive` (`:198-233`: `feed_status`, `pipeline_job_latest`, dq, `simulated_bets` pending ⨝ `matches` (≤5000), `real_bets` unconfirmed, `feed_book_stats`, `book_footprint`), then `loadJobCadence` (`admin-jobs.ts:159-192`, `pipeline_runs` 4 days ≈ 12.5k rows in sequential 5k pages), plus `realBetsWeekly` (`:151-183`) | The 30-day money card (`:165-171`) is a **second copy** of `admin-money.realMoneyWindow` (`admin-money.ts:238-258`); it has the same rule but is computed separately. |
| Admin shell (every admin page) | `admin/layout.tsx` → `admin-shell-data.ts:26` | `loadOverview(null)` in `unstable_cache` for 60 s | makes the whole Overview a dependency of every admin page |

### 1.3 Why /admin/bots is slow — evidence

**DB timings (Mac → tunnel → VPS Postgres, including about 12 ms of round trip):**

| Read | Time |
|---|---|
| `bot_scoreboard` | 184 ms |
| `bot_weekly` | 129 ms |
| `bot_market_stats` | 121 ms |
| `bot_config` | 12 ms |
| `bot_capabilities` | 14 ms |
| `pipeline_job_latest` | 154 ms |
| `bot_ledger_display` page of 51 rows for one bot | 61–69 ms |

The DB is not the bottleneck.

**Transport, from the VPS:** `curl https://api.oddsintel.app/` takes 0.12–0.30 s, against 0.04–0.06 s
for `http://127.0.0.1:3012/`. The web's `NEXT_PUBLIC_POSTGREST_URL` is `api.oddsintel.app`, whose AAAA
records are Cloudflare. **Every server-side PostgREST call goes out to Cloudflare and back into the
same box**, which adds about 80–250 ms per call.

**Call count for one cold `/admin/bots` load (shell cache miss), counted from code:**
- **Layout:**
  - `requireSuperadmin`: 1 remote Supabase `auth.getUser` + 1 profile read.
  - `loadFleetStatus`: 1.
  - `loadShellExtras` → `loadOverview`:
    - `loadBotBoard`: 6;
    - `loadControlState`: 5;
    - `readLive`: 7, plus dq, plus `loadJobCadence` with 3 **sequential** pages of `pipeline_runs`
      (about 12.5k rows, about 0.7 MB through Cloudflare);
    - `realBetsWeekly`: 1.
- **Page:**
  - `superadminGate`: a second `auth.getUser` + profile.
  - `loadBotBoard` (6) and `loadControlState` (5) **again**.
- **Total:** about 40 PostgREST calls, 2 remote auth round-trips, and a sequential depth of about
  8–10 hops, so roughly 1.5–3 s before render on a cold cache.

**Payload:** the board then serialises about 300 kB of JSON into the client component's props:
- `bot_config` 142 kB;
- `bot_scoreboard` 62 kB;
- `bot_weekly` 45 kB;
- `bot_market_stats` 26 kB;
- `bot_capabilities` 23 kB.
`bot_config` carries long descriptions and gate arrays the board only partly needs.

**Opening a bot** costs 2 more sequential hops (the ledger page, then `real_bets` + 4–6
`odds_snapshots` in parallel). If `bot_ledger_display` errors, `readLedgerPage` falls back to
`bot_ledger`, which adds another hop.

**Noise, not a cause:** the Feeds page's 60 s `router.refresh` makes Next prefetch
`/admin/bots?_rsc` once a minute (nginx access.log, 1.5 kB responses, referer `/admin/feeds`).

**Recommended fixes (web-only, low risk):**
- (a) Point the server-side PostgREST client at `http://127.0.0.1:3012` (a server-only env var; the
  browser keeps the public URL).
- (b) Have the shell read a slim attention query instead of the full `loadOverview`, or wrap
  `loadBotBoard` / `loadControlState` in `React.cache` so the layout and page share one fetch per
  request.
- (c) Check superadmin once per request (`React.cache` around the gate).
- (d) Select only the `bot_config` columns the board renders.
- (e) Measure first with a `Server-Timing` header.

`bot-board.ts` and `admin-overview.ts` are being edited right now (#159 / #139 round 6). See §6.

---

## 2. Real-money paths — inventory

| # | Path (entry → money primitive) | Runs where / how | Reads | Writes | Bots | Live? |
|---|---|---|---|---|---|---|
| P1 | `scripts/place_coolbet_ui.py main` (`:957`) → `place_for_bot` (`:770`) → `coolbet_ui_placer.stage_bet` (`:1346`) → `place()` (`:1089`) | Mac launchd `com.oddsintel.coolbet-ui-placer --all-enabled --execute`, **paused** | `shadow_bets_unique` (`load_picks :653`), `coolbet_placement_attempts`, `real_bets`, the Coolbet account (CDP) | `coolbet_placement_attempts`, `real_bets` (`placed_real=TRUE` after balance delta, `ui_placer :1678`), account-sync `real_bets` INSERT (`:634`), `user_pick_marks`, `placer_heartbeats` (`:1000`) | `effective_allowlist()` | paused |
| P2 | `best_price_router.route` (`:443`) → `_dispatch_coolbet` (`:377`) → `stage_bet`, or `_dispatch_unibet` (`:263`) → `unibet_placer.place_bet` (`unibet_placer.py:65`) | Mac launchd `com.oddsintel.best-price-router --execute` + env `ROUTER_ALLOW_REAL`, **paused** | `shadow_bets_unique` via `load_picks`, `odds_snapshots` (`_latest_book_odds :57`, with anchor sanity), `real_bets` | `real_bets` (Unibet `placed_real` TRUE/NULL `:343`; Coolbet via `stage_bet`), `coolbet_placement_attempts` (Coolbet arm), `placer_heartbeats` (`:750`) | `effective_allowlist()` when real, else `placement_path_bots()` | paused |
| P3 | `scripts/place_coolbet_bets.py --execute` → `coolbet_placer.place_all_bets` (`:2097`) → `_place_bet_api` (`:1962`, POST `/s/bets/bets`) | hand-run CLI only | `simulated_bets` (`load_qualified_bets :443`) | `real_bets` | `simulated_bets` bots → **none capable**, so the per-pick gate at `:1995` always refuses | effectively inert |
| P4 | VPS `_drain_manual_placement_queue` (`scheduler.py:1890`, every 10 s `:3331`) → `place_bet_by_id` (`coolbet_placer.py:2908`) | VPS scheduler | `manual_placement_queue` (**0 rows ever**), `simulated_bets` | `real_bets` (paper, `placed_real=False`) | — | runs, idle; `MANUAL_PLACE_EXECUTE=False` (`:2905`) |
| P5 | `place_all_inplay_bets` (`coolbet_placer.py:2734`), called by `inplay_bot.py:667` | only if `INPLAY_STRATEGIES_ENABLED` (`live_poller.py:565`) | `simulated_bets` | `real_bets` (`placed_real=False`, no POST, `:2872`) | — | off |
| P6 | combo path `_place_combo_bets` (`:2452`) | inside P3/P4 | `simulated_bets` combos | `real_bets` (`placed_real=False`, never POSTs) | — | inert |
| P7 | Manual: operator places on the book, then logs it via Pick queue "Place €X" → `record_manual_real_bet` | web | — | `real_bets` (`placed_real NULL`) | any shown pick | live (6 rows 09-08..09-15) |
| P8 | Account reconcile `reconcile_account_to_real_bets` (`place_coolbet_ui.py:518`) | inside P1 only | Coolbet account tickets | `real_bets` direct INSERT (`:634`) | — | paused with P1 |
| P9 | Human prompts that ask the owner to place: `coolbet_prekickoff_alert` (VPS every 5 min, `scheduler.py:3767`); `coolbet_signaler` operator prompt (`coolbet_signaler.py`, off by default `OPERATOR_PICK_ALERTS`; the public channel stays on) | VPS | `simulated_bets` ⨝ `bots` (calibrated), `real_bets` | Telegram | calibrated sim bots | prekickoff **live**; signaler public live |
| — | `coolbet_mac_daemon` (`_tick :513` → `place_all_bets(execute=False)`) | **retired** 2026-09-10; plist in `retired/` | — | — | — | only `_drain_operator_commands` (`:128`) is still imported, by `coolbet_feed_watchdog.py:842` |

### 2.1 Gate matrix — which path inherits which gate

✓ = enforced before money moves · ✗ = missing · ~ = different rule · n/a

| Gate (owner name) | P1 UI placer | P2 router → Coolbet | P2 router → Unibet | P3 API/CLI | P7 manual | P9 prekickoff prompt |
|---|---|---|---|---|---|---|
| placement_paused (fails closed) | ✓ run `:990` + pick `ui:1590` | ✓ run `:494` + pick (stage_bet) | ✓ run + pick `:305` | ✓ run `:2134` + pick `:1995` | n/a (human) | **✗** |
| real_money_armed | ✓ | ✓ | ✓ | ✓ | n/a | ✗ |
| capable (`placement_path_bots`, 36 h config) ∩ eligible (`coolbet_placer_bots`) | ✓ `:978` + pick | ✓ `:516` + pick | ✓ pick | ✓ pick (refuses every sim bot) | n/a | ✗ (sim bots) |
| maturity | ✗ (none in `load_picks`; only mirror jobs select `calibrated` upstream) | ✗ | ✗ | ✓ `COOLBET_RECORD_ALLOWED_MATURITY` | n/a | ✓ calibrated |
| per-bot edge floor | ~ `BOT_THRESHOLDS` or **0.03** (`:790`) → `min_odds_for` in `stage_bet` | ~ `BOT_THRESHOLDS` or 0.03 **+ selection floor** | same as the Coolbet arm | ✓ `clears_edge_floor` (selection-aware) | n/a | ✓ `clears_edge_floor` |
| per-market odds floor | ~ at `odds_at_pick` only (`:863`); the live price is checked only against the edge-derived floor (`ui:1559`); `max_odds_drop_pct` defaults to 100 (`ui:1353`) | ✓ at the live book price (`decide_book`) | ✓ + `min_odds` at the site | ✓ | n/a | ✓ |
| odds cap (`odds_max`) | ✗ | ✗ | ✗ | ✗ | n/a | ✗ |
| live re-price ≥ floor | ✓ (edge-derived floor) | ✓ twice (decide_book + stage_bet) | ✓ (`min_odds` + ±12% band) | ✓ `_MIN_REMAINING_EDGE` | n/a | n/a |
| kickoff cutoff 3 min | ✓ `:842` + gate | ✓ `:580-596` + gate | ✓ gate | ✗ (the gate is called without `kickoff_at`, `:1995`) | n/a | window −5..90 min |
| account verify + reconcile (gate 0) | ✓ `:1107` (fails closed to dry) | **✗** | n/a (no Unibet reconcile exists) | ✗ | n/a | ✗ |
| dedupe: confirmed attempt | ✓ `already_placed :255` | ✓ | ✗ (Unibet writes no attempts; covered by `_has_exposure`) | `NOT EXISTS real_bets` | same-day exact triple (mig 407) | `NOT EXISTS real_bets` |
| per-match exposure + family (2 bets / €20) | ✓ `exposure_conflict :362` | ✓ `:604` | ✓ | ✗ (`PlacementGuard` only) | ✗ (not family-aware, not canonical) | n/a |
| daily caps 80 / €800 | ✓ in-pass + gate (`spent_today`) | ✓ | **~ Unibet is not counted by `spent_today`** | ✓ (API rows counted since f882f35d) | ✗ | n/a |
| single-run lock | ✓ `single_run_lock :721` | **✗** | ✗ | ✗ | n/a | n/a |
| confirm by evidence | ✓ balance delta (`ui:1659-1672`) | ✓ | ✓ (`uncertain` → NULL row) | ✗ (ticket id from the response) | human | n/a |

**Reading the matrix.**
- The fleet switches (pause, arm, capable ∩ eligible) are genuinely ONE fail-closed gate
  (`placement_gate.py`), called by every executor. The `PLACEMENT-GATE-ALL-EXECUTORS` smoke pins it.
  That part of the design holds.
- Everything **below** the fleet layer is re-implemented per executor and has drifted:
  - edge floor;
  - odds floor basis;
  - odds cap;
  - account verify;
  - lock;
  - Unibet caps.
- The per-pick exposure and caps (`exposure_conflict`, `spent_today`) live in a `scripts/` module that
  `placement_gate` lazy-imports (`placement_gate.py:244,252`), so the gate depends on a CLI script.

### 2.2 What changed since the 2026-09-24 docs

- `b9d2d05c` / `f882f35d`:
  - per-pick gate at the only Coolbet POST (`_place_bet_api`);
  - API bets counted in the caps;
  - combo / in-play hard-coded `placed_real=False`;
  - allowlist applied before the top-edge pick.
- `2efcf007` (mig 413):
  - `PLACEABLE_BOTS` replaced by `placement_path_reason` over `bot_config` and the eligibility rows;
  - audited `admin_set_control`;
  - `placer_heartbeats`;
  - owner-only arming.
- `335635fe`: footprint pause (`daemons_paused`) is **not** a money gate (owner). `coolbet_control`
  reports it as context only (`coolbet_control.py:113-140`).
- **Doc drift left behind:**
  - `docs/COOLBET_OWN_BETTING.md` §A1 / A2 still describes `bot_coolbet_value_v1` line-shop picks,
    "gate 6 BOT_THRESHOLDS = 0.03", "Seed: value_v1 ON", "both ui_place_enabled=TRUE" (line 134), and
    "re-check the odds floor at the live price" (§A2.4). The code checks only the edge-derived floor
    at the live price.
  - Its "gate stack" framing (maturity → per-market edge floor → …) applies to Path B only. The live
    Path A has no maturity gate by design: owner policy §3.1 says "real money is not a status".
  - The banner at the top of the doc is correct; the body below it is stale. This is a
    ripple-check candidate for whoever touches the doc next.

---

## 3. Duplication (the core of the refactor)

| Thing | Copies (file:line) | Agree? |
|---|---|---|
| **Per-bot edge floor** | ① `bot_configs.py` BotConfig `edge_floor` (generation) · ② `bot_config.edge_floor` (export of ①, mig 410) · ③ `place_coolbet_ui.py:56` `BOT_THRESHOLDS` (2 bots, default 0.03) · ④ `best_price_router.py:610` (③ + selection floor) · ⑤ web `ENGINE_BOT_FLOORS` (generated from the registry) · ⑥ web `coolbet-edge.ts:85` `BOT_EDGE_THRESHOLDS` (default **0.08**, lists retired bots) · ⑦ `ou35_model_shadow.py:37`, `pick_triggers.py:130` (sources for ②) | **No.** 9 of 11 capable bots: ③ = 0.03, ⑥ = 0.08, ②/⑤ = 0.02–0.10. |
| **Per-market model floor** | `coolbet_placer.py:63` `_MIN_EDGE_BY_MARKET` + `min_edge_for_pick :209` + `clears_edge_floor :268` · generated web mirror `engine-floors.ts` · hand-kept web `coolbet-edge.ts:20-50` `COOLBET_AUTO_MIN_EDGE_BY_MARKET` | Yes today. The hand copy is guarded only by comments; the generated one by smoke `FLOORS-ONE-SOURCE-CROSS-LANGUAGE`. |
| **Odds floor** | `_MIN_ODDS_BY_MARKET` + `_min_odds_for` (shared by P1 / P2 / P3) · `bot_config.odds_min` / `odds_max` (per bot) | **No.** The placers ignore the per-bot `odds_min` (1.01 for sharp bots) and `odds_max`. |
| **Edge unit** | pp `cal_prob − 1/odds` (placers, router, Pick queue verdict) vs EV `p·odds − 1` (sharp shadow rows, VIP NEW+ bots, `edge_unit='ev'`) | **No**, for sharp bots. |
| **Exposure / dedupe** | `exposure_conflict` + `canon_bet` (P1, P2) · `_has_exposure` exact triple (router `:115`) · `NOT EXISTS real_bets` exact (P3 `:501`, signaler `:132`, prekickoff `:159`) · `record_manual_real_bet` same-day exact on `lower(selection)`, not canonical (mig 407) · reconcile canonical (`:518`) | **Partly.** There are three different vocabulary rules for one table. |
| **real_bets vocabulary at write** | `store_real_bet` canonicalises (`supabase_client.py:5791`) · account-sync INSERT (`place_coolbet_ui.py:634`) · `record_manual_real_bet` (lower only) | **No.** |
| **Daily spend** | `spent_today` (Coolbet only) · router in-pass counters · web `DAILY_MAX_*` constants (`admin-money.ts:29`) as "reference" | **No** (Unibet; the web shows defaults, not the live env). |
| **CAN STAKE conjunction** | engine `placement_gate.effective_allowlist` + `coolbet_control.can_stake` · web `src/lib/bot-controls/ladder.ts` | Mostly. The web has no 36 h config staleness rule. |
| **Per-bot scoring on admin** | `bot_scoreboard` (mig 410; /admin/bots + Overview) · `shadow_bot_scoreboard` (mig 360; Pick queue lead chip) · `bot_performance` (mig 433, #159, not applied) | **No.** Three scorings; `shadow_bot_scoreboard` uses `clv_margin_corrected`, not the sharp anchor. |
| **Real-bet CLV** | `settlement.py:1591-1650` `real_bet_closing` (own-book close + `clv_pinnacle`) vs policy CLV `leg_clv_sharp` | **No** (a different definition, only for the money ledger). |
| **30-day € window** | `admin-money.realMoneyWindow :238` · `admin-overview.ts:165-171` inline | Same rule, two code copies. |
| **Public ROI** | hero `getHeadlineFlat` (odds_public) · landing `_our_stats.py` (odds_at_pick_live, drops legs, includes retired) · `/api/v1/track-record` rows (includes retired, no `in_record`) | **No.** Covered by #159 / area C. Listed because the landing shows two of these side by side. |
| **Pick-placement "done" marks** | `user_pick_marks` (P1 writes, nobody reads back) · `simulated_bets.user_placed_at`/`user_skipped_at` (Telegram webhook) · `real_bets` link | Three "placed" markers. |

---

## 4. Drift from policy §3

| # | Rule | Where it breaks | Evidence |
|---|---|---|---|
| D1 | 4 — change a live bot only via a twin | Switching a capable bot ON runs it under the placer's floors (3pp, 2.80 1x2 floor, no cap, pp not EV), not its own rule. The staked subset is a different strategy from the one scored. | §0.1–0.2, `place_coolbet_ui.py:790`, `best_price_router.py:180,610` |
| D2 | 5 — one shared computation per number | Three per-bot admin scorings; Pick queue PLACE verdict floors (default 0.08) ≠ placer floors (default 0.03); real-bet CLV ≠ sharp-anchor CLV; the 30-day € card is duplicated | §3 |
| D3 | 1 — statuses = distribution; no second visibility setting | /performance lists a bot if `maturity_label ∈ {calibrated, beta}` **OR** `vip` **OR** `show_on_performance` (`performance/page.tsx:224`, `bot-aggregates.ts:332,358,375`), and hides pending via `hide_pending` **or** vip. `/picks` has its own `show_on_picks`. The detail route `/api/performance/bot-legs` allows any `LEDGER_BACKED_BOTS` name, including two unlisted twin arms. The retired filter reads the 30-min cached `bots` (`page.tsx:223` comment says "live state"). | code refs |
| D4 | 2 — anything sent is counted | The prekickoff "PLACE MANUALLY" DM and the operator signaler are sends outside any bot record (operator-only, so arguably out of scope). VIP-held and `hide_pending` picks are sent live to Pro/Elite but shown settled-only publicly, which is by design (#148). No customer send path without a record was found in area D. | — |
| D5 | Real-money path missing a gate | Router: no account verify, no lock. Unibet not in the daily caps. Prekickoff prompt ignores the kill switch. No executor enforces `odds_max`. | §2.1 |
| D6 | Honest money numbers | 547 known-paper rows (`placed_real NULL`) are counted as real stakes on /admin/real-bets, Overview and "Bet made", and as exposure | §0.5 query |

---

## 5. Dead / retired / unused (with evidence)

| Item | Evidence | Action |
|---|---|---|
| `coolbet_mac_daemon.py` (945 lines) except `_drain_operator_commands` | plist in `local/launchd/retired/`; the only importer is `coolbet_feed_watchdog.py:842` (that one function) | Move `_drain_operator_commands` (and its notify helpers) into the watchdog / `coolbet_control`, then delete the module. Smoke pins that import the daemon need updating. |
| `coolbet_daemon_healthcheck.py` | scheduler registration commented out (`scheduler.py:3841`) | delete |
| `coolbet_prekickoff_alert` | its health signal `mac_daemon_last_tick_at` has been frozen since 2026-09-10 12:22, so it is always "unhealthy"; 851 runs in 3 days, 0 sends | delete, or re-base on `placer_heartbeats` + `placement_paused` (owner call: is a "place manually" DM still wanted?) |
| `_drain_manual_placement_queue` (every 10 s) + `place_bet_by_id` + the Telegram "Record at Coolbet" callback | `manual_placement_queue` has 0 rows; the operator prompts are off by default; paper-only | delete the job, the route branch and the table after owner OK |
| `place_all_bets` / `load_qualified_bets` / `_place_combo_bets` / `place_all_inplay_bets` / `scripts/place_coolbet_bets.py` (the "Path B" API placer) | every `simulated_bets` bot fails `placement_path_reason`, so `--execute` can only refuse; the record mode writes paper rows into a money table | retire; keep `_place_bet_api` only if an API placement path is wanted later |
| web `engine-data.ts:698` `getPlaceableBets` (~520 lines) + `:1218` `getRealBets` + `src/lib/real-money-tier.ts` | no callers since /admin/place was deleted (grep: only a comment in `admin-money.ts:4`) | delete — **engine-data.ts is being edited by #159** |
| web `coolbet-edge.ts` except `botEdgeThreshold` | only `picks-table.tsx:2` (`botEdgeThreshold`) and the dead `getPlaceableBets` import it | replace with `ENGINE_BOT_FLOORS` and delete |
| `simulated_bets.user_placed_at/user_skipped_at`, `user_pick_marks` writes from P1 | the UI placer writes `user_pick_marks` and never reads them; the webhook sets the sim columns | fold into the `real_bets` link (§7) |
| `shadow_bot_scoreboard` (mig 360) | its only reader is the Pick queue lead chip | switch to `bot_performance` once #159 lands, then drop |
| `ROUTER_ALLOW_REAL` env | superseded by `real_money_armed`, but still a required AND (`best_price_router.py:481`) and reported by `gate_status` | the owner chose to set it false (handover §1); delete once the router is re-based |

---

## 6. Refactor candidates

Risk levels: **L** = low, **M** = medium, **H** = touches money semantics.

| ID | Change | Serves | Risk / who depends | Blocked by (files others are editing) |
|---|---|---|---|---|
| R1 | **One per-bot placement rule.** The placers read `edge_floor`, `odds_min`, `odds_max` and `edge_unit` from `bot_config` (already exported daily with a 36 h fail-closed age) via one `placement_gate.pick_clears(bot, market, sel, price, prob)`. Delete `BOT_THRESHOLDS`, the router's own floor stacking and the web `BOT_EDGE_THRESHOLDS`. Parity test: generator ↔ placer ↔ Pick queue verdict on the same rows. | §3 rules 4 + 5 | **H.** Changes what a switched-on bot stakes. All 11 switches are OFF, so nothing live changes today, but it **needs owner OK**. The two ex-real-money bots' floors must come out bit-identical (0.10 selection-aware / 0.08). | `bot_registry.py` (#155 / #161), `export_bot_config.py` (modified in the working tree) |
| R2 | **Move the per-pick checks into the gate.** `exposure_conflict`, `canon_bet`, `spent_today` (all books), `KICKOFF_CUTOFF_MIN`, the caps and account verification move from `scripts/place_coolbet_ui.py` into `workers/automation/placement_gate.py`. Every executor calls `assert_may_place(..., held=...)`, `spent_today` counts `real_bets.placed_real IS TRUE` across books, and the router takes the same flock plus a per-book account verify. | RELIABILITY §4 | **M.** The smoke pins on `scripts.place_coolbet_ui.*` imports (many) must move; behaviour is identical for Coolbet. | none (the placer files are not being edited) |
| R3 | **Back-fill `placed_real=FALSE`** on the 547 known-paper rows (`notes LIKE 'auto ticket=None%' OR 'inplay-auto%' OR 'auto-combo%'`), plus a CHECK or trigger that requires `placed_real` on INSERT. | honest money numbers | **M.** A money-record change: owner OK plus a rolled-back dry run. The 269 NULL rows with no note (05-11..06-12) and the 23 `auto ticket=<id>` rows need a human look. | none |
| R4 | **Web speed:** server-side PostgREST over localhost; `React.cache` for the board, control and superadmin reads; a slim shell attention read; column-pruned `bot_config`. | owner "Bots page slow" | **L** (read path only). | `bot-board.ts`, `admin-overview.ts`, `admin-attention.ts` (#159 / #139 round 6) |
| R5 | **One admin scoring:** /admin/bots, Overview and Pick queue all read `bot_performance`; drop `shadow_bot_scoreboard` and the `bot_scoreboard` ROI/CLV columns. | rule 5 | **L–M.** 65 smoke pins reference admin URLs; some pin the view names. | #159 (`bot_performance` not applied yet), `bot-board.ts`, `bot-board-model.ts` |
| R6 | **One visibility decision:** derive `/performance`, `/picks` and the detail-route allowlist from status + VIP channel (+ `hide_pending` as a VIP/twin property) in ONE function shared by page and API; drop `show_on_performance` / `show_on_picks` as independent switches, or make them derived. | rule 1 | **M.** Owner-chosen TESTING bots use `show_on_performance`. Needs the #155 status rollout first. | #155, `performance/page.tsx`, `bot-aggregates.ts` |
| R7 | **Delete retired money code** (§5 list): mac daemon, Path B placer + CLI, manual drain, daemon healthcheck, prekickoff alert (or re-base), web `getPlaceableBets` / `getRealBets` / `real-money-tier` / most of `coolbet-edge`. | fewer second paths | **L–M.** Smoke `PLACEMENT-GATE-ALL-EXECUTORS` enumerates executors, so update it in the same commit. `_place_bet_api` is the only Coolbet API POST; decide whether to keep it. | `scheduler.py` (modified in the working tree), `engine-data.ts` (#159) |
| R8 | **One real_bets writer:** everything through `store_real_bet` (canonical vocabulary, id routing), including account-sync and `record_manual_real_bet` (call the same canonicaliser in SQL or route manual logging through an engine RPC). Canonical dedupe in the manual RPC. | rule 5 | **M.** | `supabase_client.py` (modified in the working tree) |
| R9 | **Doc ripple:** rewrite the `COOLBET_OWN_BETTING.md` body (A1 / A2, gate table, Path B) to the post-413 reality; state explicitly that maturity is not a real-money gate (policy §3.1) and that the placers apply the bot's own floors (after R1). | ripple rule | **L** | none |

Ordering suggestion:
- R3 and R2 first; they are independent of the other sessions.
- R1 next, **before** the owner switches any bot ON.
- R4, R5, R6 and R7 after #159 / #155 close.

---

## 7. `real_bets`: a separate table, or a flag on one bets table?

**What depends on `real_bets` today (verified by grep and the DB catalog):**

- **FKs into it:**
  - `coolbet_placement_attempts.real_bet_id`;
  - `promo_ledger.real_bet_id` (0 rows).
- **FKs out of it:**
  - `simulated_bet_id` → `simulated_bets` (SET NULL);
  - `shadow_bet_id` → `shadow_bets` (SET NULL, UNIQUE partial index `real_bets_one_per_shadow_pick`);
  - `bot_id` → `bots`;
  - `match_id` → `matches`;
  - `bookmaker` → `accessible_bookmakers`.
- **No FK to `picks_forward_test`**, so bets on /picks picks cannot be linked. Every row does carry a
  `bot_id` today (992 of 992).
- **No views and no triggers depend on it.** One function does: `record_manual_real_bet` (mig 407).
- **Engine writers:**
  - `store_real_bet` (`supabase_client.py:5669`), from `coolbet_ui_placer:1678`,
    `best_price_router:343`, `coolbet_placer:2412/2614/2872` and
    `scripts/reconcile_placed_attempts_to_real_bets.py`;
  - the account-sync INSERT `place_coolbet_ui.py:634`;
  - settlement UPDATEs `settlement.py:1642/1686/2084`;
  - 4 CLV/edge back-fill scripts.
- **Web writer:** `/api/admin/real-bet` → rpc.
- **Engine readers (money-critical):**
  - exposure / dedupe: `place_coolbet_ui.match_exposure :330`, `spent_today :397`,
    `best_price_router._has_exposure :115`, `coolbet_placer.load_qualified_bets :501,565,631,749`,
    `place_bet_by_id :2935`;
  - settlement;
  - alerts: `coolbet_daily_summary :106-133`, `coolbet_prekickoff_alert :159`, `health_alerts`,
    `coolbet_signaler :132` (annotation only);
  - reports: `weekly_bot_review`, `real_perf_report`, `coolbet_clv_report`, `direct_book_clv_report`,
    `scripts/coolbet/status.py`, `scripts/ops/status.py`;
  - backup drill.
- **Web readers:**
  - `/admin/real-bets` (`admin-money.ts`);
  - Overview (`admin-overview.ts:157,213`) and attention (`admin-attention.ts`);
  - /admin/bots "Bet made" (`bot-board.ts:769`);
  - Pick queue (`shadow-bots/queries.ts:204`);
  - `/admin/shadow-bots/[bot]` (now a redirect);
  - Telegram webhook `/today` and the dedupe (`telegram/webhook/route.ts:139,533`);
  - dead `getPlaceableBets` / `getRealBets`.

**Why a flag on one bets table is the wrong shape.** A real bet is a different fact from a pick:
- it has its own stake, price, venue and time, and its own result (half-won/half-lost, voids from
  dead matches);
- it has its own CLV at its own book;
- it can exist with no pick at all (account-sync tickets, manual bets on anything);
- one pick can in principle be staked at more than one book, which is exactly the case the router's
  "never twice" rule has to *detect*.

Folding these into a `picks` row as `real_money=true` + actual columns would:
- (a) make the pick table mutable after the fact, which breaks the phase-5 immutability invariant
  (`picks_no_delete`, R8 in the schema draft);
- (b) lose bets that have no pick, or force fake picks for them;
- (c) mix € P/L with the unit record that policy §3 keeps flat.

The only real gain from a flag is the linking.

**Recommendation — keep `real_bets` as the money ledger and fix the linking.** This is consistent
with `unified-bot-model-phase5-schema-draft.md:60`:
1. Add `pick_id → picks(id)` (phase 5), and **meanwhile** add `forward_test_pick_id → picks_forward_test`
   so manual bets on /picks picks become linkable. Keep `bot_id` nullable for unlinked tickets.
2. Make `placed_real` NOT NULL with three explicit states (confirmed / unverified / paper). Paper
   should arguably not be written to `real_bets` at all once Path B is retired (R7). Then back-fill
   the rows as in R3.
3. One writer (R8) and one canonical vocabulary. The dedupe keys on (`match_id`, canonical family,
   canonical selection).
4. The per-bot "Bet made" column, the € P/L and the unified "Real money" view on /admin/bots (the
   owner's proposal to drop the page) are all **reads** of this table joined by `pick_id`, so dropping
   the `/admin/real-bets` *page* is independent of the table decision. Keep the reconcile to-do and
   the daily-limit use somewhere on Bots; the Overview already has both.

What would break if the table were folded anyway: every exposure and cap read above (the placers
would have to scan the pick ledger), settlement's separate € grading, `coolbet_placement_attempts`
and `promo_ledger` FKs, and the unique one-per-shadow-pick index.

---

## 8. Files other sessions are editing — dependencies of the candidates

In the engine working tree: `WORKFLOWS.md`, `docs/SYSTEM_MAP.md`, `scripts/export_bot_config.py`,
`scripts/publish_picks_forward_test.py`, `scripts/smoke_test.py`, `workers/api_clients/supabase_client.py`,
`workers/jobs/settlement.py`, `workers/registry/bot_registry.py`, `workers/scheduler.py`, and untracked
migrations 433 / 434 and `workers/utils/pick_price.py`.

In the web working tree: `performance/page.tsx`, `bot-aggregates.ts`, `engine-data.ts`, `bot-board.ts`,
`bot-board-model.ts`, `admin-overview.ts`, `admin-attention.ts`, `admin-jobs*.ts`, `admin-feeds*.ts`,
and untracked `bot-performance.ts`.

| Candidate | Depends on | Rows |
|---|---|---|
| R1 floors from `bot_config` | `bot_registry.py`, `export_bot_config.py` | #155 #161 |
| R4 web speed | `bot-board.ts`, `admin-overview.ts`, `admin-attention.ts` | #159, #139 round 6 |
| R5 one admin scoring | migration 433 applied, `bot-board*.ts` | #159 |
| R6 one visibility decision | `performance/page.tsx`, `bot-aggregates.ts`, status rollout | #155 #159 |
| R7 delete dead code | `scheduler.py`, `engine-data.ts` | #159 (plus whoever holds `scheduler.py`) |
| R8 one writer | `supabase_client.py` | whoever holds it (modified) |
| R2 / R3 / R9 | none of the in-flight files | can start now |

Not proposed for editing until those rows close: `daily_pipeline_v2.py` BOTS_CONFIG, the SYSTEM_MAP
bot rows, `publish_picks_forward_test.py` and `settlement.py` `dashboard_cache`.
