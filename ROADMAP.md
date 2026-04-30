# OddsIntel — Product Roadmap

> Product vision, tier structure, milestone goals, and open decisions.
> Task tracking lives in PRIORITY_QUEUE.md — not here.
> Last updated: 2026-04-29

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
- *Planned:* Personal bankroll analytics — your ROI vs model benchmark, per-league performance, drawdown (ELITE-BANKROLL)
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
**Status:** 🔲 Blocked on data collection

**Goal:** Tips product live. Requires validated ROI.

**Blocking condition:** Top-performing bot needs 60+ settled bets with positive ROI. At current pace (~5-10 bets/day), earliest: ~2 weeks from 2026-04-27.

**What's built:** 9 paper trading bots running, tier B backtest script, bot validation tracker (check_bot_validation.py exits 1 when condition met).

**Remaining:** Singapore/South Korea odds source (B6), value bets redesign (F5), tip tracking (F10).

---

## Current System State (2026-04-30)

### Backend
| Component | Status |
|-----------|--------|
| API-Football Ultra ($29/mo) + Kambi (free) | ✅ Primary + supplementary odds |
| ① Fixtures (04:00 UTC) | ✅ AF fixtures + league coverage |
| ② Enrichment (04:15/12:00/16:00 UTC) | ✅ Standings, H2H, team stats, injuries |
| ③ Odds (every 2h 05-22 UTC) | ✅ AF bulk odds + Kambi |
| ④ Predictions (05:30 UTC) | ✅ AF predictions (coverage-aware) |
| ⑤ Betting (06:00 UTC) | ✅ Poisson/XGBoost + signals + bet placement |
| ⑥ Live tracker (every 5min, 12-22 UTC) | ✅ T5/T6/T7/T8 live data |
| ⑦ AI news checker (4×/day) | ✅ Gemini 2.5 Flash, qualitative-only |
| ⑧ Settlement (21:00 UTC) | ✅ T4/T8/T12 + settle + CLV + ELO + post-mortem + weekly Platt recalibration (Sundays) |
| ⑨ Historical backfill (8 cron slots/day) | ✅ Built — 3-phase backfill of ~55K matches via spare API quota |
| 16 paper trading bots | ✅ 10 original (since 2026-04-27) + 6 new BTTS/O/U/draw bots (2026-04-30) |
| match_signals (EAV signal store) | ✅ 20+ signals per match |
| match_feature_vectors (ML training table) | ✅ Nightly ETL, wide table |
| pseudo_clv | ✅ All ~280 matches/day |
| Platt scaling (post-hoc calibration) | ✅ 2-stage: tier shrinkage → Platt sigmoid. Weekly refit from settled predictions |
| Featured leagues (frontend filtering) | ✅ `show_on_frontend` flag on leagues table. ~50 curated leagues shown on website |

### Frontend (odds-intel-web)
| Page | Status |
|------|--------|
| Landing page | ✅ Built |
| Auth (login/signup) | ✅ Built |
| /matches | ✅ Public, smart sort, dual layout, signal grade + pulse + teasers (SUX-1/2/3) |
| /matches/[id] | ✅ Free+Pro+Elite sections, server-side tier gating (B3), BTTS+O/U odds, Intelligence Summary (SUX-4/6/7), Signal Accordion (SUX-5), Signal Delta (SUX-9), Live in-play chart (FE-LIVE), Post-match signal reveal for Free (SUX-10) |
| /value-bets | ✅ Tiered: Free=teaser+stats, Pro=directional picks, Elite=full table + BET-EXPLAIN |
| /track-record | ✅ Real Supabase data |
| /welcome onboarding | ✅ Built |
| Stripe payments | ✅ Built — checkout + webhook + portal + tier gating + annual billing toggle |

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
- [x] **Stripe** — products + price IDs created (test mode), checkout + webhook + portal built
- [x] **Stripe webhook endpoint** — endpoint created, `STRIPE_WEBHOOK_SECRET` added to Vercel
- [x] **GitHub secrets** — `SUPABASE_ACCESS_TOKEN`, `SUPABASE_PROJECT_REF`, `API_FOOTBALL_KEY`, `SUPABASE_SECRET_KEY`, `SUPABASE_URL`, `SUPABASE_DB_PASSWORD`, `GEMINI_API_KEY` all set
- [x] **Vercel env var** — `GEMINI_API_KEY` added to Production (for BET-EXPLAIN `/api/bet-explain`)

---

## Open Decisions

| Decision | Options | Status |
|----------|---------|--------|
| Tier names final? | Free/Pro/Elite | ✅ Done |
| Tips: picks or signals? | "Today's picks" vs "What bot_X would bet" | ⏳ Pending |
| Design: Stitch redesign or ship now? | Polish current vs wait for Stitch designs | ⏳ Pending |
| user_bets feature at M3? | Follow a tip → personal P&L | Skip until M3 |

---

## Bot Strategy

The 9 paper trading bots running since 2026-04-27 are all based on **historical backtest data** — edge thresholds and league filters derived from football-data.co.uk 2007-2025 and beat_the_bookie 2005-2015. They answer: *"what worked in old data?"*

**Never retire current bots when adding new ones.** They are the baseline — their ROI data is what proves (or disproves) whether new bots are better.

New bots planned based on live data accumulation:

| Bot | Trigger | ~When | What's different |
|-----|---------|-------|-----------------|
| `bot_meta_v1` | 3000+ pseudo_clv rows in match_feature_vectors | ~May 9 | Uses logistic regression EV score instead of hardcoded thresholds. First bot learning from live data |
| `bot_high_alignment` | 300+ settled bot bets, alignment filter validated | ~late May | Only bets when alignment_class=HIGH. Fewer bets, higher precision |
| `bot_retrained_xgb` | HIST-BACKFILL complete (API-Football 2020-2026) | ~June | XGBoost retrained on recent data, not 2007-2025 |

---

## Notes & Context

**Why bots are internal tools, not the product:**
The 9 bots are validation instruments — they find which markets/leagues have real edge before we sell tips. The product is the picks from the best-performing bot.

**Scotland League Two cross-era signal:**
+12.3% ROI in mega backtest (2005-15) AND +21% in recent 2022-25 backtest. Two models, two eras, same direction. Most consistent signal we have.

**Greece/Turkey era sensitivity:**
Positive ROI in 2022-25 data but negative in 2005-15. Keep running for data but don't promote to tips until 30+ settled bets confirm current-era edge.

**CLV as the short-term proof metric:**
Results take weeks to accumulate (variance). CLV (did we beat the closing line?) is measurable within days. A bot consistently beating the closing line = finding real value even before wins/losses confirm it.

**Pricing rationale:**
€4.99/mo Pro is a no-brainer for anyone who bets more than once a week — one saved bad bet pays for 6 months. €14.99/mo Elite is priced for serious bettors who understand edge and CLV.
