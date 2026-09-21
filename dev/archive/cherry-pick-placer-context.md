# Cherry-pick placer — context

## Why this exists

User identified the core asymmetry: "models is getting better every week but performance page and real bets still bleed." Paper firing breadth is correct for training volume and honest performance reporting, but it's wrong for real-money placement. The fix is to decouple the two — keep paper broad, narrow `--record` and (eventually) `--execute` to validated bots only.

User-confirmed direction (2026-06-01 chat):
- Write the plan; defer flipping the gate by one more week (post-Phase-3.5)
- Performance page attractiveness is a related but separate task

## Key files

| File | Why it matters |
|---|---|
| `workers/automation/coolbet_placer.py:187` | `load_qualified_bets()` — singles loader. JOIN bots already present. |
| `workers/automation/coolbet_placer.py:379` | `load_qualified_combo_bets()` — combo loader. |
| `workers/jobs/inplay_bot.py` | `load_qualified_inplay_bets()` — inplay loader (grep to find exact line). |
| `workers/jobs/daily_pipeline_v2.py` | BOTS_CONFIG with descriptions + maturity hints. Pipeline-side bot eligibility is NOT touched by this plan. |
| `supabase/migrations/151_maturity_labels.sql` | Original maturity_label column + initial values. Reference for valid values. |
| `dev/active/self-use-validation-context.md` (existing) | Phase 3.5 paper-only window context. Cherry-pick gate must NOT flip during this window. |

## Decisions made

- **Gate column**: `bots.maturity_label`. Existing semantics fit the use case; no new column.
- **Default behaviour**: unset / `*` → no filter. Ships safely with zero behaviour change.
- **Admin bypass**: `bet_id_filter` already bypasses other filters; maturity gate follows the same pattern.
- **Promotion**: manual, not automatic. Operator reviews `/admin/promotion-candidates` and flips the label.
- **Scope exclusions**: combos default OUT of calibrated set permanently. Inplay decided per-bot in Phase 3.

## Key constraints

- **Phase 3.5 lock until 2026-06-07** — code can land but `COOLBET_RECORD_ALLOWED_MATURITY` env var stays unset on Railway until 2026-06-08. The SELF-USE-VALIDATION verdict needs the current "all bots, broad rule" cohort to read clean.
- **CI smoke gate** — `scripts/smoke_test.py` runs on every push to main. New gate logic needs smoke coverage so CI verifies the env-var pathways even with the gate disabled in prod.
- **Migration discipline** — no DB schema changes are needed for Phase 1. Maturity column already exists; allowed-value enforcement stays in app code.

## Next steps when resuming

1. Read this file + the plan + tasks
2. Find `load_qualified_inplay_bets` (grep `inplay_bot.py`)
3. Start Phase 1, Task 1 (env var parse helper)
4. Don't skip the admin-bypass test — easy to forget

## Glossary

- **maturity_label**: `testing` (new, no data) → `beta` (some data, unsettled) → `active` (established) → `calibrated` (validated real-money candidate) → `experimental` (combo/acca, excluded from headline ROI math).
- **--record mode**: coolbet placer writes `real_bets` rows at Coolbet odds but does NOT stake real money. Phase 3.5 default.
- **--execute mode**: real money. Phase 4 only, not before 2026-06-07 verdict.

## Reference data snapshot (2026-06-01)

Bots and their current maturity:
- `calibrated`: bot_v10_all, bot_aggressive, bot_dc_specialist (retired), bot_ou25_global (retired)
- `active`: bot_aggressive_v2, bot_lower_1x2 (retired today), bot_ou_specialist, bot_opt_ou_british
- `beta`: bot_high_alignment, bot_high_roi_global_v2, bot_proven_leagues_v2
- `testing`: bot_ah_home_fav, bot_ah_away_dog

After today's retirements (bot_dc_specialist, bot_lower_1x2), the *currently-active calibrated* set is just `bot_v10_all`. That's too narrow for a gate-flip — Phase 3 will need to promote 2-3 more bots first, hence the `/admin/promotion-candidates` page in Phase 2.
