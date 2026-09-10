# SELF-USE-VALIDATION — Task checklist

> Read `self-use-validation-plan.md` first for context.
> Update statuses as you work. ⬜ not started · 🔄 in progress · ✅ done

## Phase 0 — Free sanity check (1 evening)

- ✅ **0.1** Sampling script `scripts/sample_coolbet_proxy_check.py` shipped — pulls all pending paper bets on not-yet-started matches with Unibet + Bet365 + Pinnacle joined at pick time. Generates CSV at `dev/active/self-use-validation-phase0-worksheet.csv`. Reframed forward-looking (Coolbet doesn't publish historical odds — once a match kicks off, the price is gone). Re-runnable; each session adds samples. **First run produced 26 rows incl. Barcelona vs Real Madrid, PSG, Olympiakos vs PAOK.**
- ✅ **0.2** Done by 0.1 — script fetches Unibet + Bet365 directly.
- ⏭ **0.3 — SUPERSEDED by Phase 3 real-bet logging.** The `/admin/place` modal captures captured_odds + actual_odds on every real bet, and `real_bets.slippage_pct` IS the Unibet-vs-Coolbet gap measurement. Selection bias caveat: only bets you choose to place get sampled. Acceptable for hobbyist validation. CSV worksheet at `dev/active/self-use-validation-phase0-worksheet.csv` is preserved for any future unbiased sampling but not required.
- ⏭ **0.4 — SUPERSEDED.** Same data lives on `/admin/real-bets` (mean slippage stat card + per-book breakdown). After ~50 real bets logged, that's the validation signal.
- ⏭ **0.5 — DEFERRED to Week 1 of Phase 3.** Decision will fall out of `/admin/real-bets` slippage trend after first 50 bets. Unibet proxy works if mean |slippage| < 3% and slippage doesn't show systematic bias by market type.

## Phase 1 — Coolbet odds via The Odds API *(only if Phase 0 says proxy is bad)*

- ⬜ **1.1** Sign up for The Odds API (free tier). Add `THE_ODDS_API_KEY` to Railway + local `.env`.
- ⬜ **1.2** Add `workers/api_clients/the_odds_api.py` — thin client mirroring the `_get` retry pattern in `api_football.py`.
- ⬜ **1.3** Build `workers/jobs/fetch_coolbet_odds.py` — once-daily, top 200 matches by edge, write to `odds_snapshots` with `bookmaker='Coolbet'`. Reuse `_kickoff_minute` helper for date matching.
- ⬜ **1.4** Wire into `workers/scheduler.py` — `CronTrigger(hour=9, minute=0)` daily.
- ⬜ **1.5** Smoke tests: client signature, daily writer source guard, sanity check on row count vs expected.
- ⬜ **1.6** Verify after first run: rows landed, schema matches, ops dashboard shows Coolbet under "Bookmakers active".

## Phase 2 — Real-bet infrastructure

### 2.1 — Database
- ✅ **2.1.1** Migration `091_accessible_bookmakers.sql` — table + seed rows for Coolbet + Bet365 with status='active' + RLS policy.
- ✅ **2.1.2** Migration `092_real_bets.sql` — table + indexes (bot_id, match_id, placed_at, partial pending) + slippage_pct generated column + RLS policy.
- ✅ **2.1.3** Migrations applied directly to DB via psycopg2 — verified seeded rows + 16-column schema.

### 2.2 — Engine settlement integration
- ✅ **2.2.1** `_settle_real_bets_for_matches(match_ids)` added to `workers/jobs/settlement.py`. Reuses `settle_bet_result` via `actual_odds AS odds_at_pick` aliasing.
- ✅ **2.2.2** Wired into `settle_finished_matches` so real bets settle on the same 21:00/23:30/01:00 + 15-min cadence.
- ✅ **2.2.3** Smoke test `SELF-USE-VALIDATION — settlement wires _settle_real_bets_for_matches (source inspect)` passes.

### 2.3 — Backend writer
- ✅ **2.3.1** `store_real_bet(...)` added to `workers/api_clients/supabase_client.py`. Round-trip verified end-to-end.
- ✅ **2.3.2** `compute_real_pnl(stake, actual_odds, result)` helper — pure function, smoke-tested truth table (won/lost/void/half_won/half_lost/pending).

### 2.4 — Frontend
- ✅ **2.4.2** New page `src/app/(app)/admin/place/page.tsx` — server component, gated by `is_superadmin`. Uses `getPlaceableBets()` in `engine-data.ts`.
- ✅ **2.4.3** Client component `<PlaceBetTable>` — list + filter chips (all/edge/has-odds) + place modal capturing book/odds/stake/notes.
- ✅ **2.4.4** API route `src/app/api/admin/real-bet/route.ts` — superadmin-gated, validates inputs (stake>0, odds>1.0, bookmaker in accessible_bookmakers, status not banned/inactive), inserts via service-role client.
- ✅ **2.4.5** New page `src/app/(app)/admin/real-bets/page.tsx` — performance dashboard with aggregate stats (total/won-lost/PnL/ROI/mean slippage), per-book breakdown, full bet log with color-coded slippage column.
- ✅ **2.4.6** Two new nav links (Place Bet + Real Bets) added to admin profile menu in `src/components/nav.tsx`.

### 2.5 — Bot dashboard surfacing (user's explicit ask)
- ✅ **2.5.1** New API route `src/app/api/admin/bot-book-odds/route.ts` — POST {betIds[]} → {[betId]: {unibet, bet365}}. Single round-trip lookup per bot's bet set.
- ✅ **2.5.2** `bot-dashboard-client.tsx` modal — useEffect lazy-fetches bookOdds when modal opens; new Coolbet (emerald) + Bet365 (blue) columns render inline. Footnote explains the Unibet→Coolbet proxy.

### 2.6 — PRIORITY_QUEUE entry
- ✅ **2.6.1** SELF-USE-VALIDATION promoted to ⭐ Top Priority section of `PRIORITY_QUEUE.md`. Status: 🔄 In Progress until Phase 4.

## Phase 2.7 — Accessible-bookmaker filter (ACCESSIBLE-BM, 2026-05-11)

- ✅ **2.7.1** `ACCESSIBLE_BOOKMAKERS` frozenset in `daily_pipeline_v2.py` — Bet365, Unibet, Betano, Marathonbet, 10Bet, 888Sport, Pinnacle.
- ✅ **2.7.2** Odds aggregation loop: `bm_sources` still tracks all sources; `best[mid][key]` and `best_bookmaker[mid][key]` only update for accessible books. Inaccessible books (SBO, Dafabet, 1xBet, BetVictor, Betfair, William Hill) skipped after tracking.
- ✅ **2.7.3** `bet_candidates` tuple extended to 13 elements (added `os_market`, `os_selection`) so `recommended_bookmaker` lookup is possible at `store_bet` call.
- ✅ **2.7.4** Migration `094_simulated_bets_recommended_bookmaker.sql` — adds `recommended_bookmaker TEXT` to `simulated_bets`.
- ✅ **2.7.5** `store_bet()` optional_fields updated; `recommended_bookmaker` passed from pipeline.
- ✅ **2.7.6** `scripts/daily_picks.py` — morning ritual report (picks with kickoff, match, market, odds, edge, cal%, bookmaker). Flags: `--date`, `--min-edge`, `--bookmaker`.
- ✅ **2.7.7** Smoke test `ACCESSIBLE-BM` passes. Engine pushed: 0b05d3b.

## Phase 2.8 — Remaining engine work (before Phase 3 full swing)

- ✅ **2.8.1** `scripts/real_perf_report.py` — 5 sections: summary, paper vs real (via simulated_bet_id join), by bookmaker, by market, recent bets. `--days`, `--bookmaker`, `--min-bets` flags.
- ✅ **2.8.2** Frontend value-bets page: per-bet "Bet365: 2.10 · Unibet: 2.05 ← Bet365" line for Elite users. Server-side via `getValueBetBookOdds()` (single round-trip). `recommended_bookmaker` + `matchId` added to `LiveBet`.
- ✅ **2.8.3** Freshness indicator: "Odds verified Xm ago" chip in value-bets header. Green <45m, amber <90m, red ≥90m. Server-side via `getOddsVerifiedAt()`. Elite-only fetch (same path as bookOdds).

## Phase 3 — Validation period (PARTIAL RUN 2026-05-11 → 2026-05-24)

> **Result:** 476 bets logged but NO real money staked. Reframed as paper-with-Coolbet-odds. See `self-use-validation-context.md` for full findings. Original real-money task list below is preserved for historical context — it was NOT executed as designed.

- ⏭ **3.1** Daily morning ritual at `/admin/place` — superseded by Coolbet placer `--record` (auto runs)
- ⏭ **3.2** Daily afternoon ritual at `/admin/place` — same
- ⬜ **3.3** Weekly review of `/admin/real-bets` — paused during 3.5 window
- ⏭ **3.4** Track "couldn't place" reasons — deferred (placer enrichment to be done post-window)
- ⏭ **3.5** Cohort report at 250 bets — replaced by 3.5 readout below

## Phase 3.5 — New-model evaluation window (2026-05-24 → 2026-06-07)

> Reason: modeling agent shipped `v20260524_market` with 5 bug fixes on 2026-05-24. Old-model placer baseline -8.13% is partly bug-artifact. Wait 2 weeks for new model to produce a clean baseline.

- ⬜ **3.5.1** Run Coolbet placer `--record` ~3x/day at morning/midday/pre-KO when JWT is fresh. Broad rule: all bots, 5% edge minimum. No bot-level filtering.
- ⬜ **3.5.2** Do NOT use `/admin/place` during window. Avoid adding selection-biased rows to the same `real_bets` table.
- ⬜ **3.5.3** Do NOT stake real money. No `--execute` mode until Phase 4 verdict.
- ⬜ **3.5.4** Monitor modeling agent's 2026-05-31 retrain — `v20260531` should land via Sunday cron with full feature set.
- ⬜ **3.5.5** On 2026-06-07: run `python3 scripts/real_perf_split_by_source.py --days 14` to get new-model-only placer numbers.

## Phase 4 — Decision (planned 2026-06-07)

- ⬜ **4.1** Run split-by-source report on last 14 days (new-model window only).
- ⬜ **4.2** Apply Phase 3.5 decision matrix from `self-use-validation-context.md` ("What to do on 2026-06-07" section).
- ⬜ **4.3** If verdict = no pivot: file `bot-edge-debug.md`, move files to `dev/done/`, close PRIORITY_QUEUE entry.
- ⬜ **4.4** If verdict = marginal: extend window 2-4 weeks, consider narrowing placer to confirmed positive bots.
- ⬜ **4.5** If verdict = strong: lock bot list, decide whether to flip placer to `--execute` for real-money execution-friction measurement.
