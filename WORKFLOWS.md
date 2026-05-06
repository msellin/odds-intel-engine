# OddsIntel — Workflows & Pipeline Architecture

> Single source of truth for all scheduled jobs, their order, and manual run instructions.
> Last updated: 2026-05-05 — N5: afternoon (16:00) + evening (20:45) value bet alerts for Pro/Elite. N7: full enrichment at 13:00 UTC (all 4 components). N4/N6/N9: settlement 01:00, watchlist 14h lookback, watchlist 20:35. N1/N2/N3: LivePoller 24/7, betting live-match guard, fixture refresh 4×/day. SCHED-AUDIT: 6 betting runs/day, closing odds all KO windows.

### ✅ Railway Migration Complete (2026-04-30)

> All pipeline jobs now run on **Railway** as a single long-running Python process (`workers/scheduler.py`).
> GitHub Actions crons are **disabled** — kept only for manual `workflow_dispatch` triggers and DB migrations. Cleanup (full removal) deferred until Railway has 2-4 weeks of stable operation (see GH-CLEANUP task).
> Live tracker replaced by **LivePoller** (`workers/live_poller.py`) with tiered polling: **30s** (odds/scores), **60s** (stats/events), **5min** (lineups).
> **Smart priority polling (RAIL-11):** matches with pending bets get stats every 30s instead of 60s. Goals detected via score delta → immediate extra odds snapshot stored.
> Direct PostgreSQL (psycopg2 via `workers/api_clients/db.py`) used for all pipeline ops. `get_client()` (PostgREST/Supabase SDK) is now **only** used inside `workers/api_clients/supabase_client.py` internals — zero other pipeline or script code calls it directly. Full PostgREST removal completed 2026-05-03 (including `fit_platt.py`, `backfill_historical.py`, and `live_tracker.py` crash fix).

---

## Daily Schedule (UTC) — executed by Railway `workers/scheduler.py`

```
02:00  ⓪ Hist Backfill   run_backfill()            Historical fixtures/stats/events (self-stops once complete)
04:00  ① Fixtures        run_fixtures()            AF fixtures + league coverage (weekly Mon)
       ② Enrichment      run_enrichment()          Standings, H2H, team stats, injuries (full)
       ③ Odds            run_odds()                AF bulk odds (13 bookmakers)
       ④ Predictions     run_predictions()         AF predictions (coverage-aware)
       ⑤ Betting         run_betting()             Poisson/XGBoost model + signals + bet placement
       (morning pipeline — chained sequentially, completes by ~06:30)
07:00  ③ Odds            run_odds()                First daytime odds refresh
07:15  ⑩ Match Previews  run_match_previews()      Top 10 matches → Gemini 200-word previews (ENG-3)
07:30  ⑪ Email Digest    run_email_digest()        Resend — tier-gated digest to subscribed users (ENG-4)
08:00  ③ Odds            run_odds()                Second morning refresh
08:00  ⑫ Weekly Digest   run_weekly_digest()       Monday only — model week review + upcoming matches (ENG-10)
08:30  Watchlist Alerts  run_watchlist_alerts()    Kickoff reminders + odds movement alerts (ENG-8)
09:00  ⑦ News Checker    run_news_checker()        Injury/lineup/news signals (Gemini)
09:15  ① Fixtures        run_fixtures()            Status refresh — catches morning postponements
09:30  ⑨ Betting Refresh betting_refresh()         Asian KOs + acts on 08:00 odds + 09:00 news
10:00  ③ Odds            run_odds()
10:30  ② Enrichment      run_enrichment()          Injuries + standings — fresh before 11:00 betting
10:45  ① Fixtures        run_fixtures()            Status refresh — before European morning betting
11:00  ⑨ Betting Refresh betting_refresh()         European morning KOs
12:00  ③ Odds            run_odds()
12:30  ⑦ News Checker    run_news_checker()
13:00  ② Enrichment      run_enrichment()          Full enrichment — all 4 components (H2H+team_stats fresh for afternoon betting)
13:30  ③ Odds            run_odds(mark_closing)    Pre-KO closing odds (European afternoon)
14:00  ③ Odds            run_odds()
14:30  Watchlist Alerts  run_watchlist_alerts()    Kickoff reminders + odds movement alerts (ENG-8)
14:30  ⑦ News Checker    run_news_checker()        Feeds 15:00 betting refresh
14:45  ① Fixtures        run_fixtures()            Status refresh — before European afternoon betting
15:00  ⑨ Betting Refresh betting_refresh()         European afternoon KOs
16:00  ② Enrichment      run_enrichment()          Injuries + standings refresh
       ③ Odds            run_odds()
16:00  ⑪ Value Bet Alert run_value_bet_alert('afternoon')  New bets since 10:00 UTC → Pro/Elite (N5)
16:30  ⑦ News Checker    run_news_checker()
17:30  ③ Odds            run_odds(mark_closing)    Pre-KO closing odds (European evening)
18:00  ③ Odds            run_odds()
18:30  ⑦ News Checker    run_news_checker()        Feeds 19:00 + 20:30 betting (moved from 19:30)
18:45  ① Fixtures        run_fixtures()            Status refresh — before European evening betting
19:00  ⑨ Betting Refresh betting_refresh()         European early evening KOs
20:00  ③ Odds            run_odds(mark_closing)    Pre-KO closing odds (European prime-time 19-21 KO)
20:30  ⑨ Betting Refresh betting_refresh()         European prime-time KOs — uses 20:00 closing odds
20:35  Watchlist Alerts  run_watchlist_alerts()    Kickoff reminders + odds movement alerts — after betting (ENG-8)
20:45  ⑪ Value Bet Alert run_value_bet_alert('evening')    New bets since 17:00 UTC → Pro/Elite (N5)
         ⑧a Live settle   settle_finished_matches()  Per-match: triggered by LivePoller on FT (instant, 24/7)
21:00  ⑧b Settlement      settlement_pipeline()     Bulk: settle bets, post-match stats, ELO, CLV, prune
                                                    + Platt recalibration + blend refit (Wed + Sun)
                                                    + DC rho per tier (Sun only)
22:00  ③ Odds            run_odds()                Late matches (Asian/American)
23:30  ⑧c Settlement      settlement_pipeline()     Late catch-up: European evening matches finishing after 21:00
01:00  ⑧d Settlement      settlement_pipeline()     Overnight catch-up: 21:30+ KOs finishing after extra time
24/7   ⑥ LivePoller      live_poller.py            30s when live (scores+odds+stats), 120s idle — no time gate
```

### Betting refresh schedule (6x/day)
| Time | KO window covered | Fresh inputs |
|------|-------------------|-------------|
| 06:30 (morning pipeline) | All day initial | Full enrichment + odds |
| 09:30 | Asian (09-11 UTC) | 08:00 odds, 09:00 news |
| 11:00 | European morning (12-13 UTC) | 10:00 odds, 10:30 enrichment |
| 15:00 | European afternoon (15-17 UTC) | 14:00 odds, 14:30 news |
| 19:00 | European early evening (18-20 UTC) | 18:00 odds, 18:30 news |
| 20:30 | European prime-time (19-21 UTC) | 20:00 closing odds, 18:30 news |

---

## Execution: Railway vs GitHub Actions

| Component | Runs on | How |
|-----------|---------|-----|
| **All scheduled jobs (①-⑧)** | **Railway** ($5/mo) | `workers/scheduler.py` — APScheduler cron triggers |
| **Live polling (⑥)** | **Railway** | `workers/live_poller.py` — daemon thread, 30s/60s/5min tiers |
| Manual recovery runs | GitHub Actions | `workflow_dispatch` — trigger any job manually |
| DB migrations | GitHub Actions | `migrate.yml` — on push to `supabase/migrations/` |
| Historical backfill | **Railway** | `scripts/backfill_historical.py` — 02:00 UTC daily, self-stops on completion |

### Railway Environment Variables

`SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `API_FOOTBALL_KEY`, `GEMINI_API_KEY`, `DATABASE_URL`, `RESEND_API_KEY`, `DIGEST_FROM_EMAIL`, `SITE_URL`, `TZ=UTC`

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
- **10:30/16:00 (refresh):** injuries + standings only (10:30 moved from 12:00 to feed 11:00 betting)
- **13:00 (full, N7):** all 4 components — ensures H2H + team_stats are fresh for afternoon/evening betting refreshes (was only refreshed in morning pipeline)
- Coverage-aware: skips leagues AF doesn't support
- Readiness gate: won't run unless ① Fixtures completed

### ③ Odds (`fetch_odds.py`)
- AF bulk odds via `/odds?date=` — ~178 fixtures, 13 bookmakers, all markets (1X2, O/U, BTTS, DC)
- Bookmakers: 10Bet, 1xBet, 888Sport, Bet365, Betano, BetVictor, Betfair, Dafabet, Marathonbet, Pinnacle, SBO, Unibet, William Hill
- Kambi removed 2026-05-06 — all leagues already covered by AF, no unique value
- Stores all in `odds_snapshots` with `minutes_to_kickoff`
- `--mark-closing` flag for pre-kickoff runs (13:30, 17:30, 20:00)

### ④ Predictions (`fetch_predictions.py`)
- AF `/predictions` for each fixture — Poisson-based probability
- Coverage-aware: ~289 of 330 fixtures get predictions
- Stores on `matches.af_prediction` (JSONB) + `predictions` table (source='af')
- Readiness gate: won't run unless ① Fixtures completed

### ⑤ Betting (`betting_pipeline.py`)
- Runs 6x/day (morning pipeline ~06:30, then 09:30, 11:00, 15:00, 19:00, 20:30 UTC) to catch all kickoff windows
- Duplicate bets prevented by DB unique constraint `(bot_id, match_id, market, selection)` — safe to run any number of times
- Only bets on matches with `status='scheduled'` AND kickoff still in the future — never bets on live/started matches
- Reads all data from DB — no API calls (Phase 2 complete as of 2026-04-29)
- Calls `run_morning(skip_fetch=True)` in `daily_pipeline_v2.py`
- `_load_today_from_db()` reads today's matches + best pre-match odds + AF predictions from DB
- Loads historical CSVs (targets_v9, targets_global) for Poisson model
- **Batch signal writing (PERF-1):** `batch_write_morning_signals(odds_matches)` called ONCE before the match loop — 10 bulk queries cover all 400+ matches at once (ELO, PPG, injuries, standings, season stats, BDM, overnight line move, odds volatility, league meta, H2H). One `execute_values` INSERT for all signals. Reduced from 34-70 min to ~15s.
- For each match with odds: compute Poisson/XGBoost prediction + store predictions
- For each of 16 bots: calibrate, check odds movement (psycopg2), alignment (psycopg2), Kelly sizing, place bet
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
- **Runs 24/7** — no time-gate. Adaptive sleep: 30s when live matches found, 120s when idle
- When idle: 2 bulk API calls/2min (~960 calls/day) — negligible vs 75K budget
- Previously gated to 10:00-23:00 UTC, which missed Asian/early-UTC matches entirely

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
   - **Pruning (PERF-2):** `scripts/prune_odds_snapshots.py` — single SQL DELETE removes all intermediate snapshots for finished matches (keeps opening + closing). Prevents `odds_snapshots` from growing unboundedly (was 4.1M rows, ~500K/day).
   - **Sundays only:** Platt recalibration (`scripts/fit_platt.py`) — refits sigmoid α/β per market from all settled predictions → `model_calibration` table

### ⑩ Match Previews (`match_previews.py`) — ENG-3
- Runs at 07:15 UTC, after morning pipeline + 07:00 odds refresh complete
- Selects top 10 matches for today: ranked by league tier then signal count
- For each match: gathers form, H2H, injuries, odds, model prediction, key signals from DB
- Calls Gemini 2.5 Flash: generates 180-220 word preview + 40-55 word teaser
- Upserts into `match_previews` table (migration 033) — idempotent, safe to re-run
- Content is triple-duty: match detail page (Free sees teaser, Pro/Elite see full), email digest, social posts
- Manual run: `python -m workers.jobs.match_previews --dry-run`

### ⑪ Email Digest + Value Bet Alerts (`email_digest.py`) — ENG-4 + N5

**Morning digest (07:30 UTC):**
- Sends tier-appropriate HTML email via Resend REST API (httpx)
  - **Free:** 3 preview teasers + bet count teaser + upgrade CTA
  - **Pro:** 3 full previews + value bet count + link to value bets page
  - **Elite:** 3 full previews + value bet table (top 5 with odds/edge/confidence)
- One email per user per day enforced by `email_digest_log` unique constraint (migration 034)

**Value bet alerts (16:00 + 20:45 UTC) — Pro/Elite only (N5):**
- `run_value_bet_alert('afternoon')` at 16:00 — bets placed since 10:00 UTC (11:00 + 15:00 refreshes)
- `run_value_bet_alert('evening')` at 20:45 — bets placed since 17:00 UTC (19:00 + 20:30 refreshes)
- No-op if no new bets in the slot window — never sends empty alerts
- Deduped per slot: `value_bet_alert_log` UNIQUE(user_id, alert_date, slot) (migration 046)
- Pro: bet count + CTA. Elite: full table with odds/edge/confidence.

- Only sends to users with `user_notification_settings.email_digest_enabled = true`
- Requires `RESEND_API_KEY` in env (+ Railway env vars).
- Manual run: `python -m workers.jobs.email_digest --dry-run`

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
| ~~**Kambi**~~ | Removed 2026-05-06 — AF already covers all 41 Kambi leagues with 13 bookmakers | — |
| **ESPN** | Settlement results backup | Free |
| **Gemini 2.5 Flash** | AI news analysis (qualitative signals) + match previews (ENG-3) | ~$0.05/day |
| **Resend** | Email digest delivery (ENG-4) | Free to 3,000 emails/mo |

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
