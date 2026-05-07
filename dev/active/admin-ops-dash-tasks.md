# ADMIN-OPS-DASH — Detailed Task List

> Created: 2026-05-07
> Spec: PRIORITY_QUEUE.md § ADMIN-OPS-DASH
> Status: Not started

Impact scale: 5 = dashboard broken without it · 4 = high value · 3 = useful · 2 = polish · 1 = optional

---

## Phase 1 — Engine: `ops_snapshots` table + writer (~5.5h total)

| # | Task | Impact | Est. time | Notes |
|---|------|--------|-----------|-------|
| 1.1 | Migration NNN: `CREATE TABLE ops_snapshots` (42 columns per spec) | 5 | 30 min | Next migration number after current highest in supabase/migrations/ |
| 1.2 | `write_ops_snapshot()` skeleton in `supabase_client.py` — date param, opens connection, calls all query groups, bulk-inserts one row | 5 | 20 min | Follow pattern of existing bulk functions |
| 1.3 | **Fixtures & coverage queries** — `matches_today`, `matches_with_odds`, `matches_with_pinnacle`, `matches_with_predictions`, `matches_with_signals`, `matches_with_fvectors`, `matches_missing_grade`, `matches_postponed_today` | 5 | 45 min | Most joins touch `matches` + other tables via EXIST/COUNT DISTINCT |
| 1.4 | **Odds pipeline queries** — `odds_snapshots_today`, `distinct_bookmakers`, `matches_without_pinnacle` | 5 | 20 min | `COUNT(DISTINCT bookmaker)` on odds_snapshots for today — ensure index on snapshot_time exists |
| 1.5 | **Betting & bots queries** — `bets_placed_today`, `bets_pending`, `bets_settled_today`, `pnl_today`, `bets_inplay_today`, `active_bots`, `silent_bots`, `duplicate_bets` | 5 | 45 min | `silent_bots` = 17 - active_bots (or count bot_ids with 0 bets vs known bot list). `duplicate_bets` = subquery with HAVING COUNT > 1 |
| 1.6 | **Live/in-play queries** — `live_snapshots_today`, `snapshots_with_xg`, `snapshots_with_live_odds` | 4 | 20 min | `snapshots_with_live_odds` was always 0 before 2026-05-07 fix — now meaningful |
| 1.7 | **Post-match queries** — `matches_finished_today`, `post_mortem_ran_today`, `feature_vectors_today`, `elo_updates_today` | 4 | 25 min | `post_mortem_ran_today`: check `model_evaluations WHERE market='post_mortem' AND date=today`. `elo_updates_today`: COUNT from `team_elo_daily WHERE updated_at::date=today` |
| 1.8 | **Enrichment quality queries** — `matches_with_h2h`, `matches_with_injuries`, `matches_with_lineups` | 3 | 20 min | `matches_with_lineups`: JOIN via `matches.kickoff_time::date` not lineups.created_at (early-morning fixtures) |
| 1.9 | **Email & alerts queries** — `digests_sent_today`, `value_bet_alerts_today`, `previews_generated_today`, `news_checker_errors_today`, `watchlist_alerts_today` | 4 | 25 min | Check actual table/column names: `email_digest_log.sent_at`, `value_bet_alert_log.alert_date`, `match_previews.created_at` |
| 1.10 | **Backfill queries** — `backfill_total_done`, `backfill_last_run` | 3 | 15 min | `COUNT(DISTINCT match_id) FROM match_stats` (expensive — add LIMIT guard or cache it) |
| 1.11 | **User queries** — `total_users`, `pro_users`, `elite_users`, `new_signups_today` | 3 | 15 min | FROM profiles; new_signups = created_at::date = today |
| 1.12 | Hook `write_ops_snapshot()` into `run_fixtures` (end of job) | 5 | 5 min | |
| 1.13 | Hook into `run_odds` | 5 | 5 min | |
| 1.14 | Hook into `run_morning` (betting pipeline) | 5 | 5 min | |
| 1.15 | Hook into `run_settlement` | 4 | 5 min | |
| 1.16 | Hook into `run_enrichment` | 3 | 5 min | |
| 1.17 | Fallback cron in `scheduler.py` — every 60 min at :00 | 4 | 10 min | `for h in range(24): scheduler.add_job(job_ops_snapshot, CronTrigger(hour=h, minute=0))` |
| 1.18 | Smoke test: `write_ops_snapshot()` runs without error and returns expected types | 4 | 15 min | Add to `scripts/smoke_test.py` |

**Phase 1 total: ~5.5h**

---

## Phase 2 — Frontend: `/admin/ops` dashboard (~7h total)

| # | Task | Impact | Est. time | Notes |
|---|------|--------|-----------|-------|
| 2.1 | `getOpsSnapshot()` + `getOpsSnapshotHistory(days=7)` in `engine-data.ts` | 5 | 20 min | DISTINCT ON (snapshot_date) ORDER BY snapshot_date, created_at DESC for history |
| 2.2 | Live query functions: `getPipelineJobsToday()`, `getStalePendingBets()`, `getLastSnapshotAge()` | 5 | 20 min | DISTINCT ON (job_name) from pipeline_runs; JOIN simulated_bets+matches; MAX from live_match_snapshots |
| 2.3 | `/admin/ops/page.tsx` skeleton — server component, superadmin gate (same pattern as `/admin/bots`) | 5 | 20 min | Parallel fetch: Promise.all([getOpsSnapshot(), getPipelineJobsToday(), getStalePendingBets(), getLastSnapshotAge()]) |
| 2.4 | **Top 8-KPI strip** — 8 colored tiles, each green/yellow/red based on threshold logic | 5 | 30 min | Matches Today, Prediction Coverage %, Bookmakers, Bets Today, Silent Bots, Stale Pending, Last Snapshot, AF Budget % |
| 2.5 | **"Currently Broken" auto-feed** — evaluates all alert conditions, renders plain-English list | 5 | 45 min | Most complex component. Input: all fetched data. Output: string[] of issues. Empty = show green "All systems healthy". Examples: "3 bots silent", "LivePoller stale 12 min", "17 matches missing grade" |
| 2.6 | **Panel 1: Pipeline job health grid** — table with job_name, last run, status badge, duration, rows_affected, error | 5 | 45 min | Color logic: red=error or stuck, yellow=rows_affected=0 on output-producing jobs, green=success. Use live data from getPipelineJobsToday() |
| 2.7 | **Panel 2: Data funnel** — horizontal waterfall bar chart | 5 | 30 min | Each stage = one bar proportional to matches_today. % ratio label between bars. Biggest drop highlighted. Use Recharts BarChart or simple CSS width bars |
| 2.8 | **Panel 3: Bot health** — summary numbers + per-bot table | 4 | 30 min | Summary: bets_placed_today (large), active_bots / 17, bets_inplay_today. Table: bot_id row red if bets=0 and matches_today≥10. Live query from simulated_bets |
| 2.9 | **Panel 4: Live tracker** — last snapshot age (large, real-time feel) + mini sparkline | 4 | 25 min | Last snapshot age from live query. Sparkline: snapshots_with_xg %, snapshots_with_live_odds % from ops_snapshots |
| 2.10 | **Panel 5: Settlement health** — stale pending badge + P&L breakdown + ELO tick | 5 | 25 min | Red badge "X bets stuck" from live query. Won/Lost/Pending counts. ELO: boolean from elo_updates_today |
| 2.11 | **Panel 6: Data quality scorecard** — table of checks, count, color | 4 | 20 min | matches_missing_grade, matches_with_0_signals, matches_without_pinnacle %, duplicate_bets, news_checker_errors_today |
| 2.12 | **Panel 7: API budget** — progress bar 0→75K with NULL state | 3 | 20 min | Show "—" / "Requires Phase 3" when af_calls_today IS NULL. Don't show 0 — misleading |
| 2.13 | **Panel 8: Email & alerts** — 5 count cards | 3 | 20 min | digests_sent_today, value_bet_alerts_today, previews_generated_today, watchlist_alerts_today, news_checker_errors_today |
| 2.14 | **Panel 9: 7-day sparklines** — 8 mini Recharts LineCharts | 3 | 45 min | No axes, just line + today value large. Red dot if today < 7-day avg × 0.60. Series: matches_today, distinct_bookmakers, bets_placed_today, signals ratio, live_snapshots, bets_settled, af_calls, new_signups |
| 2.15 | Manual refresh button → `/api/ops-snapshot/refresh` POST route (calls write_ops_snapshot, returns fresh data) | 3 | 20 min | Superadmin-only API route. Returns 200 + timestamp on success |

**Phase 2 total: ~7h**

---

## Phase 3 — AF Budget Persistence (~50 min, independent)

| # | Task | Impact | Est. time | Notes |
|---|------|--------|-----------|-------|
| 3.1 | Migration: `CREATE TABLE api_budget_log (id SERIAL, date DATE, job_name TEXT, calls_made INT, created_at TIMESTAMPTZ)` | 3 | 15 min | Simpler than adding to ops_snapshots; allows per-job breakdown |
| 3.2 | `write_budget_log(job_name, calls_made)` in `api_football.py` or `db.py` — called after each AF job with `budget_tracker.today_calls` | 3 | 20 min | BudgetTracker already tracks `today_calls` in-memory — just persist it |
| 3.3 | Update `write_ops_snapshot()` to read `SUM(calls_made) FROM api_budget_log WHERE date=today` into `af_calls_today` | 3 | 15 min | Remove NULL guard once this is in place |

**Phase 3 total: ~50 min**

---

## Grand total

| Phase | Time | Blocker? |
|-------|------|----------|
| Phase 1 — Engine | ~5.5h | None — can start immediately |
| Phase 2 — Frontend | ~7h | Needs Phase 1 done (needs real data to test against) |
| Phase 3 — AF Budget | ~50 min | Independent — do any time |
| **Total** | **~13.5h (~1.75 days)** | |

---

## Running costs

**Zero additional cost.** Breakdown:

| Resource | Current load | ADMIN-OPS-DASH addition | Impact |
|----------|-------------|------------------------|--------|
| Supabase DB queries | ~thousands/day from pipeline | ~300 COUNT queries/day (15 writes × ~20 queries each) | Negligible — all run in <2s total per write |
| ops_snapshots rows | — | ~15 rows/day → ~5,500/year → ~2MB/year | Negligible |
| Railway CPU | Already running scheduler + all jobs | write_ops_snapshot() adds ~2–3s per job that calls it | Rounding error |
| Supabase bandwidth | — | ~10KB/day of snapshot data written | Zero |
| AF API calls | ~8K/day live ops | 0 new calls — dashboard reads DB only | Zero |
| Gemini | ~$0.55/mo | 0 new calls | Zero |
| External services | — | None needed | Zero |

**The only risk**: `COUNT(DISTINCT match_id) FROM match_stats` for backfill_total_done is a full-table scan on a growing table (~18K+ rows). If it gets slow, replace with a cached counter updated by the backfill job itself.

---

## Value vs. other open tasks

See separate answer below.
