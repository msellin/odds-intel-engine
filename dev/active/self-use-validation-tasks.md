# SELF-USE-VALIDATION — Task checklist

> Read `self-use-validation-plan.md` first for context.
> Update statuses as you work. ⬜ not started · 🔄 in progress · ✅ done

## Phase 0 — Free sanity check (1 evening)

- ✅ **0.1** Sampling script `scripts/sample_coolbet_proxy_check.py` shipped — pulls all pending paper bets on not-yet-started matches with Unibet + Bet365 + Pinnacle joined at pick time. Generates CSV at `dev/active/self-use-validation-phase0-worksheet.csv`. Reframed forward-looking (Coolbet doesn't publish historical odds — once a match kicks off, the price is gone). Re-runnable; each session adds samples. **First run produced 26 rows incl. Barcelona vs Real Madrid, PSG, Olympiakos vs PAOK.**
- ✅ **0.2** Done by 0.1 — script fetches Unibet + Bet365 directly.
- ⬜ **0.3** **(USER MANUAL STEP)** Open `dev/active/self-use-validation-phase0-worksheet.csv` in Numbers/Excel. Open coolbet.ee, look up each upcoming match + market. Write the displayed Coolbet price into the `coolbet_actual` column. Where Coolbet doesn't offer the market, leave blank or write `N/A`. Re-run script over multiple days to accumulate samples.
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
