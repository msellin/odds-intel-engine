# OddsIntel — Progress Tracker

> Shared between both agents working on this project.
> Last updated: 2026-04-28 (post T2–T13 integration)

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

**DONE — API-Football Predictions (T1, 2026-04-28):**
- [x] `get_prediction()` + `parse_prediction()` in api_football.py — fetches /predictions endpoint
- [x] Migration 008: `af_prediction` JSONB on matches, `af_home/draw/away_prob` + `af_agrees` on simulated_bets
- [x] Pipeline fetches predictions for all fixtures in morning run, stores on matches
- [x] Bets annotated with `af_agrees` (bool) — does AF's top pick agree with our selection?
- [x] `scripts/evaluate_af_predictions.py` — ROI split: AF-agrees vs AF-disagrees, by bot + market
- [x] Pipeline output shows AF probs per match: `[AF: H50%/D30%/A20%]`

**DONE — API-Football Integration (2026-04-28):**
- [x] API-Football Ultra ($29/mo) integrated as primary data source (75K req/day, 1236 leagues)
- [x] `workers/api_clients/api_football.py` — unified client with rate limiting, all endpoints
- [x] Fixtures: API-Football as primary, Sofascore as fallback (143+ fixtures/day with venue + referee)
- [x] Settlement: API-Football as primary (164 results/day), ESPN backup, Sofascore last resort
- [x] Odds: API-Football stores 13-bookmaker odds per match alongside Kambi + Sofascore
- [x] Post-match stats: auto-fetched from API-Football after settlement (shots, possession, corners)
- [x] `sofascore_event_id` + `api_football_id` now stored on every match for cross-referencing
- [x] Migration 007: added `api_football_id`, `venue_name`, `referee` columns to matches
- [x] `workers/scrapers/espn_results.py` — ESPN backup result source (28+ leagues, free)
- [x] Settlement date bug fixed: now processes all dates with pending bets (was today-only)
- [x] ELO/form/evaluation updates now cover yesterday + today (was today-only)
- [x] Sofascore fallback headers fixed (was getting 403), status codes fixed (100=finished)
- [x] Day 1 results: 26/32 bets settled (81%), P&L: -59.00, 4 wins / 22 losses
- [x] See `DATA_SOURCES.md` for full architecture, migration plan, alternatives evaluation

**DONE — Full API-Football Enrichment (T2–T13, 2026-04-28):**
- [x] **T2** — Team season stats: form string, home/away goal splits, clean sheet%, failed-to-score%, formations, penalty stats. Table: `team_season_stats`. Morning pipeline.
- [x] **T3** — Match injuries/suspensions: player name, status, reason, team side. Batched 20/call (~7 calls/day). Table: `match_injuries`. Morning pipeline.
- [x] **T4** — Half-time stats: all fixture stats split by first half and second half. Extended `match_stats` with `*_ht` columns. Settlement pipeline.
- [x] **T5** — Live odds in-play: every 5min during matches, stored in `odds_snapshots` with `is_live=true`. Live tracker.
- [x] **T6** — Live match data: AF `/fixtures?live=all` replaces Sofascore live polling in `live_tracker.py`. Live scores, minute, status for all matches.
- [x] **T7** — Pre-match lineups: formation, coach, starting XI. Stored as JSONB on `matches`. Live tracker fires 0-65min before KO, guarded by `lineups_fetched_at`.
- [x] **T8** — Match events (goals, cards, subs, VAR): AF sourced, replaces 0-capture Sofascore events. Table: `match_events` with `af_event_order` dedup key. Live tracker + settlement.
- [x] **T9** — League standings: full table per league/season with home/away splits. Table: `league_standings`. Morning pipeline (~40 calls/day).
- [x] **T10** — H2H history: last 10 meetings, win/draw/loss counts stored on `matches`. Morning pipeline (~130 calls/day).
- [x] **T11** — Player injury history (sidelined): full injury timeline per player. Table: `player_sidelined`. Backfill script collects from T3 injury player IDs.
- [x] **T12** — Per-player match stats: rating, goals, assists, passes, tackles, dribbles, fouls, cards. Table: `match_player_stats`. Settlement pipeline.
- [x] **T13** — Player transfers: all transfer history per team. Table: `team_transfers`. Opt-in via `--transfers` flag in backfill script.
- [x] Migration 009: 6 new tables + 4 altered existing tables. Applied 2026-04-28.
- [x] `scripts/backfill_api_football.py` — CLI tool to populate all T2–T13 data for any date
- [x] `WEB_DATA_TASKS.md` updated with all 15 frontend display tasks with DB location and tier

**DONE — Pipeline fixes (2026-04-28):**
- [x] Settlement now records results for ALL stored matches (not just bet matches) — gives complete labeled dataset for ML
- [x] Settlement uses `api_football_id` for direct result matching (no team-name lookup fragility)
- [x] Settlement post-match analytics (ELO, form, model evals, T4/T8/T12) always run even when no bets to settle
- [x] **AF as primary odds source in prediction pool** — was broken: AF odds were fetched and stored but never fed into the Poisson model. Fixed: `daily_pipeline_v2.py` now builds prediction pool from all AF fixtures with odds (~94/day), merges Kambi/SofaScore/BetExplorer on top for best-odds. Was generating 8 predictions/day; now ~94.
- [x] `_league_path_to_tier()` — resolves tier from league path for AF fixtures (was hardcoded to 0, causing bots to skip all AF matches)
- [x] `_merge_odds_sources()` refactored: AF-first, scrapers as additive best-odds layer. Single source of truth for prediction pool.
- [x] Live tracker column fix: `home_team_api_id` → `home_team_id` (DB column name mismatch)

**IN PROGRESS / NEXT (engine):**
- [ ] Validate model improvements with first 50+ settled bets via `scripts/validate_improvements.py`
- [ ] Activate alignment filter after 300+ bets show ROI correlating with alignment class
- [ ] Retrain XGBoost on API-Football accumulated data (broader league coverage)
- [ ] Historical backfill via API-Football spare quota (~73K req/day unused)
- [ ] Simplify `news_checker.py` to non-injury news only (T3 now handles injuries structurally)
- [ ] Remove redundant form functions once T2 data accumulates (see `DATA_SOURCES.md` cleanup list)

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

**DONE — Match detail enrichment (2026-04-28):**
- [x] Show scores on match detail (score_home / score_away)
- [x] Show venue + referee (MapPin + User icons in header)
- [x] Post-match stats bars (shots, shots on target, possession, corners, xG)
- [x] Multi-bookmaker odds comparison table (13 bookmakers, best highlighted)
- [x] Odds movement chart (recharts LineChart, hourly buckets)
- [x] H2H bar + recent meetings (free tier, from migration 009)
- [x] League standings + team form (free tier, from league_standings)
- [x] Injury report card (Pro — match_injuries, home/away split, player + status + reason)
- [x] Confirmed lineups card (Pro — formation view, coach, starting XI grid layout)
- [x] Team season stats (Pro — W/D/L, goals avg, clean sheet%, most used formation)
- [x] HT vs FT comparison (Pro — first half stats bars, second half derived)
- [x] Player ratings table (Pro — sorted by rating, goals, assists, per team side)
- [x] Match events timeline (Free: goals+cards, Pro: full inc. subs)
- [x] Pro section now shown for all authenticated users (was only when odds.length > 0)

**DONE — Auth gating (2026-04-28):**
- [x] /value-bets — redirects to /login if not authenticated
- [x] /track-record — redirects to /login if not authenticated
- [x] /welcome — new onboarding page for post-signup (free tier features + Pro teaser + CTAs)

**DONE — Backend scripts (2026-04-28):**
- [x] `scripts/check_bot_validation.py` (B7) — per-bot table with settled bets, ROI, CLV; exits 1 when launch threshold met
- [x] `scripts/backtest_tier_b.py` (B5) — validates Tier B league ROI, flags validated vs needs-more-data, saves JSON to data/logs/

**IN PROGRESS / NEXT (frontend):**
See `ROADMAP.md` → Milestone 2 (Pro tier) for the remaining task list.
Short version: Stripe integration + live odds in-play (#9) — everything else is built.

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
API-Football Ultra ($29/mo) → PRIMARY for ALL data: fixtures, odds, live, events, stats, lineups
  75K req/day, 1236 leagues  → 13 bookmaker odds, team stats, injuries, standings, H2H, player data
Sofascore API (free)        → Fixture fallback only (if AF misses a match)
Kambi API (free)            → Odds for 41 leagues (~122 matches/day) — supplemental
ESPN Results (free)         → Settlement backup (28 leagues)
                                            ↓
                    Python Daily Pipeline (08:00 UTC) — T2/T3/T9/T10 enrichment
                    AI News Checker (09:00 UTC) — Gemini 2.5 Flash, non-injury news
                    Settlement (21:00 UTC) — T4/T8/T12 post-match enrichment
                    Hourly Odds Snapshots (every 2h, 06-22 UTC)
                    Live Tracker (every 5min, 12-22 UTC) — T5/T6/T7/T8 live
                                            ↓
                                  Supabase Database (15 tables)
                                            ↓
                          Next.js Frontend (odds-intel-web) → Vercel (not yet deployed)
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

| Table | Notes |
|-------|-------|
| bots | 6 paper trading bots since 2026-04-27 |
| matches | Growing daily — includes venue, referee, scores, lineups, H2H |
| simulated_bets | Annotated with calibration, Kelly, AF agreement, alignment scores |
| teams / leagues | Auto-created from match data |
| odds_snapshots | 13 bookmakers per match, pre-match + live (is_live flag) |
| predictions | Model predictions with AF second-opinion |
| live_match_snapshots | AF live data — score, minute, status |
| match_events | Goals, cards, subs, VAR from API-Football (was 0 from Sofascore) |
| match_stats | Full stats + half-time splits (`*_ht` columns) |
| team_season_stats | Form, goal splits, clean sheet%, failed-to-score%, formations |
| match_injuries | Player-level injury/suspension data per fixture |
| league_standings | Full league table per league/season with home/away splits |
| player_sidelined | Player injury history timeline |
| match_player_stats | Per-player ratings, goals, assists, passes, tackles, cards (post-match) |
| team_transfers | Transfer history per team |

---

## Supabase Credentials

Both repos have `.env` files with credentials (gitignored).
Engine: `/odds-intel-engine/.env`
- `SUPABASE_URL` — project URL from Supabase dashboard → Settings → API
- `SUPABASE_SECRET_KEY` — service_role key (used by engine for writes)
- `SUPABASE_ANON_KEY` — anon/public key (used by frontend for reads)
- `API_FOOTBALL_KEY` — Ultra plan key ($29/mo, 75K req/day)
- `GEMINI_API_KEY` — Gemini 2.5 Flash for news checker
