# WC Bracket — Stage-Gated Rewrite (Plan)

## Why

Current bracket implementation is single-lock (whole bracket at WC kickoff 2026-06-11) + set-membership scoring ("did the user pick a team that advanced to this round, regardless of slot"). User feedback: this isn't what "bracket challenge" means — BBC / ESPN / FIFA Predictor open each knockout round STAGE-BY-STAGE as prior rounds resolve, and scoring is POSITIONAL (winner of THIS specific matchup).

## Scope

ONLY the `/world-cup/bracket` page. The pre-tournament Group Standings Predictor at `/world-cup/groups-predictor` stays untouched — it's the engagement layer before group stage settles. Golden Boot remains a single text input that locks at WC kickoff.

## Schedule

| Round | Opens when | Locks at | Picks | Points each |
|---|---|---|---|---|
| Round of 32 | After group stage settles (~Jun 27) | First R32 kickoff (~Jun 28) | 16 | 1 |
| Round of 16 | After R32 settles (~Jul 3) | First R16 kickoff (~Jul 4) | 8 | 2 |
| QF | After R16 settles (~Jul 7) | First QF kickoff (~Jul 9) | 4 | 4 |
| SF | After QF settles (~Jul 11) | First SF kickoff (~Jul 14) | 2 | 8 |
| Final | After SF settles (~Jul 15) | Final kickoff (~Jul 19) | 1 | 16 |
| Champion | Implicit in Final | Final kickoff | — | 32 |
| Golden Boot | (unchanged) | Jun 11 19:00 UTC | 1 | 10 |

Max = 16+16+16+16+16+32+10 = 122 (unchanged).

## Engine work

1. **Migration `171_wc_bracket_stage_gated.sql`** — new `wc_bracket_slot_assignments` table mapping each (round, position) → real `matches.id` + `locked_at`. Idempotent + additive (no destructive ops on existing tables). Existing `wc_bracket_picks` rows stay valid.

2. **`workers/jobs/wc_bracket_slot_sync.py`** — new job. Reads AF round labels from `matches` (added in the same migration as `matches.round_label`) and maps them to bracket slot positions. Updates `wc_bracket_slot_assignments`. Idempotent; never overwrites past `locked_at`.

3. **Scoring rewrite — `wc_bracket_scoring.py`**:
   - Replace `build_advancers()` set-membership lookup with POSITIONAL scoring: for each pick (round, position), look up slot's `match_id`, read its `result`, award points only if the picked team is the actual winner of THAT specific match.
   - Champion = derived from `(round=final, position=0)` pick.
   - Pre-seed rounds (slot's `match_id` IS NULL) → 0 for everyone, no error.
   - Keep group-standings scoring as-is.

4. **AI ghost rewrite — `generate_ai_brackets.py`**:
   - Add `--round` flag. When set, generate picks for ONLY that round.
   - Picks now positional: pull the round's slot assignments, look up each slot's home/away team strength, pick the higher one.
   - Idempotent before round's `locked_at`; refuses after.
   - Falls back to old "full bracket" mode when no `--round` passed (pre-group-stage initial generation still useful for group-standings predictions).

5. **Scheduler hook** — after `wc_bracket_slot_sync` seeds a new round, call `generate_ai_brackets --round <new_round>` to keep AI ghosts up-to-date.

## Frontend work

6. **`/world-cup/bracket/page.tsx` rewrite** — phased view. Show ALL rounds, but each round's interactivity depends on its state:
   - **Not yet seeded** (R16 before R32 finishes): grey, "Opens after {prior round}"
   - **Open** (seeded, not locked): user picks winners per matchup
   - **Locked** (after `locked_at`): read-only, shows picks + ✓/✗ as matches settle
   - **Settled** (all matches finished): read-only with score tally

7. **`wc-bracket-board.tsx` rewrite** — accept `slotAssignments` + `userPicksByRound` + `roundLockStates`. Server action `saveBracketPick(round, position, teamId)` keeps same signature, new semantics (re-check `locked_at` per round before write).

8. **Activity tiles** — add "Bracket round: {label}" (e.g. "Round of 32 — opens in 3 days").

## UI layout decision

- **Vertical columns per round** on mobile (1 column visible, swipe between rounds — same paginated approach as current mobile UI; reuses muscle memory).
- **Horizontal bracket tree** on desktop (`md:` and up) — 5 columns side-by-side (R32 → Final). Champion appears as a highlighted card at the right of the Final column.
- Each slot card shows: matchup teams (top/bottom), user's pick highlighted, actual winner badge if settled, ✓/✗ marker.

## Hard constraints

- **DO NOT commit or push.** Working tree only.
- Group standings predictor (`/world-cup/groups-predictor`) untouched.
- `wc_bracket_picks` data preserved; only scoring semantics change.
- Migration 171 idempotent + reversible.
- Mobile-first 375px.
- AI picks deterministic.
- Lock enforcement server-side only.

## Phases

1. Migration + slot-sync job + scheduler hook (engine).
2. Scoring rewrite (engine).
3. AI ghost per-round mode (engine).
4. FE rewrite — bracket page + bracket-board component.
5. Activity tiles tweak + scoring legend fix (FE).
6. Smoke tests.

## Risks

- AF round labels are free text — must match the canonical set `"Round of 32 - N"`, `"Round of 16 - N"`, `"Quarter-finals - N"`, `"Semi-finals - N"`, `"Final"`. Slot-sync parses via regex; logs unknown rounds.
- Group-stage 3rd-place advancers create 8 of the 16 R32 matchups — FIFA assigns these by table that's only finalized when ALL group fixtures finish. The slot-sync job has nothing to map until AF publishes the actual R32 fixtures (with the correct teams). Until then, the R32 column renders as "seeding in progress".
- If AF lags publishing R32 fixtures after group stage, R32 will look empty for hours. Activity tile copy handles this gracefully ("Opens when AF publishes Round of 32 fixtures").
