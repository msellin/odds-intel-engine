# OddsIntel — Data Sources

> Last updated: 2026-06-25 — WC odds sweep retired (was filling AF's WC gap; WC is over and ongoing value is minimal). The Odds API key + client retained — pivoted to tennis odds + settlement (TENNIS-PAPER-BETS).

---

## Current Stack

| Source | Role | Status |
|--------|------|--------|
| **API-Football Ultra** ($39/mo, 150K tier) | PRIMARY — all structured data | ✅ Active |
| **The Odds API** (free 500/mo) | Tennis odds + settlement via `/sports/tennis_*` and `/scores` endpoints. Pinnacle confirmed across all 3 active tour tournaments (100% coverage on probe 2026-06-25). | ✅ Active. WC sweep retired 2026-06-25. |
| **OddsPapi** (free 250/mo) | Historical Pinnacle closing-odds backfill for soccer CLV (one-shot via `scripts/ingest_oddspapi_pinnacle_closes.py`). | ⚠️ Quota exhausted 2026-06-25 — tennis scanner that was burning the budget is being replaced by The Odds API. Last backfill 2026-06-15 (12,218 rows / 219 matches into `odds_snapshots`). |
| **football-data.co.uk** (free) | Historical odds + secondary stats CSVs (CSV-FULL-EXTRACT 2026-06-04 captures 9 bookmakers × 1X2 + OU 2.5 + AH, open + close) | ✅ Active. ~80-120K net-new rows per season-set ingest. |
| ESPN (free) | Settlement result backup | ✅ Active (backup) |
| **Epicbet** (free, EE-licensed) | Second operator-reachable book in `odds_snapshots` (EPICBET-ODDS-INGEST-2026-08-27). Anonymous REST JSON — no auth, no bot-protection, runs VPS-side via `workers/automation/epicbet_explorer.py`. Markets: 1X2, OU 0.5–4.5, BTTS, AH. **No `double_chance`** — the league listing only carries market groups 45/15/69/19. | ✅ Active. ~5.6K rows / 229 matches per 30-min sweep. |
| ~~Kambi API (free)~~ | Supplementary odds — removed 2026-05-06 (all 41 leagues already covered by AF; "ub"/"paf" bookmakers provided <5% best-odds and "ub" is just Unibet which AF covers separately) | Removed |
| ~~BetExplorer~~ | Gap league odds — removed 2026-04-29 (fragile HTML scraping, low value) | Removed |

**What API-Football covers:** fixtures, 13-bookmaker odds, live scores, lineups, injuries, standings, H2H, match events, player stats, team stats, transfers, xG (post-match via /fixtures/statistics). 1,236 leagues.

---

## Daily Request Budget (API-Football Ultra — 75K/day limit)

| Operation | Calls/day | Pipeline |
|-----------|-----------|----------|
| Fixtures | ~5 | Morning |
| Pre-match odds (T1 + odds) | ~400 | Morning + every 2h |
| Predictions (T1) | ~130 | Morning |
| Team stats (T2) | ~80 | Morning |
| Injuries (T3) | ~7 | Morning |
| Standings (T9) | ~40 | Morning |
| H2H (T10) | ~130 | Morning |
| Live fixtures (T6) | ~5,280 | LivePoller fast tier (30s, bulk) |
| Live odds (T5) | ~5,280 | LivePoller fast tier (30s, bulk) |
| Live stats (T6) | ~4,300 | LivePoller medium tier (60s, per-match) |
| Events (T8) | ~4,300 | LivePoller medium tier (60s, per-match) + settlement |
| Lineups (T7) | ~50 | LivePoller slow tier (5min, pre-KO) |
| Post-match stats (T4) | ~120 | Settlement |
| Player stats (T12) | ~120 | Settlement |
| **Total** | **~10K-15K** | **13-20% of 75K limit** |

Remaining headroom: ~60K req/day. AF Ultra required — **do NOT downgrade to Pro** (7.5K limit).

### Per-minute limit is the binding constraint, not the daily quota (2026-08-24)

The daily quota has never been the problem — the VPS sits at ~8K/150K by mid-morning.
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
| T5 | `/odds/live` | Live tracker | ✅ Done |
| T6 | `/fixtures?live=all` | Live tracker | ✅ Done |
| T7 | `/fixtures/lineups` | Live tracker (pre-KO) | ✅ Done |
| T8 | `/fixtures/events` | Live tracker + settlement | ✅ Done |
| T9 | `/standings` | Morning | ✅ Done |
| T10 | `/fixtures/headtohead` | Morning | ✅ Done |
| T11 | `/sidelined` | Backfill script | ✅ Done |
| T12 | `/fixtures/players` | Settlement | ✅ Done |
| T13 | `/transfers` | Backfill (opt-in `--transfers`) | ✅ Done |

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
3. **Pinnacle open→close drift** (8,850 paired matches) — strong monotonic signal, **+8.76pp WR spread** top vs bottom quintile. New `pinnacle_drift_home/draw/away` columns added in migration 179; backfill via `scripts/backfill_pinnacle_drift.py`.

AH-bot prototype follow-up (`scripts/backtest_ah_bot_prototype.py`, 5,254 derivable-line matches) showed naive "ensemble 1X2 → AH derivation" loses to vig at every edge threshold (ROI worsens as filter tightens — signature of noise). A real AH bot requires a dedicated goals model — shelved for now.

## Remaining Cleanup

- [x] ~~Remove `betexplorer_odds.py`~~ Done 2026-04-29
- [x] ~~Remove Sofascore scrapers~~ Done 2026-04-29
- [x] ~~Activate The Odds API for Pinnacle odds~~ Done 2026-06-06 (ODDS-API-WC) → retired 2026-06-25 (WC commercial value minimal). Key + client repurposed for tennis (TENNIS-PAPER-BETS).
- [ ] Evaluate API-Football Pro ($19/mo, 7.5K req/day) after 4–6 weeks once we know which leagues are profitable

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
