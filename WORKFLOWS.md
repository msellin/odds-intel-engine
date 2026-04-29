# OddsIntel — Workflows & Pipeline Architecture

> Single source of truth for all scheduled jobs, their order, and manual run instructions.
> Last updated: 2026-04-29

---

## Daily Schedule (UTC)

```
04:00  ① Fixtures        fetch_fixtures.py         AF fixtures + league coverage (weekly Mon)
04:15  ② Enrichment      fetch_enrichment.py       Standings, H2H, team stats, injuries (full)
05:00  ③ Odds            fetch_odds.py             AF bulk odds + Kambi odds
05:30  ④ Predictions     fetch_predictions.py      AF predictions (coverage-aware)
06:00  ⑤ Betting         betting_pipeline.py       Poisson/XGBoost model + signals + bet placement
05-22  ③ Odds (repeat)   fetch_odds.py             Every 2h: 07,08,10,12,14,16,18,20,22 UTC
12:00  ② Enrichment      fetch_enrichment.py       Injuries + standings refresh
12-22  ⑥ Live Tracker    live_tracker.py           Every 5min: live scores, odds, events, lineups
       ⑦ News Checker    news_checker.py           4x/day: 09:00, 12:30, 16:30, 19:30 UTC
13:30  ③ Odds            fetch_odds.py             Pre-kickoff (European afternoon)
16:00  ② Enrichment      fetch_enrichment.py       Injuries + standings refresh
17:30  ③ Odds            fetch_odds.py             Pre-kickoff (European evening)
21:00  ⑧ Settlement      settlement.py             Settle bets, post-match stats, ELO, CLV
```

---

## Workflow Files

| # | Workflow file | Script | Cron | Env vars needed |
|---|--------------|--------|------|-----------------|
| ① | `fixtures.yml` | `workers/jobs/fetch_fixtures.py` | `0 4 * * *` | SUPABASE_*, API_FOOTBALL_KEY |
| ② | `enrichment.yml` | `workers/jobs/fetch_enrichment.py` | `15 4`, `0 12`, `0 16` | SUPABASE_*, API_FOOTBALL_KEY |
| ③ | `odds.yml` | `workers/jobs/fetch_odds.py` | `0 5,7,8,10,12,14,16,18,20,22` + `30 13`, `30 17` | SUPABASE_*, API_FOOTBALL_KEY |
| ④ | `predictions.yml` | `workers/jobs/fetch_predictions.py` | `30 5 * * *` | SUPABASE_*, API_FOOTBALL_KEY |
| ⑤ | `betting.yml` | `workers/jobs/betting_pipeline.py` | `0 6 * * *` | SUPABASE_*, API_FOOTBALL_KEY |
| ⑥ | `live_tracker.yml` | `workers/jobs/live_tracker.py` | `*/5 12-22 * * *` | SUPABASE_* |
| ⑦ | `news_checker.yml` | `workers/jobs/news_checker.py` | `0 9`, `30 12`, `30 16`, `30 19` | SUPABASE_*, GEMINI_API_KEY |
| ⑧ | `settlement.yml` | `workers/jobs/settlement.py` | `0 21 * * *` | SUPABASE_*, API_FOOTBALL_KEY, GEMINI_API_KEY |
| — | `migrate.yml` | Supabase CLI | On push to `supabase/migrations/` | SUPABASE_ACCESS_TOKEN, SUPABASE_PROJECT_REF |

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
- AF bulk odds via `/odds?date=` — ~178 fixtures, 13+ bookmakers each
- Kambi odds via `fetch_all_operators()` — ~20 fixtures, Unibet/Paf
- Stores all in `odds_snapshots` with `minutes_to_kickoff`
- `--mark-closing` flag for pre-kickoff runs (13:30/17:30)

### ④ Predictions (`fetch_predictions.py`)
- AF `/predictions` for each fixture — Poisson-based probability
- Coverage-aware: ~289 of 330 fixtures get predictions
- Stores on `matches.af_prediction` (JSONB) + `predictions` table (source='af')
- Readiness gate: won't run unless ① Fixtures completed

### ⑤ Betting (`betting_pipeline.py`)
- Reads all data from DB — no API calls (Phase 2 complete as of 2026-04-29)
- Calls `run_morning(skip_fetch=True)` in `daily_pipeline_v2.py`
- `_load_today_from_db()` reads today's matches + best pre-match odds + AF predictions from DB
- Loads historical CSVs (targets_v9, targets_global) for Poisson model
- For each match with odds: compute Poisson/XGBoost prediction, write signals
- For each of 9 bots: calibrate, check odds movement, Kelly sizing, place bet
- `daily_pipeline_v2.py run_morning(skip_fetch=False)` still works for manual full runs

### ⑥ Live Tracker (`live_tracker.py`)
- AF `/fixtures?live=all` — live scores + minute
- AF `/odds/live` — in-play odds
- AF `/fixtures/lineups` — 20-40min before kickoff
- AF `/fixtures/events` — goals, cards, subs, VAR

### ⑦ News Checker (`news_checker.py`)
- Gemini 2.5 Flash AI analysis of pending bets
- Qualitative signals: manager changes, fatigue, weather, tactical shifts
- Stores `news_impact_score`, `lineup_confidence` signals

### ⑧ Settlement (`settlement.py`)
- AF results (primary) + ESPN (fallback)
- Settle pending bets: won/lost, P&L, CLV
- Post-match: stats (T4), events (T8), player stats (T12)
- Update ELO, form, pseudo-CLV, match feature vectors
- Gemini post-mortem analysis of losses

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

## GitHub Actions Budget

~150-180 minutes/day. Free for public repos (unlimited).

| Job | Runs/day | Minutes each | Total/day |
|-----|---------|-------------|-----------|
| Fixtures | 1 | 5 | 5 |
| Enrichment | 3 | 10/5/5 | 20 |
| Odds | 12 | 5 | 60 |
| Predictions | 1 | 10 | 10 |
| Betting | 1 | 10 | 10 |
| Live Tracker | ~120 | 3 | ~60 (but very short) |
| News Checker | 4 | 10 | 40 |
| Settlement | 1 | 15 | 15 |
