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
