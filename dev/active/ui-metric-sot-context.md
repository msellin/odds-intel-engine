---
name: UI metric single-source-of-truth — context
description: Live notes for the cross-page CLV/ROI/settled metric drift + chart bugs (6 Jun 2026)
type: project
---

# Context — UI metric single-source-of-truth + chart fixes

User reported four distinct problems in one session:

1. **Cross-page metric drift.** Landing shows "+10.1% CLV · 1,015 paper bets · 36 days", `/value-bets` banner shows "CLV +10.1% / ROI +13.3% / 1,014 settled", `/performance` flickers from "+8.1%" to "+10.0%" Avg CLV with 1,020 → 1,022 settled bets.
2. **Hero flicker on `/performance`.** Server SSR shows cache values; ~1s later Suspense resolves and the client `PerformanceClient` recomputes hero metrics from `getAllBets()`, swapping in different numbers.
3. **Bot modal bankroll chart looks broken** after recent void cleanups — dramatic drop at last point on `inplay_o`, dramatic dip at index 1 on `inplay_p_v2`.
4. **Outer P&L graphs inconsistent.** Hero "Last 31d" sparkline endpoint ≠ extras "Last 90d" cumulative chart endpoint.

User principle: **single source of truth — no per-graph re-querying.**

## Root causes mapped

### #1 + #2 — flicker / drift
- `getTrackRecordStats()` in `engine-data.ts:3036` returns `avgClv: cache.avg_clv`. Settlement writes `cache.avg_clv` from ALL bots (incl. retired), excluding experimental (`settlement.py:1538-1544`).
- `PerformanceClient` overrides `heroStats.avgClv` with `computedActivePerf.avgClv` = active-only (excl. retired, excl. experimental). Different cohort → different number.
- Cache already has `cache.active_avg_clv` (settlement.py:1571, active-only). Just unused.
- Same pattern for `roi_pct` / `active_roi_pct` / `active_settled_bets`.

### #3 — bot modal chart
- `buildChartData` in `performance-leaderboard.tsx:107` filters voids out at line 156-158.
- But for remaining bets, uses `bankrollAfter` (a snapshot of bankroll at settlement time) when available (line 119-125).
- When a bet was settled, then later voided, subsequent bets' `bankrollAfter` reflects the void's original pnl. Filtering the void out without recomputing leaves stale snapshots — chart shows drops/jumps that aren't real.
- Fix: always recompute `running += b.pnl` from the filtered list. Drop the `bankrollAfter` fast-path.

### #4 — P&L graph SoT
- Hero sparkline: `cache.daily_pnl_curve_30d` — active + non-experimental + non-retired (settlement.py:1615-1626).
- Extras 90d chart: `getPublicPerformanceExtras` (engine-data.ts:4846) — non-experimental but INCLUDES retired bots. Plus 90d window.
- Endpoints diverge because cohorts diverge.
- Fix: have settlement.py write `daily_pnl_curve_90d` with same cohort as 30d. Frontend reads cache for both — extras 90d chart no longer runs its own query for the cumulative series.

## Decision log

- **Flicker fix:** read `cache.active_avg_clv` in `getTrackRecordStats`, drop client overrides in `PerformanceClient`. Cache is canonical.
- **Chart fix:** always recompute running from `pnl` in `buildChartData`. Drop `bankrollAfter` path.
- **P&L SoT:** settlement.py writes 90d curve (with same active+non-experimental cohort as 30d). Extras chart reads it from cache. Drop the duplicate query in `_getPublicPerformanceExtrasUncached` for the `cumulative` field.
- **Cross-page label drift (1015 vs 1014 vs 1022):** intentional (different cohorts/windows). Will add hero "lifetime" annotation but won't try to unify the numbers — they're honestly different aggregates.

## Files touched

- `src/lib/engine-data.ts` — `getTrackRecordStats` reads `cache.active_avg_clv`; `getPublicPerformanceExtras` reads `cumulative` from cache.
- `src/components/performance-client.tsx` — drop hero stat overrides.
- `src/components/performance-leaderboard.tsx` — `buildChartData` always uses running pnl.
- `workers/jobs/settlement.py` — write `daily_pnl_curve_90d`. Align "settled" predicate (already `IN ('won','lost')` everywhere; keep it).
- `supabase/migrations/162_dashboard_cache_curve90.sql` — add column.
- `scripts/smoke_test.py` — UI-METRIC-SOT smoke pinning invariants.
- Docs: `PRIORITY_QUEUE.md`, `WORKFLOWS.md` if cache schema changes, this context file.
