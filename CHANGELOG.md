# OddsIntel Changelog

User-facing changelog covering both engine (pipeline/model) and frontend (UI/UX) changes.
Newest entries at the top. Internal refactors and infrastructure changes are noted briefly.

---

## 2026-05-06 (5)

### Performance — DB query audit (PERF-FE-1..5, PERF-PY-1)

Six anti-patterns found and fixed across frontend and pipeline:

- **A1 — matches page render path**: `daily_unlocks` DB check was sequential after the main `Promise.all`. Moved inside the auth IIFE so it runs in parallel with `getUserTier` — saves one round-trip on every page load for logged-in users
- **C3 — `getTodayOdds()`**: Was `SELECT *` on `odds_snapshots` for all today's match IDs — returning all historical snapshots (~18k rows for 160 matches). Now uses `get_latest_match_odds` RPC (`DISTINCT ON (match, bookmaker, market, selection)`) — returns only the latest snapshot per combination
- **D1 — `getTrackRecordStats()`**: Hot path fetched 500 `odds_snapshots` rows + 2000 `matches` rows just to count distinct bookmakers/leagues in JS. Replaced with `get_coverage_counts` RPC — two `COUNT(DISTINCT)` queries returning two integers
- **C1 — `getPublicMatchBookmakerCount()`**: Fetched all 1x2 rows for a match and counted distinct bookmakers in JS. Replaced with `get_bookmaker_count_for_match` RPC — single `COUNT(DISTINCT bookmaker)`
- **C2 — `getOddsMovement()`**: Fetched all snapshots (100–1000 rows) and bucketed by hour in JS. Replaced with `get_odds_movement_bucketed` RPC — `DATE_TRUNC('hour') + MAX GROUP BY` returns ~20–50 rows
- **B1 — `compute_market_implied_strength()`** (Python pipeline): Was 2 + N queries (one per match in two loops, up to 12 total). Replaced with one `DISTINCT ON (match_id, selection)` batch query covering all match IDs — 3 queries total
- Migration 053 adds the four new RPCs

---

## 2026-05-06 (4)

### Model — Draw inflation (CAL-DRAW-INFLATE)
- Poisson draw probability now multiplied by 1.08 after Dixon-Coles correction, with home/away rescaled to compensate
- Dixon-Coles τ only patches the (0,0), (1,0), (0,1), (1,1) corner cells — higher-scoring draws (2-2, 3-3) were still underestimated
- Game-state effects (protecting leads, parking the bus) also inflate real-world draw rates vs independent Poisson
- 1.08 is the mid-point of the validated 1.05-1.15 range; fit on backtest Brier score minimisation

### Matches page — Tomorrow tab + performance (TZ-TOMORROW)
- New **Today / Tomorrow** toggle at the top of the matches page — click Tomorrow to see next day's fixtures
- Implemented as a `?tab=tomorrow` URL param: full server render, same query count, no client-side fetch
- Yesterday overhang and "What Changed Today" widget are suppressed on the Tomorrow tab (irrelevant for future matches)

### Matches page — Performance fixes
- **Odds RPC batches parallelised**: was a sequential loop (each batch waited for the previous). Now all batches fire with `Promise.all()` — for 160 matches this saves one full round-trip
- **Signal count query replaced**: was fetching up to 60,000 raw rows to count distinct signals per match in memory. Now uses a `get_signal_counts` RPC (migration 051) that returns `COUNT(DISTINCT signal_name) GROUP BY match_id` from the DB — dramatically smaller payload

---

## 2026-05-06 (3)

### Data Quality — Cutoff date established + clv_pinnacle backfill

- **Quality cutoff date: 2026-05-06.** All modeling thresholds (B-ML3, ALN-1) now filter to data from this date onward. Pre-cutoff data was collected before the full calibration pipeline (Pinnacle anchor, CAL-ALPHA-ODDS, sharp gate, full veto coverage) was live — training on it would teach the wrong patterns.
- **B-ML3 ETA updated** to ~May 17 (was May 10) — 11 days of quality `match_feature_vectors` rows with Pinnacle signals present
- **ALN-1 ETA updated** to ~June 5 (was May 9-10) — 300 clean settled bets at ~27/day from cutoff
- **`clv_pinnacle` backfill** run via `scripts/backfill_clv_pinnacle.py` — updated 26/77 existing settled bets. Remaining 51 pre-date Pinnacle odds collection (PIN-1 started May 4, is_closing snapshots only started accumulating then).

---

## 2026-05-06 (2)

### Model — Pinnacle Signal Expansion (PIN-2 through PIN-5)

**Pinnacle signals for all markets (PIN-2)**
- Morning pipeline now stores `pinnacle_implied_draw`, `pinnacle_implied_away`, `pinnacle_implied_over25`, `pinnacle_implied_under25` in `match_signals`, alongside the existing `pinnacle_implied_home`
- Separate bulk query block (3b) in `batch_write_morning_signals()` — one query per market/selection combo

**Pinnacle disagreement veto extended to all markets (PIN-3)**
- The veto that skips bets when `calibrated_prob − pinnacle_implied > 0.12` now applies to Draw, Away, Over 2.5, and Under 2.5 bets, not just Home
- Pinnacle anchor also now used as the calibration shrinkage anchor for all markets (was Home-only before)
- Threshold 0.12 applied uniformly; tune per market once 50+ settled bets accumulate

**Pinnacle line movement signal (PIN-4)**
- `pinnacle_line_move_home`, `pinnacle_line_move_draw`, `pinnacle_line_move_away` added to `match_signals`
- Computed as: current Pinnacle implied − opening Pinnacle implied. Positive = selection shortened = sharp money backing
- Requires at least 2 Pinnacle snapshots for a match to fire; otherwise skipped
- Purer sharp-money signal than generic `odds_drift` (Pinnacle lines only move on informed money)

**Pinnacle-anchored CLV (PIN-5)**
- `clv_pinnacle` column added to `simulated_bets` (migration 050)
- Computed at settlement: `(odds_at_pick / pinnacle_closing_odds) − 1`
- Pinnacle CLV is the industry-standard bet model validator — consistently positive = finding edge before the sharpest market moves
- Falls back to latest Pinnacle snapshot when `is_closing` is not flagged

---

## 2026-05-06

### Model — Calibration Overhaul (CAL tasks 1–4)

**Root-cause diagnostic (CAL-DIAG-1)**
- New diagnostic script `scripts/run_cal_diag.py` — 3 targeted SQL queries against settled 1X2 home bets
- Found: Platt sigmoid was inflating calibrated probabilities by +3.87pp (38.2% → 42%) on home bets, making calibration worse not better
- Found: Pinnacle-implied (30.2%) is much closer to actual win rate (26%) than the model (38.2%), validating the Pinnacle-anchor approach
- Found: sharp_consensus was negative on average (−0.0034), consistent with the model picking against sharp money

**Pinnacle as calibration anchor (CAL-PIN-SHRINK)**
- `calibrate_prob()` now accepts an optional `anchor_implied` parameter
- When Pinnacle-implied probability is available for a 1X2 Home bet, it replaces the soft-market average as the shrinkage anchor
- Pinnacle's 2–3% vig vs soft books' 5–8% means its implied probabilities are systematically closer to true probability

**Longshot model-weight reduction (CAL-ALPHA-ODDS)**
- When odds > 3.0, model weight (alpha) is reduced by 0.20 (floor: 0.10), forcing the calibrated probability closer to the anchor
- Addresses the 0.30–0.40 probability bin where 23 bets showed 35.5% model-predicted vs 13% actual win rate

**Sharp consensus gate (CAL-SHARP-GATE)**
- 1X2 Home bets are now skipped when `sharp_consensus_home < −0.02` (sharps disagree with a home pick)
- Batch-loads sharp consensus signals alongside Pinnacle signals at bet selection time

---

## 2026-05-05

### Bankroll Analytics (Elite)
- New `/bankroll` page for Elite subscribers: cumulative units chart, ROI, hit rate, avg CLV, max drawdown, model benchmark comparison, per-league breakdown, and last 20 picks with CLV
- Accessible via "Bankroll Analytics" link in the profile dropdown (Elite/superadmin only)

### Watchlist Alerts
- Saved matches now trigger email alerts: kickoff reminder ≤2h before KO (all tiers), odds movement ≥5% in last 6h (Pro/Elite)
- Notification settings now manageable from the Profile page — toggle switches for daily digest, weekly report, and watchlist alerts

### Weekly Performance Digest
- New Monday morning email (08:00 UTC): model W/L/units for prior week + top upcoming fixtures
- Pro/Elite version includes your personal pick stats (hit rate, net units, avg CLV)
- Opt out via the notification settings toggle on your Profile page

### League Priority Overhaul
- Matches are now sorted by a 6-tier league system: CL/WC/Euros at top, then Europa League group, then Big 5 domestic leagues, then strong secondary, then all others
- Previously the model treated Champions League and Premier League as the same tier — now corrected

### Model
- Dynamic Dixon-Coles ρ per league tier: each tier now has its own fitted correlation coefficient (from historical scoreline frequencies) instead of a global constant. Improves low-scoring draw accuracy in lower-tier leagues.
- New paper trading bot: `bot_proven_leagues` — focuses on the 5 leagues with the strongest cross-era backtest signals (Singapore, Scotland, Austria, Ireland, South Korea)

### Predictions Pages
- New `/predictions` index and `/predictions/[league]` pages — SEO-optimised match prediction pages for 8 featured leagues, with probability bars, model confidence badges, and FAQ schema
- Linked from the main nav

### Pick Cards
- Share any of your picks as a branded image — hit "Share" on the My Picks page to get a pre-rendered OG card with match, selection, odds, and result

### Match Intelligence
- Model vs Market vs Users widget on every match detail page: three colored bars showing where the model, the implied odds, and community votes each sit — highlights tension when they disagree by >5pp

---

## 2026-05-04

### Docs Restructure
- Merged `docs/reddit_warmup_comments.md` + `docs/reddit_launch_posts.md` → `docs/REDDIT_LAUNCH.md` (single file: progress tracker + all 6 post drafts + subreddit rules)
- Moved `LAUNCH_PLAN.md` → `docs/LAUNCH_PLAN.md`
- Established convention: root `/*.md` = agent protocol docs; `docs/` = strategy, playbooks, reference

### Stripe — Live Mode
- **Payments now live**: real checkout for Pro (€4.99/mo) and Elite (€14.99/mo) — annual and founding rates also active
- Production webhook active at `https://www.oddsintel.app/api/stripe/webhook` — tier upgrades apply instantly on payment

### Bot Dashboard (Superadmin)
- **Bot detail modal**: click any bot row to see its full bet history — date, match, market, odds, stake, result, P&L, closing line value (CLV), and a bankroll progression chart
- Inactive bots (no settled bets yet) are shown greyed out but still clickable

### Alignment Signals — Expanded
- **Sharp bookmaker consensus** signal added: tracks whether sharp books (Pinnacle, Betfair, etc.) agree with the model pick direction
- **Pinnacle anchor** signal added: compares model probability vs Pinnacle-implied probability — flags picks where the sharpest market in the world agrees (+) or strongly disagrees (–)
- Fixed alignment bug: bets with no active dimensions now correctly show `NONE` instead of `LOW`

### Odds Data
- Pinnacle odds now captured during every odds-collection run and stored as a reference signal

### Matches Page
- Fixed: page now shows **today's matches only** (from 00:00) plus **yesterday's matches still in progress** — previously showed a rolling 2-day window including finished yesterday matches

### Performance
- Track record page now loads from pre-computed nightly stats instead of running heavy queries on every page load

---

## 2026-05-03

### Model Calibration
- Platt scaling fitted on 400 real match outcomes — probability calibration error (ECE) reduced by 86–97% across all markets (1x2, O/U)
- Calibration now runs automatically after settlement when enough data is available

### Infrastructure
- Dashboard cache written nightly at 21:00 UTC — track record, bot stats, and system status all read from cache

---

## 2026-05-01

### Bot Dashboard
- New superadmin-only page at `/admin/bots` showing per-bot P&L, hit rate, stakes, ROI, and market breakdown
- 16 paper trading bots running since 2026-04-27 with €1,000 starting bankroll each
- Bots bet on 1x2, O/U 1.5/2.5/3.5, and BTTS markets with Kelly-sized stakes
- Explains why some bots have 0 bets: strict edge thresholds, league filters, or market conditions not yet triggered

### Pipeline Monitoring
- Daily morning update script: bar charts, threshold progress, per-bot P&L, calibration ECE, pipeline health

---

## 2026-04-29

### Betting Bots — Expanded
- 6 new bots added (total: 16): BTTS specialist, O/U 1.5 defensive, O/U 3.5 attacking, draw specialist, optimised home/away variants
- A/B test: bots split into pre-match (2h before KO) and last-minute (30min) timing cohorts to measure information timing value
- Exposure control: stake automatically halved for 3rd+ bet in same league per bot per day

### Settlement
- Instant settlement triggered on full-time detection from live tracker (previously waited for nightly batch)
- Closing Line Value (CLV) recorded per bet for model benchmarking

---

## 2026-04-28

### Infrastructure — Railway Migration
- Pipeline moved from GitHub Actions to Railway ($5/mo) — always-on scheduler, no 12-minute job limits
- Smart live polling: 30s intervals during live matches, 60s/5min when quiet, fully automatic
- All 9 pipeline jobs (fixtures, enrichment, odds, predictions, betting, live tracker, news, settlement, pre-KO refresh) now run on Railway

### Frontend
- AI match previews published daily at 09:00 UTC (Gemini-powered)
- Email digest: subscribers receive daily match picks summary

---

## 2026-04-27

### Match List UX
- Team crests displayed on match list and detail pages
- Countdown timer to kick-off for upcoming matches
- Form strip (last 5 results) shown per team

### Track Record
- New track record page design: leads with Closing Line Value and intelligence alignment
- Bot bets shown separately from model predictions — clearer distinction
- Statistical significance progress bars: tracks milestones (30 alignment bets → 100 → 200 → 500)

---

## 2026-04-26

### Model
- XGBoost ensemble blended with Poisson model (50/50) — improved accuracy on high-variance matches
- Sharp bookmaker classification: 13 books scored by historical accuracy, feeds into signal weighting
- Dixon-Coles correction applied to home/away Poisson rates

### Signal System
- 11 signals tracked per match: odds movement, line movement, injury alerts, lineup news, form delta, ELO gap, H2H record, referee stats, situational (rest days, travel), sharp consensus, Pinnacle anchor
- Signals feed into alignment score (NONE / LOW / MED / HIGH) shown on match detail

---

## 2026-04-25

### Frontend
- Signal accordion on match detail: groups signals by category with expand/collapse
- Signal delta: shows what changed since your last visit (Pro)
- Intelligence summary card on match detail (SUX-4)
- Live in-play odds chart for Pro users during live matches
- Natural language bet explanations via Gemini (Elite tier, BET-EXPLAIN)

### Tier System
- Tier structure finalised: Free / Pro (€4.99/mo) / Elite (€14.99/mo)
- Stripe checkout, webhook, and billing portal live (test mode)
- Server-side tier gating on all Pro/Elite data — client never receives data it shouldn't have

---

## 2026-04-24

### Data Sources
- API-Football Ultra: fixtures, odds (13 bookmakers), lineups, injuries, H2H, events, player stats
- Kambi scraper: supplementary odds for 41 additional leagues
- ESPN used as settlement results backup
- Historical match data backfill: 354,000 matches across 275 leagues for model training

### Model Foundation
- Global ELO ratings: 8,385 teams
- Poisson model with 3-tier fallback (A: own history / B: league averages / C: AF predictions)
- Prediction pipeline: runs at 05:30 UTC daily, betting evaluation at 06:00 UTC
- Paper trading began — all bets logged to DB, zero real money

---

## 2026-04-20

### Launch
- OddsIntel engine repo initialised
- Public matches page live (no auth required)
- Auth: magic link OTP + Google OAuth
- Free tier: match list, signal grades, today's picks teaser
- Pro tier: full signal detail, odds movement, lineups, injuries, value bets
