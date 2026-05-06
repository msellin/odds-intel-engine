# OddsIntel — Launch Plan

> Written 2026-04-29. Updated 2026-05-06 (full status sync from PRIORITY_QUEUE).
> Use this doc to brief another agent or plan the next phase.
> Detailed Reddit execution: `docs/REDDIT_LAUNCH.md`. ENG task tracking: `PRIORITY_QUEUE.md`.

---

## Product State (updated 2026-05-06)

### What's live
- oddsintel.app deployed on Vercel, domain connected, Google Search Console verified
- Landing page, auth (login/signup with magic link + Google), /matches (today + tomorrow tabs), /matches/[id], /value-bets, /track-record, /welcome onboarding, /predictions (SEO), /learn (glossary), /methodology, /bankroll (Elite)
- **Stripe production mode** live since 2026-05-04 — checkout + webhook + portal + tier gating + annual billing + founding rates + REDDIT promo code
- Free tier features: prediction tracker, 1 daily AI value pick, community voting + vote splits, favorites, match notes, interest score (🔥/⚡/—), "X analyzing" counter, What Changed Today widget, Model vs Market vs Users triangulation
- Pro tier features: full odds (13 bookmakers), odds movement charts, live in-play chart, Intelligence Summary, Signal Accordion, Signal Delta, injuries, lineups, stats, events, player ratings, bot consensus, AI match previews, directional value bets
- Elite tier features: full value bets with edge %, BET-EXPLAIN (Gemini), bankroll analytics dashboard, shareable pick cards, CLV tracking
- 24 paper trading bots: 16 pre-match (since 2026-04-27) + 8 in-play (since 2026-05-06, strategies A/A2/B/C/C_home/D/E/F)
- Pseudo-CLV tracked on ~280 matches/day
- Signal intelligence grade + teasers + pulse indicator on every match (SUX-1/2/3)
- Daily email digest + weekly performance email + value bet alerts (afternoon + evening) + watchlist alerts — all via Resend
- PostHog analytics with conversion funnel + Vercel Speed Insights
- Resend webhook tracking (email opens/clicks)
- Superadmin tier preview switcher for QA

### What's NOT ready
- **Elite tier tips launch**: blocked on data — top bot (bot_aggressive) has ~49/60 settled bets needed for validation. ETA ~May 8-9
- Track record: 9 days of data, gets meaningfully stronger at ~2 weeks (mid-May)
- Data coverage: ~43% of fixtures have model data — most popular leagues covered
- Singapore S.League: +27.5% ROI signal but no live odds feed

---

## Honest Weaknesses Addressed

1. "Early Access / Beta" label → resets credibility bar, makes thin track record acceptable ✅ done
2. Daily AI pick visible on /matches **without requiring login** → that's the hook ✅ done
3. Landing page pricing display issues (LP-1/2/3) ✅ done
4. Stripe production keys → ✅ live 2026-05-04
5. Auth email branding → ✅ Custom SMTP via Resend, magic link flow

---

## Phase 0 — Pre-launch prep ✅ Complete

- [x] Add "Early Access / Beta" framing — Beta badge in nav (LAUNCH-BETA, done 2026-04-29)
- [x] Make daily AI pick visible without login on /matches — DailyValueTeaser anon branch (LAUNCH-PICK, done 2026-04-29)
- [x] Fix LP-1, LP-2, LP-3 pricing display issues — done 2026-04-29
- [x] Supabase Pro — upgraded 2026-04-29 (PITR + daily backups active)
- [x] Founding member pricing caps enforced in code (500 Pro / 200 Elite)
- [x] Legal pages (privacy, terms) — live
- [x] Sitemap + robots.txt — live
- [x] SEO metadata (title, description, OG tags) — live
- [x] Stripe production keys — ✅ live 2026-05-04 (Pro €4.99, Elite €14.99 + annual + founding rates)
- [x] Custom SMTP (Resend) + branded magic link auth — done 2026-05-05
- [x] PostHog conversion funnel — done 2026-05-05
- [x] Vercel Speed Insights — done 2026-05-05
- [x] ~~7-day free trial on Pro~~ — removed 2026-05-06 (free tier is the trial; REDDIT promo code handles targeted free months)
- [x] REDDIT promo code (100% off first month) — done 2026-05-05

---

## Phase 1 — Organic / Zero-cost (Week 1-2) — 🔄 IN PROGRESS

### Reddit — 4/6 posts done

**Detailed execution plan and all 6 post drafts: `docs/REDDIT_LAUNCH.md`**

| # | Subreddit | Status |
|---|-----------|--------|
| 1 | Warm-up comments (r/SoccerBetting + r/soccer) | ✅ Done Apr 30 – May 3 |
| 2 | r/buildinpublic | ✅ Done May 4 (60 views) |
| 3 | r/FootballBetting | ✅ Done May 5 |
| 4 | r/SoccerPredictions | ⬜ Ready today (draft done, REDDIT code) |
| 5 | r/dataisbeautiful [OC] | ⬜ Needs odds movement screenshot |
| 6 | r/soccer Daily Discussion | Can drop anytime |

REDDIT promo code dropped as reply on all 3 active posts.

### Twitter/X — not started

- Post one AI pick daily: match, edge %, short explanation ("XGBoost + Poisson both lean home, bookmakers disagree, line moved overnight")
- Post wins AND losses equally — this is what builds credibility with sharp bettors
- Use: #valuebets #footballtips #soccerpredictions
- Write one "I built this in public" thread explaining the model — link to oddsintel.app at the end

### Discord — not started

Search for football betting Discords. Join legitimately, add value first, mention the tool naturally after a few days.

### Engagement features — ✅ all Phase 1 done

All Phase 1 engagement features shipped before Reddit traffic:
- ENG-1: "X analyzing this match" counter ✅
- ENG-2: Community vote split display ✅
- ENG-3: AI match previews (Gemini, top 10) ✅
- ENG-4: Daily email digest (tier-gated) ✅
- ENG-5: Betting glossary (12 terms at /learn/[term]) ✅
- ENG-6: Bot consensus on match detail ✅
- ENG-7: Public /methodology page ✅

---

## Phase 2 — Build social proof (Week 2-4)

The track record is the weak point at launch. By ~May 10-15 there will be 2 weeks of settled bets and real CLV data.

During this window:
- Keep the daily pick posting on Twitter
- If daily pick hits 60-70%+ accuracy over 2 weeks, screenshot and share widely
- Daily email digest (ENG-4 ✅) keeps early signups engaged
- ENG-1 (watching counter) ✅ and ENG-2 (vote splits) ✅ already shipped

### Phase 2 engagement features — ✅ all done

All Phase 2 retention features shipped ahead of schedule:
- ENG-8: Watchlist signal alerts (email, 3×/day) ✅
- ENG-9: Personal bet tracker + "Model vs You" dashboard ✅
- ENG-10: Weekly performance email (Monday 08:00 UTC) ✅
- ENG-11: "What Changed Today" widget on matches page ✅
- ENG-12: Model vs Market vs Users triangulation ✅
- ENG-13: Shareable pick cards (branded OG image) ✅
- ENG-14: Auto-generated prediction pages for SEO (/predictions/[league]) ✅

**Goal: 100-200 registered free users by end of Phase 2.**

---

## Phase 3 — Paid acquisition (~mid-May)

Only start paid ads once:
1. ~~Stripe production keys swapped~~ ✅ Done 2026-05-04
2. 2+ weeks of track record exists (~May 11+)
3. Organic posts have validated which message resonates

### Meta/Instagram Ads

| Ad type | Audience | Daily budget | Message |
|---------|----------|-------------|---------|
| Facebook | Men 25-45, EU/UK, interests: football betting, Bet365, Betfair | €5-10/day | "Free AI picks for football. Track your record vs the model." |
| Instagram Stories | Younger audience | €5/day | Visual — today's top pick card with the interest score |
| Retargeting | Landing page visitors who didn't sign up | €3/day | "One free AI pick daily. No credit card." |

Start at **€10/day total**, run for 1 week, kill what doesn't convert.

**Google Ads:** Lower priority — "football predictions" keywords are expensive. Only consider for branded terms once there's brand awareness.

---

## Validation Metrics — When to Go Heavier

| Metric | What it tells you | Target before scaling |
|--------|-------------------|-----------------------|
| Free signups / day | Acquisition message working? | 10+/day from organic |
| Day 7 retention | Are users coming back? | 30%+ return within a week |
| Prediction tracker usage | Building daily habit? | 40%+ of signups log at least 1 pick |
| Pro section click-through | Upgrade intent? | 20%+ of match detail viewers click into Pro sections |

---

## Key Value Propositions (for copy / ad creative)

**For casual fans (free tier hook):**
> Track your football predictions. See if you beat the AI. One free AI pick every day.

**For bettors (paid tier hook):**
> Full odds comparison across 13 bookmakers. Odds movement timeline. AI injury alerts. Model signal on every match. One saved bad bet pays for 6 months.

**For serious bettors (Elite / future):**
> Exact model probability and edge %. CLV tracking. Value bet list. Our top bot's picks once ROI is validated with real settled bets.

**Founding member angle (use while it lasts):**
> First 500 Pro subscribers lock in €3.99/mo forever.

---

## Pricing Summary (for ads/copy)

| Tier | Price | Founder rate |
|------|-------|-------------|
| Free | €0 | — |
| Pro | €4.99/mo | €3.99/mo (first 500) |
| Elite | €14.99/mo | €9.99/mo (first 200) |

Annual: Pro €39.99/yr · Elite annual pricing removed (LP-2 resolved)
