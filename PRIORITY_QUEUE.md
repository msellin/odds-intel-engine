# OddsIntel — Master Priority Queue

> Single source of truth for ALL open tasks. Every actionable item across all docs lives here.
> Other docs may describe features but ONLY this file tracks task status.
> Last updated: 2026-05-06 — Full cleanup: done items moved to Done sections. Open = only genuinely open tasks. CAL-* calibration tasks + PIN-2..6 Pinnacle expansion added.

**Column guide:**
- **☑** — `⬜` not started · `🔄` in progress · `✅` done
- **Ready?** — `✅ Ready` pick up now · `⏳ Waiting [reason]` blocked

---

## Gemini AI Cost Tracker

> Billing enabled 2026-05-05. Prices: `gemini-2.5-flash` $0.15/1M input + $0.60/1M output · `gemini-2.5-flash-lite` $0.075/1M input + $0.30/1M output.

### Running now

| Job | Model | Calls/day | Tokens/call | $/mo now | Scales with |
|-----|-------|-----------|-------------|----------|-------------|
| `news_checker` (4×/day per active bet) | flash | ~64 (4 × 16 bets) | ~800 | **~$0.38** | Active bets — 50 bets = ~$1.20/mo |
| `match_previews` (ENG-3, 1×/day) | flash | ~10 | ~1,200 | **~$0.11** | Fixed (top 10 matches) |
| `settlement post-mortem` (1×/day) | flash | 1 batch | ~2,000 | **~$0.04** | Fixed |
| `bet-explain` (BET-EXPLAIN, user-triggered) | flash-lite | ~5 new/day | ~800 | **~$0.02** | New bets only — cached after 1st call |
| **Total running** | | | | **~$0.55/mo** | |

### Planned (not yet built)

| ID | Feature | Model | Calls/day | $/mo at launch (10 users) | $/user/mo |
|----|---------|-------|-----------|--------------------------|-----------|
| MTI | Managerial tactical intent (press conf.) | flash | ~10 (5 matches × 2 teams) | **~$0.22** flat | negligible per user |
| RSS-NEWS | RSS news extraction pipeline | flash | ~20 articles | **~$0.30** Gemini only | negligible — data service ($30-90/mo) is the real cost |

**Current total AI cost: ~$0.55/mo running + $0/mo planned = ~$0.55/mo**

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
| LLM-RESOLVE | Run `scripts/resolve_team_names.py --apply` | 30 min | ✅ | ✅ Done | 143 total mappings. 204 unmatched names accounted for. **AI: $0 ongoing (one-time batch)** |

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
| CAL-DIAG-1 | SQL diagnostic on 77 settled home bets: avg Poisson vs XGB prob, sharp_consensus direction, pre-Platt vs post-Platt comparison | 1h | ✅ Done 2026-05-06 | ✅ Ready | Results: n=31 bets, model=38.2%, calibrated=42.0% (Platt inflated +3.87pp), market_implied=29.0%, actual=25.8%. Pinnacle=30.2% — closer to actual than model. Sharp consensus avg=−0.0034. Gate coverage: 1/23 losses caught, 7 missing signal. `scripts/run_cal_diag.py` |
| CAL-PIN-SHRINK | Switch shrinkage anchor from market avg → Pinnacle (with soft-book fallback when Pinnacle unavailable) | 30min | ✅ Done 2026-05-06 | ✅ Ready | `calibrate_prob()` now accepts `anchor_implied`; Pinnacle-implied used when available for 1X2 Home. Batch-loaded from match_signals in daily_pipeline_v2. Soft-book fallback preserved when Pinnacle unavailable. `workers/model/improvements.py` |
| CAL-ALPHA-ODDS | Odds-conditional α reduction: `if odds > 3.0: alpha = max(alpha - 0.20, 0.10)` | 30min | ✅ Done 2026-05-06 | ✅ Ready | Note: alpha = model weight in this codebase (opposite of AI consultant convention — they used α = market weight). Reducing alpha pulls harder toward anchor for longshots. Targets 0.30-0.40 bin (23 bets, 13% actual vs 35.5% predicted). `workers/model/improvements.py` |
| CAL-SHARP-GATE | Skip 1X2 Home bets when `sharp_consensus_home < −0.02` | 1h | ✅ Done 2026-05-06 | ✅ Ready | Batch-loads `sharp_consensus_home` from match_signals alongside Pinnacle. Gate fires in betting loop after PIN-VETO check. Coverage currently low (1/23 losses, 7 missing signal) — will improve as more bets settle with signal data. `workers/jobs/daily_pipeline_v2.py` |
| CAL-DRAW-INFLATE | Add draw inflation factor to Poisson convolution: `adjusted_draw = raw_draw_prob × 1.08`, renormalize home/away | 1h | ✅ Done 2026-05-06 | ✅ Ready | Applied after DC correction in `_poisson_probs()`. DRAW_INFLATE=1.08 constant; excess probability redistributed proportionally to home/away. Unlocks draw market betting. `workers/jobs/daily_pipeline_v2.py`. |
| TZ-TOMORROW | Tomorrow's matches tab on matches page | 2-3h | ✅ Done 2026-05-06 | ✅ Ready | `getPublicMatches(dayOffset)` accepts 0=today, 1=tomorrow. URL param `?tab=tomorrow`. Yesterday overhang skipped on tomorrow tab. WhatChangedToday hidden on tomorrow tab. Also shipped: parallel odds RPC batches (was sequential) + replaced 60k-row signal count query with `get_signal_counts` RPC (migration 051). |
| RAIL-POLL-TUNE | Tune LivePoller intervals to reduce Railway cost ~25% | 30min | ⬜ | ✅ Ready | Two constant changes in `live_poller.py`: (1) `FAST_INTERVAL` 30s→45s — still far faster than any competitor, saves ~35% of bulk polling calls. (2) `MEDIUM_MULTIPLIER` 2→3 — stats every 90s instead of 60s, no meaningful quality loss. Together: ~25% reduction in AF API calls + Railway CPU/network cost. Estimated savings: ~$1.50-2/mo — enough to stay within Hobby $5/mo credit. |
| B-ML3 | First meta-model: 8-feature logistic regression, target=pseudo_clv>0 | 1 day | ⬜ | ⏳ ~May 17 (need 3K quality CLV rows) | **Data quality cutoff: use only `match_feature_vectors` rows WHERE `captured_at >= 2026-05-06`** — pre-cutoff rows lack Pinnacle signals (NULLs on the strongest feature). Shifts readiness from ~May 10 to ~May 17 (11 days × 280 matches/day). Filter: `WHERE pinnacle_implied_home IS NOT NULL`. Features per META-2. Feature notes: (1) `model_prob - pinnacle_implied` — likely strongest; (2) keep `odds_drift`, drop `overnight_line_move` (0.7+ correlated); (3) validate `news_impact_score` AUC > 0.52 first; (4) add `odds_at_pick` (raw); (5) add `time_to_kickoff` (hours). Source: 4-AI Calibration Review + data quality analysis 2026-05-06 |
| BOT-TIMING | Time-window bot cohorts: morning/midday/pre-KO A/B test | 2-3h | ✅ | ✅ Done 2026-05-01 | 16 bots → 5 morning / 6 midday / 5 pre_ko. `BOT_TIMING_COHORTS` dict + cohort param in run_morning(). Migration 032 adds timing_cohort to simulated_bets. Scheduler auto-selects cohort by UTC hour. |
| POSTGREST-CLEANUP | Migrate remaining PostgREST callers to psycopg2 | 3-4h | ✅ | ✅ Done 2026-05-03 | All workers + scripts fully migrated. Last batch: `fit_platt.py` (SQL JOIN replaces paginated PostgREST), `backfill_historical.py` (all progress tracking + bulk event INSERT), `live_tracker.py` (crash fix — undefined `client`). `get_client()` lives exclusively in `supabase_client.py` internals. Backfill moved to Railway 02:00 UTC daily. |
| PERF-1 | Batch morning signal writing — replace 25-40 per-match DB queries | 2-3h | ✅ | ✅ Done 2026-05-03 | `batch_write_morning_signals()` in supabase_client.py: 10 bulk queries (ANY(match_ids[])) + one execute_values INSERT replaces ~14K serial round-trips. Reduced 34-70 min bottleneck to ~15s. Added league_id to match_dict for SIG-11. |
| PERF-2 | Rewrite prune_odds_snapshots.py — single SQL DELETE | 1h | ✅ | ✅ Done 2026-05-03 | Replaced per-match PostgREST iteration with one DISTINCT ON subquery DELETE. Prunes all finished matches in a single statement. Migrated to psycopg2. |
| STRIPE-PROD | Swap Stripe to production keys | 1h | ✅ Done 2026-05-04 | ✅ Done | Live products created (Pro €4.99, Elite €14.99 + yearly + founding). All Vercel env vars updated. Live webhook `https://www.oddsintel.app/api/stripe/webhook`. Deployed. |
| GH-CLEANUP | Remove pipeline workflow files from GitHub Actions | 30min | ✅ Done 2026-05-05 | ✅ Done | Deleted fixtures/enrichment/odds/predictions/betting/live_tracker/news_checker/settlement .yml. Only migrate.yml + backfill.yml remain. |
| BOT-PROVEN | `bot_proven_leagues` — focused strategy targeting only the 5 cross-era backtest-confirmed leagues (Singapore/Scotland/Austria/Ireland/S.Korea) | 1h | ✅ Done 2026-05-05 | Added to BOTS_CONFIG + BOT_TIMING_COHORTS (midday). 17th bot. Clean performance track for strongest backtest signals. |
| RHO-DYN | Dynamic Dixon-Coles rho per league tier — fit rho from historical scoreline frequencies instead of global -0.13 | 2h | ✅ Done 2026-05-05 | `scripts/fit_league_rho.py` → `model_calibration` (market=`dc_rho_tier_{n}`). `_load_dc_rho_cache()` + `_poisson_probs(rho=)` in pipeline. Sunday refit step 6/6. Falls back to -0.13 if <200 matches/tier. |
| N4/N6/N9 | Settlement 01:00 UTC, watchlist 14h lookback, stagger 20:35 | 30min | ✅ Done 2026-05-05 | Settlement overnight run added (21:30+ KO extra time). ODDS_LOOKBACK_HOURS 6→14 covers overnight drift. Watchlist 20:30→20:35 avoids collision with betting refresh. Tested dry-run. |
| N5 | Afternoon + evening value bet alert emails for Pro/Elite | 2h | ✅ Done 2026-05-05 | `run_value_bet_alert(slot)` in email_digest.py. Afternoon (16:00, since 10:00 UTC) + Evening (20:45, since 17:00 UTC). Migration 046: `value_bet_alert_log` UNIQUE(user_id, alert_date, slot). No-op if no new bets. Pro gets count+CTA, Elite gets full table. |
| N7 | Full enrichment (all 4 components) at 13:00 UTC | 30min | ✅ Done 2026-05-05 | `job_enrichment_full()` added to scheduler at 13:00 UTC. `run_enrichment()` with no components filter = standings+H2H+team_stats+injuries. Ensures H2H+team_stats fresh for afternoon/evening betting refreshes. |
| SCHED-AUDIT | Full cron audit — 10 gaps fixed + 3 structural bugs | 2h | ✅ Done 2026-05-05 | 6 betting runs/day (was 4): added 09:30 + 20:30. Closing odds 20:00. Enrichment 12:00→10:30. News 14:30 added, 19:30→18:30. Previews 07:00→07:15. Platt+blend Wed+Sun. N1: LivePoller 24/7 (was 10-23 UTC), adaptive 30s live / 120s idle. N2: betting pipeline now filters `status='scheduled'` AND `kickoff > now` — no more bets on live matches. N3: `fixture_to_match_dict` passes `af_status_short`; `store_match` updates status→'postponed' + kickoff time for existing scheduled matches; fixture refresh job 4×/day (09:15, 10:45, 14:45, 18:45). |
| ADMIN-TIER-PREVIEW | Superadmin tier preview switcher — switch between free/pro/elite to QA any page | 2-3h | ✅ Done 2026-05-04 | ✅ Done | Cookie-based override. (1) `src/lib/get-user-tier.ts` shared utility — wraps profile fetch, checks `preview_tier` httpOnly cookie when `is_superadmin=true` and overrides tier; replace ~5 pages that inline `.select("tier, is_superadmin")` with this. (2) `/api/set-preview-tier` POST route — sets/clears cookie, superadmin-only server-validated. (3) Floating pill UI (`src/components/superadmin-tier-bar.tsx`) — fixed overlay, only renders for superadmins, shows current preview tier badge + free/pro/elite/"My Tier" buttons, added to app layout. Cookie is httpOnly+sameSite=lax. Works cleanly: all pages are dynamic (no ISR), so cookie flip = instant re-render with different tier data. Visual banner ensures you always know which tier you're previewing. |

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
| P5.1 | Sharp/soft bookmaker classification + sharp_consensus signal | ✅ Done 2026-05-03 | `data/bookmaker_sharpness_rankings.csv` (13 bookmakers, 3 tiers). `sharp_consensus_home` signal in `batch_write_morning_signals`. |
| PIN-1 | Pinnacle anchor signal: `pinnacle_implied_home` stored per match | ✅ Done 2026-05-04 | `batch_write_morning_signals()` in supabase_client.py. |
| PIN-VETO | Pinnacle disagreement veto for 1X2 home bets (gap > 0.12 → skip) | ✅ Done 2026-05-06 | `PINNACLE_VETO_GAP = 0.12` in `daily_pipeline_v2.py`. Empirical: catches 22/34 losses, filters 6/40 wins. |
| ODDS-API | ~~Activate The Odds API for Pinnacle odds ($20/mo)~~ | ❌ Cancelled | AF already provides Pinnacle. |
| LEAGUE-ORDER | 6-tier league priority system | ✅ Done 2026-05-05 | Migration 044. |
| ALN-FIX | Alignment NONE class when active=0 | ✅ Done 2026-05-04 | `improvements.py:compute_alignment()`. |
| ALN-EXPAND | sharp_consensus + Pinnacle anchor as alignment dimensions 5+6 | ✅ Done 2026-05-04 | `improvements.py`. |
| PERF-CACHE | Pre-stored dashboard stats in DB via settlement | ✅ Done 2026-05-04 | Migration 035. `write_dashboard_cache()` in settlement.py. |
| FE-BOT-DASH | Bot P&L dashboard (superadmin-gated) | ✅ Done 2026-05-04 | `/admin/bots` page. |

### Open

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| CODE-SONAR-WEB | Fix SonarCloud findings on odds-intel-web: **B security (16 issues)** + **D reliability (52 bugs)**. Hotspots reviewed: 0%. Duplications: 4.4%. 549 maintainability issues. | 1-2 days | ⬜ | ✅ Ready | Security B on a live public app serving paying customers is the highest-priority code quality issue across both repos. Drill into Sonar dashboard: triage security hotspots first, then D-reliability bugs (likely null-checks, unhandled promise rejections, unsafe type assertions). Maintainability 549 = mostly code smells, address after bugs. |
| CODE-WEB-ESLINT | Fix 9 ESLint errors + 16 warnings in odds-intel-web. **Errors:** `signal-delta.tsx:84` setState sync in effect (cascading renders); `superadmin-tier-bar.tsx:28` JSX inside try/catch (errors won't be caught); `login-modal.tsx`, `match-notes.tsx`, `match-pick-button.tsx`, `cookie-banner.tsx`, `api/stripe/upgrade` (review each). **Warnings:** 63 complexity violations — worst offenders: `bet-explain GET` (59), engine-data functions (60, 64), `bankroll/page` (40), `my-picks` (27). 2 auto-fixable with `--fix`. Complexity rule added to `eslint.config.mjs` (threshold=10). | 2-3h | ⬜ | ✅ Ready | Do alongside or after CODE-SONAR-WEB — overlapping issues. tsc is clean (strict=true already set). |
| CODE-WEB-KNIP | Remove dead code found by Knip in odds-intel-web: **20 unused files** (components + lib files never imported), **24 unused exports**, **23 unused exported types**. Key files: `src/lib/mock-data.ts`, `src/lib/types.ts`, `src/lib/queries.ts`, `src/lib/supabase.ts` (old Supabase client?), `src/components/track-record-client.tsx`, `src/components/value-bets-client.tsx`, `src/components/match-detail-tabs.tsx`. Also: 6 unused engine-data.ts query functions (getTodayOdds, getAvailableLeagues, getDashboardCache etc). | 1-2h | ⬜ | ✅ Ready | Low risk — dead code can be deleted. Verify each file isn't dynamically imported before deleting. `src/lib/supabase.ts` may be the old PostgREST client superseded by server-side auth — confirm before deleting. |
| PIN-2 | Extend Pinnacle signals to all bet markets | 1h | ✅ Done 2026-05-06 | ✅ Ready | Added `pinnacle_implied_draw`, `_away`, `_over25`, `_under25` to `batch_write_morning_signals()` via dedicated bulk query block (3b). `workers/api_clients/supabase_client.py`. |
| PIN-3 | Extend disagreement veto to draw/away/O/U markets | 1-2h | ✅ Done 2026-05-06 | ✅ Ready | Veto gate in `daily_pipeline_v2.py` now uses a selection→dict map covering Home/Draw/Away/Over 2.5/Under 2.5. Threshold 0.12 for all markets (tune per market once 50+ settled bets). Pinnacle anchor also extended to all markets in `calibrate_prob()` call. |
| PIN-4 | Pinnacle line movement signal | 1-2h | ✅ Done 2026-05-06 | ✅ Ready | `pinnacle_line_move_home/draw/away` added to `batch_write_morning_signals()`. Uses oldest vs most recent Pinnacle snapshot (requires 2+ snapshots). Positive = home shortened = sharp money backing. `workers/api_clients/supabase_client.py`. |
| PIN-5 | Pinnacle-anchored CLV | 2h | ✅ Done 2026-05-06 | ✅ Ready | `clv_pinnacle` column added via migration 050. New `get_pinnacle_closing_odds()` helper in `settlement.py`. Computed as `(odds_at_pick / pinnacle_closing_odds) - 1` and written alongside `clv` on every settlement. Falls back to latest Pinnacle snapshot when is_closing not flagged. |
| PIN-5-BACKFILL | Backfill clv_pinnacle on existing settled bets | 30min | ✅ Done 2026-05-06 | ✅ Ready | `scripts/backfill_clv_pinnacle.py` — updated 26/77 settled bets. Remaining 51 pre-date Pinnacle odds collection (PIN-1 started May 4). Run any time to catch newly settled bets. |
| CAL-PLATT-UPGRADE | Replace single-input Platt with 2-feature logistic: `X = [shrunk_prob, log(odds)]` | half day | ⬜ | ⏳ ~300+ settled bets/market (have ~77 total) | Learns that "40% at odds 3.6" needs different correction than "40% at odds 1.8". Do NOT implement sooner — will overfit. Source: 4-AI Calibration Review 2026-05-06. |
| ALN-1 | Dynamic alignment thresholds | 2h | ⬜ | ⏳ ~June 5 (need 300 clean settled bets) | **Data quality cutoff: validate on bets WHERE `created_at >= 2026-05-06` only** — pre-cutoff bets were placed by the old pipeline (no Pinnacle anchor, no CAL-ALPHA-ODDS, different veto coverage). Training on those teaches patterns from a system we have already replaced. At ~27 bets/day post-cutoff, 300 clean bets ≈ June 5. Pseudo-CLV does NOT substitute. |
| VAL-POST-MORTEM | Review 14 days of LLM post-mortem patterns | 30min | ⬜ | ⏳ May 13+ (have 2 rows, need 14) | `SELECT notes FROM model_evaluations WHERE market='post_mortem' ORDER BY date DESC LIMIT 14` |

---

## Engagement & Growth — Phase 1 (Launch Sprint — do this week)

> Full strategy in `docs/ENGAGEMENT_PLAYBOOK.md`. Reddit execution plan + post drafts in `docs/REDDIT_LAUNCH.md`. Launch phases + paid ads in `docs/LAUNCH_PLAN.md`. Phase 1 = ship with Reddit launch. Phase 2 = retention (weeks 3-6). Phase 3 = differentiation (months 2-3).

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ENG-3 | Daily AI match previews (top 5-10, Gemini) | 1-2 days | ✅ Done 2026-05-01 | ✅ Ready | `workers/jobs/match_previews.py`. Scheduler 07:00 UTC. `match_previews` table (migration 033). Free sees teaser, Pro/Elite see full 200-word preview. Triple-duty: on-site + email + social. Fixed 2026-05-05: predictions pivot (source=ensemble), odds_snapshots pivot, match_injuries schema. **AI: ~$0.11/mo (10 calls/day, flash)** |
| ENG-4 | Daily email digest via Resend | 2-3 days | ✅ Done 2026-05-05 | ✅ Ready | `workers/jobs/email_digest.py`. Scheduler 07:30 UTC. `email_digest_log` (migration 034). Free: teasers + CTA. Pro: + bet count. Elite: + full picks table. Branded HTML: dark `#0a0a14` header, ODDS white + INTEL green logo, green CTAs/badges. Fixed 2026-05-05: migration 042 backfills `user_notification_settings` for all existing users + trigger wired for new signups (was empty → zero sends). Tested end-to-end. |
| ENG-1 | "X analyzing this match" live counter | 4-6h | ✅ Done 2026-05-04 | ✅ Done | `match_page_views` table (migration 038). `/api/track-page-view` POST route — upserts session_id+match_id, returns 30-min window count. `MatchViewingCounter` client component in match header metadata row. Hidden until 2+ people (no self-only display). |
| ENG-2 | Community vote split display | 4-6h | ✅ Done 2026-05-04 | ✅ Done | `community-vote.tsx` updated: percentages + fill bars always visible when any votes exist. Locks at kickoff (live/finished) with Lock icon + "Locked at kickoff" label. Voting disabled for locked matches. |
| ENG-6 | Bot consensus on match detail ("7/9 models agree: Over 2.5") | 3-4h | ✅ Done 2026-05-03 | ✅ Ready | Data in `simulated_bets`. Zero new data needed. Free: count. Pro: markets. Elite: full breakdown |
| ENG-7 | Public /methodology page | Half day | ✅ Done 2026-05-03 | ✅ Ready | Plain-English model explanation. Trust anchor. Nobody else publishes this |
| ENG-5 | Betting glossary (10-15 SEO pages at /learn/[term]) | 2-3 days | ✅ Done 2026-05-05 | ✅ Done | 12 terms at /learn/[term]: EV, CLV, Kelly, value betting, Poisson, xG, BTTS, O/U, odds movement, margin, ELO, bankroll. FAQ schema. /learn index. Glossary nav link. Sitemap updated. |

---

## Engagement & Growth — Phase 2 (Retention Engine, weeks 3-6)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ENG-9 | Personal bet tracker + "Model vs You" dashboard | 3-4 days | ✅ Done 2026-05-05 | ✅ Done | my-picks: ROI%, units, W/L stats. Model vs You card after 5+ settled picks. Model prob + agree/disagree icon per row. Share button (native share API + clipboard fallback + OG image). |
| ENG-11 | "What Changed Today" widget on matches page | 1 day | ✅ Done 2026-05-05 | ✅ Done | `getWhatChangedToday()` in engine-data.ts: compares last 8h signals vs 20-32h ago, top 5 by abs delta. `what-changed-today.tsx` component: links to matches, free sees magnitude dot, Pro sees exact delta. |
| ENG-12 | Model vs Market vs Users triangulation | 4-6h | ✅ Done 2026-05-05 | ✅ Done | `getModelMarketUsers(matchId)` queries ensemble 1x2_home prediction + implied_prob + match_votes. `model-market-users.tsx`: 3 colored bars + tension text when model/market gap >5pp. On every match detail page. |
| ENG-13 | Shareable pick cards (branded image generation) | 1-2 days | ✅ Done 2026-05-05 | ✅ Done | `/api/og/pick` route: Next.js ImageResponse, accepts home/away/selection/odds/model_prob/result as query params. Share button on my-picks uses native Web Share API, falls back to clipboard. |
| ENG-14 | Auto-generated prediction pages for SEO (/predictions/[league]/[week]) | 2-3 days | ✅ Done 2026-05-05 | ✅ Done | `/predictions` index + `/predictions/[league]` pages. 8 featured leagues. Prob bars, model call badges, preview teasers, FAQ schema. "Predictions" nav link added. Sitemap updated. |
| ENG-8 | Watchlist signal alerts (email/push) | 3-4 days | ✅ Done 2026-05-05 | ✅ Done | `workers/jobs/watchlist_alerts.py`. Scheduler 08:30/14:30/20:30 UTC. Migration 045: `watchlist_alerts_enabled` + `watchlist_alert_log`. Free: kickoff reminder ≤2h before KO. Pro/Elite: odds movement ≥5% alert (6h lookback). Profile page toggle for all 3 notification types (daily digest, weekly report, watchlist alerts). |
| ENG-10 | Weekly performance email (Monday 08:00 UTC) | 1 day | ✅ Done 2026-05-05 | ✅ Done | `workers/jobs/weekly_digest.py`. Scheduler Monday 08:00 UTC. `weekly_digest_log` table (migration 043). Model W/L/units + user's picks + upcoming top matches. Uses `weekly_report` column (default true). Free: model stats + CTA. Pro/Elite: + personal pick stats + CLV. |

---

## Engagement & Growth — Phase 3 (Differentiation, months 2-3)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
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
| BET-EXPLAIN | Natural language bet explanations (Gemini, Elite-gated) | ✅ | GET /api/bet-explain. **AI: ~$0.02/mo (flash-lite, cached after 1st call per bet)** |
| SUX-4/5/6/7/8/9/10 | Signal summary, accordion, labels, hooks, timeline, delta, post-match reveal | ✅ | |
| SUX-11/12 | "Why This Pick" Elite card + CLV tracker | ✅ | |
| ML-1/2/3/4/5/6/7/8 | Logos, live timer, form strip, match filter tabs, predicted score, odds arrows, BM badge, match star | ✅ | |
| FE-FAV-1/2/3 | My Leagues bug fix + league ordering + per-match star | ✅ | |
| FE-BUG-1/2 / FE-AUDIT | Pro CTA bug, select dropdown bug, full tier gating audit | ✅ | |
| PIPE-2 / XGB-FIX / POISSON-FIX / DRAW-FIX | Pipeline cleanup + model fixes | ✅ | XGBoost retrained on 95K rows, joblib loader |
| LAUNCH-BETA / LAUNCH-PICK | Beta label, daily pick visible without login | ✅ | |
| AF-EVAL | AF Ultra confirmed required — do NOT downgrade (live polling needs 18K-45K calls/day) | ✅ | |
| KAMBI-BUG-1 | Duplicate value bets when Kambi league name ≠ AF name — added Bulgaria PFL 1 mapping + improved frontend dedup to normalise club prefixes (FK/FC/etc) and key on kickoff date | ✅ Done 2026-05-06 | |
| KAMBI-DROP | Drop Kambi entirely — empirical analysis showed "ub"=Unibet (AF has it), "paf"/"kambi"=36 rows/30 days. Removed scraper from pipeline, cleaned 20 league/50 team/7 fixture dupes via migration 047. | ✅ Done 2026-05-06 | |

---

## Tier 3 — 1-2 Months

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| HIST-BACKFILL | Historical data backfill (running on Railway) | — | 🔄 | 🔄 Active | Moved from GH Actions → Railway 02:00 UTC daily (2026-05-03). Fully psycopg2, self-stops when `backfill_complete.flag` exists. See § HIST-BACKFILL Plan |
| CODE-SONAR-ENGINE | Fix SonarCloud findings on odds-intel-engine: **C reliability (6 bugs)** + **7.3% duplication**. 486 maintainability issues. Security: A (clean). | 2-3h | ⬜ | ✅ Ready | 6 real bugs take priority over unused imports. Drill into Sonar: reliability bugs likely unreachable code / bad exception handling / logic errors in pipeline functions. 7.3% duplication aligns with what Radon found — supabase_client.py copy-paste patterns. Address bugs first, note duplication hotspots as input for CODE-RADON. |
| CODE-RUFF | Ruff auto-fix pass: 168 issues (48 unused imports, 37 bare f-strings, 8 multi-statement lines, 4 duplicate dict keys, 3 ambiguous names) | 30min | ⬜ | ✅ Ready | `ruff check . --fix` clears 88 automatically. Remaining 80 need manual review (E402 import-order is structural — scripts do sys.path before imports, leave those). Run after SonarCloud analysis for full picture. Zero business risk. |
| CODE-RADON | Structural complexity refactor: 3 god-files (supabase_client.py 3532 lines, daily_pipeline_v2.py 1902 lines, settlement.py 1542 lines). Key F-rated functions: `write_morning_signals` (CC=157), `batch_write_morning_signals` (CC=188), `run_morning` (CC=133), `run_live_tracker` (CC=77). | 3-5 days | ⬜ | ⏳ After SonarCloud + when no active tasks on these files | Do NOT start while active calibration/Pinnacle tasks are touching daily_pipeline_v2.py and supabase_client.py. Approach: split supabase_client into domain modules (signals.py, bets.py, features.py, match.py); extract sub-functions from F-rated pipeline functions. Carry real merge-conflict risk on active files. |
| XGB-HIST | Retrain XGBoost on backfilled data (~43K matches with stats+events) | 1 day | ⬜ | ⏳ After HIST-BACKFILL Phase 1 | Retrain result_1x2 + over_under on full AF features. Current: 96K Kaggle rows (limited features). New: richer per-match stats |
| B6 | Singapore/South Korea odds source | Unknown | ⬜ | ⏳ Research needed | +27.5% ROI signal, no live odds. AF has Korea K League odds but NOT Singapore. Pinnacle via Odds API ($20/mo) is best path |
| P5.2 | Footiqo: validate Singapore/Scotland ROI with 1xBet closing odds | Manual | ⬜ | ✅ Ready | Independent validation. If ROI holds on 2nd source, it's real |
| P3.1 | Odds drift as XGBoost input feature | 1-2 days | ⬜ | ⏳ ~June (needs more data) | Currently veto filter only. Strongest unused signal once data accumulates |
| P3.3 | Player-level injury weighting (by position/market value) | 2-3 days | ⬜ | ⏳ Low priority | ~90% captured by injury_count + news_impact already |
| S6-P2 | Graduate meta-model to XGBoost + full signal set | 2-3 days | ⬜ | ⏳ After ALN-1 (~late June) | After alignment thresholds validated at 300+ quality bets (>= 2026-05-06). ETA pushed from May W3 due to data quality cutoff. |
| P4.1 | Audit trail ROI: stats-only vs after-AI vs after-lineups | 1 day | ⬜ | ⏳ Needs data | Proves value of each layer. Needed for Elite pricing rationale |
| P3.5 | Feature importance tracking per league | 1 day | ✅ Done 2026-05-05 | ✅ Done | `scripts/compute_feature_importance.py` + migration 040. Pearson r per (league, signal, market). Run manually or extend Sunday refit. |
| F7 | Stitch redesign (landing + matches page) | Awaiting designs | ⬜ | ⏳ Awaiting designs | Parked until after first users arrive |
| ELITE-BANKROLL | Personal bankroll analytics dashboard (Elite) | 2-3 days | ✅ Done 2026-05-05 | ✅ Done | `/bankroll` server page (Elite-gated). `getUserBankrollData()` in engine-data.ts. `bankroll-chart.tsx` (recharts AreaChart). Summary stats (ROI, hit rate, net units, avg CLV, max drawdown). Model benchmark comparison. Per-league breakdown table. Recent picks with CLV. Nav link shown for Elite/superadmin. |
| ELITE-LEAGUE-FILTER | League performance filter for Elite value bets | 1 day | ⬜ | ⏳ After 3mo data | "Show only leagues where model hit rate > 45%". Needs data to be meaningful |
| ELITE-ALERT-STACK | Custom multi-signal alert stacking (Elite) | 2-3 days | ⬜ | ⏳ After ENG-8 | "Alert when confidence > 65% AND edge > 8% AND line moved in model's direction" |

---

## Infrastructure & Platform Optimization

> Identified 2026-05-05 via infra audit — features we're paying for or have for free but not using. Sorted by priority: 🔴 Critical (do ASAP, launch is live) → 🟡 High (this week) → 🟢 Medium.

| ID | Task | Effort | ☑ | Priority | Notes |
|----|------|--------|----|----------|-------|
| INFRA-1 | Stripe free trial (7-day Pro) | 15 min | ✅ Done 2026-05-05 | 🔴 ASAP | `subscription_data.trial_period_days=7` added to checkout session. `allow_promotion_codes=true` added. Webhook already handled `trialing` status. Deployed to Vercel. |
| INFRA-2 | Stripe promo code for Reddit launch | 5 min | ✅ Done 2026-05-05 | 🔴 ASAP | Code `REDDIT` — 100% off first month (duration=once). Created live via Stripe API. Added as reply to all 3 active Reddit posts (r/buildinpublic + 2 subs). |
| INFRA-3 | Supabase Custom SMTP + Auth email templates | 30 min | ✅ Done 2026-05-05 | 🔴 ASAP | Resend SMTP configured in Supabase Auth (smtp.resend.com:465, noreply@oddsintel.app). Magic link template updated with OddsIntel branding. Auth flow refactored from OTP code → magic link (`signInWithOtp` with `emailRedirectTo`). Server-side PKCE callback (`route.ts`). Unknown email on login auto-redirects to signup with email pre-filled. Supabase Site URL space removed, `https://oddsintel.app/**` wildcard added to redirect URLs. Apple Sign In setup deferred. |
| INFRA-12 | Apple Sign In | 1-2h | ⬜ | ⏳ When ready | Apple Developer account ready. Need: Services ID (`app.oddsintel.web`), Key (.p8 + Key ID + Team ID) → Supabase Auth → Sign In/Providers → Apple. Frontend: add `<AppleSignIn />` button alongside Google/Discord in login, signup, modal. Return URL: `https://jjdmmfpulofyykzwiuqr.supabase.co/auth/v1/callback`. Required if ever shipping iOS app. |
| INFRA-4 | PostHog conversion funnel setup | 1h | ✅ Done 2026-05-05 | ✅ Done | Funnel built in PostHog dashboard (Signup → Match → upgrade_clicked → upgrade_completed). Custom events added to pricing-cards.tsx + profile/page.tsx. upgrade_cancelled also tracked. |
| INFRA-5 | Vercel Speed Insights | 15 min | ✅ Done 2026-05-05 | 🟡 This week | `@vercel/speed-insights` installed. `<SpeedInsights />` added to root layout.tsx. Will auto-report LCP/FID/CLS to Vercel dashboard once deployed. |
| INFRA-6 | Sentry Crons monitoring for Railway jobs | 1h | ✅ Done 2026-05-05 | ✅ Done | `sentry-sdk>=2.0.0` added to requirements.txt. `_init_sentry()` in scheduler.py. `_run_job()` now wraps each job with `sentry_sdk.monitor(monitor_slug=...)`. 10 monitor slugs registered. `SENTRY_DSN` set in Railway. Cron monitors registered in Sentry UI. |
| INFRA-7 | PostHog feature flags for Tips launch | 1h | ⬜ | 🟡 Before M3 | Create `tips_enabled` flag in PostHog. Gate Tips section on this flag instead of hardcoded condition. When bot_aggressive validates → flip flag, no deploy needed. |
| INFRA-8 | Resend webhook → email open/click tracking | 2h | ✅ Done 2026-05-05 | ✅ Done | Migration 041 adds `last_email_opened_at` + `last_email_clicked_at` to profiles. `/api/resend-webhook` route handles `email.opened` + `email.clicked`. Svix signature verification. Webhook created in Resend dashboard. `RESEND_WEBHOOK_SECRET` set in Vercel + local .env.local. |
| INFRA-9 | Vercel Edge Config for feature flags | 2h | ⬜ | 🟢 Week of May 12 | Replace any DB queries used for global on/off flags with Vercel Edge Config (~1ms reads vs ~20ms DB). Good for: tips_enabled, maintenance_mode, featured_match_id. |
| INFRA-10 | Supabase DB Webhooks → watchlist alerts backend | 1 day | ⬜ | 🟢 When building ENG-8 | Instead of building a polling job for ENG-8 (watchlist alerts), use Supabase DB Webhooks: INSERT on match_signals with high injury_impact → fire Next.js API route → send Resend email. Eliminates most of ENG-8 backend complexity. |
| INFRA-11 | Supabase Realtime → replace live polling | 2 days | ⬜ | 🟢 Week of May 19 | Live score auto-refresh + ENG-1 viewing counter currently use HTTP polling. Replace with Supabase Realtime WebSocket subscriptions (included in Pro). Lower DB load, truly real-time UX. |

---

## Tier 4 — 2-3 Months (needs data accumulation)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| SIG-12 | xG overperformance rolling signal | 2h | ⬜ | ⏳ ~2 wks of post-match xG data | Regression to mean signal. Needs post-match xG from live snapshots |
| MOD-2 | Learned Poisson/XGBoost blend weights (replace fixed α) | 2h | ✅ Done 2026-05-05 | ✅ Done | `scripts/fit_blend_weights.py`: optimizes Poisson weight + per-tier shrinkage alpha. improvements.py loads from model_calibration, falls back to hardcoded. Weekly refit added to Sunday settlement. |
| P3.4 | In-play value detection model | 2-3 wks | ⬜ | ⏳ 500+ live snapshots (~July) | LightGBM Poisson regression. xG pace ratio is #1 feature. See § INPLAY Plan |
| P4.2 | A/B bot testing framework | 1-2 days | ⬜ | ⏳ Needs audit trail + data | Parallel bots with/without AI layers |
| P4.3 | Live odds arbitrage detector | 1-2 days | ⬜ | ⏳ ~July | Per-bookmaker odds exist. Low priority |
| RSS-NEWS | RSS news extraction pipeline ($30-90/mo) | 1-2 days | ⬜ | ⏳ After model proves profitable | Targets news before odds adjust. Re-evaluate when Elite has subscribers. **AI: +~$0.30/mo Gemini (data service $30-90/mo is the real cost)** |
| P3.2 | Stacked ensemble meta-learner (when Poisson vs XGBoost) | 1-2 days | ⬜ | ⏳ Needs settled bets with both predictions | Logistic regression on model disagreement |
| OTC-1 | Odds trajectory clustering (DTW) | 1-2 wks | ⬜ | ⏳ 1000+ snapshots | Low priority — volatility+drift captures ~same at 5% effort |

---

## Automation Sequels — Build Alongside Parent Task

> A model task is NOT done until its retraining is automated. Without these, calibration rots as data changes.

| ID | Parent | Task | Effort | ☑ | Ready? | Notes |
|----|--------|------|--------|----|--------|-------|
| PLATT-AUTO | PLATT | Weekly Platt recalibration in settlement | 1h | ✅ | ✅ Done | Sunday step runs `scripts/fit_platt.py` → `model_calibration` table |
| BLEND-AUTO | MOD-2 | Monthly Poisson/XGBoost blend weight recalculation | 1h | ✅ Done 2026-05-05 | ✅ Done | Weekly refit added to Sunday settlement step 5/5 alongside Platt. |
| META-RETRAIN | B-ML3 | Weekly meta-model retraining job | 2h | ⬜ | ⏳ After B-ML3 | Re-run on all `match_feature_vectors` rows, write to `model_versions` |
| XGB-RETRAIN | S6-P2 | Weekly XGBoost full-model retraining | 3-4h | ⬜ | ⏳ After S6-P2 | Train/val split, track feature importances over time |
| ALN-AUTO | ALN-1 | Monthly alignment threshold refresh | 1h | ⬜ | ⏳ After ALN-1 | Bin settled bets by alignment_count → ROI per bin → update thresholds |
| INPLAY-RETRAIN | P3.4 | Quarterly in-play model retraining | 2h | ⬜ | ⏳ After P3.4 | Seasonal — late-season desperation changes how game states map to results |

---

## Tier 5 — Future / Speculative

| ID | Task | ☑ | Ready? | Notes |
|----|------|----|--------|-------|
| SLM | Shadow Line Model: predict what opening odds *should be* | ⬜ | ⏳ Blocked | Needs opening odds timestamp storage |
| MTI | Managerial Tactical Intent: press conference classification | ⬜ | ⏳ Blocked | No reliable transcript source across leagues. **AI: ~$0.22/mo flat (10 calls/day, flash)** |
| RVB | Referee/Venue full bias features | ⬜ | ⏳ Blocked | Venue-level stats not yet collected |
| WTH | Weather signal (OpenWeatherMap, free) | ⬜ | ⏳ Low priority | Defer until O/U becomes a focus market |
| SIG-DERBY | Is-derby + travel distance signals | ⬜ | ⏳ Blocked | Needs team location data |

---

## Key Thresholds to Watch

| Milestone | Query | Target | Current (2026-05-06) | ETA |
|-----------|-------|--------|---------------------|-----|
| **Platt scaling ready** | Predictions with finished match outcomes | 500+ | **586 ✅ IMPLEMENTED 2026-04-30** | Done |
| Meta-model Phase 1 ready | `match_feature_vectors WHERE captured_at >= 2026-05-06 AND pinnacle_implied_home IS NOT NULL` | 3,000+ | ~0 (quality clock starts today) | ~May 17 (~280/day) |
| Alignment threshold validation | `simulated_bets WHERE result!='pending' AND created_at >= 2026-05-06` | 300+ | ~0 (quality clock starts today) | ~June 5 (~27 bets/day post-cutoff) |
| Post-mortem patterns readable | `model_evaluations WHERE market='post_mortem'` | 14+ | 2 | ~May 13 (+1/day) |
| In-play model ready | Distinct matches in live_match_snapshots WITH xG | 500+ | 243 | ~May 7-8 (~150/day). NOTE: live odds now fixed (2026-05-05) — ou_* fields were broken before |
| Meta-model Phase 2 ready | Settled bets with dimension_scores + CLV | 1,000+ | 0 | ~Aug (needs ALN-1 first) |
| XGBoost retrain on backfill | Backfill Phase 1 complete (match_stats) | ~18,000 | 3,474 matches done (~8 leagues complete, 49 in-progress) | ~May 7 |
| LLM team name resolve | `wc -l data/logs/unmatched_teams.log` | Shrinks toward 0 | 2,287 entries | Manual |

---

## § HIST-BACKFILL Plan — ✅ IMPLEMENTED (archived from PRIORITY_QUEUE 2026-05-05)

> Implementation complete. Script at `scripts/backfill_historical.py`, running on Railway 02:00 UTC daily.
> Phase 1: ~3,474 matches done. Full plan archived in git history.

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

## § RAILWAY Plan — ✅ COMPLETE (archived from PRIORITY_QUEUE 2026-05-05)

> All 5 phases complete 2026-04-30. Architecture running on Railway. Full plan archived in git history.

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
| 4-AI Calibration Review (2026-05-06) | 4 independent AI tools analyzed calibration failure on 77 settled bets (42% pred vs 26% actual on 1X2 home). Consensus: conditional miscalibration at high odds, not global. Priority fixes: Pinnacle shrinkage anchor, odds-conditional alpha, sharp consensus gate, draw inflation. |
| Data Analysis (2026-04-29) | From pipeline refactor + data source audit session (2026-04-29) |
| Launch Plan (2026-04-29) | From LAUNCH_PLAN.md pre-launch preparation |
| Tier Access Matrix | From TIER_ACCESS_MATRIX.md feature checklist |
| Data Sources | From DATA_SOURCES.md remaining cleanup |
| Landing Page Review (2026-04-29) | From landing page pricing/UX review |
