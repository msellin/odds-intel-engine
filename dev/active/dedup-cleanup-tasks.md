# Tasks: Dedup Cleanup

## Phase 0 — State Capture (read-only)
- [ ] Run row-count baseline query, record results in context.md
- [ ] Run duplicate leagues query, record count
- [ ] Run duplicate teams query, record count
- [ ] Run duplicate fixtures query (same team_ids), record count
- [ ] Save raw query output to `dev/active/dedup-state-YYYY-MM-DD.json`

## Phase 1 — Detection Script
- [ ] Create `workers/scripts/detect_duplicates.py`
  - [ ] Connect to DB via psycopg2 (same as pipeline .env)
  - [ ] Detect duplicate leagues: same country + normalized name, different uuid
  - [ ] Detect duplicate teams: same country + normalized name, different uuid
  - [ ] Detect duplicate fixtures: same (home_id, away_id, date) more than once
  - [ ] Detect cross-team-id duplicates: fixtures where one team is a known duplicate of another
  - [ ] Output structured JSON with confidence score per pair
  - [ ] Output human-readable report to stdout
- [ ] Run script, review output
- [ ] HUMAN REVIEW: confirm each suggested merge pair is correct
- [ ] Save detection output as `dev/active/dedup-detected-YYYY-MM-DD.json`

## Phase 2 — teams.api_football_id (prevention)
- [ ] Write `supabase/migrations/047_team_api_football_id.sql`
- [ ] Update `ensure_team()` in `supabase_client.py`:
  - [ ] Accept optional `api_football_id` param
  - [ ] Lookup by `api_football_id` first if provided
  - [ ] Write `api_football_id` on team create/update from AF data
- [ ] Update all `ensure_team()` call sites to pass team api_id from AF fixture data
- [ ] Verification: count teams before/after = same; UNIQUE index confirmed

## Phase 3 — alternate_names[] (prevention)
- [ ] Write `supabase/migrations/048_alternate_names.sql`
  - [ ] Add `alternate_names text[]` to leagues and teams
  - [ ] Add GIN indexes
  - [ ] Seed alternate_names from KAMBI_TO_AF_LEAGUE dict (inline SQL or separate seed)
- [ ] Update `ensure_league()` to check `alternate_names @> ARRAY[input]`
- [ ] Update `ensure_team()` to check `alternate_names @> ARRAY[input]`
- [ ] Verification: spot-check 5 known aliases resolve correctly

## Phase 4a — Merge Duplicate Leagues
- [ ] Generate merge SQL from Phase 1 detection output (script writes it, human confirms)
- [ ] For each orphan league: UPDATE matches, seasons, model_evaluations → canonical
- [ ] For each orphan league: UPDATE canonical alternate_names with orphan name/path
- [ ] For each orphan league: DELETE orphan (will RESTRICT-fail if any FK remains → stop+investigate)
- [ ] Verification: re-run duplicate leagues query → 0 results
- [ ] Verification: match counts per canonical league look correct (sum of orphan + canonical)

## Phase 4b — Merge Duplicate Teams
- [ ] Generate merge SQL from Phase 1 detection output
- [ ] For each orphan team: UPDATE matches (home+away), lineups, players, manager_tenures, team_transfers
- [ ] For each orphan team: DELETE team_elo_daily and team_form_cache (recalculated by pipeline)
- [ ] For each orphan team: UPDATE canonical alternate_names
- [ ] For each orphan team: DELETE orphan
- [ ] Verification: re-run duplicate teams query → 0 results
- [ ] Verification: team counts reduced by exactly the number of orphans merged

## Phase 4c — Merge Duplicate Fixtures
- [ ] Generate merge SQL from Phase 1 detection output (run after 4a + 4b complete)
- [ ] For each orphan fixture:
  - [ ] Check if canonical match already has simulated_bet for same bot+market+selection
  - [ ] Re-point simulated_bets, predictions, odds_snapshots, match_signals, match_events,
        match_stats, match_weather, match_player_stats, match_injuries, live_match_snapshots,
        news_events, lineups, user_bets, match_previews, match_page_views, referee_matches
  - [ ] DELETE orphan match
- [ ] Verification: duplicate fixture query → 0 results
- [ ] Verification: simulated_bets count = Phase 0 count OR Phase 0 count minus confirmed duplicate bets

## Phase 5 — Post-Cleanup Verification
- [ ] Re-run all Phase 0 queries
- [ ] Fill in Phase 5 column in comparison table (context.md)
- [ ] Re-run detection script → must output zero duplicates
- [ ] Spot-check 3 known merged teams on frontend — confirm they show unified data
- [ ] Confirm simulated_bets count: same or reduced by duplicate-only bets (document which)
- [ ] Confirm value bets page shows no duplicates for any current fixture

## Phase 6 — Uniqueness Guards
- [ ] Run: `ALTER TABLE leagues ADD CONSTRAINT uq_leagues_country_name UNIQUE (country, name);`
  - If fails: still have duplicates → go back to Phase 4
- [ ] Run: `CREATE UNIQUE INDEX uq_matches_fixture ON matches (home_team_id, away_team_id, (date::date));`
  - If fails: still have duplicate fixtures → go back to Phase 4c
- [ ] Verification: both constraints/indexes confirmed in pg_indexes/pg_constraint

## Documentation
- [ ] Update PRIORITY_QUEUE.md with all phase completions
- [ ] Update WORKFLOWS.md: note that alternate_names mechanism replaces hardcoded KAMBI dict
- [ ] Update DATA_SOURCES.md: document team api_football_id and alternate_names fields
- [ ] Delete KAMBI_TO_AF_LEAGUE comment block or replace with note pointing to alternate_names
- [ ] Commit all docs + code together
