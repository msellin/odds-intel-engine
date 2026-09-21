# PERF-HONEST-HEADLINE — Context

## State

- Plan accepted; user picked Option C (show all-time + active-only headlines, retired strategies in collapsed section with reasons)
- Inplay stake retroactive normalization (€1 → €5) bundled in as commit 2
- Started 2026-05-17

## Key files

| File | Role |
|------|------|
| `supabase/migrations/103_retire_dead_1x2_bots.sql` | Pattern reference for retirement migration |
| `supabase/migrations/035_dashboard_cache.sql` | dashboard_cache table definition |
| `supabase/migrations/001_initial_schema.sql:333` | `bots.starting_bankroll` default = 10000 |
| `workers/jobs/settlement.py:1055-1163` | `write_dashboard_cache` — needs active-only + retired_breakdown queries added |
| `workers/jobs/daily_pipeline_v2.py:103-116` | `bot_aggressive` BOTS_CONFIG entry — needs `[RETIRED 2026-05-17]` prefix |
| `workers/jobs/inplay_bot.py:352` | `"stake": 1.0` → `"stake": 5.0` |
| `odds-intel-web/src/lib/engine-data.ts` | Frontend Supabase reads — needs `retiredBots[]` + `active_*` fields |
| `odds-intel-web/src/app/(app)/performance/page.tsx` | Headline render — needs two rows |
| `odds-intel-web/src/lib/bot-aggregates.ts` | Already filters retired bots from public stats post-BOTS-RETIRE-1X2 |

## Inplay bots in DB

Named `inplay_a` through `inplay_q` (no `bot_` prefix; see `workers/jobs/inplay_bot.py:41 INPLAY_BOTS`).

SQL filter: `name LIKE 'inplay\_%'` (escape underscore to be safe).

## bot_aggressive retire reason text

"Replaced by `bot_aggressive_v2`. -5.7% ROI / -€141 on 441 settled bets. Loss buckets: draws (61 bets / -€154), home odds 3.30+ high-edge (110 bets / -€95), OU under 2.5 (88 bets / -€46). v2 keeps 129/441 with no draws, no under 2.5, odds 1.50-3.30, edge ≥5% — replay shows +11.6% ROI / +€90."

## Backfill reasons for already-retired bots (from BOTS-RETIRE-1X2 / migration 103)

| Bot | Reason |
|-----|--------|
| `bot_lower_1x2` | "T2-4 1X2-only. Live ROI +83% on 11 bets was variance. Starved by May 17 retrain — `shrinkage_alpha_t2_1x2 = 0.00` means model has no edge over Pinnacle for T1-T2 1X2. Re-enable if alpha recovers > 0.15." |
| `bot_opt_home_lower` | "Optimizer-found T2-4 home longshots. Live +73% on 15 bets = variance. Starved by May 17 retrain (`alpha_t2_1x2 = 0.00`). Re-enable on alpha recovery or 30+ bets at ≥3% ROI in shadow_bets." |
| `bot_draw_specialist` | "T2-4 draws only. Same loss profile as bot_aggressive's draw bucket: -€154 / 61 bets. Draws are the worst 1X2 selection across the portfolio." |
| `bot_conservative` | "T1-4 1X2 at ≥10% edge. Never fired in production since launch — criteria too tight for live odds distribution." |

## Sequencing

1. Migration 104 (schema + retire + backfill) — push first, GitHub Actions applies
2. Engine code (settlement + BOTS_CONFIG) — push with migration 104 in same commit
3. Inplay stake change + normalization script — separate commit, run script manually after deploy
4. Frontend changes — separate commit on odds-intel-web

## Next steps

1. Write migration 104
2. Update `settlement.write_dashboard_cache`
3. Update BOTS_CONFIG entry for bot_aggressive
4. Write smoke tests
5. Commit + push (commit 1 done)
6. Then move to commit 2 (inplay normalize) — needs user confirmation before running destructive script on prod
7. Then commit 3 (frontend)
