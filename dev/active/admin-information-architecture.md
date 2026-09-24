Parent row: **#139 UNIFIED-BOT-MODEL-EPIC** in PRIORITY_QUEUE.md.

# Admin information architecture — audit + target sitemap (2026-09-24)

**Trigger.** The owner found the Coolbet "footprint" pause (a data-collection budget) in the
Controls card on `/admin/bots` and asked: *"carefully audit what needs to be on what page."*

**Method.** Read every `src/app/(app)/admin/**` page, every `src/app/api/admin/**` route, the
shared shell, and the Telegram operator commands. Checked page liveness against the DB
(read-only, 2026-09-24). Took the design inputs from `docs/UNIFIED_BOT_MODEL_DESIGN_2026_09_24.md`,
`dev/active/bots-control-panel-spec.md` (incl. §16 owner decisions) and `dev/active/bots-board-ux-spec.md`.
**Read-only audit: no code changed.**

> ⚠️ **Work already in flight in the web checkout (uncommitted at the time of writing):**
> `ADMIN-SHARED-SHELL`, with a new `admin/layout.tsx`, `components/admin/{admin-shell,admin-sidebar,admin-nav,admin-status}`
> and `public-chrome.tsx`. It deletes `bots/admin-shell.tsx` and edits every admin page. The sidebar
> groups in `admin-nav.ts` are *Overview · Bots & money (Bots, Shadow bots, Real bets\*, Place\*) · Data & ops
> (Feeds, Ops) · Other sports (CS2\*, LoL\*, Tennis\*)*, where \* marks items flagged `unused`. This plan
> **builds on that shell** and only changes the groups and entries. Nothing below should start
> until that session has committed.

---

## 0. The operator's jobs-to-be-done

| # | Job | Asked how often | Today answered on |
|---|---|---|---|
| J1 | **What needs my attention right now?** | every visit | nowhere as one list. Feed problems on `/admin`, bot issues in a tile on `/admin/bots`, failed jobs on `/admin/ops`, safety chips on `/admin/shadow-bots` |
| J2 | **Is everything running?** (jobs, settlement, deploy) | daily | `/admin/ops` (partly stale) |
| J3 | **Are the feeds healthy?** (books, AF, Pinnacle, closes, footprint) | daily / on alert | `/admin/feeds`, plus pieces on `/admin/ops` and `/admin/bots` |
| J4 | **Which bots are good?** | weekly | `/admin/bots` **and** `/admin/shadow-bots` **and** `/admin/shadow-bots/[bot]`, three different numbers |
| J5 | **What may bet real money, and is it on?** | on change | `/admin/bots` Real-money card, `/admin/shadow-bots` safety strip, Telegram `/status` |
| J6 | **What should I place by hand today?** (the owner places manually on shadow signals) | daily | `/admin/shadow-bots` picks table (and the dead `/admin/place`) |
| J7 | **What did we bet, and how did it go?** | weekly | `/admin/real-bets`, reachable only from a footer link on `/admin/shadow-bots` |
| J8 | **Is the customer channel (picks) sending?** 👥 | on change | `/admin/bots` Controls card, Telegram `/status` |
| J9 | **Who changed what, and when?** | on incident | `/admin/bots` Activity sheet (`control_changes`). Feed actions sit in a separate table (`feed_actions`) that no page lists |

---

## 1. Inventory

### 1.1 `/admin` — Overview (`admin/page.tsx`)
| Item | Data | Job |
|---|---|---|
| "Bookmakers & feeds" card: ok/warn/fail counts, a dot per book, up to 4 problem lines | `getFeedStatus()` → `feed_status` | J3, J1 (feeds only) |
| 3 link cards: Bots, Shadow bots, Ops | static | navigation |

It is a link index with one feed widget. **It shows no money state, no bot issues and no failed jobs.**

### 1.2 `/admin/bots` — Bots (`admin/bots/*`, `lib/bot-board.ts`, `lib/bot-controls/*`)
| Section | Items | Data | Job |
|---|---|---|---|
| Header | "20 active · 1 control · data HH:MM", Activity sheet, How to read, ⋯ | `bot_scoreboard`, `control_changes` | J4, J9 |
| **Fleet strip** (6 tiles) | Placement (paused/running + placer word), Real money (armed/off + "0 of 11 on"), Active bots (+published/Telegram counts), Verdict mix bar, Picks·7d, **Needs a look** (silent bots, no-config, "stakes without positive verdict", "/picks ≠ Telegram", unreadable views) | `coolbet_session_state`, `bot_capabilities`, `bot_scoreboard`, computed in `bot-board-model.ts` + `bots-board.tsx:212` | J1 (bots only), J4, J5 |
| **Controls card** | *Picks · customers*: **Picks channel** switch (`publishing_paused`). *Collection*: **Coolbet footprint** switch (`daemons_paused`) ← **the misplaced item**, plus "Feeds bots depend on" (a link to /admin/feeds) | `coolbet_session_state` via `POST /api/admin/bots/controls` → `admin_set_control` | J8, J3 |
| **Real money card** (danger zone) | 6-layer ladder (path, per-bot switch, kill switch, armed, executors, per-pick gates) + CAN STAKE line; Pause/Resume…; Disarm / Arm… (owner); Mac executors (`placer_heartbeats`) | `coolbet_session_state`, `coolbet_placer_bots`, `bot_config`, `placer_heartbeats` | J5 🤖 |
| Bot table | Tabs: Active / Retired. Filters: All, Published, Real-money capable, Silent, Needs a look, search. Family groups with a junk-control reference strip. Per row: name, config line, last pick, rule version, verdict, t, forest bar vs junk, 12-week strip, n·ROI, pending, caps (/picks switch, € switch) | `bot_scoreboard`, `bot_weekly`, `bot_config`, `bot_capabilities` | J4, J5 |
| Bot sheet | Overview / Settings / Performance / Picks (`/api/admin/bot-ledger`) / Activity | `bot_ledger_display`, `control_changes` | J4, J9 |
| Retired tab | 88 retired bots | `bot_scoreboard` | J4 |
| Armed bar | full-width red bar when armed (now in the shared shell) | `coolbet_session_state` | J5 |

### 1.3 `/admin/shadow-bots` — Shadow bots (`components/shadow-bots/*`, `lib/shadow-bots/*`)
| Section | Items | Data | Job |
|---|---|---|---|
| **Safety strip** | chips: placement, publishing, daemons, real money, executors, today (auto vs "+N manual" against 80 / €800), DB GATES; **embedded `CoolbetDaemonsPause` footprint switch** | `coolbet_session_state`, `coolbet_placer_bots`, `real_bets` | J5 (duplicate), J3 (misplaced) |
| Header + How it works | counts, cache age | — | — |
| **Picks table** | one verdict per pending pick, quote freshness, Now CB/UB/EB, floor checks, **`Place €X` → `/api/admin/real-bet`** (`record_manual_real_bet`) | `shadow_bets_unique`, `odds_snapshots`, `engine-floors.ts` | **J6** (the only place it is done) |
| **Scoreboard** | PROMOTE/RETIRE/OBSERVE/COLLECTING verdict, LEAD track; **per-bot `CoolbetPlacerToggle`** (OFF-only since phase A, `/api/admin/coolbet-placer-bots`) | `shadow_bot_scoreboard`, `coolbet_placer_bots` | J4 (duplicate), J5 (duplicate) |
| Promotions | `promo_terms` EV vs realised | `promo_terms`, **0 rows today** | J7-adjacent, currently empty |
| Footer | the only link to `/admin/real-bets` | — | nav |

### 1.4 `/admin/shadow-bots/[bot]` — per-bot ledger (1,004 lines)
A hand-curated `ALLOWED` prose map for about 14 bots (generic header for the rest). Stat panel: picks, awaiting, settled,
hit rate, **avg CLV, ROI (exec price)**. Progress to "50 settled / 14 days". Full bet ledger with Bet
made, Now CB/UB/EB and close+CLV columns. **#139 finding (b):** it applies the model-edge min-odds
formula and a pre-match close CLV to every bot, so in-play bots show artefacts. This is a third per-bot scoring,
on its own rules (50/14) that differ from both the shadow scoreboard (pre-registered n) and `bot_scoreboard` (n ≥ 30, family metric).

### 1.5 `/admin/feeds` — Bookmakers & feeds
| Section | Items | Data | Job |
|---|---|---|---|
| Header | legend, "status checked N min ago" (red if > 15: status job stuck) | `feed_status.updated_at` | J3 |
| Book blocks | Coolbet, Epicbet, Unibet, Tonybet, Betfair (reference); smaller row: API-Football (+Pinnacle), closing, infra deps (zone_egress, flaresolverr, unibet_chrome, betfair_egress). Detail panel per book: sweepers, **per-feed Pause / Resume / Run now** (`FeedControls` → `/api/admin/feed-control` → `feed_controls` + `feed_actions`), closing capture %, **request budget per hour (#110)** | `feed_status`, `feed_book_stats` | J3 |
| DQ findings | wrong-match boards, mirrored 1X2, swapped 2-way, results disagree (24 h) | `data_quality_findings` | J3 |

It has no Coolbet footprint switch and no Mac footprint-daemon heartbeat, although both are Coolbet-collection concerns.

### 1.6 `/admin/ops` — Ops dashboard
| Section | Data | Status |
|---|---|---|
| Fixtures & coverage | `ops_snapshots` | live (last snapshot 2026-09-24 18:30) |
| Odds pipeline (rows, bookmakers, 1X2/OU/BTTS coverage) | `ops_snapshots` | overlaps /admin/feeds |
| Betting & bots (pre-match/in-play bets today, pending, settled, P&L "$", active/idle bots, duplicates) | `ops_snapshots` | **stale copy**: "16 pre-match + 8 live … Paper trading since Apr 27", `$` currency, `inplay total={8}`. Overlaps /admin/bots |
| Live tracker | `ops_snapshots`, last live snapshot | a feed (LivePoller) |
| Post-match / settlement | `ops_snapshots`, `getStalePendingBets` | J2 |
| Enrichment quality | `ops_snapshots` | J2/J3 |
| Email & alerts (digests, value-bet alerts to Pro/Elite, previews, watchlist) | `ops_snapshots` | **dead product** (paid tiers deprecated, no checkout) |
| AF API budget (150k/day) | `ops_snapshots` | a feed budget. /admin/feeds has per-book budgets (#110) but not AF's |
| Users (total, Pro, Elite, signups) | `ops_snapshots` | mostly dead (tiers). "New signups" is still meaningful |
| Pipeline runs (per-job status) | `pipeline_runs`, `getLatestJobStatuses` | **J2 core; failed jobs belong in J1** |

### 1.7 Routes that exist but are unlisted
| Route | What | Evidence | Verdict |
|---|---|---|---|
| `/admin/real-bets` | real-money ledger: overall/today tiles, pre/post `MARKET_THRESHOLDS_V2` epoch, exposure (pending, stake at risk, max payout), real vs paper ROI, slippage, stake parity, per-bot, chart, log | `real_bets`: **992 rows, 155 in the last 30 d, last 2026-09-15** | **Live and valuable (J7).** It was dropped from the index only because the owner does not open it. Promote it. |
| `/admin/place` | "Place real bets" (SELF-USE-VALIDATION, June): pending `simulated_bets` with live edge, manual log | SELF-USE-VALIDATION closed 2026-06-07. Its job is done better by the shadow-bots picks table | **Dead duplicate → delete** |
| `/admin/cs2` (+ scrapers, backtest, log-bet) | CS2 value sheet | reads `cs2_simulated_bets`, `cs2_upcoming_matches`, **neither table exists**. CS2 jobs last ran 2026-07-31 | **Broken → delete** |
| `/admin/lol` | LoL ELO value sheet | `lol_upcoming_matches` 14 rows | dormant → archive/delete (owner's call) |
| `/admin/tennis` | tennis system overview | `tennis_value_bets` 38 rows total | dormant → archive/delete (owner's call) |

### 1.8 Admin API routes
| Route | Writes | Called from | Note |
|---|---|---|---|
| `bots/controls` POST | `admin_set_control` (audited) | /admin/bots | canonical |
| `bots/controls/arm` POST | `admin_arm_real_money` (audited, owner) | /admin/bots | canonical |
| `bots/controls/audit` GET | — | /admin/bots Activity | |
| `bot-ledger` GET | — | /admin/bots sheet | |
| `feed-control` POST | `feed_controls` upsert + `feed_actions` insert | /admin/feeds | its own audit, **not** in `control_changes` |
| **`coolbet-daemons-pause`** GET/POST | **direct `.update()` on `coolbet_session_state`, no audit row** | /admin/shadow-bots safety strip | **second, unaudited write path** for the same flag /admin/bots writes through `admin_set_control` (control-panel spec §1.3 rule 5 / §2.1 said to re-point it) |
| `coolbet-placer-bots` GET/POST | direct `.update()`, OFF-only | /admin/shadow-bots scoreboard | legacy duplicate of the € switch. Whether OFF writes land in `control_changes` is unclear (DB guard only refuses starts) |
| `real-bet` POST | `record_manual_real_bet` rpc | shadow-bots `Place €X` | J6 → J7 |
| `record-combo` POST | insert | ? (combo logging) | check callers before deleting |
| `bot-book-odds` POST | — | shadow-bots | |

### 1.9 Telegram operator commands (`api/telegram/webhook/route.ts`)
| Command | Duplicates on the page | Keep? |
|---|---|---|
| `/status` (placement, publishing, Mac daemon heartbeat, **JWT TTL, session errors**) | sidebar status + Real-money ladder. **JWT/session health appears on no page** | keep (phone); add the session health to the Feeds Coolbet block (gap G5) |
| `/today` (real_bets 24 h) | `/admin/real-bets` Today row | keep |
| `/pause <reason>` + `coolbet-pause:` button | Placement pause (audited) | keep, stop-only (owner decision 3) |
| `/resume`, `/arm`, `/unpause`, `coolbet-resume:` | — | refused with a pointer to /admin/bots ✔ |
| `/pausepicks`, `/resumepicks` | Picks channel switch | keep (👥 PICKS) |
| (none) | Footprint pause | no Telegram command. The page is the only handle, which matters because the footprint pause is an Imperva response that often has to happen from a phone. Keep in mind when it moves |

---

## 2. Map: each item → its page

### 2.1 Misplaced items
| Item | Now on | Belongs on | Why |
|---|---|---|---|
| **Coolbet footprint pause** (`daemons_paused`) | /admin/bots Controls + /admin/shadow-bots strip | **/admin/feeds → Coolbet block**, beside the Coolbet request budget, closing capture and a new footprint-daemon heartbeat | It is a collection-budget lever used in response to Imperva, the same job as the per-feed pause and the #110 budgets. The operator reaching for it is looking at a red Coolbet block, not at bot verdicts. |
| ↳ **but the switch is not "not a money switch"** | Controls card copy | Real-money ladder as a **read-only blocking layer** ("Coolbet collection paused, so the placer tick is skipped → /admin/feeds") | `coolbet_mac_daemon.py:752` skips the whole tick (JWT harvest **and placement**) and `coolbet_control.can_stake()` lists `daemons_paused` as a blocker (`coolbet_control.py:110`). The page ladder has only 6 layers and omits it, so **CAN STAKE could read YES while the engine says NO.** 🤖 OWN correctness fix, not only a move. |
| "Feeds bots depend on" row | /admin/bots Controls | drop it; replace with an **inline alert** on the bots page only when a dependent feed is red | A permanent link row is noise. The dependency matters only when broken. |
| Odds pipeline coverage, Live tracker, AF API budget | /admin/ops | **/admin/feeds** (AF block, "API budget" line; live poller as a feed row) | They are feed-health facts. /admin/feeds already owns the per-book budget. |
| Betting & bots section | /admin/ops | delete. /admin/bots fleet strip owns this; keep only "duplicate bets" as an attention rule | The copy is stale (16+8 bots, `$`, Apr 27) and the counts compete with `bot_scoreboard` |
| Promotions | /admin/shadow-bots | **/admin/money** (EV vs realised is money), collapsed when empty | `promo_terms` has 0 rows. It is a money concern, not a bot concern |
| Real-bets link | footer of /admin/shadow-bots | the sidebar | the money ledger is J7, a first-class job |

### 2.2 Duplicates (pick one owner, delete the rest)
| What | Copies | Keep | Delete / replace |
|---|---|---|---|
| **Per-bot scoring** | `/admin/bots` (`bot_scoreboard`, family admissible metric, n≥30) · `/admin/shadow-bots` Scoreboard (`shadow_bot_scoreboard`, PROMOTE/RETIRE) · `/admin/shadow-bots/[bot]` (own ROI + avg CLV, 50 settled/14 d, wrong formula for in-play) · `/admin/real-bets` per-bot (**real** money: a different thing, keep) · `/admin/ops` bot counts | `/admin/bots` + sheet | shadow Scoreboard and [bot] stats panel (design doc phase 2: "stops computing its own per-bot records"). The pre-registered PROMOTE/RETIRE rule becomes a column/chip on /admin/bots, fed from `bot_scoreboard` |
| **Per-bot real-money switch** | /admin/bots € column (audited, ON/OFF) · shadow-bots `CoolbetPlacerToggle` (OFF-only, legacy route) | /admin/bots | shadow toggle + `/api/admin/coolbet-placer-bots` POST (spec phase B) |
| **Footprint pause writer** | `admin_set_control` · `/api/admin/coolbet-daemons-pause` (unaudited `.update`) | the audited function | the legacy route |
| **Fleet state display** | sidebar status (3) · bots fleet strip (2 tiles) · Real-money card · shadow safety strip (7 chips) · Telegram `/status` | sidebar (always visible) + Real-money card (detail) + Telegram (phone) | shadow safety strip. The "today N/80 bets, €/800, +N manual" chip moves to /admin/money |
| **Picks to act on** | `/admin/shadow-bots` picks table · `/admin/place` | shadow picks table (re-homed, see §3) | `/admin/place` |
| **Bet ledger per bot** | bots sheet Picks tab (30 rows, admissible CLV only) · `/admin/shadow-bots/[bot]` ledger (all rows, Now CB/UB/EB, Bet made) | bots sheet, extended with "Bet made" + a "full ledger" link | `[bot]` page → redirect to `/admin/bots?bot=<name>` once the sheet has the placement column |
| **Feed status** | /admin overview card · /admin/feeds · ops odds section | /admin/feeds (+ attention rows on Overview) | ops odds section |
| **Audit logs** | `control_changes` (bots Activity) · `feed_actions` (no viewer) | one **Activity** view reading both | — |

### 2.3 Dead / unused (with evidence)
- `/admin/cs2`: reads two tables that no longer exist; jobs silent since 2026-07-31. **Delete.**
- `/admin/place`: its SELF-USE-VALIDATION window closed 2026-06-07; duplicate of the picks table. **Delete.**
- `/admin/lol`, `/admin/tennis`: dormant data (14 and 38 rows). **Owner's call**: delete, or keep under a collapsed "Archive" group.
- Ops "Email & alerts" and the Pro/Elite user tiles: paid tiers deprecated (CLAUDE.md), no checkout. **Delete**, and keep one "signups (7 d)" number.
- Ops "Betting & bots": stale copy, superseded (see above).
- shadow-bots **Promotions**: `promo_terms` is empty. Collapse it to one line until a term exists.
- `ALLOWED` prose map in `[bot]/page.tsx` (~200 lines): hand-written bot descriptions that drift. `bot_config.description` is the exported source. **Retire** with the page.

### 2.4 Gaps (the operator needs it; no page shows it)
| # | Gap | Where | Dir |
|---|---|---|---|
| G1 | **One attention inbox** (§3.3) | /admin | 🤖👥 |
| G2 | **Footprint pause is missing from the real-money ladder** (see 2.1) | /admin/bots ladder | 🤖 |
| G3 | **Picks channel: evidence of sending.** Last successful Telegram send, sends today, last send error. The switch says "Sending" whether or not anything went out, and a silent customer outage is the incident class `publishing_paused` was born from | /admin/bots Publishing card + attention rule | 👥 |
| G4 | **Unified activity log** across `control_changes` + `feed_actions` (+ Telegram actor) | /admin/activity or a shell-level sheet | 🤖👥 |
| G5 | **Coolbet session health** (JWT TTL, session errors, Mac footprint-daemon heartbeat), shown only by Telegram `/status` and the shadow strip | /admin/feeds Coolbet block | 🤖 |
| G6 | **Deploy drift** (`deploy_drift_check.yml`, Telegram only): "VPS N commits behind" has bitten before (CLAUDE.md, ENGINE-DEPLOY-2026-08-24) | /admin/ops + attention rule (needs the check to write a row) | 🤖👥 |
| G7 | **Forgotten pauses**: a feed or the footprint paused for > 24 h with no reason refresh | attention rule | 🤖 |
| G8 | **Model in production** (bundle version per head, `SHADOW_MODEL_VERSION` pin, last retrain). There is no page (a CS2 comment refers to an `/admin/models` that was never built) | /admin/ops "Models" section (small) | 🤖👥 |
| G9 | Manual real bets awaiting reconciliation ("+N manual" in the shadow strip) as a to-do | /admin/money + attention | 🤖 |

---

## 3. Target

### 3.1 Sitemap + sidebar groups
```
Overview                /admin                 J1 attention inbox + status + today's money line
─ Bots
  Bots                  /admin/bots            J4 registry & scoring · J5 per-bot switches · real-money
                                               ladder (owner decision §16.3: THE money control surface)
                                               · Publishing card (picks channel)
─ Money  🤖
  Pick queue            /admin/queue           J6 today's picks to act on + Place €X   (was /admin/shadow-bots)
  Real bets             /admin/money           J7 ledger, P&L, exposure, per-bot real, promotions (was /admin/real-bets)
─ Data & ops
  Feeds                 /admin/feeds           J3 books, AF+Pinnacle, closes, budgets, DQ, Coolbet footprint + session
  Jobs                  /admin/ops             J2 pipeline runs, settlement, enrichment, deploy drift, models
  Activity              /admin/activity        J9 control_changes ∪ feed_actions
─ Archive (collapsed; only if the owner keeps them)
  LoL · Tennis
```
Sidebar STATUS block (already in the shared shell): Placement / Real money / Picks channel, **plus a 4th line
"Coolbet collection: On / Paused"**. That dot is what makes moving the switch off /admin/bots safe:
the state stays visible on every page while the control lives on Feeds.

Redirects (keep old URLs working for bookmarks and Telegram links): `/admin/shadow-bots` → `/admin/queue`,
`/admin/shadow-bots/[bot]` → `/admin/bots?bot=…`, `/admin/real-bets` → `/admin/money`, `/admin/place` →
`/admin/queue`.

### 3.2 What each page owns (one line each)
- **/admin/bots**: what each bot is, whether it is good (one scoring), what it may do (/picks, €, retire), plus the fleet money ladder and the customer-channel switch. **Collection controls are gone from it.**
- **/admin/queue**: only pending picks that someone could act on today, with freshness and a Place action. No scoreboard, no safety strip; one line links to the ladder.
- **/admin/money**: what we actually staked and the result. Today vs caps, exposure, reconciliation to-dos, promotions.
- **/admin/feeds**: is data coming in and at what cost. Every collection lever (per-feed pause/run, Coolbet footprint), every budget (per book + AF), session health, DQ.
- **/admin/ops (Jobs)**: are the scheduled jobs, settlement, deploys and models healthy.
- **/admin/activity**: who changed what.

### 3.3 Overview = attention inbox (only items that need an action)
One server loader `loadAttention()` composed from the loaders that already exist. Each row has a severity, one
sentence, its age and **a link to the exact place it is fixed**. The empty state reads "All clear — checked HH:MM".
Below the inbox: the status line (4 switches) and one money line ("today: 0 auto / 2 manual bets, €20 at risk").

| Rule | Source | Severity | Links to |
|---|---|---|---|
| Real money **ARMED** (always listed while true) + CAN STAKE yes/no | `coolbet_session_state`, ladder | danger | /admin/bots#real-money |
| Armed **and** placement running **and** executor stale | + `placer_heartbeats` | danger | /admin/bots#real-money |
| Picks channel paused, or sending but no send in > 6 h (G3) | `publishing_paused` (+ new send log) | warn / danger | /admin/bots#publishing |
| Feed fail / warn; status job itself stale > 15 min | `feed_status` | per status | /admin/feeds#book |
| Coolbet footprint or any feed paused > 24 h (G7) | `coolbet_session_state`, `feed_controls` | warn | /admin/feeds#coolbet |
| Bot issues: silent, no config, "stakes without positive verdict", "/picks ≠ Telegram" | bot issue builder (**extract from `bot-board-model.ts` + `bots-board.tsx:212` into `lib/`** so both pages share one rule) | per rule | /admin/bots?bot= |
| Job failed / overdue | `getLatestJobStatuses` | danger / warn | /admin/ops#runs |
| Stale pending bets (settlement stuck) | `getStalePendingBets` | warn | /admin/ops#settlement |
| DQ findings in the last 24 h | `data_quality_findings` | warn | /admin/feeds#dq |
| Manual real bets not reconciled > 24 h (G9) | `real_bets` | warn | /admin/money |
| Deploy drift (G6) | new row from `deploy_drift_check` | danger | /admin/ops#deploy |
| Duplicate bets > 0 | `ops_snapshots.duplicate_bets` | warn | /admin/ops |

Not in the inbox: verdict mixes, pick counts, ROI. Those are information, not actions.

---

> **STATUS 2026-09-24 (end of day):** P1 ✅ (as corrected by the owner: the footprint pause stops SWEEPING only, never real bets — info-only ladder layer 7, readiness warning) · P2 ✅ · P3 ✅ · P4 ✅ (Overview) · P5 ✅ (as "Real bets", URL kept) · P6 ✅ (as "Pick queue" at /admin/shadow-bots — URLs kept: 65 smoke pins) · P7 ✅ ([bot] → redirect to the /admin/bots sheet; the bots table stays custom with a DataTable-look toolbar — it needs per-family groups, in-row switches and a shared forest-bar axis) · P8 ✅ (CS2, LoL, Tennis, Place deleted) · P9 ✅ · P10: G4 Activity ✅; G3 send proof, G5 Coolbet session health, G6 deploy drift, G8 models still open (need engine sources).

## 4. Phased moves (each small and reviewable; smoke test + 1 review agent each, 2 for anything touching money controls)

| # | Move | Dir | Size | Smoke |
|---|---|---|---|---|
| **P0** | Wait for / rebase on the in-flight `ADMIN-SHARED-SHELL` commit (the sidebar lives in `components/admin/admin-nav.ts`) | — | — | — |
| **P1** | **Add the footprint layer to the real-money ladder** (read-only, "blocks", link to Feeds) and fix the "Not a money switch" copy. It pauses the placer tick too | 🤖 OWN, correctness | S | `LADDER-INCLUDES-FOOTPRINT` (ladder.ts has a `daemons` key; mirrors `coolbet_control.can_stake` blockers) |
| **P2** | **Move the footprint switch to /admin/feeds → Coolbet block** (same `admin_set_control` write, same component), with the Mac footprint-daemon heartbeat beside it; add a 4th sidebar status line; remove the Collection group from the bots Controls card (rename the card "Publishing") | 🤖 OWN | S–M | `FOOTPRINT-SWITCH-ON-FEEDS-ONLY` (source: `daemons_paused` switch rendered only under `admin/feeds`) |
| **P3** | **Retire the unaudited writers**: delete `/api/admin/coolbet-daemons-pause` POST and the shadow `CoolbetDaemonsPause` + `CoolbetPlacerToggle` (+ `coolbet-placer-bots` POST), per spec phase B | 🤖 OWN | S | extend `CONTROL-WRITES-ONLY-VIA-FN` with no exemptions |
| **P4** | **Overview attention inbox** (§3.3) from existing loaders; extract the bot-issue builder to `lib/` | 🤖👥 BOTH: 🤖 money/feeds/jobs state, 👥 channel and published-bot issues | M | `ADMIN-ATTENTION-RULES` (each rule's source + link present) |
| **P5** | **Promote Real bets** to the sidebar as "Money" (`/admin/money`, redirect from `/admin/real-bets`); move the "today vs caps / +N manual" chip and Promotions (collapsed when empty) there | 🤖 OWN | S | `ADMIN-NAV-ROUTES-EXIST` (every nav href has a page; every page is in nav or on a delete list) |
| **P6** | **Shadow-bots → Pick queue** (`/admin/queue`): keep the picks table + Place; delete the safety strip and scoreboard (their jobs live on bots/money); redirect the old URL | 🤖 OWN | M | `QUEUE-HAS-NO-SCOREBOARD` (no `shadow_bot_scoreboard` read) |
| **P7** | **Retire `/admin/shadow-bots/[bot]`**: add "Bet made" + current-price columns to the bots sheet Picks tab, then redirect to `/admin/bots?bot=` (which also fixes #139 finding b, the in-play artefacts) | 🤖👥 BOTH: 🤖 one honest per-bot record for money decisions, 👥 same numbers as what is published | M | `ONE-PER-BOT-SCORING` (no per-bot ROI/CLV computed outside `bot_scoreboard`/`bot_ledger` in `admin/**`) |
| **P8** | **Delete dead routes**: `/admin/cs2` (+ components, `record-combo` if unused), `/admin/place`; LoL/Tennis per the owner's answer | — | S | `ADMIN-NAV-ROUTES-EXIST` |
| **P9** | **Ops → "Jobs"**: move odds coverage / live tracker / AF budget to Feeds; delete Email & alerts, Pro/Elite tiles and the stale Betting & bots section | 🤖👥 | M | source test for removed sections |
| **P10** | **Gaps with engine work**: picks-channel send log (G3, 👥), Activity page over both audit tables (G4), Coolbet session health on Feeds (G5, 🤖), deploy-drift row (G6), models section (G8) | mixed | M each | per item |

**Order rationale.** P1 comes first because it is a live honesty defect on the money page: CAN STAKE can
disagree with the engine. P2 and P3 do the move the owner asked about and remove the two unaudited write
paths in one sweep. P4 is the biggest win for "what needs my attention". P5–P9 are consolidation, and
none of them changes a money control.

## 5. Open questions for the owner
1. **Real-money ladder location.** Your decision §16.3 put it on /admin/bots, and this plan keeps it there. The alternative is a /admin/money page with ladder + ledger + queue. Keep as decided?
2. **LoL / Tennis**: delete, or keep under a collapsed Archive group?
3. **Footprint from the phone.** Once the switch lives on Feeds, do you want a Telegram `/footprint on|off` (audited)? Today there is no Telegram handle for it.
