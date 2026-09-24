# MATCH-DUPES-CLEANUP — Plan

## Context

Ops dashboard reported 3,136 "matches today" but `fetch_fixtures` had only stored 1,211 fixtures
that morning. Database investigation found 1,425 duplicate fixture groups in the `matches` table
(3,177 extra rows total).

**Smoking gun:** AF id `1516826` exists 4× — same home/away `team_id` UUIDs, identical kickoff
`2026-05-10 05:00 UTC`, but four different `matches.id` UUIDs. Created May 5, May 10 04:03,
May 10 09:17, and May 10 10:48 (the latter coincided with `fixture_refresh` running at 10:45).

## Root cause

`bulk_store_matches` (workers/api_clients/supabase_client.py:393–413) and the legacy `store_match`
(workers/api_clients/supabase_client.py:174–180) both dedup using
`(home_team_id, away_team_id, date_prefix)` where `date_prefix = match_date[:10]`.

When AF reschedules a fixture across a UTC day boundary (originally May 9 → moved to May 10):

1. May 5 fetch with kickoff May 9 → row inserted, `date::date = 2026-05-09`.
2. May 10 fetch with kickoff May 10 → dedup looks for matches in
   `[2026-05-10 00:00, 2026-05-10 23:59]`, can't see the May-9 row → INSERT new row.
3. Later, some path updates the May-9 row's `date` to May 10 too.
4. Two rows now coexist with the same teams + same kickoff.

There is no DB-level unique constraint on `api_football_id` (only a non-unique index), so the
app-level dedup miss is silently accepted.

## Downstream impact

- `matches_today` `COUNT(*)` inflated ~3× → ops dashboard wrong.
- Bets, odds, signals, predictions scatter across duplicate rows → settlement and the betting
  pipeline can place bets on the "same" match multiple times.
- `bot_ou15_defensive` page surfaced ghost high-odds OU 1.5 entries — those bets had been voided
  by Stage C of the OU cleanup but the leaderboard table didn't filter `result='void'`.

## Stages

### Stage A — Cleanup script (dry-run + --apply)

`scripts/cleanup_match_dupes.py`:

1. Build canonical map: for each `api_football_id` with >1 row, oldest by `created_at` is canonical.
2. For each of the 24 tables with `match_id`:
   - Pre-delete dupe-side rows where the canonical row already has a row at the same
     unique-key tuple (18 unique constraints across the FK-bearing tables).
   - UPDATE remaining `match_id` from dupe → canonical.
3. Mirror the dupe `matches` rows to `matches_dupe_quarantined` (auto-created on first run) for
   forensic rollback.
4. DELETE the dupe `matches` rows.

Dry-run prints what would happen per table without writing. `--apply` executes.

### Stage B — Unique constraint

`supabase/migrations/089_matches_unique_af_id.sql`:

```sql
CREATE UNIQUE INDEX matches_af_id_unique
  ON matches(api_football_id)
  WHERE api_football_id IS NOT NULL;
```

Partial index — keeps NULL-AF-id legacy rows (none expected, but safe). Once active, even if the
app-level dedup ever misses again, the INSERT will fail loudly instead of silently accepting a dupe.

### Stage C — Fix the dedup logic

`workers/api_clients/supabase_client.py`:

- `bulk_store_matches` — bulk dedup SELECT now joins on `api_football_id` first (when present),
  then falls back to the home/away/date-window join for rows whose AF id is NULL or absent.
- `store_match` — same change for the per-row helper.

This survives reschedules by design: AF id is stable across kickoff date changes.

### Stage D — Frontend fix

`src/components/performance-leaderboard.tsx` (odds-intel-web):

- `botBets` table filters `result !== 'void'` so cleanup-voided bets don't pollute the per-bot
  history view.
- Bankroll chart already uses `won|lost` only, so void rows never affected the chart.

### Stage E — Ops dashboard copy

`src/app/(app)/admin/ops/page.tsx`:

- Subtitle was "Last fixtures fetch pulled X upcoming matches… Of those, Y play today" — that
  contrasted the wrong numbers (X is one date's fetch count, Y is today's `COUNT(*)`). Rewrite
  to plain phrasing now that Y is honest.

### Stage F — Smoke tests

`scripts/smoke_test.py`:

- `MATCH-DUPES — bulk_store_matches dedup uses api_football_id first`
- `MATCH-DUPES — store_match dedup uses api_football_id first`
- `MATCH-DUPES — migration 089 has unique index on api_football_id`
- `MATCH-DUPES — performance-leaderboard filters result='void'`

## Order of operations

1. Stage A dry-run → confirm counts match expectation (~3,177 deletions).
2. Stage A `--apply`.
3. Stage B migration applied via `supabase db push`.
4. Stage C + D + E code fixes.
5. Stage F smoke tests.
6. Single commit, push to main.

## Out of scope

- `OU-LINE-DRIFT-INVESTIGATE` (still open; separate task).
- `BOT-AGGREGATES-SSOT` (still open; separate task).
- Adjusting bet-pipeline logic to detect "same AF id, multiple match rows" — moot once the unique
  index lands (Stage B), since it can't happen again.
