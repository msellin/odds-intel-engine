# WC Bracket — Stage-Gated Tasks

## Engine

- [x] `supabase/migrations/171_wc_bracket_stage_gated.sql` — new table + matches.round_label column
- [x] `workers/jobs/wc_bracket_slot_sync.py` — seed slot assignments from AF round labels
- [x] `workers/jobs/wc_bracket_scoring.py` — rewrite positional scoring; keep set-membership fallback for old rows (none today)
- [x] `scripts/generate_ai_brackets.py` — add `--round <r>` per-round mode
- [x] `workers/scheduler.py` — add `job_wc_bracket_slot_sync` (30 min, WC window); fire `generate_ai_brackets --round` from inside slot-sync after a new round seeds

## Frontend (odds-intel-web)

- [x] `src/lib/wc-bracket-types.ts` — new types: `BracketSlotAssignment`, `BracketRoundState`, `BracketRoundLockState`
- [x] `src/lib/wc-bracket.ts` — new loaders: `loadBracketState()` returns slot assignments + per-round lock states + user picks by round
- [x] `src/app/(app)/world-cup/actions.ts` — `saveBracketPick` re-checks per-round `locked_at` from `wc_bracket_slot_assignments`
- [x] `src/app/(app)/world-cup/bracket/page.tsx` — phased view
- [x] `src/components/wc-bracket-board.tsx` — per-round state rendering
- [x] `src/components/wc-activity-tiles.tsx` — add "Bracket round" tile
- [x] Fix the "Max possible: 83 pts" string → 122

## Smoke tests

- [x] `WC-BRACKET-STAGE-GATED-MIGRATION`
- [x] `WC-BRACKET-SLOT-SYNC`
- [x] `WC-BRACKET-POSITIONAL-SCORING`
- [x] `WC-BRACKET-PHASED-UI`
- [x] `WC-BRACKET-AI-PER-ROUND`
