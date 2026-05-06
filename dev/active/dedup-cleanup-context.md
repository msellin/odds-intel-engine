# Context: Dedup Cleanup

## Why this task exists

The Kambi odds scraper and API-Football use different league names and team name conventions.
When a Kambi league/team name isn't in the KAMBI_TO_AF_LEAGUE mapping, a new DB record is
created instead of reusing the existing AF record. This creates:
- Duplicate league rows
- Duplicate team rows
- Duplicate match/fixture rows (same real game, two DB records)
- Duplicate simulated bets (each duplicate fixture generates its own bets)

Example that triggered this task: "Slavia Sofia vs Septemvri Sofia" appeared twice on the
value bets page — one under "Bulgaria / First League" (AF), one under "Bulgaria / PFL 1" (Kambi).
Kambi also named the team "FK Septemvri Sofia" vs AF's "Septemvri Sofia".

Migrations 025 (leagues) and 027 (teams) already did one-time manual cleanups of prior
duplicates. This task builds proper infrastructure so we don't need emergency patch migrations.

## Prior cleanup done

- `025_league_dedup_priority.sql` — merged ~50+ Kambi league records into AF canonical
- `027_team_dedup.sql` — merged ~200+ Kambi team records into AF canonical
- Both used hardcoded UUIDs gathered from live DB queries at the time
- KAMBI_TO_AF_LEAGUE dict was populated to prevent recurrence for known pairs

## Today's hotfix (2026-05-06)

- Added `("Bulgaria", "PFL 1"): ("Bulgaria", "First League")` to KAMBI_TO_AF_LEAGUE (engine)
- Improved frontend dedup key to normalise team names + use kickoff date (odds-intel-web)
- These are stop-gaps; the underlying Bulgaria duplicate records still exist in the DB

## Kambi vs API-Football — what Kambi uniquely provides

**Nothing exclusive.** All 41 Kambi leagues are also in API-Football. Kambi's only value is:
1. **Better odds pricing** on 1X2/O/U for 41 European leagues (Unibet/Paf often sharper)
2. **Live in-play odds** via `/live.json` (AF doesn't have this)
3. **Free + no rate limit** (AF costs $29/mo, rate-limited to 450 req/min)

Actual odds data is stored in `odds_snapshots` keyed by `match_id`, not `league_id`.
This means **league merges carry zero risk of losing odds data** — the odds snapshot rows
stay intact, just their parent match's league_id changes.

**Simplification for Phase 4:** Any league record with `api_football_id IS NULL` came from
Kambi. It always has an AF counterpart. Detection can apply this rule directly.

## Unmapped LEAGUE_MAP entries (future duplicate risk)

30 of the 41 Kambi LEAGUE_MAP entries have no explicit KAMBI_TO_AF_LEAGUE entry.
Most probably match AF by name, but these are high/medium risk for name divergence:

| Kambi path | Risk | AF name (suspected) |
|---|---|---|
| Greece / Super League | **High** | Super League 1 |
| Portugal / Liga 2 | **High** | Segunda Liga |
| Poland / I Liga | Medium | I liga (case) |
| Serbia / Super Liga | Medium | Super liga (case) |
| Estonia / Esiliiga | Medium | TBD |
| Estonia / Esiliiga B | Medium | TBD |
| Norway / OBOS-ligaen | Low | Same (already in migration 025) |

Phase 1 detection script will definitively identify all mismatches.
Phase 3 (alternate_names) or KAMBI_TO_AF_LEAGUE additions will fix them.

## Key files

| File | Purpose |
|------|---------|
| `workers/api_clients/supabase_client.py` | `ensure_league()`, `ensure_team()`, `store_match()`, `KAMBI_TO_AF_LEAGUE` |
| `supabase/migrations/025_league_dedup_priority.sql` | Prior league cleanup reference |
| `supabase/migrations/027_team_dedup.sql` | Prior team cleanup reference |
| `dev/active/dedup-cleanup-plan.md` | Full plan |
| `dev/active/dedup-cleanup-tasks.md` | Task checklist |

## State Capture (Phase 0)

**NOT YET RUN** — must be populated before any writes.

Baseline queries to run:

```sql
-- Row counts (run before any changes)
SELECT
  (SELECT count(*) FROM leagues) AS leagues,
  (SELECT count(*) FROM teams) AS teams,
  (SELECT count(*) FROM matches) AS matches,
  (SELECT count(*) FROM simulated_bets) AS simulated_bets,
  (SELECT count(*) FROM predictions) AS predictions,
  (SELECT count(*) FROM odds_snapshots) AS odds_snapshots,
  (SELECT count(*) FROM match_signals) AS match_signals;

-- Leagues with no api_football_id (orphan risk)
SELECT country, name, id, created_at,
  (SELECT count(*) FROM matches WHERE league_id = l.id) AS match_count
FROM leagues l
WHERE api_football_id IS NULL
ORDER BY match_count DESC, country, name;

-- Leagues where same country+stripped_name exist more than once
SELECT
  lower(regexp_replace(country || name, '[^a-z0-9]', '', 'gi')) AS normalized_key,
  array_agg(id) AS ids,
  array_agg(name) AS names,
  array_agg(country) AS countries,
  array_agg((SELECT count(*) FROM matches WHERE league_id = l.id)) AS match_counts
FROM leagues l
GROUP BY normalized_key
HAVING count(*) > 1
ORDER BY normalized_key;

-- Teams where same country+normalized_name exist more than once
SELECT
  lower(regexp_replace(country || name, '[^a-z0-9]', '', 'gi')) AS normalized_key,
  array_agg(id) AS ids,
  array_agg(name) AS names,
  array_agg(country) AS countries
FROM teams
GROUP BY normalized_key
HAVING count(*) > 1
ORDER BY normalized_key;

-- Duplicate fixtures: same home+away team on same date (different match_id)
SELECT
  home_team_id, away_team_id, date::date,
  count(*) AS fixture_count,
  array_agg(id) AS match_ids,
  array_agg(league_id) AS league_ids
FROM matches
GROUP BY home_team_id, away_team_id, date::date
HAVING count(*) > 1
ORDER BY date::date DESC;

-- Duplicate fixtures via different team IDs (harder — requires team dedup first)
-- Run this AFTER team dedup is complete to catch cross-ID duplicates
```

**Results (fill in after running):**

| Metric | Value |
|--------|-------|
| leagues | |
| teams | |
| matches | |
| simulated_bets | |
| predictions | |
| odds_snapshots | |
| match_signals | |
| duplicate_leagues | |
| duplicate_teams | |
| duplicate_fixtures (same team_ids) | |

## Decisions made

- **Detection first**: No merge migration will be written until Phase 1 detection script
  output has been reviewed
- **Canonical = the one with api_football_id**: When merging, the AF record wins
- **Canonical = the one with more matches**: Tiebreaker when both lack api_football_id
- **Simulated bets survive**: Re-pointed to canonical match, never deleted
- **Duplicate simulated bets**: If canonical match already has a bet for same bot+market+selection,
  the orphan bet is deleted (it was a duplicate pick, not additional data)
- **team_elo_daily and team_form_cache**: Deleted from orphan (recalculated from pipeline anyway)

## Next steps (on resume)

1. Run Phase 0 state capture queries and fill in baseline table above
2. Build and run Phase 1 detection script
3. Review output before any writes
