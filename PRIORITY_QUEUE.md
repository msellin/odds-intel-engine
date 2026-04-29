# OddsIntel — Master Priority Queue

> Single source of truth for ALL open tasks. Every actionable item across all docs lives here.
> Other docs may describe features but ONLY this file tracks task status.
> Last updated: 2026-04-29 — consolidated from ROADMAP, MODEL_ANALYSIS, SIGNAL_UX_ROADMAP, TIER_ACCESS_MATRIX, DATA_SOURCES, LAUNCH_PLAN, WORKFLOWS

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
| 19 | B3 | Tier-aware data API (Next.js layer strips fields by tier) | 1-2 days | ⬜ | High | Internal | ~May 2026 | **Blocking Milestone 2.** Pro users currently get same data as Free — only UI gates exist. Need server-side field stripping. |
| — | SUPABASE-PRO | Upgrade Supabase to Pro ($25/mo) | 15 min | ⬜ | High | Infrastructure | Before production Stripe | Required before accepting real payments (point-in-time recovery). Also needed as DB approaches 500 MB free limit (~weeks away at current growth). |
| — | STRIPE-PROD | Swap Stripe to production keys (5-step checklist in INFRASTRUCTURE.md) | 1h | ⬜ | High | Infrastructure | After Supabase Pro | 1) Switch to live mode 2) Re-run setup_stripe.py with live key 3) Update all Vercel STRIPE_* env vars 4) New live webhook endpoint + new whsec_ in Vercel 5) Supabase Pro must be done first |
| — | STRIPE-ANNUAL | Add annual billing option to profile page + landing page CTA | 2-3h | ⬜ | Medium | Internal | ~May 2026 | Pro €39.99/yr and Elite €119.99/yr price IDs already exist in Stripe. Just need UI toggle on profile page checkout and landing page pricing card. |
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
| 26 | FE-LIVE | Live odds in-play on match detail (frontend only) | 1 day | ⬜ | Medium | ROADMAP Frontend Backlog #9 | ~May 2026 | `odds_snapshots` with `is_live=true` already populated. Frontend chart during live match. Pro tier feature |
| 27 | MKT-STR | Wire market-implied team strength into XGBoost as input feature | 1 day | ⬜ | Medium | Internal (MODEL_ANALYSIS 11.3) | ~May 2026 | `compute_market_implied_strength()` exists in supabase_client.py but not wired into pipeline. Needs 200+ finished matches with odds first |
| 28 | EXPOSURE-AUTO | Auto-reduce stakes on league exposure concentration | 1h | ⬜ | Medium | Internal (MODEL_ANALYSIS 11.6) | ~May 2026 | Currently warning-only. Add proportional stake reduction when 3+ bets same league same day. Low effort, pure risk management |
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
| 30 | F5 | Value bets page redesign (free=teaser, Pro=directional, Elite=full picks) | 1-2 days | ⬜ | High | Internal | ~May 2026 | Blocking Milestone 3 |
| 31 | ALN-1 | Dynamic alignment thresholds (300+ settled bot bets → ROI by alignment bin) | 2h | ⬜ | High | Internal | ~June 2026 | Needs actual placed bets — pseudo-CLV does NOT substitute |
| 32 | VAL-POST-MORTEM | Review 14 days of LLM post-mortem patterns | 30 min | ⬜ | Medium | Internal (MODEL_ANALYSIS 11.4) | May 13+ | `SELECT notes FROM model_evaluations WHERE market = 'post_mortem' ORDER BY date DESC LIMIT 14;` — check if loss categories consistent. Decides if post-mortem feature is valuable |
| 33 | BET-EXPLAIN | Natural language bet explanations (LLM from dimension scores) | 1-2 days | ⬜ | Medium | Internal (MODEL_ANALYSIS end) | ~May 2026 | Frontend LLM prompt using stored bet data. Sells Elite tier — "why we like this pick". Zero betting ROI, high subscriber retention |
| 61 | SUX-4 | Summary tab on match detail: top 3-5 key signals in plain English | 1-2 days | ⬜ | High | UX Review (2026-04-29) | ~May 2026 | Default view. Cherry-picks most interesting signal from each group. "FORM: Arsenal trending up. MARKET: Sharp money moved toward Home." The killer Pro feature |
| 62 | SUX-5 | Signal group accordion sections on match detail | 2-3 days | ⬜ | High | UX Review (2026-04-29) | ~May 2026 | Market, Form & Strength, Context, News & Injuries, Live. Accordion cards (not tabs — better mobile). Tier-gated content per section. Depends on B3 |
| 63 | SUX-6 | Plain-English signal translation layer | 1 day | ⬜ | Medium | UX Review (2026-04-29) | ~May 2026 | Convert raw values → labels. odds_volatility→"Volatile", form_slope→arrows (↑↑/↑/→/↓/↓↓), elo→percentile, importance→"Title decider". Reusable util for all signal display |
| 64 | SUX-7 | Signal-based conversion hooks (Free→Pro, Pro→Elite) | 1 day | ⬜ | High | UX Review (2026-04-29) | ~May 2026 | Free: teaser badges + "X of Y signals" CTA. Pro: model conclusion lock at bottom of signal groups + weekly "you would have found N value bets" email. Depends on B3 |
| 65 | SUX-8 | Signal Timeline component on match detail | 2-3 days | ⬜ | Medium | UX Review (2026-04-29) | ~June 2026 | Vertical stepping-line showing signal events chronologically. "Upcoming" section for next odds snapshot / expected lineups. Retention/engagement play |
| 66 | SUX-9 | Signal Delta — "what changed since last visit" | 1 day | ⬜ | Medium | UX Review (2026-04-29) | ~June 2026 | Track last-visited timestamp per user per match. Show diff: "+ Steam move toward Away, + Lineups confirmed". Creates habit + return visits |
| 67 | SUX-10 | Post-match signal reveal for Free users | 4h | ⬜ | Medium | UX Review (2026-04-29) | ~June 2026 | After settlement, show 1 retrospective insight: "Our signals detected sharp movement 4h before kickoff. Home won 2-0." Proves signal value, drives Free→Pro |

| — | PIPE-2 | Strip fetch code from betting_pipeline.py (Phase 2) | 2-3h | ⬜ | Medium | Internal (2026-04-29) | ~May 2026 | betting_pipeline.py currently wraps monolith. Phase 2: read from DB only, delete daily_pipeline_v2.py |
| — | ODDS-API | Activate The Odds API for Pinnacle odds ($20/mo) | 2h | ⬜ | High | Data Analysis (2026-04-29) | ~May 2026 | Code exists (254 lines, dormant). Pinnacle = gold standard for CLV. Depends on PIN-1 validation |
| — | LAUNCH-BETA | Add "Early Access / Beta" label to site | 15 min | ⬜ | Medium | Launch Plan (2026-04-29) | Before any promotion | Resets credibility bar, makes thin track record acceptable |
| — | LAUNCH-PICK | Make daily AI pick visible without login on /matches | 2-4h | ⬜ | High | Launch Plan (2026-04-29) | Before any promotion | The hook for organic traffic — currently requires login |
| — | ALERTS | Match alerts & notifications (email/push) | 2-3 days | ⬜ | Medium | Tier Access Matrix | ~June 2026 | Re-engagement loop. No system for this yet |
| — | EMAIL-WEEKLY | Weekly performance summary email | 1 day | ⬜ | Medium | Tier Access Matrix | ~June 2026 | Shows bot ROI, top picks, CLV stats. Retention play |
| — | AF-EVAL | Evaluate AF Pro tier ($19/mo, 7.5K req/day) vs Ultra ($29/mo) | Research | ⬜ | Low | Data Sources | ~June 2026 | After 4-6 weeks of data, check if we need 75K or 7.5K is enough |

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
| 68 | SUX-11 | "Why This Pick" reasoning card UI (Elite match detail) | 1-2 days | ⬜ | High | UX Review (2026-04-29) | ~June 2026 | Maps signals → reasoning → outcome in natural language. Builds on BET-EXPLAIN LLM work. "Key factors: Form slope ↑, 2 players out, models agree" |
| 69 | SUX-12 | CLV tracking dashboard (Elite) | 1-2 days | ⬜ | Medium | UX Review (2026-04-29) | ~June 2026 | Historical CLV% chart + running win rate + ROI. Post-match notification: "Your bet beat the closing line by 2.1%." Proves model skill over time |

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
| Data Analysis (2026-04-29) | From pipeline refactor + data source audit session (2026-04-29) |
| Launch Plan (2026-04-29) | From LAUNCH_PLAN.md pre-launch preparation |
| Tier Access Matrix | From TIER_ACCESS_MATRIX.md feature checklist |
| Data Sources | From DATA_SOURCES.md remaining cleanup |
| Landing Page Review (2026-04-29) | From landing page pricing/UX review |
