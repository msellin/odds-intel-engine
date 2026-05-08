# Codebase Audit — Context

## What's done

- Audit of `scripts/` + `workers/` complete (2026-05-09 ~21:30 UTC).
- Findings in `codebase-audit-plan.md`.
- New PRIORITY_QUEUE entries: BULK-STORE-MATCHES, OBS-LOG-ALL-JOBS,
  WORKER-SPLIT-LIVEPOLLER, AUDIT-LONG-FUNCS, AUDIT-SILENT-EXCEPT.

## What triggered this

Tonight `recover_today.py` looked frozen for 20 min on step 1 (Fixtures
Fetch). Killed by user. Investigation showed:

- `store_match` is called per-fixture in a loop, doing ~5 round-trips per
  call (ensure_team×2, dedup SELECT, INSERT-or-UPDATE).
- 1500 fixtures × ~5 round-trips × ~150ms RTT to EU pooler ≈ 15 min real time.
- Recovery script had no progress output → looked frozen.

Same `for-row: write` anti-pattern found in 8 other places, including
multiple `settlement.py` per-pick UPDATE loops.

## Already shipped tonight (2026-05-09)

- `scripts/recover_today.py` heartbeat (every 15s "still running for Xs")
- `fetch_and_store_fixtures` progress logging every 100 fixtures (rate, ETA)
- POOL-FANOUT fixes: cap settlement enrichment 4→2 workers, APScheduler
  executor capped at 4 threads, bulk-insert events via execute_values,
  DB_POOL_WAIT_TIMEOUT default 60s→15s

## Tomorrow's order (decided 2026-05-09)

1. **BULK-STORE-MATCHES** first — biggest concrete win. Fresh head, ~2-3h
   with proper smoke test. Used in 4 places.
2. **OBS-LOG-ALL-JOBS** second — closes observability hole. ~30m.
3. **WORKER-SPLIT-LIVEPOLLER** third — process isolation. ~30m + Railway
   dashboard click.

## Files to read before starting BULK-STORE-MATCHES

- `workers/api_clients/supabase_client.py:159-300` — current `store_match`
  body. Note conditional UPDATE — only sets fields that are NULL in DB.
- `workers/jobs/fetch_fixtures.py:60-95` — first caller, bulk-friendly site
  to refactor first.
- `workers/jobs/daily_pipeline_v2.py:1236, 1430` — second/third callers.
- `scripts/backfill_historical.py:337` — fourth caller.
- Existing `bulk_insert` and `bulk_upsert` helpers in
  `workers/api_clients/db.py:180-240` for the pattern to mirror.

## Open question for tomorrow

`store_match` calls `ensure_team` per fixture. That itself does a SELECT
(fuzzy-match team name + country) and possibly an INSERT. If we bulk-fetch
matches but still call `ensure_team` per fixture, we save the dedup SELECT
but still pay the team-lookup cost. Two options:

- **Option A** (simpler): bulk-fetch matches only. Per-fixture still calls
  ensure_team. Likely cuts step from 15min → 5min.
- **Option B** (full): also bulk-resolve teams up-front (all home + away
  team names → existing team_ids in one query, INSERT only the new ones).
  Cuts step to ~5 sec.

Probably do A first, ship, then B if 5 min is still too slow.
