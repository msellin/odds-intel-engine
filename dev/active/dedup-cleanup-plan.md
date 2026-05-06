# Plan: Zero Duplicate Leagues & Teams

**Goal:** End state has no duplicate league or team records. All match/bet/prediction data
is consolidated onto canonical records. Future duplicates cannot form silently.

**Constraint:** No data loss. Every match, bet, prediction, and signal must survive the merge.

---

## Approach: Data-driven, iterative, verified

Every phase:
1. Captures current state with SQL queries before touching anything
2. Makes a single scoped change (additive migration, code change, or merge migration)
3. Re-runs verification queries to confirm expected outcome
4. Only continues if the before/after diff is clean

If any verification step shows unexpected data (wrong counts, missing rows), we stop and
investigate before proceeding.

---

## Phase 0 — State Capture (no writes)

Run diagnostic SQL against the live DB. Save raw output. This is the baseline we compare
against at the end to prove zero data loss.

**Queries to run (see dedup-cleanup-tasks.md for exact SQL):**
- Total leagues, teams, matches, predictions, simulated_bets, odds_snapshots
- Leagues with no api_football_id (duplicate risk zone)
- Teams where normalized name collision exists (same country, same stripped name)
- Matches where (home_team_id, away_team_id, date::date) appears more than once
- Duplicate simulated_bets (same match date + same home/away + same market/selection)

**Output saved to:** `dev/active/dedup-cleanup-context.md` (State Capture section)

---

## Phase 1 — Detection Script

Build `workers/scripts/detect_duplicates.py`. Connects to DB via psycopg2 (same as pipeline),
outputs a structured report:

```
=== DUPLICATE LEAGUES ===
Country: Bulgaria
  DUPLICATE: "PFL 1" (id=abc, 3 matches) → CANONICAL: "First League" (id=xyz, 47 matches)
  Action: merge abc → xyz

=== DUPLICATE TEAMS ===
Country: Bulgaria
  DUPLICATE: "FK Septemvri Sofia" (id=abc, 3 matches) → CANONICAL: "Septemvri Sofia" (id=xyz, 12 matches)
  Action: merge abc → xyz

=== DUPLICATE FIXTURES ===
  Match: Slavia Sofia vs FK Septemvri Sofia, 2026-05-06 (id=abc, 2 bets) — DUPLICATE of id=xyz
  Action: re-point bets from abc → xyz, delete abc
```

Detection logic:
- **Leagues**: same country + same stripped/lowercased name (strip punctuation, spaces) → flag as potential duplicates; also flag any league with `api_football_id IS NULL` that has matches
- **Teams**: same country + same normalized name (existing `_normalize_team_name()` logic) with different UUIDs
- **Fixtures**: `(home_team_id, away_team_id, date::date)` appearing more than once — OR same match via different team IDs (detected via team-dedup candidates above)

Output is a Python dict serialized to JSON → saved as `dev/active/dedup-state-YYYY-MM-DD.json`

This script is READ-ONLY. It never writes anything.

---

## Phase 2 — Add `api_football_id` to Teams (prevention)

**Migration `047_team_api_football_id.sql`:**
```sql
ALTER TABLE teams ADD COLUMN IF NOT EXISTS api_football_id integer;
CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_api_football_id
  ON teams (api_football_id) WHERE api_football_id IS NOT NULL;
```

**Engine change in `ensure_team()`:**
- When called with a team that has an AF fixture's team ID: look up by `api_football_id` first
- On create: populate `api_football_id` from AF fixture data if available
- Kambi data (no ID): falls through to existing fuzzy matching

**Verification after:**
- Migration applies cleanly (no constraint violations)
- All existing team rows still present (count matches Phase 0 baseline)
- UNIQUE index created (verify via pg_indexes)

---

## Phase 3 — Add `alternate_names[]` to Leagues + Teams (prevention)

**Migration `048_alternate_names.sql`:**
```sql
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS alternate_names text[] DEFAULT '{}';
ALTER TABLE teams   ADD COLUMN IF NOT EXISTS alternate_names text[] DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_leagues_alternate_names ON leagues USING gin(alternate_names);
CREATE INDEX IF NOT EXISTS idx_teams_alternate_names   ON teams   USING gin(alternate_names);
```

**Seed script:** Convert `KAMBI_TO_AF_LEAGUE` dict entries into `alternate_names` updates on
the canonical league rows. Run as a one-time migration or inline in 048.

**Engine change in `ensure_league()`:**
- After KAMBI_TO_AF_LEAGUE lookup (keep as fast-path), also check:
  `WHERE alternate_names @> ARRAY['Bulgaria / PFL 1']`
- On manual duplicate resolution: add the alias to the canonical record instead of writing
  another hardcoded migration

**Engine change in `ensure_team()`:**
- After fuzzy match step, also check `alternate_names @> ARRAY[input_name]`

**Verification after:**
- alternate_names populated on canonical league rows (spot-check 5 known aliases)
- ensure_league() test: call with a known Kambi alias, confirm returns canonical ID
- League and team counts unchanged from Phase 0

---

## Phase 4 — Cleanup Existing Duplicates

This phase is generated FROM the Phase 1 detection script output — not written by hand.

**Step 4a — Merge duplicate leagues:**
For each duplicate pair identified in Phase 1:
```sql
-- Move all matches from orphan league to canonical
UPDATE matches SET league_id = '<canonical_id>' WHERE league_id = '<orphan_id>';
-- Move seasons
UPDATE seasons SET league_id = '<canonical_id>' WHERE league_id = '<orphan_id>';
-- Move model_evaluations
UPDATE model_evaluations SET league_id = '<canonical_id>' WHERE league_id = '<orphan_id>';
-- Add alias to canonical's alternate_names
UPDATE leagues SET alternate_names = array_append(alternate_names, '<kambi_path>')
  WHERE id = '<canonical_id>';
-- Delete orphan (will fail with RESTRICT if any FK still points here → safe)
DELETE FROM leagues WHERE id = '<orphan_id>';
```

Each pair runs in its own transaction — if DELETE fails due to remaining FKs, we stop
and investigate rather than forcing.

**Step 4b — Merge duplicate teams:**
For each duplicate pair identified in Phase 1:
```sql
UPDATE matches SET home_team_id = '<canonical_id>' WHERE home_team_id = '<orphan_id>';
UPDATE matches SET away_team_id = '<canonical_id>' WHERE away_team_id = '<orphan_id>';
UPDATE lineups SET team_id = '<canonical_id>' WHERE team_id = '<orphan_id>';
UPDATE players SET team_id = '<canonical_id>' WHERE team_id = '<orphan_id>';
UPDATE manager_tenures SET team_id = '<canonical_id>' WHERE team_id = '<orphan_id>';
UPDATE team_transfers SET team_id = '<canonical_id>' WHERE team_id = '<orphan_id>';
DELETE FROM team_elo_daily WHERE team_id = '<orphan_id>';
DELETE FROM team_form_cache WHERE team_id = '<orphan_id>';
-- Add alias to canonical's alternate_names
UPDATE teams SET alternate_names = array_append(alternate_names, '<kambi_name>')
  WHERE id = '<canonical_id>';
DELETE FROM teams WHERE id = '<orphan_id>';
```

**Step 4c — Merge duplicate fixtures:**
When the same real-world match appears as two DB rows (different team IDs, different league IDs):
```sql
-- Re-point all child data from orphan match to canonical match
UPDATE simulated_bets  SET match_id = '<canonical_match_id>' WHERE match_id = '<orphan_match_id>';
UPDATE predictions     SET match_id = '<canonical_match_id>' WHERE match_id = '<orphan_match_id>';
UPDATE odds_snapshots  SET match_id = '<canonical_match_id>' WHERE match_id = '<orphan_match_id>';
UPDATE match_signals   SET match_id = '<canonical_match_id>' WHERE match_id = '<orphan_match_id>';
UPDATE match_events    SET match_id = '<canonical_match_id>' WHERE match_id = '<orphan_match_id>';
-- (all other match_* tables)
-- Then delete orphan (RESTRICT on teams FK is now cleared since team was already merged)
DELETE FROM matches WHERE id = '<orphan_match_id>';
```

**Important:** 4a must complete before 4c (leagues merged first), 4b must complete before 4c
(teams merged first). Then 4c cleans up the now-orphaned fixture rows.

---

## Phase 5 — Post-cleanup Verification

Re-run the same Phase 0 diagnostic queries. Compare:

| Metric | Phase 0 | Phase 5 | Expected |
|--------|---------|---------|----------|
| Total matches | N | N - D | N minus duplicate fixtures (D) |
| Total simulated_bets | M | M | Same (re-pointed, not deleted) |
| Total predictions | P | P | Same |
| Total odds_snapshots | O | O | Same |
| Duplicate leagues | X | 0 | Zero |
| Duplicate teams | Y | 0 | Zero |
| Duplicate fixtures | Z | 0 | Zero |

Re-run the detection script. Output must be empty.

---

## Phase 6 — Uniqueness Guards (final hardening)

After data is clean:

**Add unique constraint on leagues `(country, name)`:**
```sql
ALTER TABLE leagues ADD CONSTRAINT uq_leagues_country_name UNIQUE (country, name);
```
This will succeed only if Phase 4 was complete. If it fails, there are still duplicates —
investigate before retrying.

**Do NOT add unique constraint on teams `(name, country)`** — teams legitimately share names
across different countries and sometimes within (reserve teams, women's teams). Instead,
rely on the api_football_id UNIQUE index from Phase 2.

**Add unique constraint on matches `(home_team_id, away_team_id, date::date)`:**
```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_matches_fixture
  ON matches (home_team_id, away_team_id, (date::date));
```
This prevents future duplicate fixtures even if team merge fails. Will succeed only if
Phase 4c was complete.

---

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Wrong canonical UUID in merge | Generated from detection script, not hand-written |
| Child FK blocks DELETE | Each DELETE wrapped in its own transaction; failure stops execution, doesn't corrupt |
| Simulated bets re-pointed to match that already has a bet for same bot+market+selection | Phase 4c query checks for conflict before UPDATE; skip rather than creating UNIQUE violation |
| Detection script misidentifies two real different teams as duplicates | Script outputs confidence score; only AUTO-MERGE high confidence; medium confidence requires human review |
| Migration applied twice | All UPDATE/DELETE are idempotent (UPDATE WHERE x=old_id has no effect if already changed) |

---

## Execution Order

```
Phase 0  → State capture (read-only, safe to run any time)
Phase 1  → Build + run detection script (read-only)
         → HUMAN REVIEW of detection output before proceeding
Phase 2  → Migration 047 (additive, safe)
Phase 3  → Migration 048 + seed (additive, safe)
Phase 4a → League merges (generated from Phase 1 output)
Phase 4b → Team merges (generated from Phase 1 output)  
Phase 4c → Fixture merges (generated from Phase 1 output, runs after 4a+4b)
Phase 5  → Verification (read-only)
Phase 6  → Uniqueness constraints (only if Phase 5 shows zero duplicates)
```

Total estimated time across multiple sessions: 4–6 hours of implementation + review.
