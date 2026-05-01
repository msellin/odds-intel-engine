# Reddit Launch Posts — Phase 1

> Draft posts for each subreddit. Adapt tone per audience. Space across ~1 week.
> Post from a warmed-up account (3-5 days of comments first).
> Lead with value, product link at the bottom.

---

## 1. r/SoccerBetting

**Title:** I built a tool that tracks CLV on automated betting strategies — here's the early data after 3 days

**Body:**

I've been building a football analytics platform for the last few months. The core idea: instead of selling tips, build the model in the open and let the data speak for itself.

**What I'm doing differently:**

Most "tipster" services show you W/L records that are impossible to verify. I'm tracking CLV (Closing Line Value) — whether a bet was placed at better odds than where the market closed. It's the only metric that proves genuine edge in a small sample. A tipster can get lucky for 2 weeks. Consistently beating the closing line? That's signal.

**Current state (it's early, I know):**

- 16 paper trading bots running since April 27, each targeting different markets (1X2, BTTS, Over/Under) across specific leagues
- Ensemble model: Poisson regression + XGBoost blend, with 20+ signals per match (form, xG, injuries, odds movement, referee tendencies, H2H)
- Scanning ~280 matches/day across 60+ leagues, comparing odds from 13 bookmakers
- Every prediction, every CLV calculation, every result — tracked on the site, no cherry-picking
- Track record is thin (a few days of settled bets) — that's why it says "Beta" on the site

**What you can actually use for free:**

- All fixtures + live scores across 280+ leagues
- Best available odds across all bookmakers we track
- H2H records, standings, team form
- Signal grade on every match (how many signals align — A/B/D)
- 1 free AI value pick per day
- Prediction tracker — log your picks, track your hit rate, compare vs the model

Paid tiers (Pro/Elite) are coming soon — odds comparison across 13 bookmakers, odds movement charts, injury alerts, lineups, full value bet list with model probabilities. Not live yet, building in public.

If anyone's interested: [oddsintel.app](https://oddsintel.app)

Happy to answer questions about the model, methodology, or data pipeline. Not here to sell anything — genuinely want feedback from people who understand this space.

---

## 2. r/FootballBetting

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

The model behind it runs 16 automated paper-trading strategies across different markets, tracking closing line value as the proof metric. Track record is live on the site — it's thin because we only started a few days ago, but everything is transparent. No cherry-picked screenshots.

Paid tiers are coming soon (odds from 13 bookmakers side by side, injury reports, lineups, odds movement timeline). Free tier is genuinely useful on its own though — not a bait-and-switch.

Check it out: [oddsintel.app](https://oddsintel.app)

Would love feedback on what's useful and what's missing. Built this because I was frustrated with the tools available — curious if others feel the same.

---

## 3. r/soccer

**Title:** I built a free football match tracker with AI predictions — covers 280+ leagues, live scores, and one free value pick daily

**Body:**

Hey r/soccer — I built a tool for following football matches that goes a bit deeper than the usual score apps.

**What you get (no account, completely free):**

- All today's fixtures across 280+ leagues worldwide
- Live scores with auto-refresh
- Best available odds for every match
- Head-to-head records and recent meetings
- League standings and team form
- An intelligence grade on each match — the model looks at 20+ data signals (form, odds movement, injuries, market disagreements) and rates how much the data "agrees" on an outcome
- 1 AI value pick per day — the model's best pick, visible right on the matches page

**With a free account (just email, nothing else):**

- Star your favourite teams and leagues, get a "My Matches" filtered view
- Prediction tracker — make your picks before matches start, track your hit rate over time
- Community voting on each match
- Match notes (private)

It's in early access / beta — the prediction model has only been running live for a few days so the track record is thin. But it's all transparent on the site. No fake screenshots or inflated numbers.

Built it because I wanted one place to check matches, odds, form, and signals without bouncing between 5 different apps.

[oddsintel.app](https://oddsintel.app)

---

## 4. r/SoccerPredictions

**Title:** Free prediction tracker — log your picks, track your hit rate, compare against an AI model

**Body:**

I built a prediction tracker as part of a larger football analytics tool. Thought this community might find it useful.

**How it works:**

1. Browse today's matches (280+ leagues covered)
2. Make your prediction (Home / Draw / Away) on any match
3. After the match finishes, the system settles your pick automatically
4. Track your hit rate, W/L record, and streaks over time

The interesting part: the platform also runs an AI model (Poisson + XGBoost ensemble) making its own predictions on every match. So you can see how your gut compares to the algorithm.

**Also included (free):**

- Live scores, H2H records, standings, team form
- Signal grade per match (how aligned the data is)
- 1 AI value pick per day — the model's top pick
- Community voting — see what everyone else predicts

Everything is free. The site is in beta — track record is still thin because the model only went live recently. But it's all transparent.

[oddsintel.app](https://oddsintel.app)

---

## 5. r/dataisbeautiful

**Title:** [OC] I track 20+ signals across 280 daily football matches to find where bookmakers disagree with the data

**Body:**

I built a football prediction engine that scans ~280 matches per day across 60+ leagues, pulling together 20+ data signals per match:

- Expected goals (xG) vs actual form divergence
- Odds movement across 13 bookmakers (where are lines shifting?)
- Home/away performance splits
- Injury & suspension impact
- Head-to-head patterns
- Referee tendencies (cards, penalties)
- Market consensus vs model disagreement

Each match gets graded: **A** (strong alignment — most signals point the same way), **B** (mixed), or **D** (weak data / conflicting signals). The model is a Poisson regression + XGBoost ensemble, calibrated with Platt scaling.

The interesting bit is tracking Closing Line Value (CLV) — did the model identify value *before* the market corrected? A bet placed at 2.10 that closes at 1.95 beat the closing line by 7.7%. Consistently positive CLV = genuine predictive edge, regardless of short-term W/L variance.

I'm running 16 automated paper-trading strategies to test this across different markets (1X2, Both Teams to Score, Over/Under) and leagues. Still very early (a few days of data), but everything is tracked openly.

The dashboard is free: [oddsintel.app](https://oddsintel.app) — you can browse all matches, see the signal grade, and check the track record.

*Tools: Python, XGBoost, Supabase (Postgres), Next.js, API-Football, Vercel*

---

## 6. r/buildinpublic

**Title:** Launched OddsIntel — football analytics platform tracking 16 automated strategies across 280 matches/day. Here's the stack.

**Body:**

Been building this for a few months, just went live in beta. It's a football match intelligence platform that runs AI prediction models and tracks their performance transparently.

**The pipeline:**

9 single-purpose jobs running on GitHub Actions cron:

1. **Fixtures** (04:00 UTC) — pull today's matches from API-Football
2. **Enrichment** (3x daily) — standings, H2H, team stats, injuries
3. **Odds** (every 2h, 05-22 UTC) — bulk odds from API-Football (13 bookmakers) + Kambi scraper
4. **Predictions** (05:30) — API-Football predictions for ensemble blend
5. **Betting** (06:00) — Poisson + XGBoost model runs, 16 bots place paper bets
6. **Live Tracker** (every 5 min, 12-22 UTC) — live scores, events, lineups, in-play odds
7. **News Checker** (4x daily) — Gemini AI scans for injury/suspension news
8. **Settlement** (21:00) — settle bets, post-match stats, ELO updates, CLV calculation

**Stack:**

- **Backend/Pipeline:** Python 3.14, GitHub Actions (free tier — ~11K min/month)
- **Model:** Poisson regression + XGBoost ensemble with Platt scaling calibration
- **Database:** Supabase Pro (Postgres, Row Level Security, real-time subscriptions)
- **Frontend:** Next.js 15 (App Router), TypeScript, Tailwind, Vercel
- **Auth:** Supabase Auth (email OTP + Google + Discord)
- **Payments:** Stripe (test mode — going live soon)
- **Error monitoring:** Sentry
- **Data:** API-Football Ultra ($29/mo) + Kambi (free)

**Current numbers:**

- ~280 matches scanned daily across 60+ leagues
- 13 bookmakers compared per match
- 20+ signals per match feeding the model
- 16 paper trading bots testing different strategies
- Total infra cost: ~$85/month (API-Football $29 + Supabase Pro $25 + Vercel Pro $20 + domain $12/yr)

**Business model:**

Free tier is genuinely useful (all matches, live scores, odds, form, 1 AI pick/day). Paid tiers (Pro €4.99/mo, Elite €14.99/mo) add depth — full odds comparison, injury alerts, model probabilities. Founding member pricing: first 500 Pro lock in €3.99/mo forever.

Paid tiers say "Coming Soon" — I'm not turning on real payments until the track record has 2+ weeks of data. No point charging people before the model has proven itself.

Live at [oddsintel.app](https://oddsintel.app) — would love feedback on the product or architecture. Happy to go deeper on any part of the stack.

---

## Posting Schedule

| Day | Subreddit | Notes |
|-----|-----------|-------|
| Day 1 | r/SoccerBetting | Primary target — sharp bettors |
| Day 2 | r/FootballBetting | Similar audience, slightly different tone |
| Day 3 | r/soccer | Casual angle, biggest audience |
| Day 4 | r/SoccerPredictions | Prediction tracker angle |
| Day 5 | r/dataisbeautiful | Data/methodology angle (needs a chart/visual) |
| Day 6 | r/buildinpublic | Builder/architecture angle |

## Notes

- For r/dataisbeautiful: need to create a visual (odds movement chart or signal heatmap) — OC rule requires original visualization
- Space posts across different times of day for maximum visibility
- Engage genuinely in comments — answer every question
- If a post gets traction, update it with results after 1 week
- Don't mention "beta" defensively — frame it as "launched recently, data is accumulating, everything is transparent"
