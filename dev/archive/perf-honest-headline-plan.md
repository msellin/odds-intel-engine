# PERF-HONEST-HEADLINE — Plan

## Problem

`/performance` headline ROI is ~1.8% — misleading. Root causes, in order of impact:

1. **`bot_aggressive` v1 still active.** 465 settled bets, ~-€141 P&L, -5.7% ROI. Retired in spirit (v2 shipped today) but still placing bets and still in the cache aggregate. Drags portfolio ROI by ~3-5 pp.
2. **Inplay bets staked at €1, pre-match at €5-10.** Inplay strategies are the highest-ROI bots in the portfolio but contribute almost nothing to headline ROI because `total_pnl / total_staked` weights by stake. Even when inplay prints, pre-match losses (much bigger stakes) dominate.
3. **Retired bots' historical P&L still in headline cache.** `settlement.write_dashboard_cache` queries `simulated_bets` directly for the aggregate stats (no `bots` join), so retirement only hides bots from the per-bot leaderboard — not from the headline. (Per-bot list does already filter retired.)

## Goal

Make `/performance` show an honest picture:

- Total bets placed = 1000+ (credibility, includes retired bots' bets)
- Two headline rows: **all-time** (incl. retired) + **active strategies only**
- Retired bots visible in a collapsed section with reason for retirement
- Inplay stakes normalized to €5 (going forward + historical) so per-bet weight is comparable across all strategies

## Out of scope

- Switching headline to stake-normalized units (proposed earlier in thread; user chose to fix stakes + show both rows instead — simpler and more honest)
- Time-window toggle on headline (defer; can add later)
- Frontend bot bankroll chart changes (no schema change to per-bet records' bankroll model)

## Shipping plan — three commits

### Commit 1 — Migration 104 + engine cache changes

**Migration `104_perf_honest_headline.sql`**:

- `ALTER TABLE bots ADD COLUMN retired_reason TEXT;`
- Backfill `retired_reason` for the 4 bots already retired (BOTS-RETIRE-1X2)
- `UPDATE bots ... SET is_active=false, retired_at=now(), retired_reason='...'` for `bot_aggressive` (with full v1→v2 reason text)
- `ALTER TABLE dashboard_cache ADD COLUMN active_total_staked FLOAT, active_total_pnl FLOAT, active_roi_pct FLOAT, active_settled_bets INTEGER, active_won INTEGER, active_lost INTEGER, retired_bot_breakdown JSONB;`

**Engine (`workers/jobs/settlement.py:write_dashboard_cache`)**:

- Add second pair of queries that JOIN `bots` and filter `is_active AND retired_at IS NULL` — produces `active_*` headline numbers.
- Add `retired_bot_breakdown` query mirroring `bot_breakdown` but for retired bots; SELECTs include `retired_at`, `retired_reason`.
- INSERT statement extended with the new columns.

**Engine (`workers/jobs/daily_pipeline_v2.py`)**:

- Mark `bot_aggressive` `BOTS_CONFIG` entry with `[RETIRED 2026-05-17]` description prefix (mirror BOTS-RETIRE-1X2 pattern).

**Smoke tests**:

- `PERF-HONEST-HEADLINE-ACTIVE-FIELDS` — `dashboard_cache` has both all-time and active-only fields populated after settlement run
- `PERF-RETIRED-REASON-REQUIRED` — every retired bot in DB has non-null `retired_reason`
- `BOT-AGGRESSIVE-RETIRE` — bot is `is_active=false, retired_at NOT NULL`, reason mentions v2

### Commit 2 — Inplay stake normalization (€1 → €5)

**Code (`workers/jobs/inplay_bot.py:352`)**:

- `"stake": 1.0` → `"stake": 5.0`

**Script (`scripts/normalize_inplay_stake_to_5.py`)**:

- `--dry-run` (default) prints affected bet counts + bankroll deltas without mutating
- `--apply` runs:
  1. Snapshot affected rows to `simulated_bets_pre_inplay_normalize_2026_05_17` (audit trail)
  2. `UPDATE simulated_bets SET stake = stake * 5, pnl = pnl * 5 WHERE bot_id IN (SELECT id FROM bots WHERE name LIKE 'inplay\_%') AND result IN ('won','lost')` (void rows skipped — pnl=0)
  3. Window-function recompute of `bankroll_after` per bot in `pick_time` order
  4. `UPDATE bots SET current_bankroll = (latest bankroll_after per bot)` for inplay bots
  5. Triggers `settlement.write_dashboard_cache` at the end
- Idempotency guard: aborts if any inplay bet has `stake = 5.0` already (would mean script already ran)

**Smoke tests**:

- `INPLAY-STAKE-5-NEW` — `inplay_bot.py:352` has `"stake": 5.0`
- `INPLAY-STAKE-5-NORMALIZED` — no settled inplay bet remains at `stake = 1.0` after run

### Commit 3 — Frontend (`odds-intel-web`)

- `engine-data.ts` extended: expose `activeRoi`, `activeTotalBets`, `activeTotalPnl`, `retiredBots[]`
- `/performance` headline: two rows
  - `All-time (incl. retired): X bets · +Y% ROI · +€Z`
  - `Active strategies only: X bets · +Y% ROI · +€Z`
- New collapsed "Retired Strategies (N) — kept for transparency" section below active leaderboard. Each row: name, final settled / won / ROI / P&L, retire date, reason text. Default collapsed.
- Server-side gating preserved (aggregate stats are not tier-sensitive)

## Risks

- **Retroactive `UPDATE simulated_bets`**: destructive. Mitigation: dry-run mode, snapshot table, transactional, idempotency guard.
- **`dashboard_cache` schema change** during a partial settlement run: low risk, settlement is a one-shot job, schema migration runs first via GitHub Actions.
- **Retired-bots section UX**: too prominent might look like cherry-picking; too hidden hides accountability. Plan: default-collapsed with N-count visible, expand on click.

## Verification after deploy

- Visit `/performance` — both headline rows visible, retired section shows 5 bots (bot_aggressive + 4 already-retired) each with reason
- Total bet count stays ≥ 1000 in the all-time row
- Active-only ROI clearly higher than all-time ROI
