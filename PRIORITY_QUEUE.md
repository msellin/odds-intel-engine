# OddsIntel — Master Priority Queue

> Single source of truth for ALL open tasks. Every actionable item across all docs lives here.
> Other docs may describe features but ONLY this file tracks task status.
> Last updated: 2026-05-03 — PERF-1 + PERF-2 done. Betting pipeline performance overhaul: batch signal writing (10 bulk queries instead of 25-40 per match × 416 matches, reducing 34-70min bottleneck to ~15s), improvements.py migrated fully to psycopg2 (eliminates Railway PostgREST failure), prune script rewritten to single SQL DELETE.

**Column guide:**
- **☑** — `⬜` not started · `🔄` in progress · `✅` done
- **Ready?** — `✅ Ready` pick up now · `⏳ Waiting [reason]` blocked

---

## Tier 0 — Foundation (all done)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| B-ML1 | Pseudo-CLV for all ~280 daily matches | 2-3h | ✅ | ✅ Done | `(1/open) / (1/close) - 1` per finished match. 280 rows/day |
| B-ML2 | `match_feature_vectors` nightly ETL | 1 day | ✅ | ✅ Done | Pivots signals + predictions + ELO/form → wide row per match |
| CAL-1 | Calibration validation script | 2h | ✅ | ✅ Done | `scripts/check_calibration.py` |
| S1+S2 | Migration 010: `source` on predictions + `match_signals` table | 2-3h | ✅ | ✅ Done | Unique constraint on (match_id, market, source) |
| CAL-2 | Flip calibration α: T1→0.20, T2→0.30, T3→0.50, T4→0.65 | 30 min | ✅ | ✅ Done | Was T1=0.55 (model-heavy). Now market-heavy in efficient markets |
| RISK-1 | Reduce Kelly fraction to 0.15×, cap to 1% bankroll per bet | 15 min | ✅ | ✅ Done | KELLY_FRACTION 0.25→0.15, MAX_STAKE_PCT 0.015→0.010 |
| LLM-RESOLVE | Run `scripts/resolve_team_names.py --apply` | 30 min | ✅ | ✅ Done | 143 total mappings. 204 unmatched names accounted for |

---

## Tier 1 — Next 1-2 Weeks

### Done

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| S3/S4/S5/S3b-f | All signals wired (ELO, form, H2H, referee, BDM, OLM, venue, rest, standings) | ✅ | Full signal set in match_signals |
| SIG-7/8/9/10/11 | Importance asymmetry, venue splits, form slope, odds vol, league meta | ✅ | |
| META-2 | Meta-model feature design (8 market-structure features) | ✅ | |
| PIPE-1 | Clean 9-job pipeline replacing monolith | ✅ | |
| STRIPE / F8 | Stripe test mode: checkout, webhook, portal, founding cap, annual billing | ✅ | |
| B3 | Server-side tier gating in Next.js | ✅ | |
| SUPABASE-PRO | Supabase upgraded to Pro ($25/mo) | ✅ | PITR + backups |
| LEAGUE-DEDUP | Kambi/AF dedup, priority sort, ~1100 orphan leagues pruned | ✅ | |
| SENTRY | Error monitoring wired in frontend | ✅ | |

### Open

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| B-ML3 | First meta-model: 8-feature logistic regression, target=pseudo_clv>0 | 1 day | ⬜ | ⏳ ~May 9 (need 3K CLV rows, have ~494) | Train after 3000+ pseudo-CLV rows. Features per META-2. See MODEL_ANALYSIS.md Stage 4 |
| BOT-TIMING | Time-window bot cohorts: morning/midday/pre-KO A/B test | 2-3h | ✅ | ✅ Done 2026-05-01 | 16 bots → 5 morning / 6 midday / 5 pre_ko. `BOT_TIMING_COHORTS` dict + cohort param in run_morning(). Migration 032 adds timing_cohort to simulated_bets. Scheduler auto-selects cohort by UTC hour. |
| POSTGREST-CLEANUP | Migrate remaining PostgREST callers to psycopg2 | 3-4h | ✅ | ✅ Done 2026-05-03 | settlement.py, pipeline_utils.py, news_checker.py, fetch_odds.py, fetch_enrichment.py, daily_pipeline_v2.py, **improvements.py** (load_platt_params, compute_odds_movement, _dim_news, _dim_lineup) all migrated. SQL JOINs replace PostgREST nested selects. get_client() only in supabase_client.py internals now. |
| PERF-1 | Batch morning signal writing — replace 25-40 per-match DB queries | 2-3h | ✅ | ✅ Done 2026-05-03 | `batch_write_morning_signals()` in supabase_client.py: 10 bulk queries (ANY(match_ids[])) + one execute_values INSERT replaces ~14K serial round-trips. Reduced 34-70 min bottleneck to ~15s. Added league_id to match_dict for SIG-11. |
| PERF-2 | Rewrite prune_odds_snapshots.py — single SQL DELETE | 1h | ✅ | ✅ Done 2026-05-03 | Replaced per-match PostgREST iteration with one DISTINCT ON subquery DELETE. Prunes all finished matches in a single statement. Migrated to psycopg2. |
| STRIPE-PROD | Swap Stripe to production keys | 1h | ⬜ | ⏳ Manual (user action) | 5-step checklist in INFRASTRUCTURE.md. 1) Live mode 2) Re-run setup_stripe.py 3) Update Vercel env vars 4) New webhook + whsec_ 5) Supabase Pro ✅ done |
| GH-CLEANUP | Remove pipeline workflow files from GitHub Actions | 30min | ⬜ | ⏳ ~May W3-4 (after 2-4 wks Railway stable) | Delete fixtures/enrichment/odds/predictions/betting/live_tracker/news_checker/settlement .yml. Keep migrate.yml + backfill.yml. workflow_dispatch is the fire extinguisher until then. |

---

## Signal UX — Phase 1 (all done)

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| SUX-1 | Match Intelligence Score: A/B/C/D grade on every match card | ✅ | Grade badge + signal count tooltip. All tiers |
| SUX-2 | Match Pulse composite indicator (⚡ / 🔥 / —) | ✅ | bdm>0.12 + OLM/vol threshold. ~15-20% scarcity |
| SUX-3 | Free-tier signal teasers on notable matches | ✅ | 1-2 italic hooks on 30-40% of matches |

---

## Tier 2 — 2-4 Weeks

### Done

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| MOD-1 | Dixon-Coles correction to Poisson model | ✅ | `DIXON_COLES_RHO=-0.13`. τ correction for low-score draws |
| PLATT | Platt scaling + weekly recalibration | ✅ | `scripts/fit_platt.py`. Weekly Sunday refit |
| BDM-1 | Bookmaker disagreement signal | ✅ | compute_bookmaker_disagreement() → match_signals |
| FE-LIVE / ODDS-OU-CHART / ODDS-BTTS / ODDS-MARKETS | Live in-play chart, O/U 2.5 chart, BTTS/O/U 1.5/3.5 odds table | ✅ | Pro gated |
| MKT-STR | Market-implied team strength into XGBoost | ✅ | market_implied_home/draw/away in feature row |
| EXPOSURE-AUTO | Auto-reduce stakes on league concentration | ✅ | 3rd+ bet same league = 50% stake |
| LIVE-FIX | Populate xG/shots/possession/corners in snapshots | ✅ | Was empty. Now 1 extra AF call per live match |
| BOTS-EXPAND | 10→16 bots (BTTS, O/U 1.5/3.5, draw, O/U 2.5 global) | ✅ | ~30-40 bets/day |
| KAMBI-BTTS | O/U + BTTS from Kambi event endpoint | ✅ | ~40 matches with BTTS now |
| BET-MULTI | Betting pipeline 5x/day (06/10/13/16/19 UTC) | ✅ | Idempotent — unique constraint prevents duplicates |
| TR-REDESIGN | Track record redesign: CLV-led, tier-gated | ✅ | |
| LP-1/2/3 | Landing page fixes | ✅ | Pricing/urgency cleanup |

### Open

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| P5.1 | European Soccer DB (Kaggle): sharp/soft bookmaker analysis | 1-2 days | ⬜ | ✅ Ready | `bookmaker_sharpness_rankings.csv` + `sharp_money_signal` feature |
| PIN-1 | Pinnacle anchor signal: `model_prob - pinnacle_implied` | 2-3h | ⬜ | ⏳ After P5.1 | Depends on P5.1 confirming Pinnacle is in our 13 bookmakers |
| ODDS-API | Activate The Odds API for Pinnacle odds ($20/mo) | 2h | ⬜ | ⏳ After PIN-1 | Code exists (254 lines, dormant) |
| ALN-1 | Dynamic alignment thresholds (300+ settled bot bets) | 2h | ⬜ | ⏳ ~May 9-10 (have ~30, need 300) | Needs actual placed bets — pseudo-CLV does NOT substitute |
| VAL-POST-MORTEM | Review 14 days of LLM post-mortem patterns | 30 min | ⬜ | ⏳ May 13+ (have 2 rows, need 14) | `SELECT notes FROM model_evaluations WHERE market='post_mortem' ORDER BY date DESC LIMIT 14` |

---

## Engagement & Growth — Phase 1 (Launch Sprint — do this week)

> Full strategy in docs/ENGAGEMENT_PLAYBOOK.md. Phase 1 = ship with Reddit launch. Phase 2 = retention (weeks 3-6). Phase 3 = differentiation (months 2-3).

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ENG-3 | Daily AI match previews (top 5-10, Gemini) | 1-2 days | ✅ Done 2026-05-01 | ✅ Ready | `workers/jobs/match_previews.py`. Scheduler 07:00 UTC. `match_previews` table (migration 033). Free sees teaser, Pro/Elite see full 200-word preview. Triple-duty: on-site + email + social. |
| ENG-4 | Daily email digest via Resend | 2-3 days | ✅ Done 2026-05-01 | ✅ Ready | `workers/jobs/email_digest.py`. Scheduler 07:30 UTC. `email_digest_log` table (migration 034). Free: teasers + CTA. Pro: + bet count. Elite: + full picks table. Resend REST API via httpx. Set `RESEND_API_KEY` in .env. |
| ENG-1 | "X analyzing this match" live counter | 4-6h | ⬜ | ✅ Ready | Rolling 30min page view counter per match. Supabase realtime or DB counter. All tiers. Makes site feel alive |
| ENG-2 | Community vote split display | 4-6h | ⬜ | ✅ Ready | `match_votes` table exists. Horizontal bar Home/Draw/Away % on match detail. Lock at kickoff |
| ENG-6 | Bot consensus on match detail ("7/9 models agree: Over 2.5") | 3-4h | ⬜ | ✅ Ready | Data in `simulated_bets`. Zero new data needed. Free: count. Pro: markets. Elite: full breakdown |
| ENG-7 | Public /methodology page | Half day | ⬜ | ✅ Ready | Plain-English model explanation. Trust anchor. Nobody else publishes this |
| ENG-5 | Betting glossary (10-15 SEO pages at /learn/[term]) | 2-3 days | ⬜ | ✅ Ready | EV, CLV, Poisson, Kelly, xG, BTTS etc. FAQ schema for Google |

---

## Engagement & Growth — Phase 2 (Retention Engine, weeks 3-6)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ENG-9 | Personal bet tracker + "Model vs You" dashboard | 3-4 days | ⬜ | ✅ Ready | Extend `user_picks` with odds+stake+settlement. ROI/CLV/hit rate. "Your ROI: +2.1% \| Model: +6.8%". Free: 10/mo. Pro: unlimited. Elite: per-league. 50 tracked bets = user never leaves |
| ENG-11 | "What Changed Today" widget on matches page | 1 day | ⬜ | ✅ Ready | Top 5 signal moves today: odds shifts, injuries, confidence changes. All see headlines, Pro sees details |
| ENG-12 | Model vs Market vs Users triangulation | 4-6h | ⬜ | ✅ Ready | Three-bar: Model 54% / Market 48% / Users 61%. Data all exists. "Who's wrong?" tension |
| ENG-13 | Shareable pick cards (branded image generation) | 1-2 days | ⬜ | ✅ Ready | Vercel OG image API. Free marketing on every share |
| ENG-14 | Auto-generated prediction pages for SEO (/predictions/[league]/[week]) | 2-3 days | ⬜ | ✅ Ready | Forebet/BetStudy territory. Model already produces data. New route + AI narrative |
| ENG-8 | Watchlist signal alerts (email/push) | 3-4 days | ⬜ | ⏳ After ENG-4 (needs email infra) | Odds >5% move, model confidence shift, injury. Free: kickoff reminders. Pro: signal alerts. Elite: custom (ELITE-ALERT-STACK) |
| ENG-10 | Weekly performance email (Monday 08:00 UTC) | 1 day | ⬜ | ⏳ After ENG-4 (needs email infra) | "Model 18-12 (+5.3u). Your bets: 4-2 (+1.1u). Top league: Bundesliga." |

---

## Engagement & Growth — Phase 3 (Differentiation, months 2-3)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ENG-16 | "Ask AI About This Match" freeform Gemini Q&A | 2-3 days | ⬜ | ⏳ ~June-July | Extends BET-EXPLAIN. Rate-limited by tier. ParlaySavant charges $30/mo for this |
| ENG-15 | Market inefficiency index per league (rolling 30-day edge) | 1 day | ⬜ | ⏳ ~June (needs 30 days of data) | "Eredivisie: HIGH +4.8%. Premier League: LOW +1.2%." No competitor does this |
| ENG-17 | Season-end "Year in Review" (personal, shareable) | 2-3 days | ⬜ | ⏳ ~Aug+ (needs full season of user data) | Strava-style. "312 bets, best month October." Viral potential |

---

## Railway Migration — LIVE-INFRA (all done ✅)

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| LIVE-INFRA | Full migration: GH Actions → Railway scheduler + direct SQL + tiered live polling | ✅ | All 5 phases complete 2026-05-01 |
| RAIL-1 | `workers/scheduler.py` (APScheduler + health endpoint) | ✅ | 21 jobs. Health on :8080 |
| RAIL-2 | Extract `run_*()` from all job scripts | ✅ | main() kept as CLI wrapper |
| RAIL-3 | API budget tracker in `api_football.py` | ✅ | BudgetTracker class, thread-safe |
| RAIL-4 | Dockerfile + railway.toml + .dockerignore | ✅ | Python 3.12-slim, TZ=UTC |
| RAIL-5 | Deploy + validate (shadow mode) | ✅ | Superseded — went straight live |
| RAIL-6 | Disable GH Actions crons | ✅ | schedule: commented in 7 workflows. backfill.yml kept |
| RAIL-7 | `workers/api_clients/db.py` (psycopg2 pool) | ✅ | ThreadedConnectionPool 2-10, bulk_insert/upsert |
| RAIL-8 | Live tracker DB functions → direct SQL | ✅ | 6 functions in db.py. Batched writes, no 1K limit |
| RAIL-9 | `workers/live_poller.py` (tiered 30s/60s/5min) | ✅ | LivePoller class, budget-aware |
| RAIL-10 | Decompose `live_tracker.py` into sub-functions | ✅ | fetch_live_bulk/stats/events/build_snapshot |
| RAIL-11 | Smart polling: priority tiers + event-triggered snapshots | ✅ | HIGH priority (active bets) = 30s stats. Goal → extra odds snapshot |
| RAIL-12 | Full doc sweep aligned with Railway | ✅ | 8 .md files updated |
| RAIL-13 | Instant settlement on FT + score sync fix | ✅ | finish_match_sql on FT detection. UTC rollover fix. 23:30 safety net |

---

## Frontend UX — All Done ✅

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| LP-0 / A-1/2/3/4 | Landing page rewrite + profile page redesign | ✅ | |
| B-1/2/3/4/5/6 | Track record public, confidence filter, /how-it-works | ✅ | |
| C-1 to C-6 | Match page tooltips, odds header, value bets gate, login modal | ✅ | |
| F5 | Value bets page: Free=teaser, Pro=directional, Elite=full | ✅ | |
| BET-EXPLAIN | Natural language bet explanations (Gemini, Elite-gated) | ✅ | GET /api/bet-explain |
| SUX-4/5/6/7/8/9/10 | Signal summary, accordion, labels, hooks, timeline, delta, post-match reveal | ✅ | |
| SUX-11/12 | "Why This Pick" Elite card + CLV tracker | ✅ | |
| ML-1/2/3/4/5/6/7/8 | Logos, live timer, form strip, match filter tabs, predicted score, odds arrows, BM badge, match star | ✅ | |
| FE-FAV-1/2/3 | My Leagues bug fix + league ordering + per-match star | ✅ | |
| FE-BUG-1/2 / FE-AUDIT | Pro CTA bug, select dropdown bug, full tier gating audit | ✅ | |
| PIPE-2 / XGB-FIX / POISSON-FIX / DRAW-FIX | Pipeline cleanup + model fixes | ✅ | XGBoost retrained on 95K rows, joblib loader |
| LAUNCH-BETA / LAUNCH-PICK | Beta label, daily pick visible without login | ✅ | |
| AF-EVAL | AF Ultra confirmed required — do NOT downgrade (live polling needs 18K-45K calls/day) | ✅ | |

---

## Tier 3 — 1-2 Months

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| HIST-BACKFILL | Historical data backfill (code deployed, running) | — | 🔄 | 🔄 Active | **⚠️ Workflow was failing** (DATABASE_URL missing, "unknown" team_side bug — both fixed 2026-05-01). 8 cron slots/day. See § HIST-BACKFILL Plan |
| XGB-HIST | Retrain XGBoost on backfilled data (~43K matches with stats+events) | 1 day | ⬜ | ⏳ After HIST-BACKFILL Phase 1 | Retrain result_1x2 + over_under on full AF features. Current: 96K Kaggle rows (limited features). New: richer per-match stats |
| B6 | Singapore/South Korea odds source | Unknown | ⬜ | ⏳ Research needed | +27.5% ROI signal, no live odds. AF has Korea K League odds but NOT Singapore. Pinnacle via Odds API ($20/mo) is best path |
| P5.2 | Footiqo: validate Singapore/Scotland ROI with 1xBet closing odds | Manual | ⬜ | ✅ Ready | Independent validation. If ROI holds on 2nd source, it's real |
| P3.1 | Odds drift as XGBoost input feature | 1-2 days | ⬜ | ⏳ ~June (needs more data) | Currently veto filter only. Strongest unused signal once data accumulates |
| P3.3 | Player-level injury weighting (by position/market value) | 2-3 days | ⬜ | ⏳ Low priority | ~90% captured by injury_count + news_impact already |
| S6-P2 | Graduate meta-model to XGBoost + full signal set | 2-3 days | ⬜ | ⏳ After ALN-1 (~May W3) | After alignment thresholds validated at 300+ bets |
| P4.1 | Audit trail ROI: stats-only vs after-AI vs after-lineups | 1 day | ⬜ | ⏳ Needs data | Proves value of each layer. Needed for Elite pricing rationale |
| P3.5 | Feature importance tracking per league | 1 day | ⬜ | ✅ Ready | Which signals matter in which markets |
| F7 | Stitch redesign (landing + matches page) | Awaiting designs | ⬜ | ⏳ Awaiting designs | Parked until after first users arrive |
| ELITE-BANKROLL | Personal bankroll analytics dashboard (Elite) | 2-3 days | ⬜ | ⏳ After ENG-9 | ROI vs model benchmark, CLV over time, per-league, drawdown. Turns Elite into a daily tool |
| ELITE-LEAGUE-FILTER | League performance filter for Elite value bets | 1 day | ⬜ | ⏳ After 3mo data | "Show only leagues where model hit rate > 45%". Needs data to be meaningful |
| ELITE-ALERT-STACK | Custom multi-signal alert stacking (Elite) | 2-3 days | ⬜ | ⏳ After ENG-8 | "Alert when confidence > 65% AND edge > 8% AND line moved in model's direction" |

---

## Tier 4 — 2-3 Months (needs data accumulation)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| SIG-12 | xG overperformance rolling signal | 2h | ⬜ | ⏳ ~2 wks of post-match xG data | Regression to mean signal. Needs post-match xG from live snapshots |
| MOD-2 | Learned Poisson/XGBoost blend weights (replace fixed α) | 2h | ⬜ | ⏳ 500+ settled predictions | Calibrated per-tier α from actual outcomes. Replaces hardcoded T1=0.20/T2=0.30 |
| P3.4 | In-play value detection model | 2-3 wks | ⬜ | ⏳ 500+ live snapshots (~July) | LightGBM Poisson regression. xG pace ratio is #1 feature. See § INPLAY Plan |
| P4.2 | A/B bot testing framework | 1-2 days | ⬜ | ⏳ Needs audit trail + data | Parallel bots with/without AI layers |
| P4.3 | Live odds arbitrage detector | 1-2 days | ⬜ | ⏳ ~July | Per-bookmaker odds exist. Low priority |
| RSS-NEWS | RSS news extraction pipeline ($30-90/mo) | 1-2 days | ⬜ | ⏳ After model proves profitable | Targets news before odds adjust. Re-evaluate when Elite has subscribers |
| P3.2 | Stacked ensemble meta-learner (when Poisson vs XGBoost) | 1-2 days | ⬜ | ⏳ Needs settled bets with both predictions | Logistic regression on model disagreement |
| OTC-1 | Odds trajectory clustering (DTW) | 1-2 wks | ⬜ | ⏳ 1000+ snapshots | Low priority — volatility+drift captures ~same at 5% effort |

---

## Automation Sequels — Build Alongside Parent Task

> A model task is NOT done until its retraining is automated. Without these, calibration rots as data changes.

| ID | Parent | Task | Effort | ☑ | Ready? | Notes |
|----|--------|------|--------|----|--------|-------|
| PLATT-AUTO | PLATT | Weekly Platt recalibration in settlement | 1h | ✅ | ✅ Done | Sunday step runs `scripts/fit_platt.py` → `model_calibration` table |
| BLEND-AUTO | MOD-2 | Monthly Poisson/XGBoost blend weight recalculation | 1h | ⬜ | ⏳ After MOD-2 | Re-derive CALIBRATION_ALPHA from brier score per source monthly |
| META-RETRAIN | B-ML3 | Weekly meta-model retraining job | 2h | ⬜ | ⏳ After B-ML3 | Re-run on all `match_feature_vectors` rows, write to `model_versions` |
| XGB-RETRAIN | S6-P2 | Weekly XGBoost full-model retraining | 3-4h | ⬜ | ⏳ After S6-P2 | Train/val split, track feature importances over time |
| ALN-AUTO | ALN-1 | Monthly alignment threshold refresh | 1h | ⬜ | ⏳ After ALN-1 | Bin settled bets by alignment_count → ROI per bin → update thresholds |
| INPLAY-RETRAIN | P3.4 | Quarterly in-play model retraining | 2h | ⬜ | ⏳ After P3.4 | Seasonal — late-season desperation changes how game states map to results |

---

## Tier 5 — Future / Speculative

| ID | Task | ☑ | Ready? | Notes |
|----|------|----|--------|-------|
| SLM | Shadow Line Model: predict what opening odds *should be* | ⬜ | ⏳ Blocked | Needs opening odds timestamp storage |
| MTI | Managerial Tactical Intent: press conference classification | ⬜ | ⏳ Blocked | No reliable transcript source across leagues |
| RVB | Referee/Venue full bias features | ⬜ | ⏳ Blocked | Venue-level stats not yet collected |
| WTH | Weather signal (OpenWeatherMap, free) | ⬜ | ⏳ Low priority | Defer until O/U becomes a focus market |
| SIG-DERBY | Is-derby + travel distance signals | ⬜ | ⏳ Blocked | Needs team location data |

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
- **~~Faster settlement~~**: ✅ Done (RAIL-13) — per-match settlement triggered by LivePoller on FT detection
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
