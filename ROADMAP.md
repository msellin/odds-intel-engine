# OddsIntel — Product Roadmap

> Product vision, tier structure, milestone goals, and open decisions.
> Task tracking lives in PRIORITY_QUEUE.md — not here.
> Last updated: 2026-05-06

---

## Product Vision

Two parallel lanes, one product:

**Lane 1 — Match Intelligence** (data & analytics)
A genuinely useful place to research football matches — odds comparison, form, stats, AI injury alerts. Most of the value is visible free; depth unlocks with subscription.

**Lane 2 — Betting Tips** (model-driven picks)
Our best bot's picks surfaced as tips for paying users. Only launched once ROI is validated with real settled bets. Not sold as "bots" — sold as picks with model confidence.

Both lanes feed the same frontend. Tips are the Elite tier differentiator when ready.

---

## Tier Structure

| Tier | Price | Target user |
|------|-------|-------------|
| **Free** | €0 | Casual football fan, curious about the product |
| **Pro** | €4.99/mo | Does own research, wants better data |
| **Elite** | €14.99/mo | Serious bettor, wants model-backed picks |

Founder pricing (locks in for life): Pro €3.99/mo · Elite €9.99/mo
Annual: Pro €39.99/yr (€3.33/mo) · Elite €119.99/yr (€9.99/mo)

### What each tier includes

**Free** (anonymous + signed-in)
- All ~467 daily fixtures with kickoff, league, venue, referee
- Best available odds (single best price across all bookmakers we track)
- H2H records and recent meetings
- League standings + team form (last 5 matches)
- Live scores during matches (auto-refresh)
- Match intelligence grade (A/B/D) + signal teasers on notable matches
- Match interest indicator (⚡ / 🔥 / —)

*Signed-in free additionally:*
- Favourite teams + leagues → "My Matches" filtered view
- Prediction tracker (log picks, track hit rate vs AI)
- Daily free AI value pick (1 unlock per day)
- Match notes (private journal)
- Community prediction voting (1X2 poll)

**Pro** (€4.99/mo)
- Everything in Free
- Full odds comparison across all 13 bookmakers with best-price highlighting (1X2, O/U 2.5, BTTS, O/U 1.5, O/U 3.5)
- Pre-match odds movement charts (1X2 + O/U 2.5)
- Live in-play odds chart by match minute (FE-LIVE)
- Intelligence Summary: top 5 signals in plain English with severity indicators (SUX-4/6)
- Signal group accordion: Market, Team Quality, Context, News & Injuries (SUX-5)
- Signal Delta: "what changed since your last visit" (SUX-9)
- AI injury & suspension alerts with player names
- Confirmed lineups + formation view
- Team season stats (goals avg, clean sheet %, most-used formation)
- Post-match stats (shots, possession, corners, xG) + HT vs FT comparison
- Player ratings
- Match events timeline (goals, cards, subs)
- Value bets page — directional (match + selection + edge tier, no exact %)

**Elite** ← launch when 60+ settled bets with positive ROI
- Everything in Pro
- Full value bets page: exact odds, model probability %, edge %, Kelly stake
- Natural language bet explanations — "Why this pick?" powered by Gemini (BET-EXPLAIN)
- Pro→Elite conversion hook in Intelligence Summary (model conclusion lock)
- CLV tracking (beat the closing line analysis) — per match + personal aggregate
- Tips from top-performing bot once ROI validated
- Personal bankroll analytics — `/bankroll` page: cumulative units chart, ROI, hit rate, avg CLV, max drawdown, model benchmark, per-league breakdown, recent picks (ELITE-BANKROLL ✅ live)
- *Planned:* League performance filter — restrict value bets to leagues where model has historically outperformed (ELITE-LEAGUE-FILTER)
- *Planned:* Custom multi-signal alert stacking — alert only when confidence + edge + line movement all align (ELITE-ALERT-STACK)

### Key UX principle
Everyone sees all matches. Depth of information varies by tier.
Filter toggle: "Show all matches" (default) / "Show matches with [my tier] data" — additive, not restrictive.

---

## Milestones

### Milestone 1 — Free Tier Launch
**Status:** ✅ Ready to promote — site live at oddsintel.app, Stripe set up, all core pages built.

**Goal:** Public-facing product. Someone can find the site, see today's matches, understand what the product is, and sign up.

**What's built:** Public matches page, auth, match detail (free + pro sections with server-side tier gating), signal grade + teasers + pulse (SUX-1/2/3), live scores, track record, onboarding flow, legal pages, analytics, OG image. Stripe checkout + webhook + portal.

**Remaining (post-launch polish):** Stitch redesign (F7) — parked until after first users arrive. Pre-launch items done: Beta label (LAUNCH-BETA) ✅, daily AI pick visible without login (LAUNCH-PICK) ✅.

---

### Milestone 2 — Pro Tier Launch
**Status:** ✅ Ready to launch — all blockers resolved

**Goal:** First paying customers. Enough depth to justify €4.99/mo.

**What's built:** Tier gating, match detail Pro sections (injuries, lineups, stats, events, ratings, odds), value bets page (F5 redesign ✅), track record with real data, onboarding flow. Stripe checkout + webhook + portal. Intelligence Summary (SUX-4), Signal Accordion (SUX-5), Signal Delta (SUX-9), Live In-Play Chart (FE-LIVE). Value bets page: Free=teaser, Pro=directional, Elite=full.

**Ready to promote.** Pro users now see a clear data gap vs Free — deep signal analysis, live in-play odds, full odds comparison.

---

### Milestone 3 — Elite Tier + Tips Launch
**Status:** 🔲 Blocked on data collection — close (~82% of threshold)

**Goal:** Tips product live. Requires validated ROI.

**Blocking condition:** Top-performing bot needs 60+ settled bets with positive ROI. **bot_aggressive has 49 settled bets +93 units as of 2026-05-05.** At ~5-10 bets/day, should hit 60 bets by ~May 8-9.

**What's built:** 24 paper trading bots (16 pre-match + 8 in-play since 2026-05-06), tier B backtest script, bot validation tracker (check_bot_validation.py exits 1 when condition met). In-play bots: strategies A, A2, B, C, C_home, D, E, F using Bayesian xG posterior — see § INPLAY Plan in PRIORITY_QUEUE.md. Elite bankroll dashboard live (ELITE-BANKROLL ✅).

**Remaining:** Singapore/South Korea odds source (B6), PostHog feature flag for tips toggle (INFRA-7).

---

## Current System State (2026-05-06)

### Backend
| Component | Status |
|-----------|--------|
| API-Football Ultra ($29/mo) | ✅ Primary data source (Kambi removed 2026-05-06 — empirically redundant) |
| ① Fixtures (04:00 + 4 refreshes/day) | ✅ AF fixtures + league coverage + postponement detection |
| ② Enrichment (04:15/10:30/13:00/16:00 UTC) | ✅ Standings, H2H, team stats, injuries |
| ③ Odds (every 30min 07-22 UTC + closing odds 13:30/17:30/20:00) | ✅ AF bulk odds, 13 bookmakers |
| ④ Predictions (05:30 UTC) | ✅ AF predictions (coverage-aware) |
| ⑤ Betting (8×/day: 06:00/09:30/11:00/13:30/15:00/17:30/19:00/20:30 UTC) | ✅ Poisson/XGBoost + Pinnacle anchor + sharp consensus gate + veto filters |
| ⑥ LivePoller (24/7, adaptive 30s live / 120s idle) | ✅ Live scores, events, lineups, in-play odds + 8 in-play bots |
| ⑦ AI news checker (4×/day + 14:30) | ✅ Gemini 2.5 Flash, qualitative-only |
| ⑧ Settlement (21:00 + 01:00 UTC) | ✅ Settle + CLV + Pinnacle CLV + ELO + post-mortem + weekly Platt + blend refit (Wed+Sun) + dynamic DC rho |
| ⑨ Historical backfill (02:00 UTC Railway) | ✅ Complete 2026-05-10 — 47,228 finished matches; match_stats 73.4% (terminal — AF gaps), match_events 93.4%; `backfill_complete.flag` set, scheduled job auto-disabled |
| ⑩ AI match previews (07:15 UTC) | ✅ Gemini 200-word previews for top 10 matches |
| ⑪ Email digest (10/12/14/16 UTC slots) | ✅ Smart-slot digest — first slot whose pending-bet signal-strength score (Σ edge × prestige × kelly) clears threshold sends; T4 leagues (youth/women/lower divisions) excluded from email content (EMAIL-DIGEST-SMART) + value bet alerts (16:00/20:45) + weekly (Mon 08:00) + watchlist (08:30/14:30/20:30) |
| 24 paper trading bots | ✅ 16 pre-match (since 2026-04-27, incl. bot_proven_leagues) + 8 in-play (since 2026-05-06, strategies A/A2/B/C/C_home/D/E/F) |
| Accessible-bookmaker filter | ✅ **ACCESSIBLE-BM (2026-05-11)** — edge math restricted to EU/Estonia-accessible books (Bet365, Unibet, Betano, Marathonbet, 10Bet, 888Sport, Pinnacle). `recommended_bookmaker` stored per bet (migration 094). `scripts/daily_picks.py` — morning manual-betting report. Fixes reported CLV inflation from inaccessible-book odds (SBO/Dafabet/1xBet). |
| Pinnacle signals | ✅ PIN-1 through PIN-5: implied probs (all markets), line movement, veto gate, Pinnacle-anchored CLV |
| Calibration improvements | ✅ Pinnacle shrinkage anchor, odds-conditional alpha, sharp consensus gate, draw inflation, dynamic DC rho |
| match_signals (EAV signal store) | ✅ 30+ signals per match (incl. sharp_consensus, Pinnacle implied, Pinnacle line move, injury_recurrence, h1_shot_dominance, squad_disruption) |
| match_feature_vectors (ML training table) | ✅ Nightly ETL for finished matches + **MFV-LIVE-BUILD** (2026-05-10) writes pre-KO rows on every betting refresh so v10+ XGBoost finds a row at inference instead of falling back to Poisson |
| ML model registry (Supabase Storage + `model_versions` table) | ✅ **ML-BUNDLE-STORAGE** (2026-05-10) — every trained bundle auto-uploads to Storage + auto-registers; `_load_models()` lazy-downloads on Railway cold-start. Solves Railway's ephemeral-filesystem problem for weekly retrains. 16 bundles archived. Switch versions via `MODEL_VERSION` env var → next deploy auto-pulls. Full design in `docs/ML_MODEL_REGISTRY.md` |
| Active production model | ✅ **`v12_post0e`** (Pinnacle-free, post-Stage-0e). Switched from v9a_202425 on 2026-05-10. Beats v9 by ~50% on every 1X2 market log_loss in offline_eval. Compare any time: `python3 scripts/offline_eval.py vA vB --include-v9` |
| pseudo_clv | ✅ All ~280 matches/day |
| Platt scaling (post-hoc calibration) | ✅ 2-stage: tier shrinkage → Platt sigmoid. Weekly refit (Wed+Sun) |
| Learned blend weights | ✅ MOD-2 — optimized Poisson/XGBoost weights + per-tier alpha. Weekly refit |
| Featured leagues (frontend filtering) | ✅ `show_on_frontend` flag. ~50 curated leagues. 6-tier priority system |

### Frontend (odds-intel-web)
| Page | Status |
|------|--------|
| Landing page | ✅ Built |
| Auth (magic link + Google) | ✅ Custom SMTP via Resend, branded emails |
| /matches | ✅ Today + Tomorrow tabs, smart sort, dual layout, signal grade + pulse + teasers (SUX-1/2/3), What Changed Today, "X analyzing" counter, community vote splits |
| /matches/[id] | ✅ Free+Pro+Elite sections, server-side tier gating, all odds markets, Intelligence Summary (SUX-4/6/7), Signal Accordion (SUX-5), Signal Delta (SUX-9), Live in-play chart, bot consensus, Model vs Market vs Users, AI preview, post-match signal reveal |
| /value-bets | ✅ Tiered: Free=teaser+stats, Pro=directional picks, Elite=full table + BET-EXPLAIN |
| /track-record | ✅ Real Supabase data |
| /predictions | ✅ 8 featured leagues, SEO prediction pages with FAQ schema |
| /learn | ✅ 12-term betting glossary with FAQ schema |
| /methodology | ✅ Public model explanation |
| /bankroll | ✅ Elite-gated personal bankroll analytics (ROI, CLV, drawdown, per-league) |
| /my-picks | ✅ Personal bet tracker + "Model vs You" + shareable pick cards |
| /welcome onboarding | ✅ Built |
| /admin/bots | ✅ Superadmin bot P&L dashboard |
| Stripe payments | ✅ Live production mode since 2026-05-04. Checkout + webhook + portal + tier gating + annual billing + founding rates + promo codes |
| Superadmin tier preview | ✅ Cookie-based tier switcher for QA |

### Data coverage
- Fixtures with any model data: ~200/467 (43%)
- Matches with Tier A predictions: ~50-100/day
- Matches with Tier B predictions: ~180/day
- Singapore S.League (+27.5% ROI): no live odds feed — biggest gap

---

## Launch Checklist (manual steps — only Margus can do these)

- [x] Gemini API key — created in AI Studio for OddsIntel project
- [x] Deploy to Vercel — project linked, env vars set
- [x] Domain — oddsintel.app bought and connected to Vercel
- [x] Google Search Console — verified, sitemap submitted
- [x] Migration 009 applied in Supabase SQL editor
- [x] **Stripe** — production mode live 2026-05-04. Products + prices created (Pro €4.99, Elite €14.99 + annual + founding). Checkout + webhook + portal built.
- [x] **Stripe webhook endpoint** — live endpoint at `https://www.oddsintel.app/api/stripe/webhook`, `STRIPE_WEBHOOK_SECRET` updated in Vercel
- [x] **GitHub secrets** — `SUPABASE_ACCESS_TOKEN`, `SUPABASE_PROJECT_REF`, `API_FOOTBALL_KEY`, `SUPABASE_SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_DB_PASSWORD`, `GEMINI_API_KEY` all set
- [x] **Vercel env var** — `GEMINI_API_KEY` added to Production (for BET-EXPLAIN `/api/bet-explain`)
- [x] **Resend** — account created, `oddsintel.app` domain verified, `RESEND_API_KEY` + `DIGEST_FROM_EMAIL` + `SITE_URL` set in Railway + `.env`

---

## Engagement & Growth Strategy

**Full playbook:** `docs/ENGAGEMENT_PLAYBOOK.md` — synthesized from 4 independent AI brainstorm sessions + web research (2026-04-30).

**Core principles:** No gamification, premium analytical tone, transparency as differentiator, social proof through aggregate data (not forums/profiles).

**Phase 1 (launch sprint):** ✅ All done — ENG-1 through ENG-7 shipped by 2026-05-05.

**Phase 2 (retention):** ✅ All done — ENG-8 through ENG-14 shipped by 2026-05-05. Watchlist alerts, personal bet tracker + Model vs You, weekly email, What Changed Today, Model vs Market vs Users, shareable pick cards, SEO prediction pages.

**Phase 3 (differentiation):** Market inefficiency index (ENG-15, ~June — needs 30 days data), season-end review (ENG-17, ~Aug+).

---

## Open Decisions

| Decision | Options | Status |
|----------|---------|--------|
| Tier names final? | Free/Pro/Elite | ✅ Done |
| Tips: picks or signals? | "Today's picks" vs "What bot_X would bet" | ⏳ Pending |
| Design: Stitch redesign or ship now? | Polish current vs wait for Stitch designs | ⏳ Pending |
| user_bets feature at M3? | Follow a tip → personal P&L | ✅ Promoted to ENG-9 (Phase 2, ~May W3-4) |

---

## Bot Strategy

**24 paper trading bots** running across two categories:

**Pre-match (16 bots, since 2026-04-27):** Based on historical backtest data — edge thresholds and league filters derived from football-data.co.uk 2007-2025 and beat_the_bookie 2005-2015. Includes `bot_proven_leagues` (5 cross-era confirmed leagues). 5 morning / 6 midday / 5 pre-KO timing cohorts.

**In-play (8 bots, since 2026-05-06):** Rule-based strategies A, A2, B, C, C_home, D, E, F using Bayesian xG posterior. Run inside LivePoller every 30s. See § INPLAY Plan in PRIORITY_QUEUE.md.

**Never retire current bots when adding new ones.** They are the baseline — their ROI data is what proves (or disproves) whether new bots are better.

New bots planned based on live data accumulation:

| Bot | Trigger | ~When | What's different |
|-----|---------|-------|-----------------|
| `bot_meta_v1` | 3000+ quality CLV rows (created_at >= 2026-05-06, clv IS NOT NULL) | ~late June (582/3000 as of 2026-05-12, ~60/day avg) | Uses logistic regression EV score instead of hardcoded thresholds. First bot learning from live data. **Note:** original ~May 17 estimate assumed ~273/day; actual rate ~60/day. MFV row threshold (3,819) already met — CLV outcome rows are the binding constraint. |
| `bot_high_alignment` | 300+ settled bot bets (>= 2026-05-06) | ✅ Ready now (590 settled as of 2026-05-12, was ~June 5 estimate) | Only bets when alignment_class=HIGH. Fewer bets, higher precision. Implement ALN-1 first (threshold met). |
| `bot_retrained_xgb` | ✅ HIST-BACKFILL complete 2026-05-10 — gated on ML-RETRAIN-1 run | ~June | XGBoost retrained on recent data, not 2007-2025. Backfill done; awaiting `workers/model/train.py` rerun. |
| In-play strategies G, H | Week 2 after Phase 1A launch | ~May 13 | Shot Quality Under + Corner Pressure Over |
| In-play strategies I, J, K | Week 3 after Phase 1A launch | ~May 20 | Possession Trap + Dominant Underdog + 2H Burst |

---

## Notes & Context

**Why bots are internal tools, not the product:**
The 24 bots are validation instruments — they find which markets/leagues have real edge before we sell tips. The product is the picks from the best-performing bot.

**Scotland League Two cross-era signal:**
+12.3% ROI in mega backtest (2005-15) AND +21% in recent 2022-25 backtest. Two models, two eras, same direction. Most consistent signal we have.

**Greece/Turkey era sensitivity:**
Positive ROI in 2022-25 data but negative in 2005-15. Keep running for data but don't promote to tips until 30+ settled bets confirm current-era edge.

**CLV as the short-term proof metric:**
Results take weeks to accumulate (variance). CLV (did we beat the closing line?) is measurable within days. A bot consistently beating the closing line = finding real value even before wins/losses confirm it.

**Pricing rationale:**
€4.99/mo Pro is a no-brainer for anyone who bets more than once a week — one saved bad bet pays for 6 months. €14.99/mo Elite is priced for serious bettors who understand edge and CLV.
