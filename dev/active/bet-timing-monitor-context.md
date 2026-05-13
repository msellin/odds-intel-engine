# BET-TIMING-MONITOR — Context

## Status
- Started: 2026-05-13
- Stage: implementation in progress

## Key files

| File | Role |
|---|---|
| `workers/jobs/daily_pipeline_v2.py` | `run_morning()` — the model + bet generator. Needs `shadow_mode` kwarg. `BOT_TIMING_COHORTS` dict. `store_bet` call site (~line 2241). |
| `workers/api_clients/supabase_client.py` | `store_bet()` (line 1724), bankroll mutation, ops_snapshot. New `bulk_store_shadow_bets()` lives here. |
| `workers/jobs/betting_pipeline.py` | `run_betting(cohort)` wrapper — delegates to run_morning. Used by Railway scheduler. |
| `workers/scheduler.py` | Railway cron — `job_betting_refresh()`. Add new shadow jobs. |
| `workers/jobs/settlement.py` | `run_settlement()`, `_settle_pending_bets()`. Extend to settle shadow_bets. |
| `supabase/migrations/101_shadow_bets.sql` | New migration (next sequential = 101, current top = 100) |
| `scripts/smoke_test.py` | New SHADOW-* tests |

## Decisions made

- **Separate table, not a flag on simulated_bets.** Cleaner isolation; existing queries don't accidentally include shadows.
- **3 shadow runs/day, one per cohort window.** Matches the granularity of the cohort A/B and the data we'd actually use to decide moves. Hourly is overkill for the question.
- **Shadow runs ALL bots regardless of assigned cohort.** Otherwise we just reproduce the existing cohort structure with a flag.
- **No bankroll math for shadows.** Use a fixed nominal stake (10u) for ROI math; real Kelly sizing would require tracking per-cohort bankrolls, premature complexity.
- **Migration number 101.** Current top is 100 (`100_model_calibration_logodds.sql`).

## Risks identified

1. **Settlement performance** — adding ~600 settlement updates/day for shadow_bets. Mitigation: use the same batched UPDATE path as simulated_bets.
2. **Query pollution** — anywhere `simulated_bets` is read could accidentally include `shadow_bets`. Mitigation: separate table + smoke test that greps for cross-references.
3. **Cohort dilution in shadow data** — if at 06:00 we run shadow for a bot that lives in pre_ko, the bot may be using stale lineup info. That's the *point* — we want to see whether the bot's edge holds at that earlier time.
4. **PRIORITY_QUEUE confusion** — multiple existing timing tasks (BET-TIMING-ANALYSIS done, ODDS-TIMING-VALIDATE waiting, ODDS-TIMING-OPT ready). This new task is the missing piece (factorial design); the others stay valid and complementary.

## Next steps in order

1. Migration 101
2. `bulk_store_shadow_bets` in supabase_client
3. `run_morning(shadow_mode=True)` plumbing
4. Scheduler 3 new jobs
5. Settlement extension
6. Ops snapshot counters
7. Smoke tests
8. Doc updates
9. Commit + push

If session is interrupted: read `bet-timing-monitor-tasks.md` for status, then continue from first ⬜ task.
