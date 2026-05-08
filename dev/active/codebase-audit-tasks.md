# Codebase Audit — Tomorrow's Task Checklist

## Order (do top-down)

### 1. BULK-STORE-MATCHES (~2-3h)

- [ ] Read `workers/api_clients/supabase_client.py:159-300` — understand
      conditional UPDATE logic (only sets fields where DB has NULL)
- [ ] Read `workers/api_clients/db.py:180-240` — `bulk_insert` /
      `bulk_upsert` patterns to mirror
- [ ] Decide Option A (bulk-fetch matches only) vs B (also bulk-resolve
      teams) — see context doc
- [ ] Implement `bulk_store_matches(list_of_dicts) -> dict[af_id, match_id]`
      in `supabase_client.py`
- [ ] Smoke test: 50-fixture sample, run new bulk version + old per-row,
      diff resulting `matches` table rows. Must match exactly.
- [ ] Migrate `workers/jobs/fetch_fixtures.py:63`
- [ ] Migrate `workers/jobs/daily_pipeline_v2.py:1236`
- [ ] Migrate `workers/jobs/daily_pipeline_v2.py:1430`
- [ ] Migrate `scripts/backfill_historical.py:337`
- [ ] Run smoke suite
- [ ] Commit, push, watch Railway redeploy
- [ ] Verify next morning_pipeline run succeeds with fast fixtures step
- [ ] Update PRIORITY_QUEUE.md entry to ✅ Done

### 2. OBS-LOG-ALL-JOBS (~30m)

- [ ] Modify `_run_job()` in `workers/scheduler.py:54` to call
      `log_pipeline_start` before `fn()` and `log_pipeline_complete/failed`
      after
- [ ] For jobs whose body already logs (e.g. `settlement_pipeline`,
      `run_odds`, `run_enrichment`): make the wrapper detect existing run
      via `pipeline_runs` query and skip the wrapper log, OR pass a flag
      to suppress it
- [ ] Smoke test: source-inspect `_run_job` to verify both calls present
- [ ] Verify on local: run a single job manually, confirm `pipeline_runs`
      gets a row
- [ ] Commit, push
- [ ] Verify on `/ops` dashboard: previously-invisible jobs now appear
- [ ] Update PRIORITY_QUEUE.md to ✅ Done

### 3. WORKER-SPLIT-LIVEPOLLER (~30m)

- [ ] Create `workers/live_poller_main.py` — minimal entrypoint that
      starts the LivePoller and the budget tracker (~10 lines)
- [ ] Remove LivePoller startup from `workers/scheduler.py:716` (the
      thread block)
- [ ] Update Dockerfile if needed (probably no change — Railway service
      can override CMD)
- [ ] Document the second service in `WORKFLOWS.md` and
      `INFRASTRUCTURE.md`
- [ ] Commit, push
- [ ] In Railway dashboard: create new service from same repo with
      `python -m workers.live_poller_main` start command
- [ ] Verify: scheduler service has only scheduler-side conn pool
      activity; LivePoller service has its own pool with snapshot writes
- [ ] Update PRIORITY_QUEUE.md to ✅ Done

## Awareness only (no work tomorrow)

- AUDIT-LONG-FUNCS — flag for next agent who modifies these
- AUDIT-SILENT-EXCEPT — 3 high-value spots logged in queue, do whenever
