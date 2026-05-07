# AF Data Expansion — Task Checklist

Tasks: Batch enrichment, half-time signals, sidelined, transfers
Started: 2026-05-07
**Completed: 2026-05-07**

## Task 1 — Batch fixture enrichment ✅

- [x] Add `get_fixtures_batch(ids)` to `workers/api_clients/api_football.py`
- [x] Refactor `fetch_post_match_enrichment()` in `workers/jobs/settlement.py` to batch-fetch fixture data before threading (ceil(N/20) calls instead of 4N)

## Task 2 — Half-time stats (data already stored — signals + frontend missing) ✅

- [x] Add H1 tendency signals to `batch_write_morning_signals` in `supabase_client.py` (block 13)
  - `h1_shot_dominance_home` / `h1_shot_dominance_away` (shots H1 / shots total rolling avg last 5)
- [x] Update match stats component to render H1 shots-on-target, fouls, yellow cards (`match-detail-live.tsx`)
- [x] Add signal labels (`signal-labels.ts`) + accordion rendering (`signal-accordion.tsx`)

## Task 3 — Sidelined (fetch + signal + frontend) ✅

- [x] Add `fetch_player_sidelined()` to `workers/jobs/fetch_enrichment.py` (7-day cache)
- [x] Add `injury_recurrence_home` / `injury_recurrence_away` signals to `batch_write_morning_signals` (block 12)
- [x] Update `getMatchInjuries` in `engine-data.ts` to join player_sidelined count per player
- [x] Update `MatchInjury` interface to include `injuryCount: number | null`
- [x] Update injury component to show career injury count badge (`match-detail-live.tsx`)
- [x] Add signal labels (`signal-labels.ts`) + accordion rendering (`signal-accordion.tsx`)

## Task 4 — Transfers (fetch + signal + frontend signal display) ✅

- [x] Add `fetch_transfers()` to `workers/jobs/fetch_enrichment.py` (7-day cache)
- [x] Add `squad_disruption_home` / `squad_disruption_away` signals to `batch_write_morning_signals` (block 14)
- [x] Add signal labels (`signal-labels.ts`) + accordion rendering (`signal-accordion.tsx`)

## Migration ✅

- [x] Migration 068: indexes on `player_sidelined`, `team_transfers`, `match_stats` + ops_snapshots columns

## Docs ✅

- [x] PRIORITY_QUEUE.md: AF-BATCH, AF-HALF-TIME-SIGNALS, AF-SIDELINED, AF-TRANSFERS all ✅ Done
- [x] SIGNALS.md: injury_recurrence, h1_shot_dominance, squad_disruption added
- [x] WORKFLOWS.md: enrichment components updated (sidelined + transfers)
- [x] ROADMAP.md: signal count updated to 30+
