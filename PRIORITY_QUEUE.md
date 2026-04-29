# OddsIntel — Master Priority Queue

> Single source of truth for ALL open tasks. Every actionable item across all docs lives here.
> Other docs may describe features but ONLY this file tracks task status.
> Last updated: 2026-04-29 — ML sprint complete + 8 autonomous tasks: score fix, MKT-STR, ML-3 form strip, FE-AUDIT, SUX-8 Signal Timeline, SUX-11 Why This Pick, SUX-12 CLV Tracker, AF-EVAL analysis. Migration 019 adds market_implied feature columns + form_home/form_away on matches.

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
| — | SUPABASE-PRO | Upgrade Supabase to Pro ($25/mo) | 15 min | ⬜ | High | Infrastructure | Before production Stripe | Required before accepting real payments (point-in-time recovery). Also needed as DB approaches 500 MB free limit (~weeks away at current growth). |
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
| 22 | PLATT | Platt scaling once 500+ predictions have outcomes | 1 day | ⬜ | High | Internal | ~mid-May 2026 | Replaces/complements tier-specific shrinkage |
| 23 | P5.1 | European Soccer DB (Kaggle): 13-bookmaker sharp/soft analysis | 1-2 days | ⬜ | High | Internal | ~May 2026 | `bookmaker_sharpness_rankings.csv` + `sharp_money_signal` feature |
| 24 | PIN-1 | Pinnacle anchor signal: `model_prob - pinnacle_implied` as feature | 2-3h | ⬜ | High | Internal | ~May 2026 | Depends on P5.1 to confirm Pinnacle is in our 13 bookmakers |
| 25 | BDM-1 | Bookmaker disagreement signal | 1h | ✅ Done | Medium | Internal | Done | compute_bookmaker_disagreement() written to match_signals |
| 26 | FE-LIVE | Live odds in-play on match detail (frontend only) | 1 day | ✅ Done 2026-04-29 | Medium | ROADMAP Frontend Backlog #9 | Done | getLiveMatchOdds() fetches is_live=true odds_snapshots by match minute. LiveOddsChart: recharts 1X2 lines by match minute + current best odds row. Polls /api/live-odds every 5min during live matches. GET /api/live-odds: Pro-gated API route. Shown for live + finished matches on match detail. |
| — | ODDS-OU-CHART | O/U 2.5 movement chart on match detail (Pro) | 2-3h | ✅ Done 2026-04-29 | Medium | Data audit 2026-04-29 | Done | Purple/orange Over/Under line chart below 1X2 chart. getOddsMovement() now fetches both 1x2 and over_under_25 markets. |
| — | ODDS-BTTS | BTTS odds per bookmaker in Pro match detail | 2-3h | ✅ Done 2026-04-29 | Medium | Data audit 2026-04-29 | Done | BTTS Yes/No columns added to main odds comparison table. Best odds highlighted green. Data from `btts` market in odds_snapshots. |
| — | ODDS-MARKETS | Show O/U 1.5 and O/U 3.5 lines in Pro odds table | 1-2h | ✅ Done 2026-04-29 | Low | Data audit 2026-04-29 | Done | Separate "Over/Under Lines" card with O/U 1.5 and O/U 3.5 per bookmaker. Only renders when data exists. |
| 27 | MKT-STR | Wire market-implied team strength into XGBoost as input feature | 1 day | ✅ Done 2026-04-29 | Medium | Internal (MODEL_ANALYSIS 11.3) | Done | `market_implied_home/draw/away` signals already stored in match_signals (write_morning_signals lines 1769-1780). Added extraction in `_build_feature_row()` signal loop + added to return dict. Migration 019 adds columns to match_feature_vectors. |
| 28 | EXPOSURE-AUTO | Auto-reduce stakes on league exposure concentration | 1h | ✅ Done 2026-04-29 | Medium | Internal (MODEL_ANALYSIS 11.6) | Done | 3rd+ bet in same league per bot gets 50% stake reduction. Enforced during placement in daily_pipeline_v2.py. _check_exposure_concentration() still runs as post-placement audit log. |
| 29 | F8 | Stripe integration (Pro + Elite, webhook, tier column update) | 2-3 days | ✅ Done 2026-04-29 | High | Internal | Done | See Tier 1 row — full breakdown there. |
| — | LP-1 | Landing page: fix strikethrough pricing | 15 min | ✅ Done 2026-04-29 | Low | Landing Page Review (2026-04-29) | Done | No strikethrough was present — cards already show badge-only. Verified. |
| — | LP-2 | Landing page: remove Elite annual pricing | 15 min | ✅ Done 2026-04-29 | Low | Landing Page Review (2026-04-29) | Done | Elite card never had annual pricing shown. Verified. |
| — | LP-3 | Landing page: consolidate Founding Member urgency | 15 min | ✅ Done 2026-04-29 | Low | Landing Page Review (2026-04-29) | Done | Removed bottom banner. Card badges are single source of truth now. |

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
| — | AF-EVAL | Evaluate AF Pro tier ($19/mo, 7.5K req/day) vs Ultra ($29/mo) | Research | ✅ Done 2026-04-29 | Low | Data Sources | Done | **Estimated daily usage: ~1,500–2,500 req/day** (normal days). Breakdown: morning ~300 (1 fixtures + ~100 predictions + 7 injury batches + ~40 team stats + ~50 standings + ~100 H2H + 1 bulk odds), odds pipeline ~9 (1 bulk/run × 9 runs), live tracker ~1,200 (3 calls × avg 10 live matches × 120 runs), settlement ~320 (4 calls × 80 matches). **Peak days** (CL group stage, 50+ simultaneous live): ~6,000–7,000 req/day — still within 7,500 cap. **Recommendation: switch to Pro ($19/mo)** — saves $10/mo ($120/yr) with 3-5× daily headroom. Add `get_remaining_requests()` logging to morning pipeline as safety check. |

---

## Tier 3 — 1-2 Months

| # | ID | Task | Effort | Status | Impact | Source | Timeline | Notes |
|---|-----|------|--------|--------|--------|--------|----------|-------|
| 34 | HIST-BACKFILL | Backfill historical match data using spare API quota | 2-3 days | ⬜ | Very High | Internal (MODEL_ANALYSIS 11.3) | ~May-June 2026 | ~67K spare req/day. Fetch historical matches + stats + 13-bookmaker odds. Accelerates XGBoost retraining timeline from months to weeks |
| 35 | B6 | Singapore/South Korea odds source (Pinnacle API or OddsPortal) | Unknown | ⬜ | Very High | Internal | ~June 2026 | +27.5% ROI signal has no live odds feed. Note: AF has odds for Korea K League but NOT Singapore. Pinnacle via The Odds API ($20/mo) is best path |
| 36 | P5.2 | Footiqo: validate Singapore/Scotland ROI with independent 1xBet closing odds | Manual first | ⬜ | High | Internal | ~June 2026 | Independent validation. If ROI holds on 2nd source, it's real |
| 37 | P3.1 | Odds drift as XGBoost input feature (model retraining) | 1-2 days | ⬜ | High | Internal | ~June 2026 | Currently veto filter only. Strongest unused signal once data is there |
| 38 | P3.3 | Player-level injury weighting (weight by position/market value) | 2-3 days | ⬜ | Low | Internal | ~June 2026 | ~90% captured by injury_count + news_impact per AI analysis. Lower priority than originally scoped |
| 39 | S6-P2 | Graduate meta-model to XGBoost + full signal set (1000+ bot bets) | 2-3 days | ⬜ | Very High | Internal | ~June 2026 | After alignment thresholds validated |
| 40 | P4.1 | Audit trail ROI comparison: stats-only vs after-AI vs after-lineups | 1 day | ⬜ | High | Internal | ~June 2026 | Proves value of each information layer. Needed for Elite tier pricing |
| 41 | P3.5 | Feature importance tracking per league | 1 day | ⬜ | Medium | Internal | ~June 2026 | Which signals matter in which markets |
| 42 | F10 | My bets / tip tracking (user_bets table, personal P&L) | 2 days | ⬜ | Medium | Internal | After M2 | Skip until Stripe + Elite launch |
| 43 | F7 | Stitch redesign (landing + matches page) | Awaiting designs | ⬜ | Medium | Internal | After M1 | Parked until after M1 go-live |
| 68 | SUX-11 | "Why This Pick" reasoning card UI (Elite match detail) | 1-2 days | ✅ Done 2026-04-29 | High | UX Review (2026-04-29) | Done | `why-this-pick.tsx` Elite only. Static signal→text mapping (no LLM call). `buildReasons()` translates BDM, overnight move, form slope, fixture importance diff, H2H, injuries, ELO, referee bias → plain English with confidence=strong/moderate/weak. Up to 5 reasons per match. Sparkles icon + Elite badge. |
| 69 | SUX-12 | CLV tracking dashboard (Elite) | 1-2 days | ✅ Done 2026-04-29 | Medium | UX Review (2026-04-29) | Done | `clv-tracker.tsx` Elite only. `getMatchCLVData()` fetches pseudo_clv_home/draw/away from matches + settled simulated_bets for this match. Shows CLV per selection + avg + plain-English interpretation. Settled bets table with odds at pick, closing odds, CLV badge, result. Explains "Closing Line Value" concept inline. |

---

## Tier 4 — 2-3 Months (needs data accumulation)

| # | ID | Task | Effort | Status | Impact | Source | Timeline | Notes |
|---|-----|------|--------|--------|--------|--------|----------|-------|
| 44 | SIG-12 | xG overperformance rolling signal: recent xG vs actual goals | 2h | ⬜ | Medium | AI Analysis (2026-04-28) | Needs ~2 wks data | Team over/underperforming their xG → regression to mean. Needs ~2 weeks of post-match xG from T4 enrichment |
| 45 | MOD-2 | Learned Poisson/XGBoost blend weights (replace fixed α constants) | 2h | ⬜ | High | AI Analysis (2026-04-28) | Needs 500+ settled | Calibrated per-tier blend weights from actual prediction outcomes |
| 46 | P3.4 | In-play value detection model (minute X state → final result) | 2-3 weeks | ⬜ | High | Internal | Needs 500+ live | Needs 500+ completed matches in live_match_snapshots (July-Aug 2026) |
| 47 | P4.2 | A/B bot testing framework (parallel bots with/without AI) | 1-2 days | ⬜ | Medium | Internal | Needs audit trail | Needs audit trail + data |
| 48 | P4.3 | Live odds arbitrage detector (cross-bookmaker real-time) | 1-2 days | ⬜ | Medium | Internal | ~July 2026 | Per-bookmaker odds ✅ — can build but low priority |
| 49 | P5.3 | OddAlerts API evaluation (20+ bookmakers real-time) | Research | ⬜ | Medium | Internal | Depends P5.1 | Depends on P5.1 sharp/soft model |
| 50 | RSS-NEWS | RSS news extraction pipeline (speed edge) | 1-2 days | ⬜ | High | Internal (MODEL_ANALYSIS 11.5) | Profitable first | $30-90/mo cost — deferred until model proves profitable. Targets news before odds adjust. Re-evaluate when Elite tier has subscribers |
| 51 | OTC-1 | Odds trajectory clustering (DTW on full timelines, cluster shapes) | 1-2 weeks | ⬜ | Low | Internal | Needs 1000+ | Downgraded: AI Analysis notes simple volatility+drift captures ~same signal at 5% the effort |
| 52 | P3.2 | Stacked ensemble meta-learner (logistic regression: when Poisson vs XGBoost) | 1-2 days | ⬜ | Medium | Internal | Needs settled bets | Needs settled bets with both predictions stored |

---

## Tier 5 — Future / Speculative

| # | ID | Task | Impact | Source | Notes |
|---|-----|------|--------|--------|-------|
| 53 | SLM | Shadow Line Model: predict what opening odds *should be* | High | Internal | Blocked on opening odds timestamp storage |
| 54 | MTI | Managerial Tactical Intent: press conference classification | Medium | Internal | Blocked on reliable transcript sources across leagues |
| 55 | RVB | Referee/Venue full bias features (beyond S4 referee stats) | Medium | Internal | Venue-level stats not yet collected |
| 56 | WTH | Weather signal (OpenWeatherMap, free) | Low | Internal | Low effort, defer until O/U becomes a focus market |
| 57 | SIG-DERBY | Is-derby + travel distance signals | Low | Internal | Needs team location data. SIGNAL_ARCHITECTURE.md Group 5 gap |

---

## Key Thresholds to Watch

| Milestone | Query | Target | Current |
|-----------|-------|--------|---------|
| LLM team name resolve | `wc -l data/logs/unmatched_teams.log` before vs after `--apply` | Shrinks toward 0 | 2,287 entries |
| Platt scaling ready | `SELECT COUNT(*) FROM predictions p JOIN matches m ON p.match_id = m.id WHERE m.status = 'finished'` | 500+ | ~? |
| Meta-model Phase 1 ready | `SELECT COUNT(*) FROM matches WHERE status = 'finished' AND pseudo_clv_home IS NOT NULL` | 3000+ | 0 (just built) |
| Alignment threshold validation | `SELECT COUNT(*) FROM simulated_bets WHERE result != 'pending' AND alignment_class IS NOT NULL` | 300+ | ~? |
| Meta-model Phase 2 ready | `SELECT COUNT(*) FROM simulated_bets WHERE result != 'pending' AND dimension_scores IS NOT NULL AND clv IS NOT NULL` | 1000+ | ~? |
| In-play model ready | `SELECT COUNT(DISTINCT match_id) FROM live_match_snapshots` | 500+ | ~? |
| Market-implied strength ready | `SELECT COUNT(DISTINCT m.id) FROM matches m JOIN odds_snapshots o ON m.id = o.match_id WHERE m.status = 'finished'` | 200+ | ~? |
| Post-mortem patterns readable | `SELECT COUNT(*) FROM model_evaluations WHERE market = 'post_mortem'` | 14+ | 0 (just built) |

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
