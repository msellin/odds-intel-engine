# OddsIntel — Workflows & Pipeline Architecture

> Single source of truth for all scheduled jobs, their order, and manual run instructions.
> Last updated: 2026-05-01 — Railway migration complete. All jobs run via `workers/scheduler.py` on Railway ($5/mo). Live tracker upgraded to 30s/60s/5min tiered polling via `workers/live_poller.py`.

### ✅ Railway Migration Complete (2026-04-30)

> All pipeline jobs now run on **Railway** as a single long-running Python process (`workers/scheduler.py`).
> GitHub Actions crons are **disabled** — kept only for manual `workflow_dispatch` triggers and DB migrations.
> Live tracker replaced by **LivePoller** (`workers/live_poller.py`) with tiered polling: **30s** (odds/scores), **60s** (stats/events), **5min** (lineups).
> Direct PostgreSQL (psycopg2 via `workers/api_clients/db.py`) used for all `supabase_client.py` functions + live tracker. `get_client()` still returns PostgREST for external callers (settlement, pipeline_utils) — migration ongoing.

---

## Daily Schedule (UTC) — executed by Railway `workers/scheduler.py`

```
04:00  ① Fixtures        run_fixtures()            AF fixtures + league coverage (weekly Mon)
       ② Enrichment      run_enrichment()          Standings, H2H, team stats, injuries (full)
       ③ Odds            run_odds()                AF bulk odds + Kambi odds
       ④ Predictions     run_predictions()         AF predictions (coverage-aware)
       ⑤ Betting         run_betting()             Poisson/XGBoost model + signals + bet placement
       (morning pipeline — chained sequentially, completes by ~06:30)
07-22  ③ Odds (repeat)   run_odds()                Every 2h: 07,08,10,12,14,16,18,20,22 UTC
11,15  ⑨ Betting Refresh  betting_refresh()         Pre-KO re-evaluation with fresh odds, lineups, news
19     ⑨ Betting Refresh  betting_refresh()         Evening KO window re-evaluation
12,16  ② Enrichment      run_enrichment()          Injuries + standings refresh
10-23  ⑥ LivePoller      live_poller.py            30s: scores+odds, 60s: stats+events, 5min: lineups
       ⑦ News Checker    run_news_checker()        4x/day: 09:00, 12:30, 16:30, 19:30 UTC
13:30  ③ Odds            run_odds(mark_closing)    Pre-kickoff (European afternoon)
17:30  ③ Odds            run_odds(mark_closing)    Pre-kickoff (European evening)
         ⑧a Live settle   settle_finished_matches()  Per-match: triggered by LivePoller on FT detection (instant)
21:00  ⑧b Settlement      settlement_pipeline()     Bulk: settle bets, post-match stats, ELO, CLV, prune, Platt (Sun)
23:30  ⑧c Settlement      settlement_pipeline()     Late catch-up: European evening matches finishing after 21:00
```

---

## Execution: Railway vs GitHub Actions

| Component | Runs on | How |
|-----------|---------|-----|
| **All scheduled jobs (①-⑧)** | **Railway** ($5/mo) | `workers/scheduler.py` — APScheduler cron triggers |
| **Live polling (⑥)** | **Railway** | `workers/live_poller.py` — daemon thread, 30s/60s/5min tiers |
| Manual recovery runs | GitHub Actions | `workflow_dispatch` — trigger any job manually |
| DB migrations | GitHub Actions | `migrate.yml` — on push to `supabase/migrations/` |
| Historical backfill | GitHub Actions | `backfill.yml` — temporary, budget-sensitive |

### Railway Environment Variables

`SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `API_FOOTBALL_KEY`, `GEMINI_API_KEY`, `DATABASE_URL`, `TZ=UTC`

### GitHub Actions Workflow Files (manual trigger only)

> **Crons disabled** — all `schedule:` blocks commented out. Kept for `workflow_dispatch` fallback.

| # | Workflow file | Script | Env vars needed |
|---|--------------|--------|-----------------|
| ① | `fixtures.yml` | `workers/jobs/fetch_fixtures.py` | SUPABASE_*, API_FOOTBALL_KEY |
| ② | `enrichment.yml` | `workers/jobs/fetch_enrichment.py` | SUPABASE_*, API_FOOTBALL_KEY |
| ③ | `odds.yml` | `workers/jobs/fetch_odds.py` | SUPABASE_*, API_FOOTBALL_KEY |
| ④ | `predictions.yml` | `workers/jobs/fetch_predictions.py` | SUPABASE_*, API_FOOTBALL_KEY |
| ⑤ | `betting.yml` | `workers/jobs/betting_pipeline.py` | SUPABASE_*, API_FOOTBALL_KEY |
| ⑥ | `live_tracker.yml` | `workers/jobs/live_tracker.py` | SUPABASE_*, DATABASE_URL |
| ⑦ | `news_checker.yml` | `workers/jobs/news_checker.py` | SUPABASE_*, GEMINI_API_KEY |
| ⑧ | `settlement.yml` | `workers/jobs/settlement.py` | SUPABASE_*, API_FOOTBALL_KEY, GEMINI_API_KEY |
| ⑨ | `backfill.yml` | `scripts/backfill_historical.py` | SUPABASE_*, API_FOOTBALL_KEY |
| — | `migrate.yml` | Supabase CLI | SUPABASE_ACCESS_TOKEN, SUPABASE_PROJECT_REF |

---

## What Each Job Does

### ① Fixtures (`fetch_fixtures.py`)
- Fetches all fixtures for today from AF `/fixtures?date=`
- Stores in `matches` table (~300 fixtures/day)
- On Mondays: refreshes league coverage from AF `/leagues` (1223 leagues)
- Logs to `pipeline_runs` table

### ② Enrichment (`fetch_enrichment.py`)
- **04:15 (full):** standings (T9), H2H (T10), team stats (T2), injuries (T3)
- **12:00/16:00 (refresh):** injuries + standings only
- Coverage-aware: skips leagues AF doesn't support
- Readiness gate: won't run unless ① Fixtures completed

### ③ Odds (`fetch_odds.py`)
- AF bulk odds via `/odds?date=` — ~178 fixtures, 13+ bookmakers, all markets (1X2, O/U, BTTS, DC)
- Kambi odds via `fetch_all_operators()` — ~250 events, Unibet/Paf
  - `listView` endpoint: 1X2 for all events (1 call per operator)
  - `betoffer/event/{id}` endpoint: O/U + BTTS for mapped-league events (~40-80 per operator)
- Kambi league names mapped to AF leagues via `KAMBI_TO_AF_LEAGUE` dict in `supabase_client.py` (prevents duplicate league creation)
- Stores all in `odds_snapshots` with `minutes_to_kickoff`
- `--mark-closing` flag for pre-kickoff runs (13:30/17:30)

### ④ Predictions (`fetch_predictions.py`)
- AF `/predictions` for each fixture — Poisson-based probability
- Coverage-aware: ~289 of 330 fixtures get predictions
- Stores on `matches.af_prediction` (JSONB) + `predictions` table (source='af')
- Readiness gate: won't run unless ① Fixtures completed

### ⑤ Betting (`betting_pipeline.py`)
- Runs 5x/day (06:00, 10:00, 13:00, 16:00, 19:00 UTC) to catch all kickoff windows
- Duplicate bets prevented by DB unique constraint `(bot_id, match_id, market, selection)` — safe to run any number of times
- Reads all data from DB — no API calls (Phase 2 complete as of 2026-04-29)
- Calls `run_morning(skip_fetch=True)` in `daily_pipeline_v2.py`
- `_load_today_from_db()` reads today's matches + best pre-match odds + AF predictions from DB
- Loads historical CSVs (targets_v9, targets_global) for Poisson model
- For each match with odds: compute Poisson/XGBoost prediction, write signals
- For each of 16 bots: calibrate, check odds movement, Kelly sizing, place bet
- `daily_pipeline_v2.py run_morning(skip_fetch=False)` still works for manual full runs

### ⑥ Live Tracker / LivePoller (`live_poller.py` + `live_tracker.py`)

**Runs on Railway as a daemon thread** with tiered polling (replaced 5-min GH Actions cron):

| Tier | Interval | Endpoints | Calls/cycle |
|------|----------|-----------|-------------|
| **Fast** | 30s | `/fixtures?live=all` (bulk), `/odds/live` (bulk) | 2 |
| **Medium** | 60s | `/fixtures/statistics` (per match), `/fixtures/events` (per match) | 2N |
| **Slow** | 5min | `/fixtures/lineups` (upcoming), match map refresh | ~2-5 |

- DB writes via **direct PostgreSQL** (psycopg2 bulk inserts) — 10-50x faster than PostgREST
- Pre-match model context (O/U 2.5 probability) loaded into each snapshot
- All data written to unified `live_match_snapshots` row per match per cycle
- **On FT/AET/PEN:** immediately writes final score to `matches` table + triggers per-match settlement
- `build_af_id_map()` queries today + yesterday (handles UTC midnight rollover for late matches)
- ~10K-15K AF API calls/day during live play (was ~3.4K at 5-min polling)
- Match window: 10:00-23:00 UTC (configurable)

### ⑦ News Checker (`news_checker.py`)
- Gemini 2.5 Flash AI analysis of pending bets
- Qualitative signals: manager changes, fatigue, weather, tactical shifts
- Stores `news_impact_score`, `lineup_confidence` signals

### ⑧ Settlement (`settlement.py`)

**Two modes:**

1. **Per-match (instant):** `settle_finished_matches(match_ids)` — called by LivePoller the moment it detects FT/AET/PEN status. Writes final score + result to `matches` table, settles pending bets + user picks for that match immediately. No delay.

2. **Bulk (scheduled 21:00 + 23:30 UTC):** `settlement_pipeline()` — full settlement run:
   - AF results (primary) + ESPN (fallback)
   - Settle any remaining pending bets: won/lost, P&L, CLV
   - Post-match: stats (T4), events (T8), player stats (T12)
   - Update ELO, form, pseudo-CLV, match feature vectors
   - Gemini post-mortem analysis of losses
   - **Sundays only:** Platt recalibration (`scripts/fit_platt.py`) — refits sigmoid α/β per market from all settled predictions → `model_calibration` table

### ⑨ Historical Backfill (`backfill_historical.py`)
- Fetches historical fixtures, odds, statistics, events from API-Football
- Runs during spare API quota windows (overnight + daytime gaps, 8 cron slots)
- 3 phases: Phase 1 = top ~20 leagues (3 seasons), Phase 2 = ~30 secondary (2 seasons), Phase 3 = ~50+ remaining (1 season)
- Budget-capped: aborts if < 10K API calls remaining; max 9K calls per run
- Idempotent: tracks progress in `backfill_progress` table, resumes from where it left off
- Auto-disables via `backfill_complete.flag` when all phases are done
- Manual run: `python scripts/backfill_historical.py --phase 1 --dry-run`

---

## Manual Run Order (GitHub Actions)

When you need to run the full pipeline manually (e.g. first setup, recovery, backfill):

Go to **github.com/msellin/odds-intel-engine/actions** → click workflow name → "Run workflow"

| Step | Workflow | Settings | Wait for green check |
|------|----------|----------|---------------------|
| 1 | **① Fixtures** | Tick "Refresh league coverage" = true | ~5 min |
| 2 | **② Enrichment** | Components: `all` | ~10 min |
| 3 | **③ Odds** | Defaults | ~5 min |
| 4 | **④ Predictions** | Defaults | ~10 min |
| 5 | **⑤ Betting** | Defaults | ~10 min |

After step 3: matches page should show ~200 matches.
After step 5: bets are placed, value bets page has data.

**Important:** Run in order. Each job depends on the previous ones having stored data in the DB.

---

## Data Sources

| Source | Role | Cost |
|--------|------|------|
| **API-Football Ultra** | Primary: fixtures, odds (13 bookmakers), predictions, injuries, lineups, standings, H2H, stats, live | $29/mo |
| **Kambi** | Supplementary odds (Unibet/Paf, 68 leagues) | Free |
| **ESPN** | Settlement results backup | Free |
| **Gemini 2.5 Flash** | AI news analysis (qualitative signals) | ~$0.04/day |

---

## GitHub Actions Budget (post-Railway migration)

**~100-200 minutes/month** (down from ~11,280 min/month). All scheduled jobs moved to Railway.

| Usage | Runs/month | Minutes |
|-------|-----------|---------|
| Manual pipeline runs (recovery/testing) | ~5-10 | ~50 |
| DB migrations (on push) | ~5-10 | ~20 |
| Backfill (temporary) | ~240 while active | ~600 |
| **Total** | — | **~100-200 min/month** (without backfill) |

> Repos can now go private without cost concern — well under 2,000 free min/month limit.
