# OddsIntel — Next Steps (Updated 2026-04-27)

## State After All Three Agents' Work

### What's working
- Daily pipeline runs at 08:00 UTC via GH Actions (once SUPABASE_SECRET_KEY + SUPABASE_URL secrets added)
- ~200 matches/day with odds (Kambi 41 leagues + Sofascore 30+ leagues)
- All 467 Sofascore fixtures stored in DB daily
- **92% of Kambi matches now get predictions** (was 8%) — thanks to Tier A/B/C system
- Live tracker collects in-play data every 5min (once GH secrets set)
- Hourly pre-match snapshots build CLV timeline (once GH secrets set)
- Settlement runs at 21:00 UTC — results from Sofascore, CLV computed, bot bankrolls updated
- AI news checker runs at 09:00 UTC — Gemini flags injury/lineup risk on each bet

### ✅ COMPLETED (no longer pending)
1. DB migration 002 — run in Supabase SQL editor ✓
2. Build targets_global.csv — done (scripts/build_global_targets.py, 42,581 rows, 17 leagues) ✓
3. Tiered confidence system — Tier A/B/C in compute_prediction() + stake sizing ✓
4. Sofascore Tier C fallback — on-demand team history via API ✓
5. Settlement pipeline — workers/jobs/settlement.py, wired to GH Actions ✓
6. AI news checker — workers/jobs/news_checker.py, wired to GH Actions ✓

---

## Priority Queue (Current)

### 1. Add Remaining GitHub Secrets (BLOCKING)
Required for all automated jobs to run.
- `SUPABASE_URL` — your Supabase project URL
- `SUPABASE_SECRET_KEY` — service_role key (sb_secret_... from .env)
- `GEMINI_API_KEY` — for AI news checker (AIzaSy... from .env)

### 2. Tier B Backtest (HIGH VALUE — do before trusting live Tier B bets)
The bot is NOW placing Tier B bets on Norway, Sweden, Poland, Romania, Serbia, Ukraine, Turkey, Greece, Croatia etc.
The stake cap (50%) and +2% edge threshold are conservative safeguards — but we don't know yet if these leagues are actually profitable.

Script to build: `scripts/backtest_tier_b.py`
- Load targets_global.csv (42,581 matches, 2015–present)
- For each match: run compute_prediction() using only targets_global as Tier B source
- Apply same edge thresholds as Tier B bots
- Compute: hit rate, ROI, CLV (where bookmaker odds available), by league
- Key question: which of the 17 new leagues show real edge? Which should be Tier B vs dropped?

Expected output: a league-by-league profitability table like SOCCER_FINDINGS.md.
Note: targets_global.csv has no bookmaker odds (set to NaN) — so edge is measured against Poisson fair odds, not market odds. Still valid for calibration direction.

### 3. Mega Backtest — Beat the Bookie Dataset (NOT STARTED)
479K matches, 818 leagues, with real bookmaker odds from Bet365/PS/market avg.
This is the gold standard for finding which obscure leagues have consistent edge.

Dataset: https://www.football-data.co.uk/data.php (Beat the Bookie section)
Script: scripts/mega_backtest.py already exists — check if it handles the BTB format.

Once done → update bot league_filter configs and tier thresholds.

### 4. O/U 0.5 / 1.5 / 3.5 Backtests (MEDIUM VALUE)
We can compute outcomes from existing total_goals data.
Problem: no historical bookmaker odds for 0.5/1.5/3.5 lines (only 2.5 in dataset).
Solution: use Poisson distribution to estimate fair odds for any line → compare to model.

Script: `scripts/backtest_ou_lines.py`
- For each match: compute fair odds for O/U 0.5, 1.5, 2.5, 3.5 from Poisson model
- Compare to actual outcome
- Key question: at which O/U line does our model have most edge?

Note: Results are indicative (estimated fair odds, not real bookmaker odds).
Live data collection (now running) will give real validation in 4-6 weeks.

### 5. OddsPortal Scraper (MEDIUM VALUE)
Currently 200/467 = 43% of daily fixtures have odds.
OddsPortal covers virtually every league → could push to 70-80%.
Only worthwhile after Tier B backtest confirms which leagues are worth betting.

### 6. Live Data Validation (ONGOING — starts automatically once secrets set)
- Live tracker builds live_match_snapshots + match_events every 5min
- Hourly snapshots build odds timeline (minutes_to_kickoff column)
- After 2-4 weeks of data, run CLV analysis: does our T-2h pick odds beat closing line?
- In-play hypothesis: high-xG game 0-0 at minute 10-15 → O/U 1.5 drifts upward

---

## Key Data Gaps to Fill

| Gap | Data exists? | Effort | Unlocks |
|-----|-------------|--------|---------|
| Norway/Sweden/Poland/Romania targets | Yes (global parquet) | 2h | Predictions for 30+ new leagues |
| O/U 0.5/1.5/3.5 historical odds | Partial (need Beat the Bookie check) | 1h | Better O/U backtest |
| Odds for 267 matches without odds | Need OddsPortal | 3h | More match coverage |
| Real-time xG data | Sofascore live (collecting now) | Done | In-play model improvement |
| Settlement logic | Build it | 2h | Real ROI measurement |

---

## The Bet Timing Question (Collecting Data Now)

Pre-match timing rule of thumb while we gather our own data:
- **T-2h to T-1h**: Best window for pre-match. Line has moved from sharp action but still has value windows.
- **Never at opening**: Opening lines in obscure leagues are soft but you can't predict direction.
- **Live — minutes 8-18**: For high-xG games 0-0 at minute 10-15, O/U 1.5 drifts upward. Hypothesis — the live tracker will validate or refute this with real data.
- **CLV is the only short-term metric**: Beat the closing line consistently = finding real value.

The hourly snapshot job is building the pre-match timeline. The live tracker is building the in-play dataset. In 2-4 weeks we can run the analysis queries.

---

## Bot Configuration Reality

| Bot | Strategy | Currently active on | Expected bets/day (weekends) |
|-----|----------|---------------------|------------------------------|
| bot_lower_1x2 | Tier 2-4, 1X2 only | English lower leagues mainly | 2-5 |
| bot_v10_all | All leagues, tier-adjusted | All 18 historical leagues | 5-15 |
| bot_conservative | 10%+ edge only | Very selective | 0-3 |
| bot_aggressive | 3% edge, high volume | All available | 10-30 |
| bot_greek_turkish | Greece + Turkey only | G1 + T1 | 1-3 |
