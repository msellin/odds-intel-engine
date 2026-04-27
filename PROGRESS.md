# OddsIntel — Progress Tracker

> Shared between both agents working on this project.
> Last updated: 2026-04-27

---

## Current Status

### Engine (odds-intel-engine)

**DONE:**
- [x] Historical data: 133K soccer matches, 18 leagues, 20 seasons (football-data.co.uk)
- [x] Global ELO dataset: 1.3M matches, 216 competitions, up to 2025 (schochastics)
- [x] Soccer model: 10 iterations (v0-v10), all documented in SOCCER_FINDINGS.md
- [x] Tennis model: 11 iterations (v0-v10), all documented in TENNIS_FINDINGS.md
- [x] Feature engineering: ELO, xG proxy, form, H2H, rest days
- [x] Kambi odds scraper — now covers 41 leagues (was ~22), includes Norway, Poland, Croatia, Romania, Serbia, Ukraine, Hungary, Iceland, Latvia, Cyprus, Georgia, Portugal Liga 2
- [x] Kambi scraper expanded to ALL O/U lines: 0.5, 1.5, 2.5, 3.5, 4.5 (was 2.5 only)
- [x] Kambi live odds endpoint (fetch_live_odds()) for in-play data collection
- [x] Sofascore fixture scraper — 467 fixtures/day (flashscore.py), fallback fixed
- [x] Sofascore odds scraper (sofascore_odds.py) — 119 matches/day, 30+ leagues
- [x] Combined odds coverage: ~200 matches/day (up from ~117 Kambi-only)
- [x] Team name mapping — upgraded to rapidfuzz WRatio at threshold 85, unmatched logged
- [x] Supabase client — fixed created_at → timestamp bug (odds_snapshots was storing 0 rows)
- [x] Daily pipeline v2 — stores ALL 467 Sofascore fixtures, merges Kambi + Sofascore odds
- [x] 5 bot users created with different strategies (see daily_pipeline_v2.py)
- [x] GitHub Actions workflow for automated daily runs
- [x] All pushed to github.com/msellin/odds-intel-engine

**NEW — Live Tracking System:**
- [x] DB migration 002: sofascore_event_id on matches, minutes_to_kickoff on odds_snapshots, live_match_snapshots table, match_events table
- [x] Hourly pre-match odds snapshot job (workers/jobs/odds_snapshot.py) — builds CLV timeline
- [x] Live match tracker (workers/jobs/live_tracker.py) — runs every 5min during matches, collects: score, shots, xG, possession + live O/U 0.5–4.5 odds + match events (goals/cards)
- [x] GitHub Actions: odds_snapshots.yml (every 2h), live_tracker.yml (every 5min during match hours)

**DONE — Infrastructure:**
- [x] DB migration 002 (live tracking tables) — run in Supabase
- [x] DB migration 003 (unique constraint on simulated_bets) — run in Supabase
- [x] DB migration 004 (prediction audit trail) — run in Supabase
- [x] DB migration 005 (data quality tables) — run in Supabase
- [x] SUPABASE_SECRET_KEY and SUPABASE_URL added to GitHub repo secrets
- [x] GEMINI_API_KEY added to GitHub repo secrets
- [x] RLS public read policies added to all data tables
- [x] GitHub Actions `contents: write` permission for daily pipeline git push

**DONE — Expanded prediction coverage:**
- [x] scripts/build_global_targets.py — generates targets_global.csv (42,581 rows) from global_matches_with_elo.parquet covering 17 new leagues (Norway, Sweden, Poland, Romania, Serbia, Ukraine, Turkey, Greece, Croatia, Denmark, Iceland, Hungary, Bulgaria, Cyprus, Georgia, Latvia, Portugal)
- [x] compute_prediction() refactored with 3-tier fallback: Tier A (targets_v9, 512 teams, full odds calibration) → Tier B (targets_global, 469 new teams, results only) → Tier C (Sofascore on-demand API)
- [x] Tiered bet sizing: Tier A = full stake; Tier B = 50% stake + 2% extra edge req; Tier C = 25% stake + 5% extra edge req
- [x] Coverage improved from 8% (10/122) to 92% (112/122) on Kambi matches
- [x] Settlement pipeline (workers/jobs/settlement.py) — fuzzy match results, settle 1X2 + all O/U lines, compute CLV, update bot bankrolls, --report mode
- [x] AI news checker (workers/jobs/news_checker.py) — Gemini 2.5 Flash flags bets with injury/suspension/lineup intel
- [x] GitHub Actions: news_checker.yml (09:00 UTC), settlement wired into daily_pipeline.yml (21:00 UTC)

**DONE — Model Improvements (P1-P4):**
- [x] P1: Tier-specific calibration — `calibrate_prob()` blends model prob with market (α varies: T1=0.55, T2=0.65, T3=0.80, T4=0.85)
- [x] P2: Odds movement — `compute_odds_movement()` queries snapshots for drift/velocity, soft penalty on Kelly, hard veto >10%
- [x] P3: Alignment filter (LOG-ONLY) — 4 external-signal dimensions (odds_move, news, lineup, situational), stored but doesn't filter yet
- [x] P4: Kelly stake sizing — 1/4 Kelly, 1.5% max cap, data-tier multiplier, odds movement penalty
- [x] Migration 006: 11 new columns on simulated_bets (calibrated_prob, kelly_fraction, dimension_scores, alignment_class, odds_drift, etc.)
- [x] Validation script: `scripts/validate_improvements.py` — calibration ECE, ROI by alignment, CLV, Kelly vs flat Sharpe ratio
- [x] Pipeline fully integrated: `daily_pipeline_v2.py` runs P1→P2→P3→P4 flow

**IN PROGRESS / NEXT:**
- [ ] Run migration 006 in Supabase (manual step)
- [ ] Validate improvements with first 50+ settled bets via `validate_improvements.py`
- [ ] Activate alignment filter after 300+ bets show ROI correlating with alignment class
- [ ] Tier B backtest: run Poisson model against targets_global.csv (42K matches)
- [ ] OddsPortal scraper — to reach 80%+ daily match odds coverage (currently 43%)
- [ ] O/U 0.5 / 1.5 / 3.5 backtests

### Frontend (odds-intel-web)

**DONE:**
- [x] All pages built (landing, matches, match detail, value-bets, track-record, profile)
- [x] Supabase schema deployed
- [x] Tier gating system (TierGate component, blurred pro teaser for free users)
- [x] Supabase Auth — full login/signup with email/password, middleware route protection
- [x] Real match data from Supabase — `getPublicMatches()`, `getTodayOdds()`, `getPublicMatchById()`
- [x] Real bot performance — track record page connected to `simulated_bets` table
- [x] Public matches page — works without login, smart sort (odds first), dual layout, view toggle
- [x] Public match detail — best odds + pro teaser for free users, full odds for auth users
- [x] "All matches / With odds only" view toggle
- [x] Match interest indicators (hot/warm/neutral)
- [x] RLS public read policies on all data tables

**IN PROGRESS / NEXT:**
- [ ] Stripe integration (Pro €19/mo, Elite €49/mo)
- [ ] Deploy to Vercel
- [ ] Live score display during matches
- [ ] Onboarding flow (post-signup)

---

## Key Findings (Both Sports)

Both soccer and tennis models show the SAME pattern:
- Pure stats models are 2-5% ROI short of profitability
- Bookmakers already price in ELO, form, serve stats, etc.
- The gap can ONLY be closed by real-time information (injuries, lineups, news)
- Lower-tier events (soccer tier 3-4, tennis ATP 250) are softer markets
- Selectivity (fewer, higher-edge bets) consistently improves ROI

**The real edge = SPEED, not better stats.** Getting injury/lineup news 1-2 hours before odds adjust is where professional bettors make their 3-8% ROI.

**In-play value pattern (to validate with live data):**
High-xG game, 0-0 at minute 10-15 → O/U 0.5/1.5 odds drift upward → potential value if underlying model still says goals are likely. Live tracker is now collecting this data.

---

## Architecture

```
Sofascore API (free)   → ALL fixtures (467/day) + live match stats + events
Kambi API (free)       → Odds for 41 leagues (~122 matches/day)
Sofascore odds API     → Odds for 30+ leagues (~119 matches/day)
Combined               → ~200 matches/day with odds
                              ↓
                    Python Daily Pipeline (08:00 UTC)
                    Hourly Odds Snapshots (every 2h, 06-22 UTC)
                    Live Tracker (every 5min, 12-22 UTC)
                              ↓
                      Supabase Database
                              ↓
                  Next.js Frontend (Vercel) — in progress
```

---

## Coverage Reality Check

| Stage | Count | Notes |
|-------|-------|-------|
| Sofascore fixtures | 467/day | All football worldwide |
| With odds (Kambi + Sofascore) | ~200/day | 43% coverage |
| In leagues with historical data | ~50-100/day | Depends on day — weekends much better |
| With model prediction possible | ~50-100/day | Need team in targets_v9.csv |
| With edge > threshold → bets | ~5-20/day | Depends on model calibration |

**Important:** Today (2026-04-27, Monday) is a low-European-football day. On weekends, the 10 European leagues we have data for produce 50-80 matches → much more bot activity.

---

## Historical Data Available

| Source | Matches | Leagues | Odds? | Notes |
|--------|---------|---------|-------|-------|
| football-data.co.uk (targets_v9) | 96K | 18 | Yes (O/U 2.5 + 1X2) | Core prediction dataset |
| all_matches.csv | 133K | 18 | Yes (B365, Avg) | Same source, more columns |
| global_matches_with_elo.parquet | 1.3M | 216 | No (results only) | Has results for Norway/Sweden/Poland/Romania/Serbia/Ukraine/Turkey/Greece |

**Key insight:** Norway, Sweden, Poland, Romania, Serbia, Ukraine, Turkey, Greece all exist in global_matches_with_elo.parquet with 1,900–3,600 matches each since 2015. We could build targets CSVs for these leagues to enable predictions. This would immediately unlock betting on those leagues where we now have odds.

---

## Data in Supabase (live)

| Table | Rows | Notes |
|-------|------|-------|
| bots | 5 | Different strategies |
| matches | 19+ | Growing daily |
| simulated_bets | 10+ | Pending bets across 4 bots |
| teams | ~30+ | Auto-created from match data |
| leagues | ~15+ | Auto-created from match data |
| odds_snapshots | ~0→growing | Column bug fixed, now storing correctly |
| predictions | ~0→growing | Bug fixed |
| live_match_snapshots | 0 | New table — needs migration 002 + GH secrets |
| match_events | 0 | New table — needs migration 002 + GH secrets |

---

## Supabase Credentials

Both repos have .env files with credentials (gitignored).
Engine: /odds-intel-engine/.env
SUPABASE_SECRET_KEY = service_role key from Supabase dashboard → Settings → API
