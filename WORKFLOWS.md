# OddsIntel — Workflows & Pipeline Architecture

> Single source of truth for all scheduled jobs, their order, and manual run instructions.
> Last updated: 2026-05-12 — AF caches shipped: H2H 7-day cross-match cache (saves ~360 calls/day), team_stats same-day DB cache (saves ~150 calls/day), standings moved to 23:30 UTC nightly (saves ~40 calls/day). Intraday enrichment refresh now injuries-only; full enrichment at 13:00 is injuries+H2H+team_stats. Total saving: ~550 AF calls/day.

### ✅ Railway Migration Complete (2026-04-30)

> All pipeline jobs now run on **Railway** as a single long-running Python process (`workers/scheduler.py`).
> GitHub Actions crons are **disabled** — kept only for manual `workflow_dispatch` triggers and DB migrations. Cleanup (full removal) deferred until Railway has 2-4 weeks of stable operation (see GH-CLEANUP task).
> Live tracker replaced by **LivePoller** (`workers/live_poller.py`) with tiered polling: **45s** (odds/scores), **135s** (stats/events), **7.5min** (lineups). Tuned 2026-05-08 — ~25% reduction in AF API calls with no meaningful data quality loss.
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
       ⑤ Betting         run_betting()             Poisson/XGBoost model + signals + bet placement; sends Telegram DMs to connected Pro/Elite users on each new value bet
       (morning pipeline — chained sequentially, completes by ~06:30)
07-22  ③ Odds            run_odds()                Every 30min (:00 and :30) — AF bulk odds, 13 bookmakers
                                                    + mark_closing runs at 13:30, 17:30, 20:00 (pre-KO windows)
07:15  ⑩ Match Previews  run_match_previews()      Top 10 matches → Gemini 200-word previews (ENG-3)
07:30  ⑲ WC AI Previews  run_wc_match_previews()   Every WC fixture in next 7d → Gemini 80-120 word previews. Gated to WC window (2026-06-04 → 2026-07-19); no-op outside. Idempotent (<24h-old previews skip Gemini call). Reuses `match_previews` table — distinct match_ids so no clash with club preview job. (WC-AI-PREVIEW)
10/12/14/16  ⑪ Email Digest Slots  run_email_digest()  Smart-slot digest — first slot whose pending-bet signal score ≥ EMAIL_DIGEST_MIN_SIGNAL sends; later slots see per-user lock and skip (ENG-4 / EMAIL-DIGEST-SMART)
03:00  ⑭ Weekly Retrain  job_weekly_retrain()      Sunday only — runs `train.py --version v{YYYYMMDD}` then auto-`compare_models.py {new} {production}`. Promotion stays manual (operator flips MODEL_VERSION env). ML-PIPELINE-UNIFY Stage 5a/5b.
08:00  ⑫ Weekly Digest   run_weekly_digest()       Monday only — model week review + upcoming matches (ENG-10)
08:30  Watchlist Alerts  run_watchlist_alerts()    Kickoff reminders + odds movement alerts (ENG-8)
09:00  ⑦ News Checker    run_news_checker()        Injury/lineup/news signals (Gemini)
09:15  ① Fixtures        run_fixtures()            Status refresh — catches morning postponements
:03/:33 ⑱ Coolbet Odds    job_coolbet_odds_snapshot() Every 30 min, between AF odds (:00/:30) and betting refresh (:05/:35). Walks Coolbet fo-category + per-match sidebets, stores Coolbet OU/1X2/BTTS/AH/DC odds in odds_snapshots so the same cycle's edge math sees fresh prices on our actual placement venue. Lays the data foundation for the planned COOLBET-OR-PIN-REQUIRED quality gate (replaces Pinnacle-only OU veto). Error-isolated.
:05/:35 ⑨ Betting Refresh betting_refresh()         Every 30 min, 5 min after odds refresh (07:05–22:35 UTC). DB-only, 0 AF calls. Dedup prevents duplicates. Cohort auto-detected from UTC hour.
:05/:35 ⑰ Shadow Run     job_shadow_run_interval() Every 30 min, concurrent with betting refresh. ALL bots evaluated → shadow_bets. Cohort = 'HHMM' UTC string. 32 snapshots/day.
08:00  ② Enrichment      run_enrichment()          Injuries only — single morning fetch (AF-INJURIES-LATE 2026-06-01)
10:45  ① Fixtures        run_fixtures()            Status refresh — catches morning postponements
12:30  ⑦ News Checker    run_news_checker()
12:45  ① Fixtures        run_fixtures()            Status refresh
13:00  ② Enrichment      run_enrichment()          H2H + team_stats (injuries moved to 08:00, standings nightly only)
14:30  Watchlist Alerts  run_watchlist_alerts()    Kickoff reminders + odds movement alerts (ENG-8)
14:30  ⑦ News Checker    run_news_checker()
14:45  ① Fixtures        run_fixtures()            Status refresh
16:00  ⑪ Value Bet Alert run_value_bet_alert('afternoon')  New bets since 10:00 UTC → Pro/Elite (N5)
16:30  ⑦ News Checker    run_news_checker()
17:15  ① Fixtures        run_fixtures()            Status refresh
18:30  ⑦ News Checker    run_news_checker()        Feeds evening betting
18:45  ① Fixtures        run_fixtures()            Status refresh — before European evening betting
20:35  Watchlist Alerts  run_watchlist_alerts()    Kickoff reminders + odds movement alerts — after betting (ENG-8)
20:45  ⑪ Value Bet Alert run_value_bet_alert('evening')    New bets since 17:00 UTC → Pro/Elite (N5)
         ⑧a Live settle   settle_finished_matches()  Per-match: triggered by LivePoller on FT (instant, 24/7)
21:00  ⑧b Settlement      settlement_pipeline()     Bulk: settle bets, post-match stats, ELO, CLV, prune
                                                    + Platt recalibration + blend refit (Wed + Sun)
                                                    + DC rho per tier (Sun only)
21:30  ⑬ Health Alert    run_settlement_check()    Alerts if >5 pending bets on finished matches after settlement
       ⑮ Settle Recon  settle_reconcile.run()    MONEY-SETTLE-RECON: alerts if >2 finished matches have stuck pending bets
22:00  ⑲ Tomorrow odds   job_odds_tomorrow()        OPENING-LINE-MOVE-CAPTURE 2026-05-25 — fetch odds for tomorrow's matches; gives the next morning's 04:00 fetch a yesterday→today delta to compute `overnight_line_move`. Replaces the broken 02:00/04:00 OVERNIGHT slots that fetched today's matches (no prior snapshot existed).
22:30  ⑳ MFV B-ML3 v2   job_nightly_mfv_b_ml3_refresh()  Refreshes B-ML3 v2 *_at_t6h feature columns for matches that finished today
22:45  ㉑ MFV form mom. job_nightly_mfv_form_momentum_refresh()  Catches form_momentum for any matches that the live MFV builder missed
22:50  ㉒ Team rating    job_team_avg_player_rating()    AF-PLAYER-RATINGS 2026-05-25 — rolling 10-match team avg AF player rating → match_signals
22:55  ㉓ Injury severity job_injury_severity()         INJURY-SEVERITY 2026-05-25 — bucket match_injuries by severity, weighted score → match_signals
23:00  ㉔ xG overperf    job_xg_overperformance()        SIG-12 2026-05-25 — (goals − xG) rolling 10-match per team → match_signals
23:05  ㉕ League draw rt job_league_draw_rate()          LEAGUE-DRAW-YTD 2026-05-25 — per-league season-to-date draw rate → match_signals (backtest: +11.6pp Q4 vs Q1)
23:10  ㉖ Line velocity  job_line_velocity()             LINE-VELOCITY 2026-05-25 — Pinnacle home implied-prob slope T-12h..T-2h → match_signals (REVERSE signal -6.6pp CLV-beat)
23:15  ㉗ Season phase   job_league_season_phase()       LEAGUE-SEASON-PHASE 2026-05-25 — per-match season_progress [0..1] → match_signals (+7.7pp Over 2.5 late vs early)
23:30  ② Enrichment      run_enrichment()          Standings only — nightly once is enough (AF-STANDINGS-DAILY)
23:30  ⑧c Settlement      settlement_pipeline()     Late catch-up: European evening matches finishing after 21:00
23:30  ㉘ Daily perf     job_daily_real_perf_email()     DAILY-REAL-PERF-EMAIL — yesterday + 7d real-bet ROI split (placer vs manual) via Resend
01:00  ⑧d Settlement      settlement_pipeline()     Overnight catch-up: 21:30+ KOs finishing after extra time
1st 03:30 ㉙ ALN auto    job_aln_auto_tune()             ALN-AUTO 2026-05-25 — monthly alignment-bump retune; emails diff if any class needs |Δ|≥0.005 with n≥100
Sun 02:30 ㉚ League CLV  job_league_clv_efficiency()     LEAGUE-CLV-EFFICIENCY — weekly per-league CLV beatability index → match_signals
Sun 04:00 ㉛ Meta retrain job_weekly_meta_retrain()      META-RETRAIN 2026-05-25 — weekly B-ML3 retrain → Supabase Storage, email verdict
Sun 05:00 ㉜ Meta validate job_weekly_meta_validate()     META-VALIDATE-WEEKLY 2026-06-01 — validate_meta_b_ml3 against settled bets, emails verdict
Sun 06:00 ㉝ Threshold chk job_weekly_threshold_check()   THRESHOLD-CHECK-WEEKLY 2026-06-06 — runs scripts/threshold_check.py and emails the gate-count snapshot. Prevents the "Key Thresholds to Watch" counts in PRIORITY_QUEUE.md from going stale.
24/7   ⑥ LivePoller      live_poller.py            45s when live (scores+odds+stats), 120s idle — no time gate
         ⑫ InplayBot      inplay_bot.py             Paper trading: 8 strategies (A-F + A2 + C_home), runs after each LivePoller snapshot store; sends Telegram DMs to connected Pro/Elite users on new bets
*/30   ⑯ Dash Cache Ref  write_dashboard_cache()   Rebuilds dashboard_cache at :15 and :45 — keeps /performance fresh
*/5    ⑭ Healthcheck     job_healthcheck_ping()    Pings healthchecks.io every 5min — external dead-man's switch
09:35  ⑬ Health Alert    run_morning_checks()      Alerts if 0 bets placed or >10 matches missing Pinnacle odds
10-22  ⑬ Health Alert    run_snapshot_check()      Hourly: alerts if last LivePoller snapshot >25min stale
```

### ⑰ Shadow Runs (`daily_pipeline_v2.run_morning(shadow_mode=True)`)

BET-TIMING-MONITOR — 32 runs/day at :05/:35 past each hour 07–22 UTC. Each invokes
`run_morning(skip_fetch=True, shadow_mode=True, shadow_cohort=<HHMM>)` which:

- Runs ALL bots regardless of their `BOT_TIMING_COHORTS` assignment
- Skips the active-cohort filter, the bot active-flag check, and the
  per-league exposure cap
- Never mutates `_running_bankroll` or calls `store_bet`
- Bulk-inserts into `shadow_bets` with a fresh `shadow_run_id`
- Fixed nominal stake of 10.00 per row for clean cross-hour ROI math
- Cohort label = 'HHMM' UTC (e.g. '0705', '1435') — enables per-hour ROI analysis

Shadow bets are settled nightly. Analysis: `scripts/shadow_timing_report.py`.

**Acca leg shadows (ACCA-LEG-SHADOW, 2026-05-25):** `run_acca_pass` also writes
every picked leg (across all 5 acca variants, deduped) as a `shadow_bets` row
attributed to virtual bot `bot_acca_leg_shadow` with `shadow_cohort='morning'`.
These rows ride the same settlement pass. Used to evaluate whether widening
singles-bot filters to catch the acca's matches would be +EV — revisit cadence
tracked under `ACCA-LEG-SHADOW-EVAL` in `PRIORITY_QUEUE.md`.

### Betting refresh schedule (32x/day — every 30 min after odds refresh)
| Time | KO window covered | Fresh inputs |
|------|-------------------|-------------|
| 06:30 (morning pipeline) | All day initial | Full enrichment + odds |
| 09:30 | Asian (09-11 UTC) | 08:00 odds, 09:00 news |
| 11:00 | European morning (11-12 UTC) | 10:30 odds, 10:30 enrichment |
| 13:30 | European midday (12:00-14:30 UTC) | 13:30 pre-KO odds, 13:00 full enrichment |
| 15:00 | European afternoon (14:30-16 UTC) | 15:00 odds, 14:30 news |
| 17:30 | European late-afternoon (16-18:30 UTC) | 17:30 pre-KO odds, 16:00 enrichment |
| 19:00 | European early evening (18-20 UTC) | 18:30 odds, 18:30 news |
| 20:30 | European prime-time (19-21 UTC) | 20:00 closing odds, 18:30 news |

---

## Service topology (WORKER-SPLIT-LIVEPOLLER, 2026-05-25)

Two Railway service deployment is now supported:

  **Service A — Scheduler (`python -m workers.scheduler`)**
    Owns all cron jobs and the scheduled betting pipeline. Set
    `LIVE_POLLER_IN_SCHEDULER=false` to skip starting the in-process
    LivePoller thread (a crash in the poller won't take down the scheduler).

  **Service B — LivePoller (`python -m workers.live_poller_main`)**
    Standalone 24/7 LivePoller. SIGTERM/SIGINT graceful shutdown.
    Independent Railway crash-restart cycle.

Default config (single service) still works: scheduler starts the
in-process poller because `LIVE_POLLER_IN_SCHEDULER` defaults to `true`.
To switch, deploy Service B from the same repo with the alternate CMD,
then flip `LIVE_POLLER_IN_SCHEDULER=false` on Service A. Cost +$2-3/mo
on Hobby plan; blast radius isolated.

## Execution: Railway vs GitHub Actions

| Component | Runs on | How |
|-----------|---------|-----|
| **All scheduled jobs (①-⑧)** | **Railway** ($5/mo) | `workers/scheduler.py` — APScheduler cron triggers |
| **Live polling (⑥)** | **Railway** | `workers/live_poller.py` — daemon thread, 30s/60s/5min tiers |
| Manual recovery runs | GitHub Actions | `workflow_dispatch` — trigger any job manually |
| DB migrations | GitHub Actions | `migrate.yml` — on push to `supabase/migrations/` |
| Historical backfill | **Railway** | `scripts/backfill_historical.py` — 02:00 UTC daily, self-stops on completion |

### Railway Environment Variables

`SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `API_FOOTBALL_KEY`, `GEMINI_API_KEY`, `DATABASE_URL`, `RESEND_API_KEY`, `DIGEST_FROM_EMAIL`, `SITE_URL`, `TZ=UTC`, `HEALTHCHECKS_IO_PING_URL`, `ADMIN_ALERT_EMAIL`

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
- **Backfill mode (WC-PHASE-1, 2026-06-02):** `--league NN --season YYYY` switches
  to `get_fixtures_by_league_season()` and skips daily-featured + ops_snapshot
  (those are date-scoped). Used to pre-load WC 2026 group stage in advance of
  kickoff, and any other competition the daily date-mode wouldn't pick up
  ahead of time. Same logging + bulk_store_matches path, idempotent on
  `api_football_id`.

### ② Enrichment (`fetch_enrichment.py`)
- **04:15 (full):** standings (T9), H2H (T10), team stats (T2), injuries (T3), coaches (MGR-CHANGE), venues (AF-VENUES), sidelined (AF-SIDELINED), transfers (AF-TRANSFERS)
- **10:30/16:00 (refresh):** injuries only (AF-STANDINGS-DAILY — standings no longer intraday; 10:30 moved from 12:00 to feed 11:00 betting)
- **13:00 (full, N7):** injuries + H2H + team_stats — ensures fresh context for afternoon/evening betting refreshes (standings excluded per AF-STANDINGS-DAILY)
- **23:30 (nightly):** standings only — standings update ~1x/week so daily once-at-night is sufficient (~40 calls/day saved)
- **H2H (T10):** Tier 1 leagues only + same-day cache (`h2h_raw IS NOT NULL` on the match row). Morning run: ~50-80 calls. Intraday runs: 0 calls (all cached after morning). No API batching available for H2H, so scope + cache is the only optimisation.
- Venues: one call per unique venue, cached in `venues` table; skips already-cached venues (near-zero ongoing cost)
- Sidelined: 7-day cache per player; only fetches players currently listed as injured in today's matches (~5-20 calls/day)
- Transfers: 30-day cache per team (transfers only change in windows); capped at 100 teams/run; today's fixture teams are prioritised
- Coaches: 48-hour cache per team; capped at 50 teams/run
- Coverage-aware: skips leagues AF doesn't support
- Readiness gate: won't run unless ① Fixtures completed

### ③ Odds (`fetch_odds.py`)
- AF bulk odds via `/odds?date=` — ~178 fixtures, 13 bookmakers, all markets (1X2, O/U, BTTS, DC)
- Bookmakers: 10Bet, 1xBet, 888Sport, Bet365, Betano, BetVictor, Betfair, Dafabet, Marathonbet, Pinnacle, SBO, Unibet, William Hill
- Kambi removed 2026-05-06 — all leagues already covered by AF, no unique value
- Stores all in `odds_snapshots` with `minutes_to_kickoff`
- `--mark-closing` flag for pre-kickoff runs (13:30, 17:30, 20:00)
- **OU quality gates (ODDS-QUALITY-CLEANUP, 2026-05-10)**: `filter_garbage_ou_rows` (in `workers/utils/odds_quality.py`) drops OU rows from blacklisted bookmakers (`api-football`, `api-football-live`, `William Hill`) and both sides of impossible `(over, under)` pairs (`1/over + 1/under < 1.02`). Applied at every write path. 1X2 / BTTS rows from the same bookmakers pass through unchanged. See `DATA_SOURCES.md` for the why.

### ④ Predictions (`fetch_predictions.py`)
- AF `/predictions` for each fixture — Poisson-based probability
- Coverage-aware: ~289 of 330 fixtures get predictions
- Stores on `matches.af_prediction` (JSONB) + `predictions` table (source='af')
- Readiness gate: won't run unless ① Fixtures completed
- **Runs ONCE per day at 05:30 UTC.** P-PRED-1 (2026-05-10) removed the per-betting-refresh refetch — `/predictions` has no bulk form (probed and rejected: `ids`, `fixtures`, `date`, `league`), and AF documents the endpoint as updating at most hourly. Re-pulling ~3,000 fixtures × 5 betting_refresh slots was burning ~10K calls/day for data identical to what's already on `matches.af_prediction`. Betting refreshes now read the cached JSONB instead.

### ⑤ Betting (`betting_pipeline.py`)
- Runs 8x/day (morning pipeline ~06:30, then 09:30, 11:00, 13:30, 15:00, 17:30, 19:00, 20:30 UTC) to catch all kickoff windows
- Duplicate bets prevented by DB unique constraint `(bot_id, match_id, market, selection)` — safe to run any number of times
- Only bets on matches with `status='scheduled'` AND kickoff still in the future — never bets on live/started matches
- Reads all data from DB — no API calls (Phase 2 complete as of 2026-04-29)
- Calls `run_morning(skip_fetch=True)` in `daily_pipeline_v2.py`
- `_load_today_from_db()` reads today's matches + best pre-match odds + AF predictions from DB
- **Bot gating (ODDS-QUALITY-CLEANUP, 2026-05-10)**: pipeline now skips any bot with `bots.is_active=false` or `retired_at IS NOT NULL`, so a paused bot stops placing bets immediately without a code change.
- **OU quality gates**: same SQL exclusion as the write path (blacklisted bookmakers excluded from best-price aggregation; impossible `(over, under)` pairs zeroed out before bot evaluation).
- **Accessible-bookmaker filter (ACCESSIBLE-BM, 2026-05-11)**: `ACCESSIBLE_BOOKMAKERS = frozenset({"Bet365","Unibet","Betano","Marathonbet","10Bet","888Sport","Pinnacle"})` — only these books contribute to `best[mid][key]` odds aggregation. Inaccessible books (SBO, Dafabet, 1xBet, BetVictor, Betfair, William Hill) are still fetched and logged in `bm_sources` but excluded from edge math. `best_bookmaker[mid][key]` tracks which accessible book had the best price per market/selection. Stored as `recommended_bookmaker` on `simulated_bets` (migration 094) so `scripts/daily_picks.py` can tell the user exactly where to place.
- Loads historical CSVs (targets_poisson_history, targets_global) for Poisson model
- **Batch signal writing (PERF-1):** `batch_write_morning_signals(odds_matches)` called ONCE before the match loop — 10 bulk queries cover all 400+ matches at once (ELO, PPG, injuries, standings, season stats, BDM, overnight line move, odds volatility, league meta, H2H). One `execute_values` INSERT for all signals. Reduced from 34-70 min to ~15s.
- **MFV-LIVE-BUILD (2026-05-10):** `build_match_feature_vectors_live(today)` called immediately after the morning signals batch and before the match loop. Writes one `match_feature_vectors` row per pre-KO match (status != 'finished') so v10+ XGBoost inference (`_build_row_from_mfv`) finds a row instead of falling back to Poisson. Re-runs on every betting_refresh because opening_implied_* / odds_drift_home pick up newer snapshots between cron passes. Twin of the nightly `build_match_feature_vectors` (which only runs at settlement for finished matches); both share `_build_mfv_rows_for_matches`.
- For each match with odds: compute Poisson/XGBoost prediction + store predictions
- For each of 16 bots: calibrate, check odds movement (psycopg2), alignment (psycopg2), Kelly sizing, place bet
- `daily_pipeline_v2.py run_morning(skip_fetch=False)` still works for manual full runs

#### Paper-bet chain — operating invariants (GROWTH-TRACK-RECORD-CONTINUITY)

The `simulated_bets` table is the **public track-record chain** — the basis for every CLV/ROI claim on `/performance` (canonical URL; `/track-record` is a redirect) and the "tracked across N matches since YYYY" headline on the landing page. The marketing value compounds with time; every broken day is permanently lost from public history.

**Hard invariants (must hold every UTC day from 2026-04-27 onwards):**

| Invariant | Mechanism | Recovery if violated |
|---|---|---|
| `simulated_bets` daily insert count > 0 | Betting pipeline (job ⑤) runs ≥ 8× per UTC day | Rerun `betting_pipeline.py` manually; investigate scheduler/bots before next morning |
| Daily count typically ≥ 15 (anomaly threshold: < 5) | Alerted via `check_track_record_continuity()` in `health_alerts.py` (09:30 UTC) | Check `silent_bots` in ops_snapshots; check `pipeline_runs` for failed jobs; check AF Pinnacle availability |
| Rows are never deleted | No DELETE in pipeline code; rows are append-only | Postgres point-in-time-recovery via Supabase |
| `/performance` page never returns 5xx | `getDashboardCache()` reads from `dashboard_cache` table (refreshed every 30 min) | Refresh cache via `write_dashboard_cache.py` |

**Monitoring:** `workers/jobs/health_alerts.py::check_track_record_continuity()` runs daily at 09:30 UTC and fires two distinct admin alerts:
- `chain_broken_yesterday`: 0 picks yesterday → email to `ADMIN_ALERT_EMAIL`
- `chain_weak_yesterday`: 1..4 picks yesterday → email anomaly warning

**One-shot audit:** `python scripts/audit_track_record_chain.py --days N` walks the last N days and exits non-zero if any gap exists. Run before publishing any "tracked across N matches" claim that cites a count.

**Known historical gap (do not regress):** Pre-launch days (before 2026-04-27) are excluded from the chain. The 2026-05-02 single zero-day exists from before continuity monitoring was wired (this task). All days from 2026-05-03 onwards must remain populated.

### ⑥ Live Tracker / LivePoller (`live_poller.py` + `live_tracker.py`)

**Runs on Railway as a daemon thread** with tiered polling (replaced 5-min GH Actions cron):

| Tier | Interval | Endpoints | Calls/cycle |
|------|----------|-----------|-------------|
| **Fast** | 45s | `/fixtures?live=all` (bulk), `/odds/live` (bulk) | 2 |
| **Medium** | 135s | `/fixtures/statistics` (per match), `/fixtures/events` (per match) | 2N |
| **Slow** | 7.5min | `/fixtures/lineups` (upcoming), match map refresh | ~2-5 |

- DB writes via **direct PostgreSQL** (psycopg2 bulk inserts) — 10-50x faster than PostgREST
- Pre-match model context (O/U 2.5 probability) loaded into each snapshot
- All data written to unified `live_match_snapshots` row per match per cycle
- **On FT/AET/PEN:** immediately writes final score to `matches` table + triggers per-match settlement
- `build_af_id_map()` queries today + yesterday (handles UTC midnight rollover for late matches)
- ~8K-12K AF API calls/day during live play (was 12K-18K at 30s/60s — tuned 2026-05-08)
- **Runs 24/7** — no time-gate. Adaptive sleep: 45s when live matches found, 120s when idle
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

### ⑲ WC AI Match Previews (`wc_match_previews.py`) — WC-AI-PREVIEW
- Runs at 07:30 UTC daily, after fetch_predictions (04:00) + the national-team predictor have settled
- **Window-gated**: no-op outside `2026-06-04 → 2026-07-19` so APScheduler doesn't burn Gemini quota the rest of the year
- Selects every WC fixture (`leagues.api_football_id=1`) in the next 7 days
- For each: pulls international ELO (`team_elo_international`), last-5 internationals form, H2H from `matches.h2h_*`, our model's 1X2 from `predictions` (`source='national_team_v1'`), venue, derived bracket stage
- Calls Gemini 2.5 Flash-Lite: 80-120 word preview + 30-50 word teaser, OddsIntel voice, cliché blacklist baked into the prompt + scrubbed on output (no "clash of titans", no "should be a cracker", no "guaranteed")
- **Idempotent**: skips fixtures whose existing preview is < 24h old; `--force` regenerates everything in the window
- Rate-limited to 1 req/sec; Gemini errors caught + the loop continues (never crashes the cron)
- Reuses the existing `match_previews` table (migration 033) — distinct match_ids so no clash with the daily club preview job. Frontend `getMatchPreview()` and `getWorldCupPreviews()` both pick this up automatically.
- **Cost**: ~728 calls per tournament cycle (104 matches × ~7 refreshes each) at ~$0.01 / call on flash-lite ≈ **~$7 for the whole World Cup**
- Manual run: `python -m workers.jobs.wc_match_previews --dry-run`

### ⑪ Email Digest + Value Bet Alerts (`email_digest.py`) — ENG-4 + N5

**Smart-slot digest (10:00 / 12:00 / 14:00 / 16:00 UTC) — EMAIL-DIGEST-SMART:**
- Four scheduler slots; the first one whose pending-bet **signal-strength score** clears `EMAIL_DIGEST_MIN_SIGNAL` (default 5.0) sends the digest. Later slots see the per-user `email_digest_log` lock and skip — exactly one digest per user per day.
- Score = Σ(edge_pct × prestige_weight × kelly_fraction) over today's pending bets with edge ≥ 3%.
- **League prestige weights** (`workers/utils/league_prestige.py`):
  - **1.0** — Big-5 European tops (PL, La Liga, Bundesliga, Serie A, Ligue 1) + UCL/UEL/UECL + national-team showpieces
  - **0.7** — Eredivisie, Primeira, Belgian Pro, Turkish Süper Lig, Brazil Série A, Argentina, MLS, J1, K-League, Saudi Pro, plus Big-5 second tiers (Championship, Bundesliga 2, Serie B, La Liga 2, Ligue 2)
  - **0.4** — Other established top divisions (Switzerland, Austria, Greece, Russia, Ukraine, Czech, Poland, Croatia, Romania, Serbia, Israel, Cyprus, Finland, China, UAE, Egypt, S-Africa, Chile, Colombia, Uruguay, Indonesia, Iran, etc.)
  - **0.0** — Excluded entirely. Youth (U17-U23, Primavera), women's leagues, lower divisions, low-coverage countries.
- Email content (previews + Elite value-bet picks) is filtered to weight > 0 — no more "Brescia U19" in the email.
- Tier-appropriate HTML via Resend REST API:
  - **Free:** 3 preview teasers + bet count teaser + upgrade CTA
  - **Pro:** 3 full previews + value bet count + link to value bets page
  - **Elite:** 3 full previews + value bet table (top 5 with odds/edge/confidence)
- One email per user per day enforced by `email_digest_log` unique constraint (migration 034).
- `--force` CLI flag bypasses qualification for ad-hoc sends.

**Value bet alerts (16:00 + 20:45 UTC) — Pro/Elite only (N5):**
- `run_value_bet_alert('afternoon')` at 16:00 — bets placed since 10:00 UTC (11:00 + 15:00 refreshes)
- `run_value_bet_alert('evening')` at 20:45 — bets placed since 17:00 UTC (19:00 + 20:30 refreshes)
- No-op if no new bets in the slot window — never sends empty alerts
- Deduped per slot: `value_bet_alert_log` UNIQUE(user_id, alert_date, slot) (migration 046)
- Pro: bet count + CTA. Elite: full table with odds/edge/confidence.

- Only sends to users with `user_notification_settings.email_digest_enabled = true`
- Requires `RESEND_API_KEY` in env (+ Railway env vars).
- Manual run: `python -m workers.jobs.email_digest --dry-run`

### ⑨ Historical Backfill (`backfill_historical.py`) — ✅ COMPLETE 2026-05-28
- **All 5 phases complete** (phase 1: 57, phase 2: 54, phase 3: 23, phase 4: 176, phase 5: 123 combos). `backfill_complete.flag` exists; the scheduled job is auto-disabled.
- Phase 1–3 (original): 47,228 finished matches. Phase 4–5 (added 2026-05-28, COVERAGE-EXTENDED): 86 additional leagues, 73K+ more fixtures.
- Stats/events coverage is terminal for many lower-tier leagues — AF-permanent gap (empty `statistics` arrays). Fixtures + scores always present, which is all the Poisson model needs.
- 5 phases: Phase 1 = top ~20 leagues (3 seasons), Phase 2 = ~30 secondary (2 seasons), Phase 3 = ~50 remaining (1 season), Phase 4 = 44 extended global leagues (4 seasons, 2022–2025), Phase 5 = 42 depth/lower-tier leagues (3 seasons, 2023–2025)
- Budget-capped: aborts if < 10K API calls remaining; idempotent — resumes from `backfill_progress` table
- Auto-disables via `backfill_complete.flag`; stale flag auto-removed if new phases have work
- Manual run: `python scripts/backfill_historical.py --phase 4 --max-requests 5000 --batch-size 2000`

### ⑫ WC Bracket + Group-Standings Scoring (`wc_bracket_scoring.py`) — WC-BRACKET-SCORING / WC-GROUP-PREDICTOR (2026-06-02)
- Every 30 min at `:05/:35` UTC (lands after settlement + live-tracker writes at `:00/:30`)
- Gated on `2026-06-11 ≤ today ≤ 2026-07-19` — APScheduler still fires outside the window but the job returns immediately with no DB work
- **Bracket score** (max 122, includes Golden Boot 10): derive who advanced from finished WC matches (group stage by points → GD → GF; knockouts by match winner), score user picks using R32=1, R16=2, QF=4, SF=8, F=16, Champion=32
- **Group-standings score** (max 192 across 12 groups): per-group 1st=5 / 2nd=3 / 3rd=2 / 4th=1, +5 perfect-group bonus. Gated on all six fixtures in the group being finished — partial standings would bounce mid-stage
- Iterates over **both real users AND AI ghost entries** (rows with `ai_label IS NOT NULL`) in `wc_bracket_meta`. Writes `current_score` (bracket), `group_standings_score`, `total_score = sum`, `current_rank` (dense, combined ranking), `current_percentile` (Top X% — used by UI when total entries < 200)
- Golden Boot actual winner read from `app_settings` key `wc_golden_boot_actual` (manual operator stamp); AF top-scorer endpoint automation filed as WC-GOLDEN-BOOT-AUTO
- Manual run: `python -m workers.jobs.wc_bracket_scoring`

### ⑫b WC Achievement Detection (`wc_achievement_detection.py`) — WC-ACHIEVEMENTS (2026-06-02)
- Every 15 min at `:00/:15/:30/:45` UTC, gated on the same `2026-06-11 ≤ today ≤ 2026-07-19` window
- PARALLEL to scoring — never mutates `wc_bracket_meta`; adds idempotent rows only to `wc_user_achievements`
- 15-slug catalog: submission timing (`first_to_lock`, `early_bird`, `last_minute`), groups (`groups_perfect_one`, `groups_perfect_three`, `groups_all_perfect`), bracket skill (`r32_beat_ai` vs OddsIntel Elite AI, `final_called`, `champion_correct`, `called_the_upset` — lower-ELO winner from `team_elo_international`), per-match (`vs_you_streak_5`, `vs_you_streak_10`, `vs_you_perfect_day`), engagement (`viewed_all_groups` — reads optional `wc_group_views` table, silent no-op when absent), `golden_boot_correct`
- AI ghosts excluded — achievements are a user-engagement loop, not a benchmark
- Idempotent via UNIQUE (user_id, slug) in migration 172 — re-runs are safe
- Manual run: `python -m workers.jobs.wc_achievement_detection`

### One-shot: WC AI Ghost Generator (`generate_ai_brackets.py`) — WC-AI-GHOSTS (2026-06-02)
- One-off, not on cron. Run before WC kickoff (2026-06-11 19:00 UTC) to seed the 5 AI ghost entries on the leaderboard
- Strategies: `OddsIntel Elite AI`, `OddsIntel Pro AI`, `OddsIntel Free AI`, `Market Implied`, `Chalk` — each writes a complete bracket + 48-row group-standings prediction. AI rows have `ai_label` set and `user_id` NULL
- Idempotent until lock: each re-run wipes-and-replaces AI rows. After lock the script refuses to overwrite (use `--force` for dev override)
- Manual run: `python scripts/generate_ai_brackets.py` (all 5) · `--strategy chalk` · `--dry-run`

### ⑨b Internationals Backfill (`backfill_internationals.py`) — WC-PHASE-2 (2026-06-02)
- One-off script to pull historical national-team competition fixtures (WC 2018/2022, Euro 2020/2024, Copa America, AFCON, Asian Cup, Gold Cup, all UEFA Nations League editions, WC 2022 + 2026 qualifiers across all 6 confederations, Friendlies). Required because the regular date-mode `fetch_fixtures` only pulls today and never sees historical international matches.
- 59 (league, season) tuples → ~3,000 finished matches.
- Phase A: fixtures via `get_fixtures_by_league_season` + `bulk_store_matches` (idempotent upsert).
- Phase B: nested data (lineups, events, statistics, player stats) via `get_fixtures_batch(20-at-a-time)` for finished matches only. Skips matches that already have a `match_stats` row.
- Manual run: `python scripts/backfill_internationals.py` (full) or `--filter "World Cup,Euro"` for a subset, or `--dry-run` / `--no-enrichment` to scope down.
- Not in the cron schedule — invoke once after each WC qualification cycle / major tournament finishes.

### ⑩ Extended CSV Export (`generate_targets_extended.py`) — run after any new backfill
- Exports all finished DB matches not already in `targets_poisson_history.csv` or `targets_global.csv` to `data/processed/targets_extended.csv`
- Single PostgreSQL `COPY TO STDOUT` — no Python row loops; completes in seconds
- **426 leagues, 85,379 rows** as of 2026-05-28
- `daily_pipeline_v2.py` `pd.concat`s this into `hist_targets_global` at startup → those teams become Tier B (+2% edge bump) instead of Tier C (+8%)
- Manual run: `python scripts/generate_targets_extended.py`
- After regenerating, force-commit: `git add -f data/processed/targets_extended.csv && git push`

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
