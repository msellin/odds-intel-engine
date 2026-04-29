# OddsIntel — Launch Plan

> Written 2026-04-29. Updated 2026-04-29. Stripe test mode fully wired — checkout, webhook, portal, tier gating all built. Awaiting production key swap before accepting real payments.
> Use this doc to brief another agent or resume after ongoing tasks complete.

---

## Product State at Time of Writing

### What's live
- oddsintel.app deployed on Vercel, domain connected, Google Search Console verified
- Landing page, auth (login/signup), /matches, /matches/[id], /value-bets, /track-record, /welcome onboarding
- Free tier features: prediction tracker, 1 daily AI value pick, community voting, favorites, match notes, interest score (🔥/⚡/—)
- 6 paper trading bots running since 2026-04-27, pseudo-CLV tracked on ~280 matches/day

### What's NOT ready
- **Stripe production keys**: test mode is fully wired (checkout + webhook + portal + tier gating). Need live key swap before accepting real money — see INFRASTRUCTURE.md checklist
- Track record: only 2 days of bot data — thin, but honest transparency is the right frame
- Signal teasers (SUX-1/2/3): ✅ Done 2026-04-29
- Email notifications: not built — no re-engagement loop yet
- Data coverage: 43% of fixtures have model data (200/467) — most popular leagues are covered

---

## Honest Weaknesses to Address Before Posting

1. Add "Early Access" or "Beta" label to the site — resets credibility bar, makes thin track record acceptable
2. Make the daily AI pick visible on the matches page **without requiring login** — that's the hook
3. Fix landing page pricing (3 quick tasks from PRIORITY_QUEUE.md LP-1/2/3): remove misleading strikethroughs, remove Elite annual pricing that has no real discount, consolidate the "Founding Member" scarcity message to one location — these should be done before running any ads

---

## Phase 0 — Pre-launch prep (1-2 days)

- [ ] Add "Early Access / Beta" framing to site header or landing page
- [ ] Make daily AI pick visible without login on /matches or landing page
- [x] Fix LP-1, LP-2, LP-3 pricing display issues — done 2026-04-29
- [ ] Ensure /welcome onboarding clearly explains what happens next after signup
- [ ] Write the Reddit post (see Phase 1 template below)

---

## Phase 1 — Organic / Zero-cost (Week 1-2)

### Reddit

Post across these subreddits, spaced over ~1 week. Do not cross-post the same text — adapt for each audience.

| Subreddit | Audience | Angle |
|-----------|----------|-------|
| r/soccerbetting | Sharp bettors | Lead with model methodology, CLV, transparency |
| r/footballbetting | UK/EU bettors | Same as above, more UK culture |
| r/soccer | 4M+ casual fans | "Free football match tracker + AI picks" |
| r/SoccerPredictions | Casual predictors | Focus on the prediction tracker |
| r/dataisbeautiful | Data nerds | Share a visual — odds movement, form slope, something with a chart |
| r/buildinpublic | Builders | Behind-the-scenes post about the pipeline |

**Rules to avoid bans:**
- Don't post from a brand new account — warm it up with 3-5 days of comments first
- Lead with value, product link at the bottom
- In r/soccerbetting: show the actual model data (probabilities table, CLV numbers) — bettors respond to transparency
- Space posts across different days

**Sample r/soccerbetting post hook:**
> "I've been running 6 automated betting bots on paper money since last week, tracking CLV (closing line value) as the proof metric because results take too long to accumulate. Here's what the early data looks like. I also built a free tool to track this openly — happy to share if anyone's interested."

### Twitter/X

- Post one AI pick daily: match, edge %, short explanation ("XGBoost + Poisson both lean home, bookmakers disagree, line moved overnight")
- Post wins AND losses equally — this is what builds credibility with sharp bettors
- Use: #valuebets #footballtips #soccerpredictions
- Write one "I built this in public" thread explaining the model — link to oddsintel.app at the end
- Tag relevant football betting/analytics accounts

### Discord

Search for football betting Discords. Join legitimately, add value first, then mention the tool naturally after a few days of participation.

---

## Phase 2 — Build social proof (Week 2-4)

The track record is the weak point at launch. By ~May 10-15 there will be 2 weeks of settled bets and real CLV data — the product story gets significantly stronger then.

During this window:
- Keep the daily pick posting on Twitter going
- If daily pick hits 60-70%+ accuracy over 2 weeks, screenshot and share widely
- Set up a simple email newsletter (Resend free to 3K/mo, integrates with Supabase) — "This week's AI picks and results" keeps early signups engaged and re-activates dormant users

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

Start at **€10/day total**, run for 1 week, kill what doesn't convert. Free entry point means CPA should be low if the message is right.

**Google Ads:** Lower priority — "football predictions" keywords are expensive. Only consider for branded terms once there's brand awareness.

---

## Validation Metrics — When to Go Heavier

Do not scale spend until these signals are visible:

| Metric | What it tells you | Target before scaling |
|--------|-------------------|-----------------------|
| Free signups / day | Acquisition message working? | 10+/day from organic |
| Day 7 retention | Are users coming back? | 30%+ return within a week |
| Prediction tracker usage | Building daily habit? | 40%+ of signups log at least 1 pick |
| Pro section click-through | Upgrade intent? | 20%+ of match detail viewers click into Pro sections |

You don't need paid subscribers to validate. Seeing free users hit upsell surfaces tells you paid conversion will work once Stripe production keys are active.

---

## Key Value Proposition (for copy / ad creative)

**For casual fans (free tier hook):**
> Track your football predictions. See if you beat the AI. One free AI pick every day.

**For bettors (paid tier hook):**
> Full odds comparison across 13 bookmakers. Odds movement timeline. AI injury alerts. Model signal on every match. One saved bad bet pays for 6 months.

**For serious bettors (Elite / future):**
> Exact model probability and edge %. CLV tracking. Value bet list. Our top bot's picks once ROI is validated with real settled bets.

**Founding member angle (use while it lasts):**
> First 500 Pro subscribers lock in €3.99/mo forever. Price raises to €7.99 at 2K paid users.

---

## Pricing Summary (for ads/copy)

| Tier | Price | Founder rate |
|------|-------|-------------|
| Free | €0 | — |
| Pro | €4.99/mo | €3.99/mo (first 500) |
| Elite | €14.99/mo | €9.99/mo (first 200) |

Annual: Pro €39.99/yr (shown on landing page) · Elite annual not shown yet (no real discount — LP-2 resolved)

---

## What to Do Today (ordered)

1. Add "Early Access" framing to site
2. Make daily AI pick visible on /matches without login
3. ~~Fix LP-1/LP-2/LP-3 pricing issues~~ — done 2026-04-29
4. Write and post r/soccerbetting Reddit thread
5. Start daily pick posting on Twitter
6. Swap Stripe to production keys when ready to accept real payments (see INFRASTRUCTURE.md)
