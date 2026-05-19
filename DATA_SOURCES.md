# OddsIntel — Data Sources

> Last updated: 2026-04-28 — Migration complete. All T1–T13 endpoints integrated.

---

## Current Stack

| Source | Role | Status |
|--------|------|--------|
| **API-Football Ultra** ($29/mo) | PRIMARY — all structured data | ✅ Active |
| ~~Kambi API (free)~~ | Supplementary odds — removed 2026-05-06 (all 41 leagues already covered by AF; "ub"/"paf" bookmakers provided <5% best-odds and "ub" is just Unibet which AF covers separately) | Removed |
| ESPN (free) | Settlement result backup | ✅ Active (backup) |
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

### Training-pipeline data sources (clarification)

The Sunday weekly retrains (`fit_platt_offline.py`, `fit_league_rho.py`, `train.py`) all read from the **DB** (`matches`, `predictions`, `odds_snapshots`). They do NOT read `targets_poisson_history.csv` directly.

The CSVs only feed `daily_pipeline_v2.compute_prediction()` at runtime for team-form lookup on live matches. Expanding the CSV (Lever 1 / TIER-C-EXPAND) helps live inference; it does not by itself feed training. The chain that feeds training is: backfill matches → backfill odds → backfill predictions → Sunday retrains pick up the new rows.

---

## Remaining Cleanup

- [x] ~~Remove `betexplorer_odds.py`~~ Done 2026-04-29
- [x] ~~Remove Sofascore scrapers~~ Done 2026-04-29
- [ ] Evaluate API-Football Pro ($19/mo, 7.5K req/day) after 4–6 weeks once we know which leagues are profitable
- [ ] Activate The Odds API for Pinnacle odds (code exists, dormant)

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
