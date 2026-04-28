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
| **Free (Scout)** | €0 | Casual football fan, curious about the product |
| **Pro (Analyst)** | €4.99/mo | Does own research, wants better data |
| **Elite (Sharp)** | €14.99/mo | Serious bettor, wants model-backed picks |

Founder pricing (locks in for life): Pro €3.99/mo · Elite €9.99/mo
Annual: Pro €39.99/yr (€3.33/mo) · Elite €119.99/yr (€9.99/mo)

### What each tier includes

**Free (Scout)**
- All 467 daily fixtures (teams, kickoff, league)
- Live scores during matches
- Odds from 2-3 bookmakers (not full comparison)
- Match interest indicator (🔥 / ⚡ / —) based on data coverage
- Today only — no historical view

**Pro (Analyst)**
- Everything in Free
- Full odds comparison across all bookmakers
- Pre-match odds movement timeline
- Team form, H2H, goals stats, standings
- AI injury/suspension alerts per match
- Directional model signal (Home lean / Away lean / Even — no raw %)
- Full match history, not just today

**Elite (Sharp)** ← launch when 60+ settled bets validated
- Everything in Pro
- Exact model probability % and edge %
- Value bet list (what the model would pick today)
- CLV tracking (beat the closing line analysis)
- Natural language bet explanations (why we like this pick)
- Tips from top-performing bot once ROI validated
- Early access to new league signals

### Key UX principle
Everyone sees all matches. Depth of information varies by tier.
Filter toggle: "Show all matches" (default) / "Show matches with [my tier] data" — additive, not restrictive.

---

## Milestones

### Milestone 1 — Free Tier Launch
**Status:** 🟡 In progress — blocked on F7 (Stitch redesign, awaiting designs)

**Goal:** Public-facing product. Someone can find the site, see today's matches, understand what the product is, and sign up.

**What's built:** Public matches page, auth, match detail (free + pro sections), interest score, live scores, track record, onboarding flow, legal pages, analytics, OG image.

**Remaining:** Stitch redesign (F7) — parked until after M1 go-live. Site is technically launchable now.

**Blocking condition for launch:** Stripe account + products created (manual step — only Margus can do this).

---

### Milestone 2 — Pro Tier Launch
**Status:** 🔲 Not started — blocked on Stripe

**Goal:** First paying customers. Enough depth to justify €4.99/mo.

**What's built:** Tier gating, match detail Pro sections (injuries, lineups, stats, events, ratings), value bets page, track record with real data, onboarding flow.

**Remaining:** Stripe integration (F8), tier-aware API (B3), value bets page redesign (F5).

**Ready to launch when:** Stripe works, Pro users see odds/form/directional signals, free users see the gap.

---

### Milestone 3 — Elite Tier + Tips Launch
**Status:** 🔲 Blocked on data collection

**Goal:** Tips product live. Requires validated ROI.

**Blocking condition:** Top-performing bot needs 60+ settled bets with positive ROI. At current pace (~5-10 bets/day), earliest: ~2 weeks from 2026-04-27.

**What's built:** 6 paper trading bots running, tier B backtest script, bot validation tracker (check_bot_validation.py exits 1 when condition met).

**Remaining:** Singapore/South Korea odds source (B6), value bets redesign (F5), tip tracking (F10).

---

## Current System State (2026-04-29)

### Backend
| Component | Status |
|-----------|--------|
| API-Football Ultra ($29/mo) | ✅ Primary — 75K req/day, T1–T13 integrated |
| Daily pipeline (08:00 UTC) | ✅ T2/T3/T9/T10 enrichment + predictions + bets |
| AI news checker (09:00 UTC, 4×/day) | ✅ Gemini 2.5 Flash, qualitative-only |
| Settlement pipeline (21:00 UTC) | ✅ T4/T8/T12 + settle + CLV + ELO + post-mortem |
| Live tracker (every 5min, 12-22 UTC) | ✅ T5/T6/T7/T8 live data |
| Odds snapshots (every 2h, 06-22 UTC) | ✅ 13 bookmakers per match |
| 6 paper trading bots | ✅ Running since 2026-04-27 |
| match_signals (EAV signal store) | ✅ 20+ signals per match |
| match_feature_vectors (ML training table) | ✅ Nightly ETL, wide table |
| pseudo_clv | ✅ All ~280 matches/day |

### Frontend (odds-intel-web)
| Page | Status |
|------|--------|
| Landing page | ✅ Built |
| Auth (login/signup) | ✅ Built |
| /matches | ✅ Public, smart sort, dual layout |
| /matches/[id] | ✅ Free + Pro sections |
| /value-bets | ✅ Built — needs tier gating |
| /track-record | ✅ Real Supabase data |
| /welcome onboarding | ✅ Built |
| Stripe payments | ❌ Not built — blocking M2 |

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
- [ ] **Stripe** — create account + products (Pro €4.99/mo, Elite €14.99/mo), add keys to Vercel
- [ ] **GitHub secret** — add `SUPABASE_ACCESS_TOKEN` for DB migration workflow

---

## Open Decisions

| Decision | Options | Status |
|----------|---------|--------|
| Tier names final? | Scout/Analyst/Sharp vs Free/Pro/Elite | ⏳ Pending |
| Tips: picks or signals? | "Today's picks" vs "What bot_X would bet" | ⏳ Pending |
| Design: Stitch redesign or ship now? | Polish current vs wait for Stitch designs | ⏳ Pending |
| user_bets feature at M3? | Follow a tip → personal P&L | Skip until M3 |

---

## Notes & Context

**Why bots are internal tools, not the product:**
The 6 bots are validation instruments — they find which markets/leagues have real edge before we sell tips. The product is the picks from the best-performing bot.

**Scotland League Two cross-era signal:**
+12.3% ROI in mega backtest (2005-15) AND +21% in recent 2022-25 backtest. Two models, two eras, same direction. Most consistent signal we have.

**Greece/Turkey era sensitivity:**
Positive ROI in 2022-25 data but negative in 2005-15. Keep running for data but don't promote to tips until 30+ settled bets confirm current-era edge.

**CLV as the short-term proof metric:**
Results take weeks to accumulate (variance). CLV (did we beat the closing line?) is measurable within days. A bot consistently beating the closing line = finding real value even before wins/losses confirm it.

**Pricing rationale:**
€4.99/mo Pro is a no-brainer for anyone who bets more than once a week — one saved bad bet pays for 6 months. €14.99/mo Elite is priced for serious bettors who understand edge and CLV.
