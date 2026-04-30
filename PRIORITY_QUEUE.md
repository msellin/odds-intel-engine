# OddsIntel — Master Priority Queue

> Single source of truth for ALL open tasks. Every actionable item across all docs lives here.
> Other docs may describe features but ONLY this file tracks task status.
> Last updated: 2026-04-30 — LIVE-INFRA Phase 1 complete: scheduler.py (21 jobs), BudgetTracker, Dockerfile, job refactoring. RAIL-1 through RAIL-4 done. Railway project created + env vars set.

---

## Tier 0 — Do This Week (foundation for everything)

| # | ID | Task | Effort | Status | Impact | Source | Timeline | Notes |
|---|-----|------|--------|--------|--------|--------|----------|-------|
| 1 | B-ML1 | Pseudo-CLV for all ~280 daily matches | 2-3h | ✅ Done | Very High | Internal | Done | `(1/open) / (1/close) - 1` for every finished match. Grows ML training data 280/day |
| 2 | B-ML2 | `match_feature_vectors` nightly ETL (wide ML training table) | 1 day | ✅ Done | Very High | Internal | Done | Pivots signals + predictions + ELO/form → wide row per match |
| 3 | CAL-1 | Calibration validation script | 2h | ✅ Done | High | Internal | Done | `scripts/check_calibration.py` — predicted vs actual win rate in 5% bins |
| 4 | S1+S2 | Migration 010: `source` on predictions + `match_signals` table | 2-3h | ✅ Done | Very High | Internal | Done | Unique constraint on (match_id, market, source). Append-only signal store |
| 5 | CAL-2 | Flip calibration α: T1→0.20, T2→0.30, T3→0.50, T4→0.65 | 30 min | ✅ Done 2026-04-29 | **Very High** | AI Analysis (2026-04-28) | Done | CALIBRATION_ALPHA updated in improvements.py. Was T1=0.55 (model-heavy in efficient markets) — now T1=0.20 (market-heavy) |
| 6 | RISK-1 | Reduce Kelly fraction to 0.15×, cap to 1% bankroll per bet | 15 min | ✅ Done 2026-04-29 | **Very High** | AI Analysis (2026-04-28) | Done | KELLY_FRACTION 0.25→0.15, MAX_STAKE_PCT 0.015→0.010 in improvements.py |
| 7 | LLM-RESOLVE | Run `scripts/resolve_team_names.py --apply` and validate output | 30 min | ✅ Done 2026-04-29 | High | Internal (MODEL_ANALYSIS 11.2) | Done | 3 new mappings added (Brondby→Brøndby, Dinamo Bucuresti→Dinamo Bucureşti, IFK Goeteborg→IFK Göteborg). 140 existing + 3 = 143 total. 204 unmatched names now all accounted for |

---

## Tier 1 — Next 1-2 Weeks

| # | ID | Task | Effort | Status | Impact | Source | Timeline | Notes |
|---|-----|------|--------|--------|--------|--------|----------|-------|
| 8 | S3 | Wire existing signals into match_signals | 1 day | ✅ Done | Very High | Internal | Done | Opening odds, ELO, form, injuries, BDM-1, fixture importance, referee avg, news_impact |
| 9 | S4 | Referee signals (referee_stats table + daily enrichment) | 1 day | ✅ Done | High | Internal | Done | Migration 011. Morning pipeline writes referee_cards_avg |
| 10 | S5 | Fixture importance signal (standings → 0-1 urgency score) | <2h | ✅ Done | High | Internal | Done | compute_fixture_importance() from league_standings |
| — | S3b | Standings signals: league_position, points_to_relegation/title | 1h | ✅ Done | High | Internal | Done | Normalised rank + points gap signals, home+away |
| — | S3c | H2H signal: h2h_win_pct | <1h | ✅ Done | Medium | Internal | Done | h2h_home_wins/total, min 3 meetings |
| — | S3d | Referee home_win_pct + over25_pct | <1h | ✅ Done | Medium | Internal | Done | From referee_stats; needs ≥3 matches/ref to populate |
| — | S3e | Overnight line move signal | 1h | ✅ Done | High | Internal | Done | yesterday-last vs today-first implied prob delta |
| — | S3f | Rest days home/away | 1h | ✅ Done | Medium | Internal | Done | Days since each team's last finished match |
| — | S1-AF | Store AF prediction as predictions rows source='af' | <1h | ✅ Done | High | Internal | Done | _fetch_af_predictions stores 1x2_home/draw/away with source='af' |
| — | T2-scoped | Re-enable T2 team stats for Tier A only | 1h | ✅ Done | High | Internal | Done | Batch tier check; goals_for/against_avg wired as signals |
| 11 | SIG-7 | Importance asymmetry: `fixture_importance_home/away` + `importance_diff` | 30 min | ✅ Done 2026-04-29 | Medium | AI Analysis (2026-04-28) | Done | Per-team urgency from standings (0.10–0.85 scale) + diff stored in match_signals |
| 12 | SIG-8 | Home/away venue splits from T2: `goals_for/against_venue_home/away` | 1h | ✅ Done 2026-04-29 | Medium | AI Analysis (2026-04-28) | Done | goals_for_home/played_home for home team, goals_for_away/played_away for away team. Min 3 games played |
| 13 | SIG-9 | Form slope: PPG(last 5) − PPG(prior 5) per team | 1h | ✅ Done 2026-04-29 | Medium | AI Analysis (2026-04-28) | Done | `form_slope_home/away` — rising vs falling form. Needs ≥6 historical matches per team |
| 14 | SIG-10 | Odds volatility: std dev of home implied prob over last 24h | 1h | ✅ Done 2026-04-29 | Medium | AI Analysis (2026-04-28) | Done | `odds_volatility` — needs ≥3 snapshots in 24h window. High = market uncertain |
| 15 | SIG-11 | League meta-features: home_win_pct, draw_pct, avg_goals per league | 1h | ✅ Done 2026-04-29 | Medium | AI Analysis (2026-04-28) | Done | `league_home_win_pct`, `league_draw_pct`, `league_avg_goals` — last 200 finished matches per league. Needs ≥20 matches |
| 16 | META-2 | Meta-model feature design: drop raw fundamentals, keep market structure features | 2h design | ✅ Done 2026-04-29 | High | AI Analysis (2026-04-28) | Done | Features: `edge` (ensemble_prob−market_implied), `odds_drift`, `bookmaker_disagreement`, `overnight_line_move`, `model_disagreement`, `league_tier`, `news_impact_score`, `odds_volatility`. NOT ELO/form — market already priced those |
| — | PIPE-1 | Clean pipeline: 9 single-purpose jobs replacing monolith | 1 day | ✅ Done 2026-04-29 | **Very High** | Data Analysis (2026-04-29) | Done | ①Fixtures(04:00) ②Enrichment(04:15/12/16) ③Odds(2h) ④Predictions(05:30) ⑤Betting(06:00) ⑥Live ⑦News ⑧Settlement. Removed Sofascore+BetExplorer. 192 matches with odds. Migration 014+015 |
| 17 | B-ML3 | First meta-model: 8-feature logistic regression, target=pseudo_clv>0 | 1 day | ⬜ | Very High | Internal | ~May 9 | Train after ~3000+ pseudo-CLV rows. Features per META-2 design. See MODEL_ANALYSIS.md Stage 4 |
| 18 | STRIPE | Stripe setup: Pro €4.99/mo + Elite €14.99/mo products, keys to Vercel | External | ✅ Done 2026-04-29 | High | Internal | Done | Products + 6 price IDs created in Stripe test mode. Keys in .env + Vercel (Production). |
| — | F8 | Stripe frontend: checkout, webhook, portal, tier gating | 2-3 days | ✅ Done 2026-04-29 | High | Internal | Done | Checkout API, webhook handler, portal API, profile upgrade buttons, value-bets Elite gate, Pro→Elite upgrade flow, founding cap (500 Pro / 200 Elite auto-enforced), middleware fix for value-bets + track-record |
| — | STRIPE-WEBHOOK-URL | Fix Stripe webhook 301 redirect (www vs bare domain) | 5 min | ✅ Done 2026-04-29 | High | Internal | Done | Vercel redirects oddsintel.app → www.oddsintel.app with 301. Stripe doesn't follow redirects — webhook was silently failing. Updated endpoint to https://www.oddsintel.app/api/stripe/webhook. |
| 19 | B3 | Tier-aware data API (Next.js layer strips fields by tier) | 1-2 days | ✅ Done 2026-04-29 | High | Internal | Done | **Unblocked Milestone 2.** profiles.tier checked server-side in matches/[id]/page.tsx. Pro data (oddsMovement, events, lineups, stats, injuries detail) only fetched + passed to components when isPro. Free/anon never receive pro data in payload. CTAs in MatchDetailFree context-aware (signup vs upgrade). |
| — | SUPABASE-PRO | Upgrade Supabase to Pro ($25/mo) | 15 min | ✅ Done 2026-04-29 | High | Infrastructure | Done | PITR + daily backups active. 8 GB DB limit. ~€23/mo. |
| — | LEAGUE-DEDUP | Deduplicate Kambi/AF leagues, add priority sorting, fix ensure_league() | 2-3h | ✅ Done 2026-04-30 | **Critical** | Launch blocker (2026-04-30) | Done | Migration 025: merged ~70 Kambi duplicate leagues into AF counterparts (moved matches, deleted orphans). Added `priority` column (10=top leagues/cups, 20=major secondary, 30=notable). Fixed `ensure_league()` with KAMBI_TO_AF_LEAGUE mapping dict (~55 entries). Frontend: sort by priority then alphabetical (like FlashScore). Europa/Conference League games now visible. Set `show_on_frontend=true` on merged AF leagues (UCL, UEL, UECL, Libertadores, etc). Cleaned up ~1100 zero-match orphan leagues. |
| — | STRIPE-PROD | Swap Stripe to production keys (5-step checklist in INFRASTRUCTURE.md) | 1h | ⬜ | High | Infrastructure | After Supabase Pro | 1) Switch to live mode 2) Re-run setup_stripe.py with live key 3) Update all Vercel STRIPE_* env vars 4) New live webhook endpoint + new whsec_ in Vercel 5) Supabase Pro must be done first |
| — | STRIPE-ANNUAL | Add annual billing option to profile page + landing page CTA | 2-3h | ✅ Done 2026-04-29 | Medium | Internal | Done | Monthly/annual toggle on profile upgrade buttons (swaps priceId to annual) and landing page pricing cards (updates displayed prices). Pro €39.99/yr, Elite €119.99/yr. BillingToggle + PricingCards components. |
| — | STRIPE-EMAIL | Transactional email via Resend (welcome + payment receipt) | 1 day | ⬜ | Medium | Infrastructure | Milestone 2 | Resend free to 3K/mo. Welcome email on signup, payment receipt on checkout.session.completed. Re-engagement loop. |
| 20 | SENTRY | Sentry error monitoring (free tier) | 1h | ✅ Done | Medium | Internal | Done | @sentry/nextjs wired in frontend, DSN configured |

---

## Signal UX — Phase 1 (no blockers, signal data already exists)

> From 4 independent UX/product reviews (2026-04-29). Full strategy in SIGNAL_UX_ROADMAP.md.

| # | ID | Task | Effort | Status | Impact | Source | Timeline | Notes |
|---|-----|------|--------|--------|--------|--------|----------|-------|
| 58 | SUX-1 | Match Intelligence Score: signal count + A/B/C/D grade on every match card | 1-2 days | ✅ Done 2026-04-29 | High | UX Review (2026-04-29) | Done | Grade badge (A=xgboost, B=poisson, D=af-only) on every match row. Signal count in tooltip. All tiers see this. batchFetchSignalSummary() in engine-data.ts |
| 59 | SUX-2 | Match Pulse composite indicator (Routine/Interesting/High Alert) | 4h | ✅ Done 2026-04-29 | High | UX Review (2026-04-29) | Done | ⚡ badge on high-alert matches (bdm>0.12 + olm/vol threshold). ~15-20% scarcity preserved. Derived from bookmaker_disagreement, overnight_line_move, odds_volatility, importance_diff |
| 60 | SUX-3 | Free-tier signal teasers on notable matches | 4h | ✅ Done 2026-04-29 | Medium | UX Review (2026-04-29) | Done | 1-2 italic hooks on 30-40% of matches below team names. "High bookmaker disagreement", "Odds shifted overnight", "Key injury news detected", etc. No raw numbers |

---

## Tier 2 — 2-4 Weeks

| # | ID | Task | Effort | Status | Impact | Source | Timeline | Notes |
|---|-----|------|--------|--------|--------|--------|----------|-------|
| 21 | MOD-1 | Dixon-Coles correction to Poisson model | 4h | ✅ Done 2026-04-29 | **High** | AI Analysis (2026-04-28) | Done | `DIXON_COLES_RHO=-0.13` applied in `_poisson_probs()`. τ correction for 0-0/1-0/0-1/1-1, 1x2 renormalised. Takes effect in tomorrow's 08:00 UTC pipeline |
| 22 | PLATT | Platt scaling once 500+ predictions have outcomes | 1 day | ✅ Done 2026-04-30 | High | Internal | Done | Sigmoid post-hoc calibration per market. `scripts/fit_platt.py` fits α/β from settled predictions → `model_calibration` table. Pipeline applies after tier shrinkage in `calibrate_prob()`. Weekly recalibration via settlement workflow (Sundays). |
| 23 | P5.1 | European Soccer DB (Kaggle): 13-bookmaker sharp/soft analysis | 1-2 days | ⬜ | High | Internal | ~May 2026 | `bookmaker_sharpness_rankings.csv` + `sharp_money_signal` feature |
| 24 | PIN-1 | Pinnacle anchor signal: `model_prob - pinnacle_implied` as feature | 2-3h | ⬜ | High | Internal | ~May 2026 | Depends on P5.1 to confirm Pinnacle is in our 13 bookmakers |
| 25 | BDM-1 | Bookmaker disagreement signal | 1h | ✅ Done | Medium | Internal | Done | compute_bookmaker_disagreement() written to match_signals |
| 26 | FE-LIVE | Live odds in-play on match detail (frontend only) | 1 day | ✅ Done 2026-04-29 | Medium | ROADMAP Frontend Backlog #9 | Done | getLiveMatchOdds() fetches is_live=true odds_snapshots by match minute. LiveOddsChart: recharts 1X2 lines by match minute + current best odds row. Polls /api/live-odds every 5min during live matches. GET /api/live-odds: Pro-gated API route. Shown for live + finished matches on match detail. |
| — | ODDS-OU-CHART | O/U 2.5 movement chart on match detail (Pro) | 2-3h | ✅ Done 2026-04-29 | Medium | Data audit 2026-04-29 | Done | Purple/orange Over/Under line chart below 1X2 chart. getOddsMovement() now fetches both 1x2 and over_under_25 markets. |
| — | ODDS-BTTS | BTTS odds per bookmaker in Pro match detail | 2-3h | ✅ Done 2026-04-29 | Medium | Data audit 2026-04-29 | Done | BTTS Yes/No columns added to main odds comparison table. Best odds highlighted green. Data from `btts` market in odds_snapshots. |
| — | ODDS-MARKETS | Show O/U 1.5 and O/U 3.5 lines in Pro odds table | 1-2h | ✅ Done 2026-04-29 | Low | Data audit 2026-04-29 | Done | Separate "Over/Under Lines" card with O/U 1.5 and O/U 3.5 per bookmaker. Only renders when data exists. |
| 27 | MKT-STR | Wire market-implied team strength into XGBoost as input feature | 1 day | ✅ Done 2026-04-29 | Medium | Internal (MODEL_ANALYSIS 11.3) | Done | `market_implied_home/draw/away` signals already stored in match_signals (write_morning_signals lines 1769-1780). Added extraction in `_build_feature_row()` signal loop + added to return dict. Migration 019 adds columns to match_feature_vectors. |
| 28 | EXPOSURE-AUTO | Auto-reduce stakes on league exposure concentration | 1h | ✅ Done 2026-04-29 | Medium | Internal (MODEL_ANALYSIS 11.6) | Done | 3rd+ bet in same league per bot gets 50% stake reduction. Enforced during placement in daily_pipeline_v2.py. _check_exposure_concentration() still runs as post-placement audit log. |
| — | LIVE-FIX | Fix live tracker: populate xG, shots, possession, corners, red cards in snapshots | 1h | ✅ Done 2026-04-30 | **Critical** | Internal (2026-04-30) | Done | Snapshots were empty (only minute + score). Added `/fixtures/statistics` call per live match (1 extra API call, ~240/day). Parses xG, shots, SoT, possession, corners into snapshot. Derives red card state from events. Loads pre-match model context (O/U 2.5 prob). Every day without this was wasted in-play training data. |
| — | BOTS-EXPAND | Add 6 new bots: BTTS, O/U 1.5/3.5, draw specialist, O/U 2.5 global | 2h | ✅ Done 2026-04-30 | **High** | Internal (2026-04-30) | Done | Poisson extended with BTTS + O/U 1.5 + O/U 3.5 probs. Draw selection wired into 1X2 candidate specs. 10→16 bots. Target: ~30-40 bets/day (was ~10) to accelerate ALN-1 from ~27 days to ~9-10 days. New bots: bot_btts_all, bot_btts_conservative, bot_ou15_defensive, bot_ou35_attacking, bot_ou25_global, bot_draw_specialist. |
| — | KAMBI-BTTS | Add O/U + BTTS odds from Kambi event endpoint | 1h | ✅ Done 2026-04-30 | **High** | Internal (2026-04-30) | Done | `listView` only returned 1X2. Added `betoffer/event/{id}` enrichment for mapped-league events (~44 per operator). Parses O/U 0.5-4.5 + BTTS ("Yes/No" with "Both Teams To Score" criterion). ~40 matches now have BTTS from Kambi. `store_odds` updated to store BTTS rows. |
| — | BET-MULTI | Run betting pipeline 5x/day instead of 1x | 30min | ✅ Done 2026-04-30 | **High** | Internal (2026-04-30) | Done | Cron: 06:00, 10:00, 13:00, 16:00, 19:00 UTC. Catches all European kickoff windows. Duplicate bets prevented by `uq_bet_per_bot_match_market_selection` unique constraint — fully idempotent. New bots were getting zero bets because the 06:00 run only saw matches that had already kicked off. |
| 29 | F8 | Stripe integration (Pro + Elite, webhook, tier column update) | 2-3 days | ✅ Done 2026-04-29 | High | Internal | Done | See Tier 1 row — full breakdown there. |
| — | LP-1 | Landing page: fix strikethrough pricing | 15 min | ✅ Done 2026-04-29 | Low | Landing Page Review (2026-04-29) | Done | No strikethrough was present — cards already show badge-only. Verified. |
| — | LP-2 | Landing page: remove Elite annual pricing | 15 min | ✅ Done 2026-04-29 | Low | Landing Page Review (2026-04-29) | Done | Elite card never had annual pricing shown. Verified. |
| — | LP-3 | Landing page: consolidate Founding Member urgency | 15 min | ✅ Done 2026-04-29 | Low | Landing Page Review (2026-04-29) | Done | Removed bottom banner. Card badges are single source of truth now. |

| — | TR-REDESIGN | Track record page redesign: CLV-led, tier-gated, no bankroll sim | 1 day | ✅ Done 2026-04-30 | **Very High** | 4-AI UX Review (2026-04-30) | Done | Removed LayeredSimulation (4 declining bankroll curves). New hero: CLV, value bets, coverage. CLV education, system status, significance progress bar. Early results collapsible. Prediction history tier-gated (free: 20 rows + blurred Pro/Elite columns, Pro: full + CLV, Elite: + edge %). Feature comparison table. Tiered today's picks. Footer CTA. Based on 8 independent AI reviews (4 page structure + 4 tier gating). |

---

## Railway Migration — LIVE-INFRA (promoted from Tier 5)

> Full architecture migration: GitHub Actions → Railway long-running process + direct PostgreSQL + tiered live polling.
> 5 phases, ~10 days. See § RAILWAY Plan for full details.

| # | ID | Task | Effort | Status | Impact | Source | Timeline | Notes |
|---|-----|------|--------|--------|--------|--------|----------|-------|
| — | LIVE-INFRA | Railway migration: long-running scheduler + direct SQL + tiered live polling | 10 days | 🔄 In Progress | **Very High** | Infra Analysis (2026-04-30) | ~May 2026 | Phase 1 code complete (2026-04-30). Railway project created, service linked, env vars set. See § RAILWAY Plan. |
| — | RAIL-1 | Create `workers/scheduler.py` (APScheduler + health endpoint + SIGTERM) | 1 day | ✅ Done 2026-04-30 | High | LIVE-INFRA Phase 1 | Done | 21 jobs registered. Morning pipeline chained. Settlement pipeline chained (incl. Platt Sundays). Budget sync hourly. Health on :8080. |
| — | RAIL-2 | Refactor job scripts: extract `run_*()` from all `main()` functions | 4h | ✅ Done 2026-04-30 | High | LIVE-INFRA Phase 1 | Done | run_fixtures(), run_enrichment(), run_odds(), run_predictions() extracted. main() kept as CLI wrapper. |
| — | RAIL-3 | API budget tracker in `api_football.py` | 2h | ✅ Done 2026-04-30 | Medium | LIVE-INFRA Phase 1 | Done | BudgetTracker class: thread-safe call counting, midnight reset, can_call(), sync_with_server(), status() for health endpoint. Integrated into _get(). |
| — | RAIL-4 | Deployment files: Dockerfile, railway.toml, .dockerignore | 1h | ✅ Done 2026-04-30 | High | LIVE-INFRA Phase 1 | Done | Python 3.12-slim, TZ=UTC, PYTHONUNBUFFERED=1. Health check path /health. |
| — | RAIL-5 | Deploy Railway + shadow mode validation (2-3 days parallel run) | 2h + wait | ⬜ | High | LIVE-INFRA Phase 1 | — | `SHADOW_MODE=true` prefixes job names in pipeline_runs. Compare Railway vs GH Actions output for 2-3 days. |
| — | RAIL-6 | Disable GH Actions crons (keep workflow_dispatch for fallback) | 30min | ⬜ | Medium | LIVE-INFRA Phase 1 | — | Comment out `schedule:` in 7 workflow files. Keep backfill.yml unchanged. |
| — | RAIL-7 | Create `workers/api_clients/db.py` (psycopg2 connection pool) | 4h | ⬜ | **Very High** | LIVE-INFRA Phase 2 | — | ThreadedConnectionPool, execute_query(), bulk_insert(). Replaces PostgREST for live ops. Eliminates 1K row cap, enables JOINs, 10-50x faster bulk writes. |
| — | RAIL-8 | Migrate live tracker DB functions to direct SQL | 1 day | ⬜ | **Very High** | LIVE-INFRA Phase 2 | — | 6 functions: store_live_snapshot (batched), store_live_odds (batched), store_match_events_af, update_match_status, _build_af_id_map (no 1K limit), get_match_by_teams_and_date. |
| — | RAIL-9 | Create `workers/live_poller.py` (tiered 15s/60s/5min polling) | 1-2 days | ⬜ | **Very High** | LIVE-INFRA Phase 3 | — | Fast (15s): bulk fixtures+odds. Medium (60s): per-match stats+events. Slow (5min): lineups+match map refresh. ~14K-20K AF calls/day. |
| — | RAIL-10 | Decompose `live_tracker.py` into sub-functions | 4h | ⬜ | High | LIVE-INFRA Phase 3 | — | fetch_live_bulk(), fetch_match_stats(), fetch_match_events(), build_snapshot(), store_snapshot_batch(). Keep run_live_tracker() wrapper. |
| — | RAIL-11 | Smart polling: priority tiers + event-triggered snapshots | 1 day | ⬜ | High | LIVE-INFRA Phase 4 | — | HIGH (active bet, 30s stats) / NORMAL (60s) / LOW (5min). Instant odds snapshot on goal/red card detection. |
| — | RAIL-12 | Update WORKFLOWS.md, INFRASTRUCTURE.md, AF-EVAL notes | 1h | ⬜ | Medium | LIVE-INFRA Phase 5 | — | Railway in service stack, $59/mo total, 24% AF budget, GH Actions manual-only. |

---

## Frontend UX — Completed (2026-04-29)

> Full UX pass completed this session. All items below are done and pushed to main.

| ID | Task | Notes |
|----|------|-------|
| LP-0 | Landing page full rewrite | New headline, product mockup, pricing before comparison table, FAQ, trust stats, 23 items |
| A-1/A-2/A-3 | Profile page redesign | Dynamic starred leagues, auto-save, quick-add popular leagues |
| A-4 | My Matches empty state copy | Clearer call to action matching new profile language |
| B-1 | Model accuracy component | Public, all users, no login required |
| B-2 | Track record login gate removed | `/track-record` is now fully public |
| B-3 | Confidence tier filter | All / Confident 50%+ / Strong 60%+ — stats update per filter |
| B-4/B-5 | Confidence tooltip + explanation banner | Explains statistical confidence vs value bet edge |
| B-6 | `/how-it-works` page | Model explanation, 58 signals breakdown, correct tier info (Pro=match intel, Elite=value bets), FAQ |
| C-1 | Date tooltip on matches page | "Date picker coming soon" hint |
| C-2 | Odds column H/X/A header + tooltip | Decimal odds explained, best-value highlighting |
| C-3 | Match detail tooltips | Best Odds (decimal explained), Data Coverage grade (A/B/C/D), Interest indicator (🔥/⚡/—) |
| C-4 | My Picks empty state | Explains exactly how to make a pick, teaser about hit rate comparison |
| C-5 | Edge % tooltip on value bets | Model prob minus implied prob, colour-coded examples |
| C-6 | Value bets gate | Blurred preview + feature explanation + sign-in modal trigger |
| BONUS | Login modal system | `openLoginModal()` from anywhere via AuthContext, renders in app layout |
| BONUS | Signup banner uses modal | Matches page sign-up CTA triggers modal instead of navigating away |
| 30 | F5 | Value bets page redesign (free=teaser, Pro=directional, Elite=full picks) | 1-2 days | ✅ Done 2026-04-29 | High | Internal | Done | Free: count + edge stats + blurred preview + upgrade CTA. Pro: directional view (match+selection+edge tier, no exact %). Elite: full table with odds/model prob/stake. ValueBetsLive now accepts userTier prop. |
| 31 | ALN-1 | Dynamic alignment thresholds (300+ settled bot bets → ROI by alignment bin) | 2h | ⬜ | High | Internal | ~June 2026 | Needs actual placed bets — pseudo-CLV does NOT substitute |
| 32 | VAL-POST-MORTEM | Review 14 days of LLM post-mortem patterns | 30 min | ⬜ | Medium | Internal (MODEL_ANALYSIS 11.4) | May 13+ | `SELECT notes FROM model_evaluations WHERE market = 'post_mortem' ORDER BY date DESC LIMIT 14;` — check if loss categories consistent. Decides if post-mortem feature is valuable |
| 33 | BET-EXPLAIN | Natural language bet explanations (LLM from dimension scores) | 1-2 days | ✅ Done 2026-04-29 | Medium | Internal (MODEL_ANALYSIS end) | Done | GET /api/bet-explain: Elite-gated, fetches bet+signals, Gemini 2.0 Flash generates 2-3 sentence explanation. BetExplainButton: on-demand "Why this pick?" collapsible. Added to Elite value bets table + mobile cards. NOTE: Add GEMINI_API_KEY to Vercel env vars (Production). |
| 61 | SUX-4 | Summary tab on match detail: top 3-5 key signals in plain English | 1-2 days | ✅ Done 2026-04-29 | High | UX Review (2026-04-29) | Done | MatchSignalSummary component. getMatchSignals() fetches all signals for match. Free: 1 teaser + lock banner. Pro/Elite: top 5 prioritised signals with icons, severity dots, plain-English descriptions. Rendered on all match detail pages when signals exist. |
| 62 | SUX-5 | Signal group accordion sections on match detail | 2-3 days | ✅ Done 2026-04-29 | High | UX Review (2026-04-29) | Done | SignalAccordion component. 4 collapsible sections: Market Signals (BDM/OLM/vol/implied), Team Quality (ELO/form/H2H/rest), Context (importance/referee/league meta), News & Injuries. Market open by default. Pro: full data + descriptions. Free: locked structure with count badges + Pro CTA. |
| 63 | SUX-6 | Plain-English signal translation layer | 1 day | ✅ Done 2026-04-29 | Medium | UX Review (2026-04-29) | Done | src/lib/signal-labels.ts — 12 typed label functions (formSlopeLabel, oddsVolatilityLabel, overnightMoveLabel, bookmakerDisagreementLabel, fixtureImportanceLabel, importanceDiffLabel, newsImpactLabel, injuryCountLabel, refereeCardsLabel, h2hEdgeLabel, eloStrengthLabel/Diff, leagueAvgGoalsLabel). signalLabel() consolidated entry point. SignalLabel type with label/icon/severity/description. |
| 64 | SUX-7 | Signal-based conversion hooks (Free→Pro, Pro→Elite) | 1 day | ✅ Done 2026-04-29 | High | UX Review (2026-04-29) | Done | Free→Pro: "N more signals on Pro" lock in summary card. Pro→Elite: model conclusion lock ("model analysed X signals — see full probability breakdown"). Signal divergence alert: amber banner when overnight move conflicts with form trend, or bookmakers deeply disagree. |
| 65 | SUX-8 | Signal Timeline component on match detail | 2-3 days | ✅ Done 2026-04-29 | Medium | UX Review (2026-04-29) | Done | `signal-timeline.tsx` Pro/Elite only. `getMatchSignalHistory()` fetches all captures ordered asc. Groups by hour bucket. Shows time dot + signal name + value per group. "Upcoming" marker with next run estimate (+2h from last capture). Rendered in match detail page when signalHistory.length > 0 and isPro. |
| 66 | SUX-9 | Signal Delta — "what changed since last visit" | 1 day | ✅ Done 2026-04-29 | Medium | UX Review (2026-04-29) | Done | SignalDelta component. localStorage tracks last-visited per match. On return: compares signal captured_at vs stored timestamp. Dismissable sky banner: "N signals updated since your last visit · Xh ago" + tag-style badges per signal. Pro only. |
| 67 | SUX-10 | Post-match signal reveal for Free users | 4h | ✅ Done 2026-04-29 | Medium | UX Review (2026-04-29) | Done | On finished matches, Free users see "Signal Reveal" card instead of upgrade teaser. Plain-English retrospective: what signals detected (sharp move, BDM disagreement, injuries) + actual score. Proves signal value before upgrade ask. |

| — | PIPE-2 | Strip fetch code from betting_pipeline.py (Phase 2) | 2-3h | ✅ Done 2026-04-29 | Medium | Internal (2026-04-29) | Done | betting_pipeline.py calls run_morning(skip_fetch=True). _load_today_from_db() reads matches+odds+predictions from DB only. store_match/store_odds skipped when match.id is pre-set. run_morning(skip_fetch=False) still works for manual standalone runs. |
| — | XGB-FIX | Retrain XGBoost models + fix loader (pickle→joblib) | 1h | ✅ Done 2026-04-30 | **Very High** | Internal (2026-04-30) | Done | result_1x2.pkl and over_under.pkl were corrupted (invalid load key '\x01') — Python 3.14 can't unpickle CalibratedClassifierCV saved by old sklearn. Root cause: xgboost_ensemble.py used pickle.load() but training used joblib.dump(). Fix: (1) retrained both classifiers on 95,847 rows with current sklearn 1.8.0/xgboost 3.2.0 (scripts/retrain_xgboost.py), (2) switched loader to joblib.load(). XGBoost ensemble now active for ~512 Tier A teams. |
| — | POISSON-FIX | Store Poisson predictions for all 3 markets unconditionally | 30min | ✅ Done 2026-04-30 | High | Internal (2026-04-30) | Done | Previously only stored when XGBoost also ran (ensemble_prediction() output had poisson_home_prob). Since XGBoost was broken, 0 Poisson predictions in DB. Fixed: store all three 1x2 markets directly from poisson_pred before ensemble, for every match with odds. Also fixed XGBoost storage for draw+away markets (was only home). |
| — | DRAW-FIX | Store 1x2_draw in predictions table | 30min | ✅ Done 2026-04-29 | High | Internal (2026-04-30) | Done | Added ("1x2_draw", "draw_prob") to market storage loop + "1x2_draw": "odds_draw" to odds_key dict in daily_pipeline_v2.py. |
| — | ODDS-API | Activate The Odds API for Pinnacle odds ($20/mo) | 2h | ⬜ | High | Data Analysis (2026-04-29) | ~May 2026 | Code exists (254 lines, dormant). Pinnacle = gold standard for CLV. Depends on PIN-1 validation |
| — | LAUNCH-BETA | Add "Early Access / Beta" label to site | 15 min | ✅ Done 2026-04-29 | Medium | Launch Plan (2026-04-29) | Done | Beta badge added to nav header next to ODDSINTEL logo |
| — | LAUNCH-PICK | Make daily AI pick visible without login on /matches | 2-4h | ✅ Done 2026-04-29 | High | Launch Plan (2026-04-29) | Done | Top AI pick (match, selection, edge%, market, odds) now visible to anonymous visitors on /matches. CTA: "Sign up free for 1 more pick daily" → /signup |
| — | ML-5 | Today / Live / Upcoming / Finished filter tabs on matches page | 3-4h | ✅ Done 2026-04-29 | **Very High** | 4-AI Match UX Review (2026-04-29) | Next | **Priority 1 of ML group.** All 4 AIs: must-do first. 470 matches is unusable without filtering. Filter by `status` field (live/scheduled/finished) and kickoff date. Tabs replace the existing league-only accordion view. All tiers. |
| — | ML-2 | Live match timer + FT/HT status label | 2-3h | ✅ Done 2026-04-29 | **High** | UX audit (2026-04-29) | Next | **Priority 2.** All 4 AIs agree: match list without live status feels broken. Finished: show "FT". Live: show "22'" from `live_minute` in live_match_snapshots (already polled every 60s). HT: show "HT". Scheduled: show kickoff time. Do NOT estimate minute client-side from kickoff time — misleads on delays/stoppage. All tiers. |
| — | ML-1 | Team crests/logos on match rows | 2-4h | ✅ Done 2026-04-29 | **High** | 4-AI Match UX Review (2026-04-29) | Next | **Priority 3.** All 4 AIs agree. API-Football already returns `team.logo` URL per fixture. Store `logo_url` in teams table (backfill from fixture data). Display as 20px circle next to team name in LeagueAccordion. `loading="lazy"` + `onError` fallback: colored circle with first letter. All tiers. |
| — | ML-6 | Predicted score on match row | 3-4h | ✅ Done 2026-04-29 | **Very High** | 4-AI Match UX Review (2026-04-29) | Next | **Priority 4. THE differentiator** — all 4 AIs ranked this as the highest strategic impact. No competitor shows model prediction inline on the match list. Show "2:1" + win probability % next to each fixture using existing `predictions` table data. ~40% coverage is fine — put it in a distinct column. Free: show score (drives conversion). Pro: show confidence %. Rows without predictions show nothing (empty cell, no broken look). |
| — | ML-7 | Odds movement arrows (↑↓) on match rows | 3-4h | ✅ Done 2026-04-29 | **High** | 4-AI Match UX Review (2026-04-29) | Next | **Priority 5.** 3/4 AIs: high ROI, directly uses existing 2h snapshot data. Compare current best odds vs snapshot from 24h ago — show ↑ (green) / ↓ (red) / — per selection. Bettors watch line movement before anything else. **Pro tier only** — intelligence signal, not table stakes. Skip if fewer than 2 snapshots exist for a match. |
| — | ML-8 | Bookmaker count badge on match rows | 1-2h | ✅ Done 2026-04-29 | Medium | 4-AI Match UX Review (2026-04-29) | Next | **Priority 6.** 3/4 AIs agree (1 says skip). Very cheap (1-2h), signals market liquidity. Small badge "13 BMs" next to odds column. Helps users filter mentally — 2 bookies = skip. Data already in odds_snapshots. All tiers. |
| — | ML-3 | W/D/L form strip on match rows | 2-3h | ✅ Done 2026-04-29 | Low | 4-AI Match UX Review (2026-04-29) | Done | `form_home text, form_away text` columns added to matches (migration 019). `write_morning_signals()` stores form string from league_standings.form (last 5). Frontend: formHome/formAway in PublicMatch + fetched in getPublicMatches(). FormStrip component in league-accordion: green=W, amber=D, red/muted=L dots. Only shown when BOTH teams have form data. |
| — | ML-4 | Per-match favourite star | 1 day | ⬜ | Low | 4-AI Match UX Review (2026-04-29) | With ALERTS | **Defer.** Star icon on individual match rows (separate from league star). localStorage for anon, `user_match_favorites(user_id, match_id, created_at)` table for logged-in. Merge on login. 2/4 AIs say build for retention — but all agree it only pays off once ALERTS exists. Without notifications the star has no payoff. Design DB schema now, build with ALERTS. |
| — | FE-BUG-1 | MatchDetailFree shows "Upgrade to Pro" CTA for Pro/Elite users | 30 min | ✅ Done 2026-04-29 | High | Screenshot audit (2026-04-29) | Done | Added `isPro` prop to MatchDetailFree. Hides Pro lock hints and blurred odds preview for users who already have Pro access. |
| — | FE-BUG-2 | Select dropdowns show `__all__` raw string instead of display label | 30 min | ✅ Done 2026-04-29 | Low | Screenshot audit (2026-04-29) | Done | Fixed in value-bets-live.tsx, value-bets-client.tsx, track-record-live.tsx. Radix Select `SelectValue` now uses explicit children for display text. |
| — | FE-AUDIT | Full frontend code vs specs comparison (tier gating, data display, edge cases) | 2-3 days | ✅ Done 2026-04-29 | Medium | Screenshot audit (2026-04-29) | Done | **Bugs found and fixed:** (1) value-bets/page.tsx: `isPro = !isElite && tier==="pro"` was semantically wrong — Elite users had isPro=false. Fixed to `isPro = isElite \|\| tier === "pro"`. (2) matches/page.tsx: `is_superadmin \|\|` without `=== true`. Fixed. **No critical security gaps** — all Pro/Elite data fetched server-side only. **Gaps noted (no bugs):** saved matches frontend TBD, model prob per match Elite feature not built, full bot ROI separate page not built. |
| — | ALERTS | Match alerts & notifications (email/push) | 2-3 days | ⬜ | Medium | Tier Access Matrix | ~June 2026 | Re-engagement loop. No system for this yet |
| — | EMAIL-WEEKLY | Weekly performance summary email | 1 day | ⬜ | Medium | Tier Access Matrix | ~June 2026 | Shows bot ROI, top picks, CLV stats. Retention play |
| — | AF-EVAL | Evaluate AF Pro tier ($19/mo, 7.5K req/day) vs Ultra ($29/mo) | Research | ✅ Done 2026-04-29 | Low | Data Sources | Done | **Estimated daily usage: ~1,500–2,500 req/day** (normal days). ⚠️ **SUPERSEDED by LIVE-INFRA:** 15s live polling uses ~18K-45K calls/day, which exceeds AF Pro's 7.5K limit. **Do NOT downgrade to AF Pro.** Ultra (75K/day) required for tiered live polling. |

---

## Tier 3 — 1-2 Months

| # | ID | Task | Effort | Status | Impact | Source | Timeline | Notes |
|---|-----|------|--------|--------|--------|--------|----------|-------|
| 34 | HIST-BACKFILL | Historical match data backfill via automated cron during spare API quota windows | 3-4 days | 🔄 In Progress | Very High | Internal (MODEL_ANALYSIS 11.3) | ~May 2026 | **Code deployed 2026-04-30.** Fixtures + stats + events working (live tested). Historical odds NOT available from AF `/odds` endpoint (returns empty for completed fixtures) — skipped. 8 cron slots/day running Phase 1. Awaiting first overnight run confirmation → then Phase 2+3. See § HIST-BACKFILL Plan |
| — | XGB-HIST | Retrain XGBoost on backfilled historical data (43K matches with stats+events) | 1 day | ⬜ | **Very High** | Internal (2026-04-30) | After HIST-BACKFILL Phase 1 | Retrain result_1x2 + over_under classifiers on ~43K matches with full match_stats (xG, shots, possession, corners). Build referee card/foul profiles from match_events. Compute SIG-12 xG rolling signal. No odds needed — trains on outcomes + stats features. Current training: 96K rows from Kaggle but limited features. New: richer AF features on 43K+ matches. Also recompute `scripts/retrain_xgboost.py` with expanded feature set. |
| 35 | B6 | Singapore/South Korea odds source (Pinnacle API or OddsPortal) | Unknown | ⬜ | Very High | Internal | ~June 2026 | +27.5% ROI signal has no live odds feed. Note: AF has odds for Korea K League but NOT Singapore. Pinnacle via The Odds API ($20/mo) is best path |
| 36 | P5.2 | Footiqo: validate Singapore/Scotland ROI with independent 1xBet closing odds | Manual first | ⬜ | High | Internal | ~June 2026 | Independent validation. If ROI holds on 2nd source, it's real |
| 37 | P3.1 | Odds drift as XGBoost input feature (model retraining) | 1-2 days | ⬜ | High | Internal | ~June 2026 | Currently veto filter only. Strongest unused signal once data is there |
| 38 | P3.3 | Player-level injury weighting (weight by position/market value) | 2-3 days | ⬜ | Low | Internal | ~June 2026 | ~90% captured by injury_count + news_impact per AI analysis. Lower priority than originally scoped |
| 39 | S6-P2 | Graduate meta-model to XGBoost + full signal set (1000+ bot bets) | 2-3 days | ⬜ | Very High | Internal | ~June 2026 | After alignment thresholds validated |
| 40 | P4.1 | Audit trail ROI comparison: stats-only vs after-AI vs after-lineups | 1 day | ⬜ | High | Internal | ~June 2026 | Proves value of each information layer. Needed for Elite tier pricing |
| 41 | P3.5 | Feature importance tracking per league | 1 day | ⬜ | Medium | Internal | ~June 2026 | Which signals matter in which markets |
| 42 | F10 | My bets / tip tracking (user_bets table, personal P&L) | 2 days | ⬜ | Medium | Internal | After M2 | Skip until Stripe + Elite launch |
| 43 | F7 | Stitch redesign (landing + matches page) | Awaiting designs | ⬜ | Medium | Internal | After M1 | Parked until after M1 go-live |
| 70 | ELITE-BANKROLL | Personal bankroll analytics dashboard (Elite) | 2-3 days | ⬜ | High | Product 2026-04-30 | After F10 | Elite only. Builds on F10 (user_bets table). Shows: personal ROI vs model benchmark, CLV over time, per-league performance, streak tracking, risk metrics (max drawdown, avg stake/bet). Turns Elite into a tool serious bettors use daily, not just a data source. The benchmark comparison ("you: +3.1% ROI vs model: +8.4%") shows the gap that better signal usage closes. |
| 71 | ELITE-LEAGUE-FILTER | League performance filter for Elite value bets | 1 day | ⬜ | Medium | Product 2026-04-30 | After 3mo data | Show per-league model hit rate on value bets page. Elite users can restrict picks to leagues where model has historically outperformed (e.g. "only show picks in leagues where hit rate > 45%"). Needs ~3 months of settled data per league to be meaningful. |
| 72 | ELITE-ALERT-STACK | Custom multi-signal alert conditions (Elite) | 2-3 days | ⬜ | High | Product 2026-04-30 | After ALERTS | Elite users define stacked alert rules: e.g. "alert me only when confidence > 65% AND edge > 8% AND overnight line moved in model's direction simultaneously". Requires ALERTS infrastructure first. Differentiates Elite from Pro alerts meaningfully. |
| 68 | SUX-11 | "Why This Pick" reasoning card UI (Elite match detail) | 1-2 days | ✅ Done 2026-04-29 | High | UX Review (2026-04-29) | Done | `why-this-pick.tsx` Elite only. Static signal→text mapping (no LLM call). `buildReasons()` translates BDM, overnight move, form slope, fixture importance diff, H2H, injuries, ELO, referee bias → plain English with confidence=strong/moderate/weak. Up to 5 reasons per match. Sparkles icon + Elite badge. |
| 69 | SUX-12 | CLV tracking dashboard (Elite) | 1-2 days | ✅ Done 2026-04-29 | Medium | UX Review (2026-04-29) | Done | `clv-tracker.tsx` Elite only. `getMatchCLVData()` fetches pseudo_clv_home/draw/away from matches + settled simulated_bets for this match. Shows CLV per selection + avg + plain-English interpretation. Settled bets table with odds at pick, closing odds, CLV badge, result. Explains "Closing Line Value" concept inline. |

---

## Tier 4 — 2-3 Months (needs data accumulation)

| # | ID | Task | Effort | Status | Impact | Source | Timeline | Notes |
|---|-----|------|--------|--------|--------|--------|----------|-------|
| 44 | SIG-12 | xG overperformance rolling signal: recent xG vs actual goals | 2h | ⬜ | Medium | AI Analysis (2026-04-28) | Needs ~2 wks data | Team over/underperforming their xG → regression to mean. Needs ~2 weeks of post-match xG from T4 enrichment |
| 45 | MOD-2 | Learned Poisson/XGBoost blend weights (replace fixed α constants) | 2h | ⬜ | High | AI Analysis (2026-04-28) | Needs 500+ settled | Calibrated per-tier blend weights from actual prediction outcomes |
| 46 | P3.4 | In-play value detection model — full research plan below (§ INPLAY) | 2-3 weeks | ⬜ | **Very High** | Internal + 4-AI Review (2026-04-30) | Needs 500+ live | See § INPLAY Plan. 3 phases: feature pipeline (now→200 matches), LightGBM training (200→500), live execution (500+). 6 strategies validated by 4 independent AI reviews. Core: predict `lambda_home/away_remaining` via LightGBM Poisson regression → derive all market probabilities. xG pace ratio is #1 feature. |
| 47 | P4.2 | A/B bot testing framework (parallel bots with/without AI) | 1-2 days | ⬜ | Medium | Internal | Needs audit trail | Needs audit trail + data |
| 48 | P4.3 | Live odds arbitrage detector (cross-bookmaker real-time) | 1-2 days | ⬜ | Medium | Internal | ~July 2026 | Per-bookmaker odds ✅ — can build but low priority |
| 49 | P5.3 | OddAlerts API evaluation (20+ bookmakers real-time) | Research | ⬜ | Medium | Internal | Depends P5.1 | Depends on P5.1 sharp/soft model |
| 50 | RSS-NEWS | RSS news extraction pipeline (speed edge) | 1-2 days | ⬜ | High | Internal (MODEL_ANALYSIS 11.5) | Profitable first | $30-90/mo cost — deferred until model proves profitable. Targets news before odds adjust. Re-evaluate when Elite tier has subscribers |
| 51 | OTC-1 | Odds trajectory clustering (DTW on full timelines, cluster shapes) | 1-2 weeks | ⬜ | Low | Internal | Needs 1000+ | Downgraded: AI Analysis notes simple volatility+drift captures ~same signal at 5% the effort |
| 52 | P3.2 | Stacked ensemble meta-learner (logistic regression: when Poisson vs XGBoost) | 1-2 days | ⬜ | Medium | Internal | Needs settled bets | Needs settled bets with both predictions stored |

---

## Automation Sequels — Run These When Implementing Their Parent Task

> These tasks must be built **at the same time** as their parent. A model task is not "done" until its retraining is automated. Without these, the model calibration slowly rots as new data changes the distribution.

| ID | Parent | Task | Effort | Status | Notes |
|----|--------|------|--------|--------|-------|
| PLATT-AUTO | PLATT | Add weekly Platt recalibration to settlement pipeline | 1h | ✅ Done 2026-04-30 | Sunday step in `settlement.yml` runs `scripts/fit_platt.py`. Stores new α/β in `model_calibration` table each week. Pipeline reads latest row per market on startup. |
| BLEND-AUTO | MOD-2 | Add monthly Poisson/XGBoost blend weight recalculation | 1h | ⬜ | Re-derive per-tier α constants (CALIBRATION_ALPHA dict in improvements.py) from actual outcomes monthly. Script reads settled predictions → brier score per source → optimal weight. Replaces current hardcoded T1=0.20, T2=0.30 etc. with data-driven values. |
| META-RETRAIN | B-ML3 | Weekly meta-model retraining job (logistic regression v1) | 2h | ⬜ | After meta-model v1 is built at 3K CLV rows: add a weekly GitHub Action that re-runs training on all available `match_feature_vectors` rows, writes new model coefficients to a `model_versions` table, logs performance delta vs prior version. Should auto-deploy if accuracy improves. |
| XGB-RETRAIN | S6-P2 | Weekly XGBoost full-model retraining schedule | 3-4h | ⬜ | When XGBoost meta-model replaces logistic v1: set up weekly retraining with train/val split, track feature importances over time (signals that mattered in April may not matter in August — seasons change team dynamics). Log to model_versions. Alert if validation loss spikes. |
| ALN-AUTO | ALN-1 | Monthly alignment threshold refresh | 1h | ⬜ | After ALN-1 first pass at 300 bets: re-run threshold derivation monthly. Alignment thresholds (what signal count produces edge) shift as model quality improves. Script: bin settled bets by alignment_count → compute ROI per bin → update threshold constants → log to DB. |
| INPLAY-RETRAIN | P3.4 | Quarterly in-play model retraining | 2h | ⬜ | After in-play model is built: retrain quarterly (game states are seasonal — late-season desperation changes how minute-80 scores translate to final results). Separate train set per season if data allows. |

---

## Tier 5 — Future / Speculative

| # | ID | Task | Impact | Source | Notes |
|---|-----|------|--------|--------|-------|
| 53 | SLM | Shadow Line Model: predict what opening odds *should be* | High | Internal | Blocked on opening odds timestamp storage |
| 54 | MTI | Managerial Tactical Intent: press conference classification | Medium | Internal | Blocked on reliable transcript sources across leagues |
| 55 | RVB | Referee/Venue full bias features (beyond S4 referee stats) | Medium | Internal | Venue-level stats not yet collected |
| 56 | WTH | Weather signal (OpenWeatherMap, free) | Low | Internal | Low effort, defer until O/U becomes a focus market |
| 57 | SIG-DERBY | Is-derby + travel distance signals | Low | Internal | Needs team location data. SIGNAL_ARCHITECTURE.md Group 5 gap |
| 58 | DB-DIRECT | ~~Switch from PostgREST to direct PostgreSQL connection~~ | — | — | **Merged into LIVE-INFRA** — now Phase 2 of the Railway migration. See § RAILWAY Plan. |
| 59 | LIVE-INFRA | ~~Move live tracker to long-running process~~ | — | — | **Promoted to Tier 2** — now a full Railway migration with 5 phases. See Tier 2 and § RAILWAY Plan. |

---

## Key Thresholds to Watch

| Milestone | Query | Target | Current (2026-04-30) | ETA |
|-----------|-------|--------|---------------------|-----|
| **Platt scaling ready** | Predictions with finished match outcomes | 500+ | **586 ✅ IMPLEMENTED 2026-04-30** | Done |
| Meta-model Phase 1 ready | `matches WHERE status='finished' AND pseudo_clv_home IS NOT NULL` | 3,000+ | 494 | ~May 9 (+280/day) |
| Alignment threshold validation | `simulated_bets WHERE result!='pending' AND alignment_class IS NOT NULL` | 300+ | 30 | ~May 9-10 (~30-40/day with 16 bots) |
| Post-mortem patterns readable | `model_evaluations WHERE market='post_mortem'` | 14+ | 2 | ~May 12 (+1/day) |
| In-play model ready | Distinct matches in live_match_snapshots | 500+ | 49 | ~July (~10-20/day) |
| Meta-model Phase 2 ready | Settled bets with dimension_scores + CLV | 1,000+ | 0 | ~Aug (needs ALN-1 first) |
| XGBoost retrain on backfill | Backfill Phase 1 complete (match_stats) | ~18,000 | 149 | ~May 1-2 (backfill running) |
| LLM team name resolve | `wc -l data/logs/unmatched_teams.log` | Shrinks toward 0 | 2,287 entries | Manual |

---

## § HIST-BACKFILL Plan — Automated Historical Data Backfill

> Created: 2026-04-30. Detailed implementation plan for task #34.

### 1. API Rate Limit Analysis

**API-Football Ultra plan:** 75,000 req/day, 450 req/min (7.5 req/sec)
**Current daily usage:** ~1,500 req/day (2% of limit)
**Spare capacity:** ~73,500 req/day

#### Hour-by-Hour API-Football Usage (UTC)

```
Hour  | Jobs Running                          | Est. AF Calls | Backfill OK?
------|---------------------------------------|---------------|-------------
00:00 | —                                     |       0       | ✅ PRIME
01:00 | —                                     |       0       | ✅ PRIME
02:00 | —                                     |       0       | ✅ PRIME
03:00 | —                                     |       0       | ✅ PRIME
04:00 | ① Fixtures + ② Enrichment (full)      |    ~265       | ❌ Skip
05:00 | ③ Odds + ④ Predictions                |    ~135       | ⚠️ Light use
06:00 | ⑤ Betting (DB only, 0 AF calls)       |       0       | ✅ FREE
07:00 | ③ Odds (bulk)                          |      ~5       | ✅ Nearly free
08:00 | ③ Odds (bulk)                          |      ~5       | ✅ Nearly free
09:00 | ⑦ News (Gemini only, 0 AF calls)      |       0       | ✅ FREE
10:00 | ③ Odds (bulk)                          |      ~5       | ✅ Nearly free
11:00 | —                                      |       0       | ✅ FREE
12:00 | ② Enrichment + ③ Odds + ⑥ Live start  |     ~85       | ⚠️ Moderate
13:00 | ③ Odds(13:30) + ⑥ Live                |     ~45       | ⚠️ Light
14:00 | ③ Odds + ⑥ Live                        |     ~45       | ⚠️ Light
15:00 | ⑥ Live                                 |     ~40       | ⚠️ Light
16:00 | ② Enrichment + ③ Odds + ⑥ Live        |     ~85       | ⚠️ Moderate
17:00 | ③ Odds(17:30) + ⑥ Live                |     ~45       | ⚠️ Light
18:00 | ③ Odds + ⑥ Live                        |     ~45       | ⚠️ Light
19:00 | ⑦ News + ⑥ Live                        |     ~40       | ⚠️ Light
20:00 | ③ Odds + ⑥ Live                        |     ~45       | ⚠️ Light
21:00 | ⑧ Settlement + ⑥ Live                  |    ~360       | ❌ Skip
22:00 | ③ Odds + ⑥ Live (last)                 |     ~45       | ⚠️ Light
23:00 | —                                      |       0       | ✅ PRIME
      |                                       | ~1,340 total  |
```

#### Backfill Windows (ranked by priority)

| Window (UTC) | Duration | Competing AF Calls | Available Capacity |
|-------------|----------|-------------------|-------------------|
| **23:00 – 03:59** | 5h | 0 | ~36,750 req (at 450/min throttled to ~300/min safe) |
| **06:00 – 06:59** | 1h | 0 | ~18,000 req |
| **09:00 – 09:59** | 1h | 0 | ~18,000 req |
| **11:00 – 11:59** | 1h | 0 | ~18,000 req |
| **07:00 – 08:59** | 2h | ~10 | ~36,000 req |
| **10:00 – 10:59** | 1h | ~5 | ~18,000 req |
| **Total safe capacity** | **~11h** | **~15** | **~73,000+ req/day** |

**Recommended cron slots (conservative — zero-competition only):**
- `0 23,0,1,2,3 * * *` — 5 runs during the prime overnight window
- `0 6,9,11 * * *` — 3 runs during daytime gaps
- **= 8 runs/day, each run processes a batch of ~9,000 requests max**

### 2. What to Backfill

#### Target Data (ordered by ML impact)

| Priority | Data | AF Endpoint | Calls/Match | Status | Why |
|----------|------|-------------|-------------|--------|-----|
| P1 | Historical fixtures + results | `/fixtures?league=X&season=Y` | 1 per league/season (batch) | ✅ Working | Match results = training labels |
| P2 | Historical odds (13 bookmakers) | `/odds?fixture=ID` | 1 per match | ❌ **Skipped** — AF returns empty for completed fixtures | CLV analysis needs separate source (The Odds API historical?) |
| P3 | Match statistics (xG, shots, possession) | `/fixtures/statistics?fixture=ID` | 1 per match | ✅ Working | Feature engineering for XGBoost |
| P4 | Match events (goals, cards, subs) | `/fixtures/events?fixture=ID` | 1 per match | ✅ Working | Referee profiles, team discipline signals |

#### Scope

| Scope | Leagues | Seasons | Est. Matches | Est. API Calls |
|-------|---------|---------|-------------|----------------|
| **Phase 1: Tier 1 leagues** | 19 top leagues | 2023, 2024, 2025 | ~18,000 | ~36,000 (2 calls/match + 57 fixture batch calls) |
| **Phase 2: Tier 2 leagues** | 29 secondary leagues | 2024, 2025 | ~17,000 | ~34,000 |
| **Phase 3: Tier 3 leagues** | 28 remaining active | 2025 only | ~8,000 | ~16,000 |
| **Total** | **76 leagues** | **1-3 seasons** | **~43,000** | **~86,000** |

**At 73K spare/day → Phase 1 done in ~1 day, all phases done in ~1.5 days**
**Note:** Odds skipped (AF doesn't serve historical odds for finished fixtures). 2 API calls/match = stats + events.

### 3. Implementation Plan

#### 3a. New Script: `scripts/backfill_historical.py`

```
Purpose: Fetch historical match data in batches, respecting API budget
Args:
  --phase 1|2|3           Which league tier to process
  --batch-size 500        Max matches per run (default 500)
  --max-requests 9000     Budget cap per run (default 9000)
  --skip-existing         Skip matches already in DB (default true)
  --dry-run               Count only, no writes

Flow:
  1. Check get_remaining_requests() — abort if < 10,000 remaining today
  2. Query DB for target leagues (by tier) that need backfill
  3. For each league+season not yet fully backfilled:
     a. Fetch /fixtures?league=X&season=Y → store in matches table
     b. For each finished match missing odds: /odds?fixture=ID → store
     c. For each finished match missing stats: /fixtures/statistics → store
     d. For each finished match missing events: /fixtures/events → store
  4. Track progress in `backfill_progress` table:
     - (league_api_id, season, phase, fixtures_total, fixtures_done,
        odds_done, stats_done, events_done, last_run_at)
  5. Log run summary to pipeline_runs (job_name='hist_backfill')
  6. Check get_remaining_requests() at end — log remaining budget
```

#### 3b. Progress Tracking Table (Migration)

```sql
CREATE TABLE IF NOT EXISTS backfill_progress (
    league_api_id   integer NOT NULL,
    season          integer NOT NULL,
    phase           smallint NOT NULL DEFAULT 1,
    fixtures_total  integer DEFAULT 0,
    fixtures_done   integer DEFAULT 0,
    odds_done       integer DEFAULT 0,
    stats_done      integer DEFAULT 0,
    events_done     integer DEFAULT 0,
    status          text DEFAULT 'pending',  -- pending | in_progress | complete
    last_run_at     timestamptz,
    created_at      timestamptz DEFAULT now(),
    PRIMARY KEY (league_api_id, season)
);
```

#### 3c. GitHub Actions Workflow: `backfill.yml`

```yaml
name: Historical Backfill
on:
  schedule:
    # Prime overnight window: 23, 00, 01, 02, 03 UTC
    - cron: '0 23,0,1,2,3 * * *'
    # Daytime gaps: 06, 09, 11 UTC
    - cron: '0 6,9,11 * * *'
  workflow_dispatch:
    inputs:
      phase: { type: choice, options: ['1','2','3'], default: '1' }
      batch_size: { type: string, default: '500' }
      dry_run: { type: boolean, default: false }

jobs:
  backfill:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    steps:
      - Checkout + setup Python + install deps
      - Run: python scripts/backfill_historical.py
          --phase $PHASE --batch-size 500 --max-requests 9000
      - If all phases complete → disable cron (see §3e)
```

#### 3d. Completion Detection (Auto-Stop)

The script auto-detects completion and the workflow self-disables:

1. **Per-run check:** After each run, query `backfill_progress`:
   ```sql
   SELECT COUNT(*) FROM backfill_progress WHERE status != 'complete';
   ```
   If 0 rows remaining → backfill is done.

2. **Auto-disable workflow:** When complete, the script:
   - Creates a file `backfill_complete.flag` in the repo
   - The workflow checks for this flag at the start and exits early with a success message
   - Alternatively: use `gh workflow disable "Historical Backfill"` via the GitHub CLI

3. **Notification:** On final run, write to `pipeline_runs` with `job_name='hist_backfill_complete'` containing total stats:
   ```
   Phase 1: 18,234 matches (17,891 with odds, 17,456 with stats)
   Phase 2: 21,567 matches (...)
   Phase 3: 14,892 matches (...)
   Total: 54,693 matches backfilled in 2.4 days
   ```

4. **Dashboard query** to check progress anytime:
   ```sql
   SELECT phase, status, COUNT(*) as leagues,
          SUM(fixtures_done) as matches, SUM(odds_done) as odds
   FROM backfill_progress
   GROUP BY phase, status
   ORDER BY phase, status;
   ```

#### 3e. Safety Guards

| Guard | Implementation |
|-------|---------------|
| **Budget cap** | Each run checks `get_remaining_requests()` at start — abort if < 10,000 left today |
| **Rate limiting** | Uses existing 150ms throttle (6.7 req/sec) from `api_football.py` |
| **Idempotent** | `--skip-existing` is default — re-running is safe, picks up where it left off |
| **No interference** | Runs only during verified zero-competition windows |
| **Graceful stop** | Catches SIGTERM, commits progress to `backfill_progress` before exit |
| **Batch size** | 500 matches/run = ~2,000 API calls/run, well under 9,000 cap |

### 4. Sub-Tasks

| # | Sub-ID | Task | Effort | Status | Notes |
|---|--------|------|--------|--------|-------|
| 1 | HIST-1 | Create migration: `backfill_progress` table | 15 min | ✅ Done | `021_backfill_progress.sql` |
| 2 | HIST-2 | Write `scripts/backfill_historical.py` | 4-6h | ✅ Done | Fixtures + stats + events. Odds skipped (AF limitation). Batch dedup, budget caps, SIGTERM handling |
| 3 | HIST-3 | Create `.github/workflows/backfill.yml` (8 cron slots + manual trigger) | 30 min | ✅ Done | 5 overnight + 3 daytime slots. Completion flag check at start |
| 4 | HIST-4 | Add completion detection + auto-disable logic | 1h | ✅ Done | `backfill_complete.flag` + `check_all_complete()` |
| 5 | HIST-5 | Dry run test + live test | 30 min | ✅ Done | Dry run: 57 league/season combos. Live: 38 fixtures, 23 stats, 16 events stored correctly |
| 6 | HIST-6 | Deploy Phase 1 (monitor first overnight run) | 30 min | ⬜ Awaiting | Workflow deployed, first cron run tonight 23:00 UTC |
| 7 | HIST-7 | After Phase 1 complete: trigger Phase 2+3 via workflow_dispatch | 30 min | ⬜ Awaiting | Phase 1 ~1 day, then manually dispatch Phase 2 |

### 5. Expected Timeline

| Day | What Happens |
|-----|-------------|
| Day 0 | Build script + migration + workflow, dry-run test |
| Day 1 | Phase 1 runs overnight (8 cron runs × ~9K req = ~72K calls → 18K matches) |
| Day 1 afternoon | Phase 1 complete, Phase 2 begins |
| Day 2 | Phase 2 completes overnight, Phase 3 begins |
| Day 2-3 | Phase 3 completes, workflow auto-disables, `pipeline_runs` logs final summary |
| Day 3 | **55K+ matches with odds + stats available for XGBoost training** |

### 6. What This Unlocks

- **B-ML3 meta-model** can train on 43K+ match outcomes (vs waiting for daily accumulation)
- **PLATT scaling** has abundant data (500+ → 43K+ predictions with outcomes)
- **SIG-12 xG signal** has historical match stats for rolling calculations
- **Referee profiles** have thousands of historical events for cards/fouls patterns
- XGBoost retraining timeline: **months → days**
- ~~P3.1 odds drift~~ — needs historical odds; AF doesn't provide. Would need The Odds API historical endpoint ($20/mo)

---

## § INPLAY Plan — In-Play Value Detection Model

> Created: 2026-04-30. Synthesized from 4 independent AI strategy reviews. Detailed plan for task #46 (P3.4).

### 1. Core Hypothesis (validated by all 4 reviews)

**"Conditional mispricing occurs when realized goal output < expected output, but forward-looking hazard rate remains high."**

The market adjusts live odds primarily on **time elapsed + scoreline**, but lags on **true chance quality (xG)** and **game state intensity (tempo, pressure)**. The edge is NOT "0-0 = bet Overs" — it's "0-0 but underlying goal process is ABOVE expectation."

Key qualifier: **xG pace ratio > 1.0** (live xG/min exceeds pre-match expected xG/min). Without this, the market's drift is mathematically correct.

### 2. Model Architecture (all 4 reviews agreed)

**Target:** Predict `lambda_home_remaining` and `lambda_away_remaining` (Poisson rates for remaining goals per team) — NOT classification.

**Why:** One regression model derives ALL market probabilities:
- P(Over 2.5) = P(Poisson(λ_total_remaining) ≥ 2.5 - current_goals)
- P(BTTS) = derived from per-team lambdas via bivariate Poisson
- P(Home Win) = derived from goal difference distribution

**Algorithm:** LightGBM with `objective='poisson'` (primary) + XGBoost as ensemble partner.

**Time handling:** Single model with:
- `match_minute` as continuous feature
- `match_phase` as categorical: [0-15, 15-30, 30-45, 45-60, 60-75, 75-90]
- `time_remaining = 90 - match_minute`
- Non-linear transforms: `minute_squared`, `log(90 - minute)`

**Red cards:** V1: hard-skip matches with red cards. V2 (2000+ matches): add `man_advantage` + `minutes_since_red` features.

### 3. Feature Engineering (ranked by predictive power)

#### Tier 1 — Build immediately
| Feature | Formula | Signal |
|---------|---------|--------|
| **xG pace ratio** | `(live_xg / minutes_played) / (prematch_xg / 90)` | Core edge — >1.0 means game more open than market expects |
| **xG-to-score divergence** | `live_xg_total - actual_goals` | Large positive = "unlucky", regression due |
| **Implied probability gap** | `model_prob - (1 / live_odds)` | Direct value measure |
| **Per-team shot quality** | `team_xg / team_shots` | High = dangerous chances; low = shooting from distance |
| **Odds velocity** | `(odds_t - odds_t_minus_5min) / odds_t_minus_5min` | Sharp moves without goals = information |

#### Tier 2 — Build by Phase 2
| Feature | Formula | Signal |
|---------|---------|--------|
| Possession efficiency | `team_xg / (possession_pct × minute / 90)` | Strips time-wasting possession |
| Score-state adjustment | All metrics segmented by leading/drawing/trailing | Trailing team stats more predictive |
| Corner momentum | `corners_last_10min / corners_total` | Acceleration predicts pressure |
| Bookmaker consensus | `std(implied_probs_across_13_bookmakers)` | High disagreement = value opportunity |
| xG acceleration | `last_10_min_xg / previous_10_min_xg` | Momentum proxy |

#### Tier 3 — Refinements
| Feature | Formula | Signal |
|---------|---------|--------|
| Bayesian lambda update | `prematch_lambda × (1 - min/90) + xG_evidence` | Formal Bayesian update |
| ELO-adjusted xG | `xG × (opponent_elo / league_avg_elo)` | xG vs strong defense worth more |
| Importance × score state | `importance × (trailing: 1.3, leading: 0.7, drawing: 1.0)` | Must-win + trailing = max pressure |

### 4. Validated Strategies (from 4 AI reviews)

#### Strategy A: "xG Divergence Over" (appeared in all 4 reviews)
- **Entry:** Min 20-35, score 0-0 or 0-1/1-0
- **Signal:** Combined xG ≥ 0.7+ at min 20 AND xG pace ratio > 1.0 AND pre-match O2.5 > 52%
- **Market:** Over 2.5 goals (or Over 1.5 if minute > 30)
- **Skip:** xG per shot < 0.08 (low-quality shots), red card, live odds < 2.20
- **Edge:** 3-8% when conditions align

#### Strategy B: "BTTS Momentum" (3/4 reviews)
- **Entry:** Min 15-40, score 1-0 or 0-1
- **Signal:** Trailing team xG ≥ 0.4 AND shots on target ≥ 2 AND pre-match BTTS > 48%
- **Market:** BTTS Yes
- **Skip:** Trailing team xG < 0.2, score becomes 2-0, red card for trailing team
- **Edge:** 4-7%

#### Strategy C: "Favorite Comeback" (3/4 reviews)
- **Entry:** Min 25-60, pre-match favorite trailing by 1
- **Signal:** Favorite xG > underdog xG AND possession ≥ 60%
- **Market:** 1X2 Favorite or Draw No Bet
- **Skip:** Favorite not generating xG, underdog counter-xG high
- **Edge:** 3-6%

#### Strategy D: "Late Goals Compression" (3/4 reviews)
- **Entry:** Min 55-75, score 0-0 or 1-0
- **Signal:** Combined xG ≥ 1.0 AND live odds > 2.50 AND pre-match expected goals > 2.3
- **Market:** Over 0.5 remaining (or Over 1.5 total)
- **Skip:** Combined xG < 0.6 (dead game), no fixture importance
- **Edge:** 3-6% — final-30-min scoring rate is ~65-70% regardless of prior score

#### Strategy E: "Dead Game Unders" (2/4 reviews)
- **Entry:** Min 25-50, score 0-0 or 1-0
- **Signal:** xG pace < 70% of expected AND shots slowing AND corners low
- **Market:** Under 2.5 / Under 1.5
- **Edge:** Market assumes constant hazard rate; tempo collapse is real

#### Strategy F: "Odds Momentum Reversal" (2/4 reviews)
- **Entry:** Any minute, triggered by odds velocity
- **Signal:** Odds move > 15% in < 10 min WITHOUT goal AND contrary to xG trend
- **Market:** Fade the move (bet against direction)
- **Skip:** Red card, score change, only 1 bookmaker moved
- **Edge:** 5-10% when triggered, rare (~2-3% of matches)

### 5. Staking (in-play specific)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Kelly fraction | **Quarter Kelly** (not half) | Higher model uncertainty in-play |
| Time decay | `(minutes_remaining / 90)^0.5` | Min 30: 82% stake, min 60: 58%, min 75: 41% |
| Max stake per bet | 1.5% bankroll | Lower than pre-match (2%) |
| Max exposure per match | 3% bankroll | Prevents doubling down |
| Bankroll allocation | 70% pre-match, 30% in-play | Pre-match is more reliable |

**Minimum edge thresholds by minute:**

| Minute | Min edge | Rationale |
|--------|----------|-----------|
| 15-30 | 3% | Good signal, plenty of time |
| 30-45 | 4% | HT reset incoming |
| 46-60 | 5% | Post-HT uncertainty |
| 60-75 | 6% | Time running out |
| 75+ | 8% | Extreme value only |

### 6. Data Gaps to Fix

| Gap | Priority | Solution |
|-----|----------|----------|
| Open-play xG vs set-piece xG | High | Separate penalty/free-kick xG from open-play in snapshots |
| Substitution timestamps + type | High | Already in match_events — extract and add to snapshot features |
| Event-triggered odds capture | High | Snapshot odds at goal/red card moments, not just 5-min cycle |
| 1-minute trigger checks | Medium | When model flags potential entry, poll odds at 1-min for execution |
| Formation changes | Medium | Capture at HT and after goals |
| Dangerous attacks count | Low | Available from AF, add to snapshot |

### 7. Implementation Phases

| Phase | Data | Timeline | What to build |
|-------|------|----------|---------------|
| **Phase 1: Feature Pipeline** | Now → 200 matches (~2 weeks) | May 2026 | Transform `live_match_snapshots` → training rows at minute checkpoints. Compute all Tier 1 features. Paper trade Strategy A (xG Divergence) with fixed 1% stakes. Track hit rate and CLV. |
| **Phase 2: Model Training** | 200 → 500 matches (~4 weeks) | June 2026 | Train LightGBM Poisson on `lambda_home/away_remaining`. Backtest all 6 strategies. Identify which have genuine edge. Add XGBoost ensemble. |
| **Phase 3: Live Execution** | 500+ matches | July 2026 | Deploy in-play bots for 1-2 winning strategies. Quarter Kelly + time decay. 1-min trigger checks. Track CLV (entry odds vs closing odds). |

### 8. What This Unlocks

- **Entirely new revenue stream** — in-play betting is ~60% of global sports betting volume
- **Pro/Elite product differentiation** — "Live Win Probability" updating every 5 min on match detail
- **Higher bet volume** — each match can generate multiple in-play bets at different checkpoints
- **xG-based edge** — most retail bettors and many bookmaker algorithms anchor on scoreline, not xG

---

## § RAILWAY Plan — Railway Migration (GH Actions → Long-Running Process)

> Created: 2026-04-30. Full architecture migration for LIVE-INFRA task.
> Decision: Railway ($5/mo) chosen over GCP Cloud Run, Fly.io, Render, Hetzner VPS, pg_cron.
> Architecture longevity: 18-24+ months before next major change needed.

### 1. Why

- GH Actions cron is unreliable: 10-20 minute gaps observed on live tracker (should be every 5 min)
- API-Football updates live odds every **15 seconds** — we poll every 5 min (**20x slower**)
- Cold start overhead: ~90s wasted per GH Actions run (checkout + setup Python + pip install) × 132 runs/day
- PostgREST HTTP overhead: 24 HTTP requests per live tracker cycle (~3s) — at 15s polling this is 20-30% of cycle
- Blocks in-play model (P3.4) which needs 15s odds + 60s stats + <5s execution latency

### 2. Architecture

```
Railway ($5/mo)                          Supabase Pro ($25/mo)
┌─────────────────────────┐              ┌──────────────────┐
│  workers/scheduler.py   │              │   PostgreSQL DB   │
│  ┌───────────────────┐  │              │                  │
│  │ APScheduler       │  │   psycopg2   │  matches         │
│  │ • morning_pipeline│──┼──────────────│  odds_snapshots   │
│  │ • odds_refresh    │  │  connection  │  live_match_snap  │
│  │ • enrichment      │  │    pool      │  simulated_bets   │
│  │ • news_checker    │  │  (direct SQL)│  pipeline_runs    │
│  │ • settlement      │  │              │  ...              │
│  └───────────────────┘  │              └──────────────────┘
│  ┌───────────────────┐  │                      │
│  │ LivePoller        │  │   API-Football       │
│  │ • 15s: odds+score │──┼──── Ultra $29/mo ────┘
│  │ • 60s: stats+evts │  │   75K req/day
│  │ • 5min: lineups   │  │
│  │ • smart priority  │  │
│  └───────────────────┘  │
│  ┌───────────────────┐  │
│  │ Health endpoint   │  │
│  │ :8080/health      │  │
│  └───────────────────┘  │
└─────────────────────────┘
```

### 3. Five Phases

| Phase | Days | What | Key deliverable |
|-------|------|------|----------------|
| **1. Scheduler** | 1-3 | APScheduler replaces GH Actions crons. Same behavior, better reliability. | `workers/scheduler.py`, Dockerfile, railway.toml |
| **2. Direct SQL** | 4-5 | psycopg2 connection pool for live tracker ops. 10-50x faster writes. | `workers/api_clients/db.py`, migrated live tracker functions |
| **3. Tiered Polling** | 6-8 | 15s/60s/5min polling loop. 20x faster live data. | `workers/live_poller.py`, decomposed live_tracker.py |
| **4. Smart Polling** | 9 | Dynamic priority per match. Event-triggered snapshots. | Priority classification, goal/card → instant odds capture |
| **5. Validation** | 10 | Shadow mode testing, monitoring, doc updates. | End-to-end checklist, WORKFLOWS.md + INFRASTRUCTURE.md |

### 4. API Budget Impact

| Stage | AF calls/day (avg) | AF calls/day (peak) | % of 75K limit |
|-------|-------------------|--------------------|--------------:|
| **Current** (5min polling) | ~1,500 | ~7,000 | 2-9% |
| **After Phase 3** (15s/60s tiered) | ~18,000 | ~32,000 | 24-43% |
| **After Phase 4** (smart polling) | ~18,000 | ~45,000 | 24-60% |

AF Ultra (75K/day) required. **Do NOT downgrade to AF Pro** ($19/mo, 7.5K/day).

### 5. Cost Impact

| Item | Before | After |
|------|--------|-------|
| Railway | $0 | $5/mo |
| Total monthly | ~€52 | ~€57 |

### 6. DB Access Migration Strategy

Two patterns coexist during migration:

| Pattern | Used by | Migrate when |
|---------|---------|-------------|
| PostgREST (supabase SDK) | Morning pipeline, odds, enrichment, settlement | Gradually over weeks — HTTP overhead negligible at 2h intervals |
| **Direct SQL** (psycopg2 pool) | Live poller, live tracker, all new code | Immediately for Phase 2+ |

End state (4-8 weeks): all DB access through `db.py`. PostgREST kept only for Supabase Auth if needed.

### 7. What This Unlocks

- **In-play model (P3.4)**: 15s odds + 60s xG data → LightGBM training starts immediately
- **Event-triggered odds capture**: odds at moment of goal/red card → CLV analysis
- **Live Win Probability**: frontend can show updating probabilities during matches (Pro feature)
- **Faster settlement**: detect finished matches within 15s, not 5min
- **Future in-play bots**: <1s execution latency for bet placement

---

## Source Legend

| Source | Meaning |
|--------|---------|
| Internal | Planned before external AI analysis — from ROADMAP/BACKLOG/MODEL_ANALYSIS |
| AI Analysis (2026-04-28) | Identified during external 4-agent AI architecture review session on 2026-04-28 |
| ROADMAP Frontend Backlog | From the Frontend Data Display Backlog section of ROADMAP.md |
| Internal (MODEL_ANALYSIS X.X) | Exists in MODEL_ANALYSIS.md but was not yet tracked in this queue |
| UX Review (2026-04-29) | Identified during 4 independent UX/product reviews of signal surfacing strategy. Full details in SIGNAL_UX_ROADMAP.md |
| 4-AI Match UX Review (2026-04-29) | 4 independent AI tools assessed 11 match list UX improvements. Unanimous on: filter tabs, live timer, team crests, predicted score (THE differentiator). Strong consensus on: odds movement arrows (Pro), bookmaker count badge. Skip: odds freshness (highlights 2h staleness as a weakness). |
| Data Analysis (2026-04-29) | From pipeline refactor + data source audit session (2026-04-29) |
| Launch Plan (2026-04-29) | From LAUNCH_PLAN.md pre-launch preparation |
| Tier Access Matrix | From TIER_ACCESS_MATRIX.md feature checklist |
| Data Sources | From DATA_SOURCES.md remaining cleanup |
| Landing Page Review (2026-04-29) | From landing page pricing/UX review |
