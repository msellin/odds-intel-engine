# OddsIntel — Data Sources

> Last updated: 2026-09-05 — AF plan, limits and request-budget table corrected (AF-DOCS-STALE-2026-09-05). Previous substantive change 2026-06-25: WC odds sweep retired; The Odds API key + client retained, pivoted to tennis odds + settlement (TENNIS-PAPER-BETS).
>
> **Last verified 2026-09-05 and how:** AF plan name, expiry and both rate limits read from live `/status` response headers; usage percentages from `api_budget_log`; schedules and env gates read from `workers/scheduler.py`, `workers/live_poller.py` and `workers/jobs/live_tracker.py`. Sections below the request-budget table (backfill state, CSV ingest, dedup) were **not** re-verified in this pass and carry their own dates.

---

## Current Stack

| Source | Role | Status |
|--------|------|--------|
| **API-Football Mega** (150,000 req/day, 900 req/min; plan active to 2026-11-28) | PRIMARY — all structured data | ✅ Active. Plan name + both limits verified from live `/status` headers 2026-09-05. This row previously said "Ultra ($39/mo, 150K tier)" — the plan name was wrong; the **monthly price has not been re-verified**, so no figure is carried forward. |
| **The Odds API** (free 500/mo) | Tennis odds + settlement via `/sports/tennis_*` and `/scores` endpoints. Pinnacle confirmed across all 3 active tour tournaments (100% coverage on probe 2026-06-25). | ✅ Active. WC sweep retired 2026-06-25. |
| **OddsPapi** (free 250/mo) | Historical Pinnacle closing-odds backfill for soccer CLV (one-shot via `scripts/ingest_oddspapi_pinnacle_closes.py`). | ⚠️ Quota exhausted 2026-06-25 — tennis scanner that was burning the budget is being replaced by The Odds API. Last backfill 2026-06-15 (12,218 rows / 219 matches into `odds_snapshots`). |
| **football-data.co.uk** (free) | Historical odds + secondary stats CSVs (CSV-FULL-EXTRACT 2026-06-04 captures 9 bookmakers × 1X2 + OU 2.5 + AH, open + close) | ✅ Active. ~80-120K net-new rows per season-set ingest. |
| ESPN (free) | Settlement result backup | ✅ Active (backup) |
| **Epicbet** (free, EE-licensed) | Second operator-reachable book in `odds_snapshots` (EPICBET-ODDS-INGEST-2026-08-27). Anonymous REST JSON (tRPC), no auth/JWT/session. **Cloudflare-protected: a plain VPS request gets a 403 'Just a moment' JS challenge, but FlareSolverr (a real browser) solves it FROM THE VPS and returns 200** (EPICBET-403-FROM-VPS-2026-08-29) — so it runs VPS-side on the normal scheduler via `workers/automation/epicbet_explorer.py`, no Mac. This is the **most robust of the 3 direct books** (Coolbet=Imperva+Mac, Unibet=DataDome+Mac; Epicbet=Cloudflare, VPS-beatable via FS) — which is direct evidence that the Coolbet/Unibet Mac dependency is a datacenter-IP block, not generic protection. Markets **as of EPICBET-SIDEBETS-CORNERS 2026-09-06**: 1X2, OU 0.5–4.5, BTTS, AH, **total/team corners, corners handicap, total/team cards, 1H goals O/U, 1H 1X2, team totals and double chance**. The earlier claim that Epicbet has no `double_chance` was **wrong** — DC (group 96) is on every fixture sampled; it is simply absent from the shallow `match.getFoByLeague` listing we used to read. The deep board comes from `match.getSidebets?marketType=main`, one call per matched fixture, bounded by `EPICBET_SIDEBETS_LIMIT` (default 250). | ✅ Active. **601K rows / 495 matches / 119 markets per 2-day window, newest ~5 min (measured 2026-09-10)** — the richest of the 3 direct books. DB-side staleness watchdog: `job_epicbet_odds_freshness`. |
| **Unibet via Kambi** (free) | Direct Unibet pre-match prices every 30 min at :04/:34 (UNIBET-KAMBI-ODDS 2026-09-04, `workers/automation/unibet_kambi.py`). Public unauthenticated JSON. Writes `bookmaker='Unibet-Kambi'`, deliberately kept separate from AF's own `Unibet` feed so the two can be compared. **Not** a live/in-play source. **⚠️ NOT A PLACEABLE PRICE as of 2026-09-06 (KAMBI-FEED-DIVERGENCE)** — unibet.ee has moved off the Kambi offering API this reads (its event pages render against Kindred's own `sportsbff-ams` host and make zero requests to `kambicdn.com`). On 130 of 341 sampled selections (38%) the stored price is HIGHER than the site actually offers — median +3.3%, max +23.5% — at an identical 38% both under 6h and over 15h to kickoff, so it is feed divergence rather than staleness. **Removed from `ACCESSIBLE_BOOKMAKERS` 2026-09-06; INGESTION STOPPED 2026-09-15 (UNIBET-KAMBI-RETIRED).** The "keep ingesting so the fix stays measurable" rationale did not survive an audit: nine days on, the feed had **no consumer at all** — it is named in `daily_pipeline_v2`, `settlement`, `bot_inventory`, `lineshop_new_markets`, `own_line_movement`, `own_movement_snapshot`, `own_sharp_config_sweep` and `publish_picks_forward_test` **only as an exclusion**, two of them with comments warning the next author not to re-type the exclusion set. A feed whose sole appearance is "do not use this" is a standing trap rather than a measurement: ~815k rows in ten days into the largest table in the DB, and it had already become the #1 `recommended_bookmaker` (403 of 1,015 picks in three days) at prices that did not exist. Its one advantage — corners/cards breadth — is now taken from the true site feed. **Cost, stated honestly:** Kambi reached ~3,011 fixtures against Unibet-Site's ~2,026, so this trades Unibet-brand coverage for obtainable prices. Module + job function kept for manual runs; only the cron is gone. | ⛔ Retired 2026-09-15 (historical rows kept) |
| **Unibet SITE via Kindred** (real placeable price) | The TRUE unibet.ee site prices, captured from the SPA's own `contest-page` responses (Kindred `sportsbff-ams`) via a RAW CDP Network session on the operator's established, logged-in tab (`workers/automation/unibet_odds_feed.py`, UNIBET-UI-PLACER build-step 1, 2026-09-09). Writes `bookmaker='Unibet-Site'` — the price we can actually bet, as opposed to the divergent `Unibet-Kambi` above (proven Derby: Kambi 3.20 vs Site 3.50). **Broad sweep (2026-09-09, `run_bulk`):** the SPA rejects a bare request (HTTP 400) and DataDome blocks headless tabs, but an **injected `fetch()` WITH the SPA's static headers** (`brand:unibet, jurisdiction:EE, locale:et_EE, ksp_jurisdiction:mga`) from the operator's established tab returns 200 with true prices — so the sweep enumerates events per country (root quickbrowse → country RNs → country lobby) and injected-fetches each matched fixture's contest-page. Rate-limited (1.2s, cap 180, abort on repeated blocks), fail-safe, non-disruptive (never navigates the tab). **Scheduled on the VPS since 2026-09-23 (UNIBET-ON-VPS)** — scheduler job `unibet_site_odds` (:15/:45) reading a **logged-OUT** tab in `oddsintel-unibet-chrome.service` (Chrome under Xvfb, CDP on loopback, Estonian egress). The prices are public: logged-out reads matched the Mac's logged-in rows exactly on 11/13 fixtures (the other two had moved). Login is needed only to PLACE, and from the VPS egress it draws a DataDome captcha, so the reader never attempts it. (Was: Mac launchd `com.oddsintel.unibet-site-odds`, logged-in tab.) Read-only; placement is `unibet_placer`. **Markets (widened 2026-09-15, UNIBET-SITE-MARKET-WIDENING):** an audit of the captured contest-page found it returns **21 propositions while the parser took 3** — so this book wrote 1X2 + O/U + team totals while Epicbet and Coolbet wrote BTTS, double chance, corners and cards for the same fixtures. Now parsed: 1X2, match O/U, team totals (`{competitor1}_total`/`{competitor2}_total`), **BTTS**, **double chance** (`1x`/`12`/`x2`), **draw no bet**, **1st-half goals** (`over_under_1h_NN`), **corners** (`corners_ou_NN`), **1st-half corners** (`corners_1h_ou_NN`) and **cards** (`cards_ou_NN`) — 24 rows from the fixture where 9 were taken before. ⚠️ **Asian handicap is deliberately NOT parsed and cannot simply be added:** `2_way_handicap`/`3_way_handicap` arrive with `total: null`, i.e. the handicap LINE is absent from the payload, and an AH row without a line is unpriceable. Tracked as UNIBET-SITE-AH-LINE. The contest page also has **no first-half 1X2** proposition (only `half_time_full_time`), so `1x2_1h` cannot come from this feed. | ✅ Active (broad sweep, VPS) |
| **Tonybet** (EE-licensed, Osaühing Tonybet; 20bet is the same platform and prices) | **Added 2026-09-23 (#101).** Anonymous REST `platform.tonybet.com/api/event/list` through the zone.ee Estonian exit; scheduler job `tonybet_odds_snapshot` :01/:31, next 48 h pre-match main board (~6 requests). Matched 188 of 252 DB fixtures on the first dry run — more than any other direct book. Stored in the shared vocabulary keyed on **Sportradar UOF ids** (`vendorMarketId`/`vendorOutcomeId`): `1x2`, `over_under_05–45`, `asian_handicap` (lines where both sides price 1.25–4.0), `btts`, `double_chance`, `draw_no_bet`. **Every outcome carries Sportradar's margin-free probability** → `book_fair_probs` (migration 383, latest value per key = fair close). **Raw archive:** every response gzipped to `/opt/oddsintel/raw/tonybet/YYYY/MM/DD/` (~60 MB/day) so later parsers can rebuild history. Placeable (`ACCESSIBLE_BOOKMAKERS`) since the 2026-09-23 site check (14/15 prices identical); part of the public forward test's "all books" universe since 2026-09-23 (owner's go-ahead). **Phase 1b (2026-09-23):** full market board (`main=0`, ~120–140 types) for our matched fixtures at ~24 h / 3 h / 30 min before kickoff and at the close (near-kickoff timer) → also `team_total_*`, `team_total_1h_*`, `1x2_1h`, `1x2_2h`, `over_under_1h/2h_*`, `btts_1h/2h`, `double_chance_1h`, `corners_*` (total, team, 1H, handicap) and **`bookings_*`** (Sportradar bookings — deliberately NOT filed as `cards_*`: counting rule unproven). Player/goalscorer, correct score, combos and interval markets stay in the raw archive only. **Phase 2:** live score/clock/status/corners/cards for EVERY live football event every 120 s → `book_live_stats`; FT/HT/2H results + final corners/cards every 2 h → `book_match_results` (migration 385; Tonybet purges results after ~1–2 days). Design: `dev/active/tonybet-sweeper-plan.md`, capability map `dev/active/tonybet-af-capability-map.md`. | ✅ Active (pre-match + live + results, VPS) |
| **The Odds API — Coolbet** (licensed fallback) | **Adapter built 2026-09-23 (#110 step 4), NOT scheduled — needs the owner's `ODDS_API_KEY`.** `workers/automation/odds_api_fallback.py` reads Coolbet (`coolbet`, region `eu`) 1X2 + goal totals from the-odds-api.com, runs only while our own `coolbet_prematch` sweep is paused (else `--force`), and stores as bookmaker **`Coolbet-OddsAPI`** — deliberately NOT in `ACCESSIBLE_BOOKMAKERS`, so it cannot price a pick or a stake until the owner compares it with our own Coolbet rows and promotes it. ~2 credits per soccer competition per sweep (~80 per sweep). A paid, licensed source used during an outage — not a way round a block. | ⏸ Built, awaiting key |
| ~~Kambi API — original 41-league sweep~~ | Supplementary odds — removed 2026-05-06 (all 41 leagues already covered by AF; "ub"/"paf" bookmakers provided <5% best-odds). Superseded by the narrower Unibet-only ingest above. | Removed |
| ~~BetExplorer~~ | Gap league odds — removed 2026-04-29 (fragile HTML scraping, low value) | Removed |

**What API-Football covers:** fixtures, odds, live scores, lineups, injuries, standings, H2H, match events, player stats, team stats, transfers, xG (post-match via /fixtures/statistics). 1,236 leagues (figure not re-verified since 2026-04; treat as approximate).

We call **18** of the ~37 endpoints the plan exposes (`workers/api_clients/api_football.py`).

Three odds caveats worth knowing before planning against this feed:

- **Bookmaker coverage is partitioned per fixture.** "13 bookmakers" is an account-level ceiling, not what any given fixture returns. Unibet and Betano lost forward coverage for fixtures dated 2026-09-06 onward.
- **AF retains odds for exactly 7 days**, then drops them. Anything older must come from our own `odds_snapshots` or a historical source.
- **Pinnacle sends 19 bet types through the bulk `/odds` response, not 8 — we parse 15.** See `docs/ANALYSIS_GOTCHAS.md` § 45.

---

## Odds retention & anchors (what we keep, and for how long)

`odds_snapshots` is the largest object in the database by far — **46.3M rows /
20 GB**, versus 864 MB for the next biggest table — and it is governed by
`scripts/prune_odds_snapshots.py` (nightly 03:00 UTC). The policy: keep every
tick for 7 days, then keep only the **opening** and **closing** anchors plus the
latest pre-kickoff row per price series. In-play rows are **downsampled to one
per minute** and kept indefinitely.

**DIRECT-BOOK-ANCHORS-2026-09-11 — the books we bet had no anchors.** Anchor
flags are stamped at write time as `abs(minutes_to_kickoff) <= N`. The
API-Football writer uses N=15; the direct-book writers used N=5, and our direct
sweeps run every 30 minutes, so a 10-minute-wide window almost never contained a
snapshot. Measured all-time before the fix:

| book | rows | `is_closing` | `is_opening` |
|---|---|---|---|
| Pinnacle | 3,732,910 | 1,462,147 (39%) | 405,615 |
| Betano | 3,135,310 | 1,017,790 (32%) | 447,467 |
| Unibet (AF feed) | 2,393,734 | 729,509 (30%) | 372,498 |
| **Epicbet** | 1,663,230 | **1,544 (0.09%)** | **0** |
| **Coolbet** | 481,753 | **1,057 (0.22%)** | **12** |
| **Unibet-Site** | 35,340 | **0 (0.00%)** | **0** |

So retention reduced the three books we can actually stake at to a single
surviving row per series, with no opening price anywhere — hence no own-book
open-to-close drift, while we hold 405,615 Pinnacle openings. Both direct
writers now use the 15-minute window and compute `is_opening` in the INSERT
(partitioned on `handicap_line`, so each AH rung gets its own opening).
**Historical openings are unrecoverable** — those early rows are already pruned;
this only accrues forward, at ~24k rows/day.

**NEAR-KICKOFF-CAPTURE-2026-09-11 — a real CLOSE at the books we bet.** The
15-minute window above only helps when a sweep happens to land near kickoff, and
the sweeps are slow: Coolbet's evening board sweep takes 60-75 minutes end to
end, so the last Coolbet price before kickoff was typically 1-5h old (3-day
closing coverage: Coolbet 23 matches, Epicbet 82, Unibet-Site 139, Pinnacle 539).
Two pieces fix it:

- `book_event_map` (migration 333) — the AF fixture ↔ book event id pairing the
  three sweeps already compute, now persisted instead of discarded.
- `workers/jobs/near_kickoff_capture.py` (Mac launchd, every 5 min) — for
  fixtures kicking off in the next 15 min, fetches that one event per book by id
  and writes it with `minutes_to_kickoff` ≤ 15, so it is stamped `is_closing`.
  It never walks a board; a (match, book) priced in the last 6 minutes is skipped.

Consumer: own-book real-bet CLV (DIRECT-BOOK-CLV, migration 332 —
`real_bets.clv` / `closing_bookmaker` / `closing_minutes_before_ko`), and the
report `scripts/direct_book_clv_report.py`.

**REFERENCE-BOOK-OPENING-TRIM-2026-09-11 (owner-approved).** Books we can
neither bet from Estonia nor use as the sharp anchor no longer keep `is_opening`
rows past the retention window. Their **closing** rows stay — the published
best-of-books comparison reads them, and `ou25_bookmaker_disagreement` +
`market_implied_btts_yes` are recomputed from full history across all books on
every Sunday retrain. Two exemptions make this safe and cost most of the saving:
`market='1x2'` is untouched (the MFV builder takes `opening_implied_*`,
`odds_drift_home` and `steam_move` from the earliest 1x2 row across all books,
not from the flag), and a series must keep at least one other row. Eligible
today: **1,767,302 rows ≈ 760 MB**, plus ~74k reference openings written per day
of which roughly 56% become eligible — about **6.5 GB/yr** of avoided permanent
growth. (An earlier estimate of ~3.3 GB for the one-off was wrong: it halved a
6.6 GB figure that covered both anchor kinds.)

CLV was never affected: `CLOSING-PRE-KO-FALLBACK` (settlement.py) resolves
against the surviving pre-kickoff row, and measured coverage is `real_bets`
Coolbet 930/977 = 95.2%, `shadow_bets` 30d Coolbet 100.0% / Epicbet 99.4%.

## Daily Request Budget (API-Football **Mega** — 150,000/day, 900/min)

Verified 2026-09-05 from live `/status` headers. The old version of this table was
headed "Ultra — 75K/day limit" and every percentage in it was computed against
75,000; the real daily ceiling is **150,000** and has been for as long as
`api_budget_log` goes back.

**Measured** account-wide usage over recent days: **9–23% of the daily limit**
(roughly 13,700–35,000 calls/day). That measured total is the number to trust —
the per-operation rows below are **estimates last derived 2026-04/05 and not
re-measured**, so they no longer sum to the observed total (pre-match odds in
particular now runs every 30 min 24/7 rather than every 2h, so its ~400 is low).

| Operation | Calls/day (estimate) | Pipeline |
|-----------|-----------|----------|
| Fixtures | ~5 | Morning |
| Pre-match odds (T1 + odds) | ~400 (stale — now every 30 min, 24/7) | Morning + every 30 min |
| Predictions (T1) | ~130 | Morning |
| Team stats (T2) | ~80 | Morning |
| Injuries (T3) | ~7 | Morning |
| Standings (T9) | ~40 | Morning |
| H2H (T10) | ~130 | Morning |
| Live fixtures (T6) | ~3,500 (derived from cadence, not measured) | LivePoller fast tier (45s live / 120s idle, bulk) |
| Live odds (T5) | **0** | Gated off 2026-08-21 (`INPLAY_LIVE_ODDS_POLL_ENABLED`) |
| Live stats (T6) | **0** | Gated off 2026-08-21 (`INPLAY_STATS_EVENTS_POLL_ENABLED`) |
| Live events (T8) | **0** live; still fetched at settlement | Gated off 2026-08-21 |
| Lineups (T7) | ~50 | LivePoller slow tier (7.5 min, pre-KO) |
| Post-match stats (T4) | ~120 | Settlement |
| Player stats (T12) | ~120 | Settlement |
| **Measured total** | **~13,700–35,000** | **9–23% of 150,000** |

Before the 2026-08-21 gates the ceiling was genuinely hit — 149,800/150,000 on
Aug 1/2/8/9. The in-play product is retired: no in-play bot picks and no
`is_live=true` odds rows since 2026-08-21. Reviving it means re-enabling those
env gates and re-accepting the quota cost.

Headroom is therefore large: ~115K–136K req/day unused. **Do not downgrade** —
the cheaper AF tiers are an order of magnitude smaller (the old Pro tier was
7,500 req/day). Exact current tier names and prices were **not** re-verified in
this pass; check AF's pricing page before acting on any downgrade.

### Per-minute limit is the binding constraint, not the daily quota (2026-08-24)

The daily quota is not the problem *today* — usage runs 9–23% of the 150,000/day
limit (it did hit the ceiling in early August, before the 2026-08-21 in-play gates).
The **per-minute** limit is what actually bites: `journalctl -u oddsintel-scheduler`
shows 100–2,300 HTTP 429 `"exceeded the limit of requests per minute"` responses
*every day*, and those 429s are what fed both scheduler hangs (SCHEDULER-AF-429-DEADLOCK,
SCHEDULER-STALL-RCA).

Two structural reasons, both worth knowing before adding any AF-touching job:

1. **The rate limiter is per-process, the quota is per-account.** `MIN_REQUEST_INTERVAL`
   in `workers/api_clients/api_football.py` throttles one Python process to ~8 req/s.
   But the same API key is used concurrently by the VPS scheduler, the LivePoller
   thread, the `coolbet_health_ping` subprocess, the `match_status_sweeper` GitHub
   Actions cron, and any manual script — none of which can see each other's rate.
   Adding a new AF caller adds its full burst on top.
2. **Bursts, not averages, trip it.** Startup catch-up and any per-fixture fan-out
   loop issue their calls back-to-back.

Every AF request is now bounded by `AF_TIMEOUT_S` / `AF_MAX_ATTEMPTS` /
`AF_RETRY_BUDGET_S`, and `Retry-After` is honoured when AF sends it, so a 429 storm
costs bounded time instead of hanging a scheduler job. That makes the 429s survivable;
it does not make them go away. A real fix (a shared cross-process token bucket, or
simply fewer callers) is not yet filed as its own task — see AF-QUOTA-REALLOCATION.

---

## Integrated Endpoints (T1–T13)

| Task | Endpoint | Pipeline | Status |
|------|----------|----------|--------|
| T1 | `/predictions` | Morning | ✅ Done |
| T2 | `/teams/statistics` | Morning | ✅ Done |
| T3 | `/injuries` (batched 20/call) | Morning | ✅ Done |
| T4 | `/fixtures/statistics?half=1/2` | Settlement | ✅ Done |
| T5 | `/odds/live` | Live tracker | ⏸️ Built, **gated off** 2026-08-21 (`INPLAY_LIVE_ODDS_POLL_ENABLED`, default false) |
| T6 | `/fixtures?live=all` | Live tracker | ✅ Done — still polling |
| T7 | `/fixtures/lineups` | Live tracker (pre-KO) | ✅ Done |
| T8 | `/fixtures/events` | Live tracker + settlement | ⏸️ Live polling **gated off** 2026-08-21 (`INPLAY_STATS_EVENTS_POLL_ENABLED`); settlement path still runs |
| T9 | `/standings` | Morning | ✅ Done |
| T10 | `/fixtures/headtohead` | Morning | ✅ Done |
| T11 | `/sidelined` | Backfill script | ✅ Done |
| T12 | `/fixtures/players` | Settlement | ✅ Done |
| T13 | `/transfers` | Backfill (opt-in `--transfers`) | ⛔ **Retired from the daily default 2026-09-21** (AF-TRANSFERS-NO-READER). Fetcher kept for manual runs; 1.44M historical rows kept. |

---

## Backfill state — what's actually in the DB (audit 2026-05-19)

Audit triggered by TIER-C-EXPAND debugging surfaced several non-obvious facts about our historical data state. Captured here so future agents (and the human) don't re-derive them.

### `backfill_historical.py` is "complete" but partial

- `backfill_complete.flag` (repo root, dated 2026-05-10) makes the script short-circuit. **To force a re-run: `rm backfill_complete.flag`.** Rarely useful — see next bullet.
- Every `backfill_progress` row is marked `status='complete'` and has `fixtures_done == fixtures_total`. So all PHASE 1/2/3 leagues were processed.
- BUT `stats_done < fixtures_done` for many rows (e.g. Mexico Liga MX 2025: 327 fixtures, 147 stats — 45%). The `ROADMAP.md` figure of "73.4% match_stats coverage" is the global aggregate of this. **The 27% gap is irreducible** — AF doesn't supply stats for many small-league / lower-tier / women's / U-21 matches. Re-running the backfill won't add data AF doesn't have.
- **Critically**: every row has `odds_done = 0`. The historical-odds path was never wired up in `backfill_historical.py` — the comment in the code reads `"AF doesn't serve historical odds for completed fixtures"`. This is the gap that `scripts/ingest_football_data_extras_odds.py` (TIER-C-EXPAND-ODDS) closes for the 14 TIER-C-EXPAND countries by pulling Pinnacle / Bet365 closing odds from football-data.co.uk.

### DB match coverage for the TIER-C-EXPAND countries

Snapshot 2026-05-19 (top division + tier-0 catch-all leagues):

| Country | League | AF ID | Finished matches in DB |
|---|---|---|---|
| USA | MLS | 253 | 1,641 |
| Argentina | Liga Profesional | 128 | 1,297 |
| Argentina | Primera Nacional | 129 | 1,433 |
| Brazil | Série A | 71 | 1,170 |
| Brazil | Série B | 72 | 790 |
| Mexico | Liga MX | 262 | 1,015 |
| Japan | J1 League | 98 | 811 |
| Sweden | Allsvenskan | 113 | 754 |
| Norway | Eliteserien | 103 | 749 |
| Switzerland | Super League | 207 | 689 |
| Poland | Ekstraklasa | 106 | 603 |
| Austria | Bundesliga | 218 | 582 |
| Denmark | Superliga | 119 | 578 |
| Czech | Liga | 345 | 546 |
| China | Super League | 169 | 516 |
| Russia | Premier League | 235 | 484 |

All dating back to 2023-01-26. Total finished matches in DB across all leagues: ~52K.

### football-data.co.uk gotchas

- `/new/CHE.csv` returns **Chinese Super League** data (collision with `/new/CHN.csv`). Switzerland is unavailable via this directory. Use the mainstream `mmz4281/<season>/SC0.csv` route if needed.
- `/new/<CODE>.csv` files contain **all seasons in one file**, columns: `Country, League, Season, Date, Time, Home, Away, HG, AG, Res, PSCH/D/A, MaxCH/D/A, AvgCH/D/A, BFECH/D/A, B365CH/D/A`. No separate file per season (unlike the mainstream `E0/SP1/D1` style which has one CSV per season).
- Team-name churn across seasons is real: Norway has both `"Ham-Kam"` and `"HamKam"` strings; Russia has `"Arsenal Tula"` which can fuzzy-collide with English `"Arsenal"`. Existing `normalize_team_name` + `resolve_team` (with `rapidfuzz`) handles most cases; watch the `unmatched_teams` log on each ingest run.

### targets_extended.csv — Phase 4+5 DB export (added 2026-05-28)

`data/processed/targets_extended.csv` is generated by `scripts/generate_targets_extended.py` (single PostgreSQL COPY TO STDOUT — no Python row loops). It exports all finished DB matches whose AF league ID is NOT already covered by `targets_poisson_history.csv` or `targets_global.csv`. At pipeline startup, `daily_pipeline_v2.py` `pd.concat`s it into `hist_targets_global`, promoting those teams to Tier B (2% edge bump vs Tier C's 8%).

**To rebuild after new backfill data:**
```bash
python3 scripts/backfill_historical.py --phase 4  # then --phase 5
python3 scripts/generate_targets_extended.py
```

The script auto-discovers eligible leagues from the DB (≥10 finished matches, not in existing CSVs) — no code change needed when new leagues are added.

### National-team data — WC 2026 prep (added 2026-06-02, WC-PHASE-2)

The original `backfill_historical.py` is club-league focused. National-team competitions sit under AF country=`"World"` and have to be opted in separately. Before 2026-06-02 the DB had only `Friendlies` (73 matches, all 2026-04 onward) — no World Cups, no Euros, no qualifiers. This is a problem because the existing prediction model is trained on club-level features (`league_tier`, season-form), and we have nothing to train a national-team variant on either.

`scripts/backfill_internationals.py` (WC-PHASE-2) pulls 59 (league, season) tuples covering:

- World Cup 2018, 2022 (group + knockout)
- Euro 2020 (+ qualification), Euro 2024 (+ qualification)
- Copa America 2021, 2024
- AFCON 2019, 2021, 2023, 2025
- Asian Cup 2019, 2023 (+ qualification)
- CONCACAF Gold Cup 2019-2025 (all 4 editions), CONCACAF Nations League 2022-2024
- UEFA Nations League — all 4 editions (2018-19, 2020-21, 2022-23, 2024-25)
- WC 2022 qualifiers — all 6 confederations + intercontinental playoffs
- WC 2026 qualifiers — all 6 confederations + intercontinental playoffs
- Friendlies 2022-2025 (deduplicated against the 2026 set already in DB)
- Regional: ASEAN Championship, Gulf Cup, SAFF Championship, CAFA Nations Cup, Arab Cup, Finalissima

Total: ~3,000+ finished international matches. Two-phase: (A) fixtures via `get_fixtures_by_league_season` + `bulk_store_matches`, (B) nested data (lineups, events, statistics, player stats) via `get_fixtures_batch` for finished matches only. Idempotent — re-running skips already-stored fixtures (upsert on `api_football_id`) and already-enriched matches (filter on existing `match_stats` rows).

WC 2026 group-stage fixtures (72 matches, league=1 season=2026) were backfilled separately under WC-PHASE-1 via the new `fetch_fixtures --league/--season` mode. They land in DB with `season=2025` per our football-season convention (June = previous year); frontend filters by date + `show_on_frontend`, not season.

**WC odds gap — RETIRED 2026-06-25.** Previously filled via daily The Odds API sweep of `soccer_fifa_world_cup` (5,858 row first sweep + daily 06:30 UTC cron 2026-06-11 → 2026-07-19). Removed because WC's commercial relevance to us is minimal and the credit budget is better spent on tennis (TENNIS-PAPER-BETS). The Odds API key + `workers/api_clients/odds_api.py` client retained.

### Training-pipeline data sources (clarification)

The Sunday weekly retrains (`fit_platt_offline.py`, `fit_league_rho.py`, `train.py`) all read from the **DB** (`matches`, `predictions`, `odds_snapshots`). They do NOT read `targets_poisson_history.csv` directly.

The CSVs only feed `daily_pipeline_v2.compute_prediction()` at runtime for team-form lookup on live matches. Expanding the CSV (Lever 1 / TIER-C-EXPAND) helps live inference; it does not by itself feed training. The chain that feeds training is: backfill matches → backfill odds → backfill predictions → Sunday retrains pick up the new rows.

---

## football-data.co.uk CSV ingest — full extraction (CSV-FULL-EXTRACT, 2026-06-04)

The CSV ingest (`scripts/ingest_football_data_csvs.py`) was previously only writing 4 of the 120 columns per main-league CSV row (Pinnacle + Bet365 1X2 closing + OU 2.5 closing). CSV-FULL-EXTRACT extended it to capture the complete column set across 9 bookmakers (Pinnacle, Bet365, Betfair Exchange, BetWin, Betfred, William Hill, 1xBet, plus synthetic Max and Avg consensus) for 1X2, OU 2.5, and Asian Handicap markets, closing **and** opening lines, with `handicap_line` set on every AH row. Also backfills match secondary stats (HS/HST/HC/HY/HR/HF and away counterparts) into `match_stats` and `matches.referee` where AF's value is NULL.

Row count delta on the recent-seasons run (2223 + 2324 + 2425 + 2526, all 14 main leagues):

| Bookmaker | Markets | Rows | Notes |
|---|---|---|---|
| Betfair Exchange | 1X2 + OU 2.5 + AH (close + open) | ~118K | net-new — was 0 |
| Max consensus | 1X2 + OU 2.5 + AH (close) | ~80K | net-new — was 0 |
| Avg consensus | 1X2 + OU 2.5 + AH (close) | ~80K | net-new — was 0 |
| Pinnacle | AH closing with `handicap_line` | ~18K | net-new — pre-CSV-FULL-EXTRACT all 184K Pinnacle AH rows were from AF live feed (post-Apr 2026) with NULL line |
| Bet365 | AH closing with `handicap_line` | ~23K | net-new |
| Betfair Exchange | AH closing with `handicap_line` | ~17K | net-new |
| BetWin / Betfred | 1X2 closing | ~85K | net-new |

Older CSV seasons (2009-2022) are on disk but skipped — the `matches` table only goes back to 2023.

Backtest verdicts (`scripts/backtest_csv_full_extract.py`, results in `dev/active/csv-full-extract-backtest-results.md`):

1. **Pinnacle vs Betfair Exchange anchor** (7,328 paired matches) — identical to 4 decimals (Brier 0.5886/0.5887, LogLoss 0.9862). **Keep Pinnacle anchor** (CAL-PIN-SHRINK).
2. **AH market sanity** (8,868 paired matches) — flat home ROI −5.4%, away +0.9%. Market efficient at Pinnacle close. Backtest universe now exists for future AH bot development.
3. **Pinnacle open→close drift** (8,850 paired matches) — strong monotonic signal, **+8.76pp WR spread** top vs bottom quintile. New `pinnacle_drift_home/draw/away` columns added in migration 179; backfill via `scripts/backfill_pinnacle_drift.py`. ✅ **Writer fixed 2026-09-11 (DRIFT-FEATURE-WRITER): coverage 0% → 46.5%** (10,418 of 22,413 MFV rows in the last 60 days), now refreshed nightly at 02:30 UTC. The script had two faults — never scheduled, and an open/close join that fanned out and silently dropped 84% of computable matches. ⛔ Note it is **not** a model input and never will be: it needs the closing price, so it cannot be computed before we bet. The +8.76pp figure is a post-hoc result; the pre-kickoff equivalents are the `*_at_t6h` columns.

AH-bot prototype follow-up (`scripts/backtest_ah_bot_prototype.py`, 5,254 derivable-line matches) showed naive "ensemble 1X2 → AH derivation" loses to vig at every edge threshold (ROI worsens as filter tightens — signature of noise). A real AH bot requires a dedicated goals model — shelved for now.

## Remaining Cleanup

- [x] ~~Remove `betexplorer_odds.py`~~ Done 2026-04-29
- [x] ~~Remove Sofascore scrapers~~ Done 2026-04-29
- [x] ~~Activate The Odds API for Pinnacle odds~~ Done 2026-06-06 (ODDS-API-WC) → retired 2026-06-25 (WC commercial value minimal). Key + client repurposed for tennis (TENNIS-PAPER-BETS).
- [x] ~~Evaluate API-Football Pro ($19/mo, 7.5K req/day)~~ — dropped 2026-09-05. Written when we believed the plan was 75K/day; we are on **Mega (150K/day)** and use 9–23% of it, but 7.5K/day is far below even that floor, so a downgrade to a Pro-sized tier is not viable. Tier names/prices unverified — re-check AF pricing if this is ever revisited.

---

## Evaluated and rejected — do not reconsider without new information

Sources that were looked at properly and ruled out. They are recorded here *with
the reason* so the same evaluation is not paid for twice; several of these look
attractive on their pricing page and the disqualifier is not visible there.

| Source | Looks like | Why it is ruled out | Ruled out |
|---|---|---|---|
| **Betfair Exchange website (public read endpoints)** | Sharp exchange prices for a wider anchor ([[#115]]) | **Geo-empty.** From the Finnish VPS the site loads but every market query returns no markets, even in a real browser — the exchange serves nothing to this jurisdiction. Reaching markets would need an IP where Betfair operates = geo-evasion; declined. AF's live 'Betfair' is the sportsbook (11.3% margin), not the exchange. Licensed route: The Odds API `betfair_ex_eu` (67 top competitions only). | 2026-09-24 |
| **Betfair Exchange via London exit** ([[#117]]) | Second sharp reference beside AF-Pinnacle; liquidity per quote | **LIVE 2026-09-24.** Reads the exchange's public pages (the site's own queries) through a DigitalOcean London Droplet (SOCKS :1082, `oddsintel-egress@betfair`) — the exchange serves no markets to our Finnish VPS. Owner's decision: reading prices only, accepting Betfair's no-scraping terms risk. `workers/automation/betfair_exchange_feed.py`, every 15 min, ~11 requests/run: first run 194 events / 386 markets (MATCH_ODDS + O/U 2.5), 136 matched to our fixtures. Stored in `exchange_quotes` (back, lay, sizes, market matched volume) — NOT odds_snapshots; thin markets are placeholders (1.10/110, €0 matched), `is_liquid` = spread ≤5% and ≥€1,000 matched. Not placeable, never published. | 2026-09-24 |
| **Betfair Exchange Developer Program** — free Delayed Application Key | A free exchange feed (1–180 s data lag) — i.e. the one price series with no bookmaker margin in it, which is exactly what a sharp anchor wants | **Licence, not latency.** The free Delayed App Key is granted for **personal use only**. OddsIntel is a commercial project (paid tiers, published picks), so ingesting it would breach the Developer Program terms. The 1–180 s lag is irrelevant to pre-match use and is *not* the blocker — the blocker is that we are not an eligible user of the free key. A paid/Live App Key is a separate commercial application and is not covered by this note. | 2026-09-21 |

**Re-open only if** the licence position changes (a commercial key is obtained,
or Betfair's terms change), not because the lag or the coverage looks acceptable.

---

## Over/Under bookmaker blacklist (ODDS-QUALITY-CLEANUP, 2026-05-10)

These three sources ship clearly broken Over/Under data and are excluded from
both ingestion (`workers/jobs/fetch_odds.py`, `workers/api_clients/supabase_client.py:store_odds`)
and the read-path best-price aggregator (`workers/jobs/daily_pipeline_v2.py:_load_today_from_db`).
1X2 and BTTS rows from the same sources are kept — those markets verified clean.

| Source | Why blacklisted |
|---|---|
| `api-football` | Synthetic AF source; 100% of OU pairs invalid (avg implied-sum 0.63 across all OU lines). Not a real market feed. |
| `William Hill` | Line labels appear shifted: 88% Under-favored on OU 1.5, 100% Under-favored on OU 2.5/3.5/4.5. Stored "Over 1.5" matches real Over 2.5 prices. |
| `api-football-live` | In-play live odds; max 21.0. Belongs in live snapshots, not pre-match best-price. |

In addition to the source blacklist, both write paths and the read-path
aggregator apply an **implied-sum sanity gate**: drop both sides of any
`(over, under)` pair where `1/over + 1/under < 1.02` (mathematically impossible
market — every legit feed has overround ≥ 2%). This auto-quarantines any
future broken source without code changes.

Constants live in `workers/utils/odds_quality.py` (`BLACKLISTED_OU_SOURCES`,
`MIN_OU_IMPLIED_SUM`, `filter_garbage_ou_rows`). Smoke tests prefixed
`ODDS-QUALITY-CLEANUP — …` guard each path.

**Nordic books (Paf, Coolbet, Veikkaus, Svenska Spel, Norsk Tipping)** are not
in the AF feed — adding them requires a separate scraper (`NORDIC-BOOKS-INTEGRATION`).

## Match deduplication (MATCH-DUPES-CLEANUP, 2026-05-10)

`matches` table now has a partial unique index `matches_af_id_unique ON matches(api_football_id) WHERE api_football_id IS NOT NULL` (migration 089). Every fixture from API-Football is keyed on `api_football_id` at the DB level — the previous app-only dedup on `(home_team_id, away_team_id, date_prefix)` silently dropped a fixture's identity when AF rescheduled it across a UTC day boundary, producing 1,425 dupe groups before the cleanup.

`bulk_store_matches` and `store_match` (workers/api_clients/supabase_client.py) now look up existing rows **by `api_football_id` first**, falling back to the team/date window only for legacy rows without an AF id. This makes the dedup survive reschedules.

Historical dupes (3,177 rows) are preserved in `matches_dupe_quarantined` with `canonical_id` and `quarantined_at` columns for forensic rollback.

## xG — what we actually have, and the option we are currently destroying (2026-09-23)

Measured live against the AF statistics endpoint while answering *"how can we
improve xG coverage?"*. Full detail on [[#078]].

**Coverage today: 26,017 of 56,463 stats rows carry xG, and it is FALLING.** Daily
xG on our stats rows went from **109/156 on 2026-08-30** to **0–1/day across
2026-09-04..08**, with partial recovery around 09-19, while stats-row volume held
steady. Probed directly: a live MLS fixture still returns `expected_goals`, an
Argentine Liga Profesional fixture returns **no such field at all** — and that
league had 109 xG matches in the preceding 90 days. **This is a supplier coverage
change, not a parse bug.**

> **CORRECTED 2026-09-24 ([[#111]]): it is a supplier DELAY, not a withdrawal.** From
> ~2026-08-31 AF publishes `expected_goals` 1-4 days after the match instead of within
> hours. On 496 rows stored without xG, AF later had it for 0/3 matches one day old,
> 3/4 at two days, 6/8 at three and 8/8 at four; EPL fixtures from 09-04 that we hold
> without xG return it today. We fetch once, hours after kickoff, so we stored the
> gap. Fixed by `job_xg_late_fill` (daily 02:20 UTC) and a backfill from 2026-08-25.

⚠️ Our parse is **silent** when the field is absent (`if xg is not None`, no
logging), so a supplier withdrawing a field is indistinguishable from a quiet day.

**Can we compute xG ourselves? Not real xG.** That needs per-shot COORDINATES.
AF's `/fixtures/statistics` gives team aggregates; `/fixtures/events` gives goals,
cards and substitutions — not every shot. Neither carries coordinates.

**But we are discarding the next best thing.** Every statistics response we
already pay for carries **`Shots insidebox`** and **`Shots outsidebox`** (plus
`goals_prevented`), and we parse **none** of them — zero hits for `insidebox` in
the repo. They are present on fixtures with **no xG at all**, which is exactly
where they would matter: **30,446 rows**.

That supports a **binned shot-quality model** — location × outcome instead of an
exact coordinate — fitted on our own goals. An approximation, and it must be named
as one wherever it surfaces. Never call it xG.

### And a silent reach-into-history bug found alongside it (2026-09-23)

`get_fixture_statistics` always sent `half=true`. That parameter does not
degrade — AF answers `results: 0`, so the caller gets an empty list and the
fixture looks like it has **no statistics at all**. One fixture sampled per year
from our own ledger, plain vs `half=true`: **2018, 2019, 2020, 2022, 2023 all
return 2 vs 0**; 2021, 2024, 2025, 2026 return 2 vs 2. Per-fixture, not a clean
cutoff.

Recent fixtures are unaffected, so the live pipeline never lost rows — what was
lost is **reach into history, silently, for every caller**. The wrapper now
retries without the parameter when the half call is empty. It costs one extra
request only where we previously got nothing, and immediately recovered a 2018
fixture (11/3 and 4/2 inside/outside shots).

**If buying instead:** Understat is shot-level with coordinates and free, but
**6 leagues only**; Sportmonks sells xG as a €15/mo add-on. FBref **lost xG in
January 2026** (Opta termination) and is no longer an option.
⚠️ **xG is not comparable across providers** — match-level correlations run
0.86–0.96, and only **76.1%** of matches have four providers agreeing which team
won the xG. Never mix providers in one column; refit any calibration on a switch.

## Full AF field audit — what we get, what we store, what does not exist (2026-09-23)

Sampled 25 fixtures / 50 team-rows across leagues, straight from the live
`/fixtures/statistics` endpoint. Every type AF returned, with how often:

| AF field | seen | stored? |
|---|---|---|
| Total Shots · Shots on Goal · Blocked Shots · Corner Kicks · Ball Possession · Yellow Cards | 50/50 | ✅ already |
| Red Cards | 48/50 | ✅ already |
| Goalkeeper Saves · Offsides · Fouls · Total passes · Passes accurate | 42/50 | ✅ already |
| **Shots insidebox / Shots outsidebox** | 42/50 | ✅ **added [[#078]]** |
| **Free Kicks** | 40/50 | ✅ **added [[#078]]** |
| **goals_prevented** | 4/50 | ✅ **added [[#078]]** |
| `expected_goals` | **4/50** | ✅ already — and 4/50 is the collapse, see above |
| Shots off Goal | 50/50 | ❌ **deliberately not** — `Total = on + off + blocked` held **30/30**, so it is exactly derivable and carries zero information |
| Passes % | 6/50 | ❌ **deliberately not** — `accurate / total`, same reason |

**There is no further `match_stats` coverage to win.** Only **132 of 1,461**
leagues carry `coverage_statistics_fixtures`, and probing **30 fixtures across 30
flagged-FALSE leagues returned statistics for 0 of them**. The flag is accurate;
those 46,089 matches are uncovered at source, not by our gating.

### Where the real gap is — enrichment fill against 175,450 finished matches

| table | matches | fill |
|---|---|---|
| **`match_events`** | **141,805** | **80.8%** — and **no feature is derived from it** |
| `match_stats` | 56,463 | 32.2% |
| `match_player_stats` | 8,559 | 4.9% |
| `match_injuries` | 1,572 | 0.9% |

### AF endpoints: we call 18 — three only ever ran forward

| endpoint | stored | fill of 175,450 finished | why |
|---|---|---|---|
| `fixtures/lineups` | `matches.lineups_*` / `formation_*` | **6.5% / 5.1%** | fetched ~40 min BEFORE kickoff for upcoming matches only; never retrospectively |
| `fixtures/players` | `match_player_stats` (363,765 rows) | **4.9%** | forward-only, same shape |
| `injuries` + `sidelined` | `match_injuries` 12,102 · `player_sidelined` 13,039 | **0.9%** | forward-only |

All three are backfillable — AF serves historical fixtures (verified to **2018**).
The constraint is quota: 150k calls/day shared with the live pipeline against
~175k finished matches per endpoint, i.e. ~1.2 days of total quota each. Metered,
resumable, off-peak. See [[#081]].

**Dead schema:** the `lineups`, `injuries`, `players` and `seasons` tables all
exist with **0 rows** — lineups are written onto `matches`, not into `lineups`.
An empty table with a plausible name is a trap for the next person who greps.

⚠️ **And the `match_stats` gap is not recoverable.** 17,393 finished matches sit in stats-COVERED leagues with no stats row; probing 12 random ones per year returned **0/48 across 2022-2025**. `coverage_statistics_fixtures` is a LEAGUE-level flag and AF's per-fixture coverage inside those leagues is patchy. Do not spend quota on it.

`match_events` has **2.5× the coverage of `match_stats`**, holds **361,677 goal
events with minutes across 129,518 matches** and **30,356 red cards with minutes**
— and feeds the model nothing. Half-time scores are on **172,334 matches (98.2%)**.
That is the largest untouched signal we own: [[#080]].
