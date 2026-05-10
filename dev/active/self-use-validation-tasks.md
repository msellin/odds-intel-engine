# SELF-USE-VALIDATION — Task checklist

> Read `self-use-validation-plan.md` first for context.
> Update statuses as you work. ⬜ not started · 🔄 in progress · ✅ done

## Phase 0 — Free sanity check (1 evening)

- ⬜ **0.1** Pick 20 settled paper bets from the last 2–3 days. Mix of bots (ou15_defensive, btts_all, lower_1x2, opt_*, etc.) and league tiers.
- ⬜ **0.2** For each, query `odds_snapshots` for Unibet + Bet365 row at the closest timestamp ≤ pick time. Build a CSV: bot, match, market, sel, bot_odds, unibet_odds, bet365_odds.
- ⬜ **0.3** Open coolbet.ee, look up each match (post-match results pages still show line history). Add `coolbet_odds` column manually. Where Coolbet didn't offer the market, mark `N/A`.
- ⬜ **0.4** Compute: mean abs gap (Unibet vs Coolbet), worst-case gap, % of cases where Coolbet didn't offer. Save as `dev/active/self-use-validation-phase0-results.md`.
- ⬜ **0.5** Decision recorded in plan: "Unibet proxy works" or "Need direct Coolbet API".

## Phase 1 — Coolbet odds via The Odds API *(only if Phase 0 says proxy is bad)*

- ⬜ **1.1** Sign up for The Odds API (free tier). Add `THE_ODDS_API_KEY` to Railway + local `.env`.
- ⬜ **1.2** Add `workers/api_clients/the_odds_api.py` — thin client mirroring the `_get` retry pattern in `api_football.py`.
- ⬜ **1.3** Build `workers/jobs/fetch_coolbet_odds.py` — once-daily, top 200 matches by edge, write to `odds_snapshots` with `bookmaker='Coolbet'`. Reuse `_kickoff_minute` helper for date matching.
- ⬜ **1.4** Wire into `workers/scheduler.py` — `CronTrigger(hour=9, minute=0)` daily.
- ⬜ **1.5** Smoke tests: client signature, daily writer source guard, sanity check on row count vs expected.
- ⬜ **1.6** Verify after first run: rows landed, schema matches, ops dashboard shows Coolbet under "Bookmakers active".

## Phase 2 — Real-bet infrastructure

### 2.1 — Database
- ⬜ **2.1.1** Migration `090_accessible_bookmakers.sql` — table + seed rows for Coolbet + Bet365 with status='active'.
- ⬜ **2.1.2** Migration `091_real_bets.sql` — table + indexes (bot_id, match_id, placed_at, result). Generated column for slippage.
- ⬜ **2.1.3** Apply migrations via `supabase db push` or GH Actions migrate workflow.

### 2.2 — Engine settlement integration
- ⬜ **2.2.1** Add `_settle_real_bets(match_ids)` to `workers/jobs/settlement.py`. Same logic as `_settle_simulated_bets` but writes to `real_bets`. Reuse `settle_bet_result` for win/loss decision.
- ⬜ **2.2.2** Wire into `run_settlement()` and `settle_ready_matches()` so real bets settle on the same cadence.
- ⬜ **2.2.3** Smoke test: real bet on finished match, assert settled correctly with right pnl.

### 2.3 — Backend writer
- ⬜ **2.3.1** Add `store_real_bet(...)` to `workers/api_clients/supabase_client.py`. Single-row insert. Returns bet ID.
- ⬜ **2.3.2** Add `compute_real_pnl(stake, actual_odds, won)` helper — pure function, easy to unit test.

### 2.4 — Frontend
- ⬜ **2.4.1** Server-side guard helper `requireSuperadmin()` in `src/lib/auth.ts` (or wherever similar helpers live). Returns `notFound()` for non-superadmins.
- ⬜ **2.4.2** New page `src/app/(app)/admin/place/page.tsx` — server component:
  - Calls new `getPlaceableBets()` in `engine-data.ts` that joins paper bets + odds_snapshots + accessible_bookmakers.
  - Renders table with Match | Bot | Market | Sel | Paper bot odds | Coolbet (or proxy) | Bet365 | Edge | Stake | "Place" button.
- ⬜ **2.4.3** Client component `<PlaceBetModal>` — opens on row click, captures actual odds + stake, POSTs to `/api/admin/real-bet`.
- ⬜ **2.4.4** API route `src/app/api/admin/real-bet/route.ts` — superadmin-gated, validates input, inserts via Supabase admin client.
- ⬜ **2.4.5** New page `src/app/(app)/admin/real-bets/page.tsx` — performance dashboard reading `real_bets`. Per-bot ROI, slippage stats, per-book breakdown, mini bankroll chart.

### 2.5 — Bot dashboard surfacing (user's explicit ask)
- ⬜ **2.5.1** Modify per-bet expansion table on `/admin/bots` to add columns: Coolbet (or Unibet proxy) at pick time, Bet365 at pick time. Pulled from `odds_snapshots` via `getBetsForBot()` extension.
- ⬜ **2.5.2** Visual indicator: highlight the row in green if Coolbet/Bet365 ≥ 95% of bot's recorded odds (placeable in real life), red if both <80% (paper-only edge).

### 2.6 — PRIORITY_QUEUE entry
- ⬜ **2.6.1** Add `SELF-USE-VALIDATION` row to `Critical bugs found 2026-05-09` section of `PRIORITY_QUEUE.md` — link to this plan. Status: 🔄 In Progress until Phase 4 closes.

## Phase 3 — Validation period (4–6 weeks)

- ⬜ **3.1** Daily morning ritual (~5 min): open `/admin/place`, place 5–10 bets at €1–3 each at Coolbet, log via the modal.
- ⬜ **3.2** Daily afternoon ritual (~3 min): same again for late-day matches.
- ⬜ **3.3** Weekly review: check `/admin/real-bets`, note any per-book limit warnings, update `accessible_bookmakers.status` if a book starts limiting you.
- ⬜ **3.4** Track "couldn't place" reasons in a separate notes file or just the `notes` column. Quantify execution friction.
- ⬜ **3.5** After ~250 bets, generate `dev/active/self-use-validation-results.md` cohort report.

## Phase 4 — Decision

- ⬜ **4.1** Cohort report drafted with real ROI, slippage, hit rate, per-book breakdown.
- ⬜ **4.2** Compare against pivot decision matrix in plan. Document the chosen direction.
- ⬜ **4.3** If pivoting: file new tasks for stake scaling, account rotation, SaaS-deprioritisation strategy.
- ⬜ **4.4** If not pivoting: file `bot-edge-debug.md` with the analysis of why paper-trading edge didn't survive — feeds back into model improvements.
- ⬜ **4.5** Move all phase docs from `dev/active/` to `dev/done/` (creating `done/` if needed).
