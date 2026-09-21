# BET-TIMING-MONITOR — Task Checklist

## Phase 1 — Infrastructure (this session)

- [x] Move OU bots morning → midday (BOT-TIMING-OU-MIDDAY)
- [x] Fix recover_today.py NULL bookmaker (RECOVER-PHASE2)
- [x] Create dev/active docs (plan + context + tasks)
- [x] Migration 101 — `shadow_bets` table (+ ops_snapshots columns)
- [x] `bulk_store_shadow_bets` in supabase_client
- [x] `run_morning(shadow_mode=True)` path in daily_pipeline_v2
- [x] Scheduler jobs at 06:30 / 11:30 / 15:30 UTC
- [x] Settlement extension over shadow_bets
- [x] Ops snapshot: `shadow_runs_today`, `shadow_bets_today`
- [x] Smoke tests (5: SHADOW-BETS-TABLE, SHADOW-MODE-WIRED, SHADOW-NO-BANKROLL, SHADOW-SETTLE-WIRED, SHADOW-SCHEDULER)
- [x] Doc updates: PRIORITY_QUEUE, WORKFLOWS, INFRASTRUCTURE
- [ ] Commit + push

## Phase 2 — Collect data (2026-05-14 → 2026-06-12)

- [ ] No action — Railway scheduler runs shadow jobs daily

## Phase 3 — Analyze (~2026-06-15)

- [ ] Build `scripts/bot_timing_recommendation.py`
  - Per-bot × cohort ROI from shadow_bets
  - Output: "bot X currently in Y; data suggests Z (n=N, ROI Δ +X%)"
- [ ] Sanity-check shadow vs real bets for the cohort each bot actually placed in (should match)
- [ ] Apply 1–2 cohort moves with highest confidence

## Phase 4 — Continuous monitoring

- [ ] Weekly cron: `bot_timing_recommendation.py` → digest email or `/ops` widget
- [ ] Decision rule: only auto-act on n≥50, ROI Δ ≥10%, simple bootstrap CI excludes 0
