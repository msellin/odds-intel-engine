# OddsIntel — Launch Plan

> Written 2026-04-29. Updated 2026-05-04 (moved to docs/, Reddit section consolidated into REDDIT_LAUNCH.md).
> Use this doc to brief another agent or plan the next phase.
> Detailed Reddit execution: `docs/REDDIT_LAUNCH.md`. ENG task tracking: `PRIORITY_QUEUE.md`.

---

## Product State at Time of Writing (2026-04-29)

### What's live
- oddsintel.app deployed on Vercel, domain connected, Google Search Console verified
- Landing page, auth (login/signup), /matches, /matches/[id], /value-bets, /track-record, /welcome onboarding
- Free tier features: prediction tracker, 1 daily AI value pick, community voting, favorites, match notes, interest score (🔥/⚡/—)
- 16 paper trading bots running since 2026-04-27, pseudo-CLV tracked on ~280 matches/day
- Signal intelligence grade + teasers + pulse indicator on every match (SUX-1/2/3)

### What's NOT ready
- **Stripe production keys**: test mode is fully wired (checkout + webhook + portal + tier gating). Need live key swap before accepting real money — see `INFRASTRUCTURE.md` checklist.
- Track record: thin at launch, gets meaningfully stronger at ~2 weeks (mid-May)
- Email notifications: daily digest pipeline live (ENG-4 ✅) but depends on `RESEND_API_KEY` being set
- Data coverage: ~43% of fixtures have model data — most popular leagues covered

---

## Honest Weaknesses to Address Before Posting

1. "Early Access / Beta" label → resets credibility bar, makes thin track record acceptable ✅ done
2. Daily AI pick visible on /matches **without requiring login** → that's the hook ✅ done
3. Landing page pricing display issues (LP-1/2/3) ✅ done

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
- [ ] Stripe production keys — **manual action required** (5-step checklist in INFRASTRUCTURE.md)

---

## Phase 1 — Organic / Zero-cost (Week 1-2)

### Reddit

**Detailed execution plan and all 6 post drafts: `docs/REDDIT_LAUNCH.md`**

Summary:
- r/SoccerBetting, r/FootballBetting, r/soccer (Daily Discussion only), r/SoccerPredictions, r/dataisbeautiful, r/buildinpublic
- Space posts across ~1 week, different angle per subreddit
- Warm up the account first (3-5 days of comments before launch posts)

### Twitter/X

- Post one AI pick daily: match, edge %, short explanation ("XGBoost + Poisson both lean home, bookmakers disagree, line moved overnight")
- Post wins AND losses equally — this is what builds credibility with sharp bettors
- Use: #valuebets #footballtips #soccerpredictions
- Write one "I built this in public" thread explaining the model — link to oddsintel.app at the end

### Discord

Search for football betting Discords. Join legitimately, add value first, mention the tool naturally after a few days.

---

## Phase 2 — Build social proof (Week 2-4)

The track record is the weak point at launch. By ~May 10-15 there will be 2 weeks of settled bets and real CLV data.

During this window:
- Keep the daily pick posting on Twitter
- If daily pick hits 60-70%+ accuracy over 2 weeks, screenshot and share widely
- Daily email digest (ENG-4 ✅) keeps early signups engaged
- Engagement features from PRIORITY_QUEUE.md Phase 1: ENG-1 (watching counter), ENG-2 (vote splits) — build these before Reddit traffic peaks

**Goal: 100-200 registered free users by end of Phase 2.**

---

## Phase 3 — Paid acquisition (after Stripe production keys, ~mid-May)

Only start paid ads once:
1. Stripe production keys swapped (real payments enabled — see INFRASTRUCTURE.md checklist)
2. 2+ weeks of track record exists
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
