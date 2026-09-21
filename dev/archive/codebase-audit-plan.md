# Codebase Audit — Performance & Design Cleanup

## Context

Tonight (2026-05-09) we hit `psycopg2.pool.PoolError: connection pool exhausted`
multiple times in production. Root-cause work uncovered structural issues
beyond the immediate pool fix. User asked for a full audit of `scripts/` and
`workers/` to find similar problems before they bite us in prod.

Audit completed 2026-05-09 ~21:30 UTC. Findings below.

## Findings — by category, priority order

### A. Per-row DB writes inside loops (perf + conn-hold)

These are the same anti-pattern that made `recover_today.py` look frozen for
20 min on the fixtures step. Each iteration grabs a conn, does 1–5
round-trips, releases.

| File:line | Loop | Per-iter cost | Fix |
|---|---|---|---|
| `workers/api_clients/supabase_client.py:159` (`store_match`) | called per fixture | ~5 round-trips (ensure_team×2, dedup SELECT, INSERT/UPDATE) | **bulk_store_matches** — pre-load existing, bulk INSERT/UPDATE. Used by 3 callers. |
| `workers/jobs/fetch_fixtures.py:63` | per AF fixture | 5 round-trips × 1500 ≈ 15min | Switch to bulk_store_matches |
| `workers/jobs/daily_pipeline_v2.py:1236` | per AF fixture | same | Switch to bulk_store_matches |
| `workers/jobs/daily_pipeline_v2.py:1430` | per odds match | same | Switch to bulk_store_matches |
| `scripts/backfill_historical.py:337` | per finished fixture | same | Switch to bulk_store_matches |
| `workers/jobs/settlement.py:445` | per match row | execute_write | Bulk UPDATE with VALUES list |
| `workers/jobs/settlement.py:535` | per user pick | execute_write | Bulk UPDATE |
| `workers/jobs/settlement.py:1156` | per pick | execute_write | Bulk UPDATE |
| `workers/jobs/news_checker.py:370` | per injured player | execute_write | Bulk INSERT or `bulk_upsert` |

**API-bound loops (don't bother with bulk DB — bottleneck is HTTP):**
- `backfill_team_enrichment.py:142` (coaches per team, AF endpoint is per-team)
- `backfill_team_enrichment.py:160` (transfers per team)
- `fetch_predictions.py:65` (AF predictions per match)

### B. Observability — 11 scheduled jobs don't log to `pipeline_runs`

This is why the ops dashboard shows "everything failing" — half the system
is invisible. `_run_job` in `scheduler.py` only does console logging + an
in-memory `_last_job` snapshot; the job function itself must call
`log_pipeline_start/complete/failed` to appear in the table.

Missing log:
- `morning_pipeline` (parent — sub-steps log, parent doesn't)
- `betting_refresh`
- `news_checker`, `match_previews`, `email_digest`, `weekly_digest`, `watchlist_alerts`
- `settle_ready` (only `settlement` parent logs)
- `backfill_coaches`, `backfill_transfers` (only `hist_backfill` logs)
- `live_tracker`
- `budget_sync`

**Fix**: lift logging into `_run_job` so every scheduled job is automatically
visible. Adds ~3 lines to one function and instantly closes the gap. Sub-step
logging inside complex jobs (settlement chain) stays intact.

### C. Long functions (testability/maintenance)

Functions >150 lines doing too many things. Rough headline order:

| Lines | File:line | Function |
|---|---|---|
| 709 | `workers/jobs/daily_pipeline_v2.py:1174` | `run_morning` |
| 654 | `workers/api_clients/supabase_client.py:3069` | `add` (signal/feature builder) |
| 606 | `workers/api_clients/supabase_client.py:4058` | `write_ops_snapshot` |
| 308 | `workers/jobs/live_tracker.py:271` | `run_live_tracker` |
| 271 | `workers/jobs/news_checker.py:203` | `run_news_checker` |
| 204 | `scripts/backfill_historical.py:282` | `backfill_league_season` |
| 203 | `workers/jobs/settlement.py:559` | `run_settlement` |
| 201 | `workers/live_poller.py:197` | `_run_cycle` |
| 192 | `workers/jobs/inplay_bot.py:115` | `run_inplay_strategies` |

**Recommendation**: not a refactor for tomorrow — these are battle-tested
critical paths. Only worth touching when actively making behavioral changes
(extract helpers as you go). Flag for awareness.

### D. Silent exception handling — 64 `except: pass` cases

Most are in `supabase_client.py` ML feature-row builders that are
intentionally tolerant of missing data. **Don't blanket-fix** — each is a
judgment call. But there are some bad ones:

- `workers/scheduler.py:186, 196, 205` — `log_pipeline_start/complete` errors
  swallowed silently. If logging is broken, we'd never know.
- `workers/live_poller.py:308` — `store_match_events_batch` errors
  swallowed. Means events disappear silently if DB write fails.
- `workers/api_clients/db.py:176` (mine, in pool reset path) — fine, intentional.

**Fix**: add a one-line console warning to the high-value ones (scheduler
logging, events store). Skip the rest until a real bug forces it.

### E. SQL f-string interpolation — 2 cases

Both are dynamic SET clause builders, not user-input concatenation:
- `workers/utils/pipeline_utils.py:45` — `update_pipeline_run` builds SET clauses from kwargs keys
- `workers/jobs/news_checker.py:344` — same pattern, simulated_bets SET clauses

These columns are hardcoded, not user input — **safe in practice**. Worth
adding a column-name allowlist for defense in depth, but low priority.

### F. Job timeouts / unbounded operations

- `scripts/stripe_reconcile.py:58` — `while True` with proper `break` on
  pagination end. OK.
- `workers/api_clients/db.py:119` — `while True` in `_acquire_conn` with
  deadline. OK (mine).
- No unbounded retry loops found.

### G. Connection-held-during-API-call — 0 cases ✅

The classic anti-pattern (grab DB conn, then make slow HTTP call while
holding it) is **not present**. Code reliably opens conns just for the DB
work. Good.

## Priority for tomorrow

1. **`bulk_store_matches`** — biggest concrete win. Cuts fetch_fixtures from
   ~15min → ~5sec. Used in 4 places. ~2–3h with proper smoke test.
2. **Lift `pipeline_runs` logging into `_run_job`** — closes the
   observability hole that's been making "everything looks broken" look
   worse than it is. ~30min.
3. **LivePoller service split** — already discussed, ~30min once 1 + 2 land.

The other findings (long functions, silent excepts) go into PRIORITY_QUEUE
as awareness items, not "fix tomorrow."
