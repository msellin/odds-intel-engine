# SELF-USE-VALIDATION — Plan

> Status: planning · Owner: superadmin only · Created 2026-05-10

## The pivot premise

User dislikes B2C marketing. SaaS is hard to grow. The engine, ML stack, and 25+ signals already exist. **If the bots have a real edge that survives real-money execution, switching to a personal betting tool is higher-leverage than trying to acquire SaaS users.**

Hard prerequisite: validate whether paper-trading edge is real, or measurement artifact (data quality bugs we keep finding suggest the latter is non-zero).

## Constraints / decisions already made

- **Bookmakers in scope:** Coolbet (preferred) + Bet365 (secondary). Both accessible from Estonia.
- **Bet placement:** **Manual only.** No third-party tool automates Coolbet (Smartbet.io supports only Pinnacle/Bet365). Custom scraper-based auto-placement violates ToS — not doing it.
- **Stake size during validation:** €1–3 per bet to avoid limit triggers and minimise damage if edge is illusory.
- **Audience:** superadmin (Margus only) — gated by `profiles.is_superadmin`. Don't expose to any other tier.
- **Coexistence:** Don't drop SaaS during validation. The two run in parallel until the answer is clear (~6 weeks).

## What we already have

- `odds_snapshots` already contains:
  - **Bet365** — 422K rows, fresh today (via API-Football)
  - **Unibet** — 443K rows, fresh today (via API-Football). Unibet runs on Kambi, same B2B platform that powers Coolbet → strong proxy.
- `simulated_bets` table with `odds_at_pick` for every bot bet
- Settlement code + signal pipeline + ML stack — all reusable
- `bots.is_active=false` gate already exists for codeless bot pause

Net: we don't need to integrate a new data source to *start*. Phase 0 uses existing data. Only if Unibet diverges meaningfully from real Coolbet do we add a paid API in Phase 1.

## What we don't have / need to decide later

- **Coolbet odds direct.** Options:
  1. Free: assume Unibet ≈ Coolbet (validate empirically in Phase 0)
  2. Cheap: The Odds API ($0 free / $30/mo for 20K reqs). Coolbet listed under EU.
  3. DIY: Playwright scraper hitting `coolbet.ee` (1–2d build, ongoing maintenance)
  4. Enterprise: OpticOdds (sales-led, ~$500–2000/mo) — overkill
- **Real bet logging schema** — new `real_bets` table (Phase 2)
- **Bookmaker availability registry** — new `accessible_bookmakers` table (Phase 2)

## Phases

### Phase 0 — Free sanity check (1 day, no code)
**Goal:** Decide whether Unibet ≈ Coolbet in practice. If yes, no API spend needed.

1. Pull 20 of yesterday's bot picks (already settled or close to it).
2. For each, look up `odds_snapshots` Unibet + Bet365 row at the bot's pick time.
3. Open coolbet.ee (or a recent screenshot/replay if game is over) and find the same market.
4. Build a small comparison sheet: bot's `odds_at_pick`, Unibet, Bet365, Coolbet actual.
5. **Decision metrics:**
   - Mean abs % gap between Unibet and Coolbet across 20 samples
   - Worst-case gap
   - Frequency of cases where Coolbet didn't even offer the market
6. **Decision:**
   - Gap consistently <3% → Unibet is a good proxy. Skip Phase 1, go to Phase 2 with `bookmaker IN ('Unibet','Bet365')` as the data source.
   - Gap 3–8% → marginal. Probably still skip API; document the systematic shrinkage.
   - Gap >8% or markets often missing → Phase 1 (add Coolbet via The Odds API).

### Phase 1 — Optional: add Coolbet odds directly (1–2 days, only if Phase 0 fails)
**Goal:** Real Coolbet prices in `odds_snapshots`.

1. Sign up for The Odds API free tier (500 reqs/mo).
2. Add `workers/api_clients/the_odds_api.py` — a thin client analogous to `api_football.py`.
3. Add `workers/jobs/fetch_coolbet_odds.py` — runs once daily on the day's matches, fetches Coolbet rows, writes to `odds_snapshots` with `bookmaker='Coolbet'`.
4. Free-tier sufficient if we limit to once/day on top-200 matches; otherwise upgrade to $30/mo (20K reqs).
5. Smoke test, plus a daily sanity diff vs Unibet logged to ops snapshot.
6. **Decision again** if Coolbet diverges from Unibet meaningfully: keep both columns visible in the UI.

### Phase 2 — Real-bet infrastructure (2–3 days)
**Goal:** A superadmin-only page that surfaces bot picks with Coolbet + Bet365 prices side-by-side, accepts manual bet logging, and tracks real PnL separately from paper trading.

#### 2.1 — Data model
- Migration N: `accessible_bookmakers` table
  ```
  bookmaker TEXT PRIMARY KEY
  status TEXT NOT NULL          -- 'active' | 'limited' | 'banned'
  notes TEXT
  updated_at TIMESTAMPTZ
  ```
  Seed: `('Coolbet','active',...)`, `('Bet365','active',...)`. Manually maintained.

- Migration N+1: `real_bets` table
  ```
  id UUID PRIMARY KEY
  simulated_bet_id UUID REFERENCES simulated_bets(id)  -- the paper-trading row this maps to
  bot_id UUID REFERENCES bots(id)
  match_id UUID REFERENCES matches(id)
  market TEXT
  selection TEXT
  bookmaker TEXT REFERENCES accessible_bookmakers(bookmaker)
  captured_odds NUMERIC      -- what we showed in the UI
  actual_odds NUMERIC         -- what user actually got at placement
  slippage_pct NUMERIC GENERATED ALWAYS AS ((actual_odds - captured_odds) / captured_odds * 100) STORED
  stake NUMERIC
  placed_at TIMESTAMPTZ DEFAULT NOW()
  result TEXT                 -- 'pending' | 'won' | 'lost' | 'void'
  pnl NUMERIC
  resolved_at TIMESTAMPTZ
  notes TEXT
  ```

#### 2.2 — Engine wiring
- `workers/jobs/settlement.py` — add `_settle_real_bets(match_ids)` that mirrors `_settle_simulated_bets` semantics. Run at the same 21:00/23:30/01:00 UTC settlement windows + 15-min `settle_ready` sweep.
- `workers/api_clients/supabase_client.py` — add `store_real_bet(...)` writer (single-row, no bulk needed at €1-3 stakes).
- New helper `compute_real_pnl(stake, actual_odds, won) → pnl_signed` (won: stake×(odds-1); lost: -stake; void: 0).

#### 2.3 — Frontend (`odds-intel-web`)
- New route: `/admin/place` — server component, gated by `is_superadmin`.
- Lists today's pending paper bets where:
  - `bot.is_active = true`
  - At least one of `accessible_bookmakers` (Coolbet, Bet365) has odds_at_pick within 10% of paper bet's recorded odds
  - Match KO is in the future
- Columns:
  | Match | Bot | Market | Selection | Bot's price | Coolbet (Unibet proxy) | Bet365 | Edge | Suggested €1-3 stake | "Place" |
- Click "Place" → modal:
  - Confirm bookmaker, actual odds taken, stake
  - Submit → POST `/api/admin/real-bet` → inserts `real_bets` row
- A second view: `/admin/real-bets` — like the bot performance page but reading `real_bets`. Shows real PnL, slippage stats, hit rate, per-book breakdown.

#### 2.4 — Bot dashboard surfacing (the user's specific ask)
- Existing `/admin/bots` page: add two extra columns to the per-bet expansion table — `Coolbet (proxy)` and `Bet365` — pulled from `odds_snapshots` at bet time. Read-only, no actions. Helps you spot which paper bets had real-world prices that justify a real bet.

### Phase 3 — Run for 4–6 weeks (validation period)
**Goal:** Collect enough real bets to know if edge survives real-world friction.

- Daily ritual: open `/admin/place` morning + late-afternoon, place 5–10 bets at €1–3 each at Coolbet (or Bet365 if Coolbet doesn't offer or has worse price).
- Manually log each placement — actual odds, stake, book.
- After 4 weeks: ~150–250 real bets. Run analysis:
  - Real ROI per bot
  - Slippage distribution per book (median + tail)
  - Hit rate vs paper-trading hit rate
  - Per-book limit status — flag any "max stake reduced" events
  - Markets where edge survived vs didn't (1X2 vs OU vs BTTS, top leagues vs lower divs)
- Build `dev/active/self-use-validation-results.md` with the cohort report.

#### Critical bet-tracking discipline
- **Don't skip logging losers.** Selection bias destroys this study faster than anything else.
- **Log every "couldn't place" too** (Coolbet didn't offer the market / price moved before you clicked / max stake too low). These are the real cost of execution that paper trading hides.
- Add a `notes` column for "didn't place because…" so we can quantify execution friction.

### Phase 4 — Decision (week 6–8)
**Goal:** Decide whether to pivot product direction.

Outcomes and what each means:

| Real ROI over 200+ bets | Decision |
|---|---|
| **<0%** | Edge isn't real. Don't pivot. SaaS becomes a "ship-as-portfolio-piece" project, low-effort maintenance only. |
| **0–3%** | Marginal. Real but tight. Keep playing at small stakes; don't pivot publicly yet. SaaS continues. |
| **3–8%** | Real edge. Plan stake scale-up to €5–20. Start derisking SaaS dependence — minimum-effort maintenance. |
| **>8%** | Strong signal. Pivot fully: keep SaaS only if it covers infra costs, focus engineering on bankroll growth, account rotation, scaling. |

## Open questions (revisit after Phase 0)

1. **Should we add a third book?** Pinnacle is in our data and is the cleanest reference, but EU residents can't bet there. If the user gets a non-EU residence (or for benchmark purposes), it could be the value-finder anchor. Unrelated to Coolbet betting but useful for measuring how good Coolbet's prices are vs the sharp benchmark.
2. **What about Olybet / Paf?** Both Estonian-licensed. Olybet not in any API I checked. Paf runs on Kambi too (so Unibet proxy applies). Probably not worth integrating unless Coolbet limits hit fast.
3. **Tax accounting?** Estonian gambling winnings from licensed operators are tax-free for the bettor. Verify before any meaningful bankroll. Coolbet is licensed locally so this likely applies.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Bot edge is paper-trading artifact (data bugs) | Phase 0 cheap validation; €1-3 stakes during Phase 3 cap real loss at €100-500 worst case |
| Coolbet limits hit early | Bet365 as fallback; rotate stake size; track via `accessible_bookmakers.status` |
| Unibet ≠ Coolbet → Phase 0 results are misleading | Phase 1 escape hatch using The Odds API direct Coolbet |
| Slippage eats edge | Already measuring via `slippage_pct` generated column — explicit metric in cohort report |
| Auto-bet temptation | Hard rule: manual-only. Don't even build a "click-to-place at Coolbet" flow. Friction is a feature. |
| 6 weeks is too short for statistical confidence | At ~250 bets, edge of 5%+ should be visible above noise. <5% real edge is barely-worth-the-effort anyway. Document N-too-small cases as such; don't draw false conclusions. |

## Cost estimate

- Phase 0: free, 1 evening
- Phase 1: free (the Odds API free tier 500 reqs/mo, sufficient if used sparingly) or +$30/mo
- Phase 2: free (engineering time only, ~3 days)
- Phase 3: real-bet bankroll — user's choice. €500 covers 200 bets at €2.50 avg.
- Phase 4: zero, just analysis

Total worst case: ~€500 (your bankroll) + €0–30/mo recurring (Coolbet API if Phase 1 needed).
