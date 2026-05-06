# Reddit Launch — Strategy, Progress & Posts

> Merged from `reddit_warmup_comments.md` + `reddit_launch_posts.md` (2026-05-04). Updated 2026-05-06.
> Full launch strategy in `docs/LAUNCH_PLAN.md`. ENG task tracking in `PRIORITY_QUEUE.md`.

---

## Progress

| Day | Date | Target | Status |
|-----|------|--------|--------|
| 1 | Apr 30 | Warm-up comments (r/SoccerBetting + r/soccer) | ✅ Done — 4+ comments: Forest/Villa, Atletico/Arsenal, Braga match threads, SoccerBetting Daily Picks |
| 2 | May 1–3 | Additional warm-up comments | ✅ Done — further warm-up comments on r/SoccerBetting + r/soccer visible in profile |
| 3 | May 4 | Launch post → r/buildinpublic | ✅ Done — "Building a football analytics platform in public — week 1 update. What am I missing?" — 60 views, 1 upvote |
| 4 | May 5 | r/FootballBetting | ✅ Done — OddsIntel posted |
| 5 | May 6 | r/SoccerPredictions | ⬜ Upcoming — draft ready (with REDDIT code) |
| 6 | May 6+ | r/dataisbeautiful [OC] | ⬜ Upcoming — needs Arsenal-Atletico odds movement screenshot |

### Day 4 detail (May 5 — DONE)

OddsIntel posted to 2 subreddits (update with which subs + any engagement data when known).

Promo code `REDDIT` (1 month free) added as reply to all 3 active posts (r/buildinpublic + 2 launch subs). Copy used: "Quick add — a few people DMed asking about pricing. If you want to try Pro, use code **REDDIT** at checkout for your first month free. No pressure, the free tier has most of what you need anyway."

### Day 3 detail (May 4 — DONE)

r/buildinpublic — "Building a football analytics platform in public — week 1 update. What am I missing?"
- 60 views, 1 upvote as of May 5 morning
- Post title was adapted from Post 6 draft — shorter, curiosity-hook style

### Day 1 detail (Apr 30 — DONE)

- r/SoccerBetting → Daily Picks Thread — Forest vs Villa take
- r/soccer → Daily Discussion — CL semis recap + EL tonight preview
- r/soccer → Atletico-Arsenal Post-Match Thread — Eze penalty VAR take
- r/soccer → Braga match thread — "Strong start from Braga"

### Day 2 (May 1-3 — DONE)

Additional warm-up comments across r/SoccerBetting + r/soccer. Visible in profile history.

---

## Subreddit Rules

| Sub | Standalone post? | Self-promo? | Notes |
|-----|-----------------|-------------|-------|
| r/SoccerBetting | Yes (lead with picks + data) | Cautiously — link at the bottom | No paid tipster promotion. Show data first. |
| r/FootballBetting | Yes | Same as above | Smaller community, similar rules |
| r/soccer | **No** — Daily Discussion only | Very strict, auto-removed | 5.8M members, heavy moderation |
| r/dataisbeautiful | Yes — requires [OC] tag + visual | Ok if genuine data viz | Must include a chart/screenshot, not a pitch |
| r/buildinpublic | Yes | Yes — that's the whole point | Builder/maker community, expects project posts |

**Key rules:**
1. Never mention OddsIntel in warm-up comments
2. Space comments across the day, not all at once
3. Engage with every reply on launch posts
4. Different text for every subreddit — no cross-posting
5. If a post gets removed, message mods before reposting

---

## Post Drafts

### Post 1 — r/SoccerBetting

**Title:** I built a tool that tracks CLV on automated betting strategies — here's the early data after a week

**Body:**

I've been building a football analytics platform for the last few months. The core idea: instead of selling tips, build the model in the open and let the data speak for itself.

**What I'm doing differently:**

Most "tipster" services show you W/L records that are impossible to verify. I'm tracking CLV (Closing Line Value) — whether a bet was placed at better odds than where the market closed. It's the only metric that proves genuine edge in a small sample. A tipster can get lucky for 2 weeks. Consistently beating the closing line? That's signal.

**Current state (it's early, I know):**

- 24 paper trading bots running (16 pre-match since April 27 + 8 in-play since May 6), each targeting different markets (1X2, BTTS, Over/Under) across specific leagues
- Ensemble model: Poisson regression + XGBoost blend, with 25+ signals per match (form, xG, injuries, odds movement, Pinnacle line moves, sharp consensus, referee tendencies, H2H)
- Scanning ~280 matches/day across 60+ leagues, comparing odds from 13 bookmakers
- Every prediction, every CLV calculation, every result — tracked on the site, no cherry-picking
- Track record is still accumulating — that's why it says "Beta" on the site

**What you can actually use for free:**

- All fixtures + live scores across 280+ leagues
- Best available odds across all bookmakers we track
- H2H records, standings, team form
- Signal grade on every match (how many signals align — A/B/D)
- 1 free AI value pick per day
- Prediction tracker — log your picks, track your hit rate, compare vs the model

Paid tiers (Pro/Elite) add full odds comparison across 13 bookmakers, odds movement charts, injury alerts, lineups, model probabilities, and value bet edge %.

If anyone's interested: [oddsintel.app](https://oddsintel.app)

Happy to answer questions about the model, methodology, or data pipeline. Not here to sell anything — genuinely want feedback from people who understand this space.

---

### Post 2 — r/FootballBetting

**Title:** Free match intelligence tool — 280+ leagues, odds comparison, 1 AI pick daily (no signup needed for basics)

**Body:**

Been working on a football research tool that pulls together the stuff I always wished I had in one place when doing match research.

**What it does (free, no account needed):**

- Today's fixtures across 280+ leagues with best available odds
- H2H records and recent meetings
- League standings + team form (last 5)
- Live scores during matches
- Signal grade on each match — how many data signals align (form, odds movement, injuries, market data)
- 1 AI value pick visible daily — no login required to see it

**If you create a free account (also free, just email):**

- Favourite teams & leagues with a "My Matches" filtered view
- Prediction tracker — log your picks, see your hit rate over time
- Match notes (private journal per match)
- Community voting — see what others think (1X2 poll)
- Saved matches watchlist

The model behind it runs 24 automated paper-trading strategies across different markets (pre-match + in-play), tracking closing line value as the proof metric. Track record is live on the site — it's thin because we only started a few weeks ago, but everything is transparent. No cherry-picked screenshots.

Paid tiers add full odds from 13 bookmakers side by side, injury reports, lineups, odds movement timeline, and model probability breakdown.

Check it out: [oddsintel.app](https://oddsintel.app)

Would love feedback on what's useful and what's missing. Built this because I was frustrated with the tools available — curious if others feel the same.

---

### Post 3 — r/soccer (Daily Discussion only — no standalone post)

**Title:** (reply in Daily Discussion thread — no standalone post allowed)

**Short version for Daily Discussion:**

Hey — I built a free football match tracker that goes a bit deeper than the usual score apps. It covers 280+ leagues, shows best odds, H2H records, team form, and has a signal grade per match (basically a score of how much the data aligns).

Also runs an AI prediction model — shows its best pick for the day right on the matches page, no login needed.

If you want to track your own picks against the AI, you can create a free account (just email). It settles predictions automatically after matches.

Still in beta — [oddsintel.app](https://oddsintel.app) if anyone wants a look.

---

### Post 4 — r/SoccerPredictions

**Title:** Free prediction tracker — log your picks, track your hit rate, compare against an AI model

**Body:**

I built a prediction tracker as part of a larger football analytics tool. Thought this community might find it useful.

**How it works:**

1. Browse today's matches (280+ leagues covered)
2. Make your prediction (Home / Draw / Away) on any match
3. After the match finishes, the system settles your pick automatically
4. Track your hit rate, W/L record, and streaks over time

The interesting part: the platform also runs an AI model (Poisson + XGBoost ensemble with Pinnacle-anchored calibration) making its own predictions on every match. So you can see how your gut compares to the algorithm.

**Also included (free):**

- Live scores, H2H records, standings, team form
- Signal grade per match (how aligned the data is)
- 1 AI value pick per day — the model's top pick
- Community voting — see what everyone else predicts

Everything is free. The site is in beta — track record is still accumulating. But it's all transparent.

[oddsintel.app](https://oddsintel.app)

---

### Post 5 — r/dataisbeautiful

**Title:** [OC] I track 20+ signals across 280 daily football matches to find where bookmakers disagree with the data

**Body:**

I built a football prediction engine that scans ~280 matches per day across 60+ leagues, pulling together 25+ data signals per match:

- Expected goals (xG) vs actual form divergence
- Odds movement across 13 bookmakers (where are lines shifting?)
- Home/away performance splits
- Injury & suspension impact
- Head-to-head patterns
- Referee tendencies (cards, penalties)
- Sharp vs soft bookmaker consensus (do Pinnacle/Pinnacle-tier books agree with the public lines?)
- Market consensus vs model disagreement

Each match gets graded: **A** (strong alignment — most signals point the same way), **B** (mixed), or **D** (weak data / conflicting signals). The model is a Poisson regression + XGBoost ensemble, calibrated with Platt scaling.

The interesting bit is tracking Closing Line Value (CLV) — did the model identify value *before* the market corrected? A bet placed at 2.10 that closes at 1.95 beat the closing line by 7.7%. Consistently positive CLV = genuine predictive edge, regardless of short-term W/L variance.

I'm running 24 automated paper-trading strategies to test this across different markets (1X2, Both Teams to Score, Over/Under) and leagues — 16 pre-match + 8 in-play.

The dashboard is free: [oddsintel.app](https://oddsintel.app) — you can browse all matches, see the signal grade, and check the track record.

*Tools: Python, XGBoost, Supabase (Postgres), Next.js, API-Football, Vercel*

> **Note for posting:** Needs a visual — screenshot of odds movement timeline or signal heatmap. r/dataisbeautiful requires [OC] tag + actual visualization.

---

### Post 6 — r/buildinpublic

**Title:** Launched OddsIntel — football analytics platform tracking 16 automated strategies across 280 matches/day. Here's the stack.

**Body:**

Been building this for a few months, just went live in beta. It's a football match intelligence platform that runs AI prediction models and tracks their performance transparently.

**The pipeline:**

11 jobs running on **Railway** (long-running APScheduler process — no cold starts):

1. **Fixtures** (04:00 UTC + 4 refreshes) — pull today's matches from API-Football
2. **Enrichment** (4x daily) — standings, H2H, team stats, injuries
3. **Odds** (every 2h, 05-22 UTC) — bulk odds from API-Football (13 bookmakers)
4. **Predictions** (05:30) — API-Football predictions for ensemble blend
5. **Betting** (6x daily) — Poisson + XGBoost model runs, 24 bots place paper bets
6. **Live Poller** (24/7 adaptive 30s/120s) — live scores, events, lineups, in-play odds + 8 in-play betting strategies
7. **News Checker** (5x daily) — Gemini AI scans for injury/suspension news
8. **Settlement** (21:00 + 01:00 UTC) — settle bets, post-match stats, ELO updates, CLV calculation, weekly model recalibration
9. **AI Match Previews** (07:15) — Gemini 200-word previews for top 10 matches
10. **Email Digest** (07:30) — tier-gated daily digest + value bet alerts + weekly report
11. **Watchlist Alerts** (3x daily) — odds movement + kickoff reminder emails

**Stack:**

- **Backend/Pipeline:** Python 3.12, Railway ($5/mo — long-running process, no cold starts)
- **Model:** Poisson regression + XGBoost ensemble with Platt scaling, Pinnacle-anchored calibration, dynamic Dixon-Coles
- **Database:** Supabase Pro (Postgres, Row Level Security, real-time)
- **Frontend:** Next.js 15 (App Router), TypeScript, Tailwind, Vercel
- **Auth:** Supabase Auth (magic link + Google) with custom SMTP
- **Payments:** Stripe (live — checkout, webhooks, customer portal, promo codes)
- **Error monitoring:** Sentry (frontend)
- **AI:** Gemini 2.5 Flash (~$0.55/mo — news analysis + match previews + bet explanations + post-mortems)
- **Email:** Resend (transactional + digest + alerts)
- **Analytics:** PostHog (conversion funnel) + Vercel Speed Insights
- **Data:** API-Football Ultra ($29/mo, 75K req/day)

**Current numbers:**

- ~280 matches scanned daily across 60+ leagues
- 13 bookmakers compared per match
- 25+ signals per match feeding the model (including Pinnacle line moves, sharp consensus)
- 24 paper trading bots — 16 pre-match + 8 in-play strategies
- Total infra cost: ~$85/month (API-Football $29 + Supabase Pro $25 + Railway $5 + Vercel $20 + domain + Gemini ~$0.55)

**Business model:**

Free tier is genuinely useful (all matches, live scores, odds, form, 1 AI pick/day, prediction tracker, community voting). Paid tiers (Pro €4.99/mo, Elite €14.99/mo) add depth — full odds comparison across 13 bookmakers, odds movement charts, live in-play chart, AI injury alerts, intelligence summary, value bet page, bankroll analytics. Founding member pricing: first 500 Pro lock in €3.99/mo forever.

Live at [oddsintel.app](https://oddsintel.app) — would love feedback on the product or architecture. Happy to go deeper on any part of the stack.
