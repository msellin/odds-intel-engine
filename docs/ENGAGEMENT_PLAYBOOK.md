# OddsIntel — Engagement & Growth Playbook

> Synthesized from 4 independent AI brainstorm sessions + web research (2026-04-30).
> Cross-referenced against existing product decisions in SIGNALS.md, TIER_ACCESS_MATRIX.md, ROADMAP.md.
> Task tracking lives in PRIORITY_QUEUE.md — this doc explains the WHY and WHAT.

---

## Guiding Principles (Already Decided)

These constraints come from the 4-reviewer UX consensus (SIGNALS.md lines 539-557) and product positioning:

- **No gamification** — badges, XP, streaks, leaderboards all rejected. Risks feeling like a gambling site.
- **Premium analytical tone** — Bloomberg Terminal, not casino. Intelligence tool, not tipster service.
- **Responsible gambling** — no "BET NOW", no countdown timers, no flashing colors, no "guaranteed" language.
- **Transparency as differentiator** — public track record, show losses, honest Grade C/D confidence.
- **Social proof through data** — aggregate sentiment, not profiles/forums/comments.

Everything below respects these constraints.

---

## Consensus Matrix (4 AI Brainstorms + Own Research)

| Feature | R1 | R2 | R3 | R4 | Own | Verdict |
|---------|:--:|:--:|:--:|:--:|:---:|---------|
| Daily email digest | ✅ | ✅ | ✅ | ✅ | ✅ | **Do — Phase 1** |
| AI match previews (auto-generated) | ✅ | ✅ | ✅ | ✅ | ✅ | **Do — Phase 1** |
| "X watching this match" counter | ✅ | ✅ | ✅ | ✅ | ✅ | **Do — Phase 1** |
| Community vote split display | ✅ | ✅ | ✅ | ✅ | ✅ | **Do — Phase 1** (DB ready, frontend TBD) |
| Betting glossary (10-15 SEO pages) | ✅ | ✅ | ✅ | ✅ | ✅ | **Do — Phase 1** |
| Watchlist + signal alerts | ✅ | ✅ | ✅ | ✅ | ✅ | **Do — Phase 2** |
| Personal bet tracker + Model vs You | ✅ | ✅ | ✅ | ✅ | ✅ | **Do — Phase 2** |
| Weekly performance email | ✅ | ✅ | ✅ | ✅ | ✅ | **Do — Phase 2** |
| AI signal change alerts | ✅ | ✅ | ✅ | ✅ | ✅ | **Do — Phase 2** |
| Model vs Market vs Users triangulation | ✅ | — | ✅ | ✅ | ✅ | **Do — Phase 2** |
| Shareable pick cards (image) | — | ✅ | — | ✅ | ✅ | **Do — Phase 2** |
| Bot consensus ("7/9 bots agree") | — | — | — | ✅ | ✅ | **Do — Phase 1** (data exists) |
| Methodology page (public whitepaper) | ✅ | ✅ | ✅ | ✅ | — | **Do — Phase 1** |
| Auto-gen weekly prediction pages (SEO) | — | — | — | ✅ | ✅ | **Do — Phase 2** |
| Market inefficiency index per league | — | — | ✅ | — | ✅ | **Do — Phase 3** |
| "What changed today" dashboard | — | — | ✅ | — | ✅ | **Do — Phase 2** |
| Time-decay insights (best time to bet) | — | — | ✅ | — | — | **Defer** — needs 3mo+ data |
| Natural language match queries | ✅ | — | — | ✅ | — | **Defer** — high effort, Elite-only |
| Season-end "Year in Review" | — | — | — | ✅ | — | **Defer** — need a full season |
| Elite vs Free sentiment split | ✅ | — | — | — | — | **Defer** — needs scale |
| Bot personalities/narratives | — | — | ✅ | — | — | **Skip** — conflicts with serious tone |
| Forums / chat / comments | — | — | — | — | — | **Skip** — moderation burden |
| Copy trading / bet sync | — | — | — | — | — | **Skip** — sportsbook integration scope |
| Free-to-play contests | — | — | — | — | — | **Skip** — pulls toward casual |

---

## Phase 1 — Launch Sprint (First 2 Weeks)

### ENG-1: "X Analyzing This Match" Counter

**What:** Show a live counter on each match page: "47 users analyzing this match."

**Why:** Cheapest social proof. Makes the site feel alive from day one. SofaScore shows 50K+ viewers on big matches — you don't need those numbers, even "12 analyzing" signals an active platform.

**Implementation:** Count page views per match in a rolling 30-minute window. Supabase realtime presence or a simple Redis/Postgres counter. Show on match detail page header.

**Tier:** All users see the counter.

**Effort:** 4-6h (backend counter + frontend display)

---

### ENG-2: Community Vote Split Display

**What:** Show the 1X2 vote as a horizontal bar: "Home 62% | Draw 18% | Away 20%"

**Why:** Already have `match_votes` table + voting UI planned in TIER_ACCESS_MATRIX.md. The vote itself isn't the feature — the **split display** is. Creates tension: "Do I agree with the crowd?" Action Network's public betting %s is their stickiest feature.

**Implementation:** Frontend component on match detail. Aggregate query on `match_votes`. Lock voting at kickoff.

**Enhancement (Phase 2):** "Model says X, Users say Y, Market says Z" — three-way triangulation. This is the killer view.

**Tier:** All signed-in users can vote and see splits. Anonymous see the split (read-only).

**Effort:** 4-6h (frontend component + aggregate query)

---

### ENG-3: Daily AI Match Previews

**What:** Auto-generate 200-word match previews for today's top 5-10 matches using Gemini. Published on the site as content cards, also used in email digest and social media posts.

**Why:** Triple-duty feature (all 4 replies flagged this). One Gemini call per match produces:
1. On-site content (match detail page)
2. Email digest content
3. Social media shareable content (Reddit, Twitter)

Competitors: Flashscore launched match previews in 2025. Covers has done this manually for years. Rithmm auto-generates and posts to social.

**Implementation:**
- New cron job at 07:00 UTC: select top 10 matches by signal count + league priority
- Feed Gemini: form, H2H, key injuries, odds movement, model prediction, signal summary
- Store in `match_previews` table (match_id, preview_text, generated_at)
- Frontend: render on match detail page (all users, free content)
- Email: include top 3 previews in daily digest

**Tier:** Free users see previews (it's content marketing, not gated data). Pro/Elite see enhanced previews with signal specifics.

**Effort:** 1-2 days (cron job + Gemini prompt + storage + frontend card)

---

### ENG-4: Daily Email Digest

**What:** Morning email at 07:00 UTC: "Today on OddsIntel — 3 value bets, 2 high-alert matches, your watchlist has 4 games."

**Why:** Every reply ranked this as the #1 retention driver. Picklebet achieved 13% higher 2-month retention with cross-channel journeys. Action Network's daily email is what keeps users coming back.

**Implementation:**
- Resend (already planned as STRIPE-EMAIL, free to 3K/mo)
- Template: Top 3 value bets (blurred for free) + top 3 AI previews + watchlist matches + model performance snippet
- Deep links back to match detail pages
- Unsubscribe + frequency controls in profile

**Tier:**
- Free: top 3 match previews + site activity stats + upgrade CTA
- Pro: + value bet count + signal alerts for watchlist
- Elite: + full value bet details + AI analysis highlights

**Effort:** 2-3 days (Resend setup + email template + cron job + user preferences)

---

### ENG-5: Betting Glossary (SEO Foundation)

**What:** 10-15 pages at `/learn/[term]`: Expected Value, CLV, Poisson Distribution, Asian Handicap, Kelly Criterion, xG, ELO Ratings, Over/Under, BTTS, Odds Movement, Bankroll Management, Value Betting, Bookmaker Margin, Implied Probability.

**Why:** Every serious competitor has this (OddsChecker, Covers, Pinnacle). Zero content pages = zero organic search traffic. These are evergreen SEO pages that rank for long-tail queries like "what is closing line value betting" and convert searchers into users.

**Implementation:**
- Use Gemini to draft initial content, then human-edit for accuracy and tone
- Each page links to where the concept appears in the app: "See CLV in action on our Track Record page"
- Structured data (FAQ schema) for Google featured snippets
- Internal linking from match detail tooltips

**Key insight from Reply 1:** Model Pinnacle's Betting Resources — deep, technical, authoritative. Not generic "How to bet on soccer" filler.

**Effort:** 2-3 days (AI draft + review + build /learn/[term] route + 15 pages)

---

### ENG-6: Bot Consensus Display

**What:** On matches where multiple bots have placed bets, show: "7 of 9 models agree: Over 2.5 is value"

**Why:** You literally have this data already in `simulated_bets`. Zero new data collection needed. Creates powerful social proof from your own model infrastructure. Makes the "9 bots" story tangible.

**Implementation:** Query `simulated_bets` for current match, group by market+selection, show consensus count. Display on match detail (near value bet section) and value bets page.

**Tier:** Free users see consensus count. Pro see which markets. Elite see full bot breakdown.

**Effort:** 3-4h (query + frontend component)

---

### ENG-7: Methodology Page (Public Whitepaper Lite)

**What:** Adapt MODEL_WHITEPAPER.md into a public-facing `/methodology` page explaining the model in plain English.

**Why:** All 4 replies + Reddit research confirmed: transparency is the #1 trust builder. Nobody else publishes their methodology. r/SoccerBetting's top complaint is unverified "expert" picks with no explanation. This page is your credibility anchor.

**Implementation:**
- Adapt whitepaper: Poisson + XGBoost blend explained simply, 58 signals overview, calibration approach, track record link
- No proprietary details (per SIGNALS.md: never reveal weights, blend formula, hyperparams)
- Link from landing page, track record, how-it-works, and all email footers

**Effort:** Half day (content adaptation + new route)

---

## Phase 2 — Retention Engine (Weeks 3-6)

### ENG-8: Watchlist + Signal Alerts

**What:** Users star matches/teams/leagues. Get push/email alerts when:
- Signal changes on a watchlisted match (odds move >5%, model confidence shifts, injury reported)
- Match in their league is about to kick off with a value bet

**Why:** This is what creates the "pull" that brings users back mid-day. Covers' "My Picks" and Action Network's alerts drive daily engagement without forums.

**Already have:** `saved_matches` table exists. `user_notification_settings` table exists. FE-FAV-3 (per-match star) done.

**What's new:** The alert trigger system. Needs notification infrastructure (Resend for email, web push API for browser).

**Tier:** Free: kickoff reminders only. Pro: signal change alerts. Elite: custom multi-signal conditions (see ELITE-ALERT-STACK).

**Effort:** 3-4 days (alert trigger logic + notification delivery + user preferences UI)

---

### ENG-9: Personal Bet Tracker + "Model vs You"

**What:** Users log their bets (match, selection, odds, stake). System tracks ROI, CLV, hit rate. Dashboard shows "Your ROI: +2.1% | Model ROI: +6.8% | You beat the model in Serie A but underperform in Bundesliga."

**Why:** Once a user has 50+ tracked bets, switching cost is enormous. This is what makes people stay. The "Model vs You" comparison creates a game-like dynamic without gamification — it's analytical benchmarking.

**Already have:** `user_picks` table exists (prediction tracker). This extends it with odds + stake + settlement.

**Tier:** Free: track 10 bets/month, basic W/L. Pro: unlimited + ROI + CLV. Elite: + per-league breakdown + model comparison.

**Note:** This is task F10 in PRIORITY_QUEUE (already planned as Tier 3). Promoting to Phase 2 based on unanimous AI consensus.

**Effort:** 3-4 days (extend user_picks, settlement integration, dashboard UI)

---

### ENG-10: Weekly Performance Email

**What:** Every Monday: "Last week — Model: 18-12 (+5.3u). Best call: Dortmund Over 2.5 at 2.10. Your tracked bets: 4-2 (+1.1u). Top league: Bundesliga."

**Why:** Reinforces value proposition weekly. Different from daily digest — this is reflection/analysis, not today's action. Creates "Monday morning" ritual.

**Implementation:** Cron job Monday 08:00 UTC. Pull settled bets from prior 7 days + user's tracked bets. Gemini generates narrative summary.

**Tier:** Free: model performance only. Pro: + personal stats. Elite: + per-league breakdown + CLV.

**Effort:** 1 day (builds on email infrastructure from ENG-4)

---

### ENG-11: "What Changed Today" Dashboard Widget

**What:** Homepage/matches page widget: "Today's biggest moves — Arsenal vs Liverpool: odds shifted 12%, 3 signals flipped. Juventus vs Roma: 2 injuries reported, model confidence dropped."

**Why:** Reply 3 called this "HUGE" — it's a market intelligence briefing. Creates urgency to check the site daily. Think Bloomberg terminal ticker, not sports news.

**Implementation:** Query today's signal changes (overnight_line_move, injury changes, model confidence shifts). Rank by magnitude. Show top 5 on matches page header.

**Tier:** All users see headlines. Pro sees signal details. Elite sees edge impact.

**Effort:** 1 day (query + frontend widget)

---

### ENG-12: Model vs Market vs Users Triangulation

**What:** On match detail, show three predictions side by side:
```
Model: 54% Home    Market: 48% Home    Users: 61% Home
```

**Why:** Reply 3 identified this as "insanely sticky" because it creates tension: "Who's wrong here?" This is the analytical version of community engagement — it makes users think, not just scroll. When model disagrees with both market and users, that's the most interesting signal.

**Implementation:** Model prediction exists. Market implied probability exists. User votes from ENG-2. Render as a three-bar comparison component.

**Tier:** All users see the bars. Pro sees the percentages. Elite sees historical accuracy of each source.

**Effort:** 4-6h (frontend component, data already exists)

---

### ENG-13: Shareable Pick Cards

**What:** Generate a clean, branded image for any match prediction: "OddsIntel | Arsenal vs Liverpool | Model: Over 2.5 @ 2.10 | Grade A | 85% confidence". One-tap share to Twitter/Reddit.

**Why:** Reply 2 flagged this — Action Network lets users share winning bet tickets. Free marketing every time someone shares. The branded image acts as a mini ad.

**Implementation:** Server-side image generation (Vercel OG image or canvas API). Share button on match detail + value bets.

**Tier:** All users can share (it's free marketing). Elite cards show edge %.

**Effort:** 1-2 days (image generation + share buttons)

---

### ENG-14: Auto-Generated Weekly Prediction Pages (SEO)

**What:** `/predictions/premier-league/week-34` — auto-generated from model output with AI narrative context.

**Why:** This is what Forebet, BetStudy, and Windrawwin rank for. Your model already produces this data — you just need a page. Highest-volume SEO opportunity. "Premier League predictions this weekend" is a massive search query every Friday.

**Implementation:** New route `/predictions/[league]/[week]`. Generate page from predictions + AI preview. New pages every matchday automatically.

**Tier:** Free: show match + prediction score. Pro: show odds + confidence. Elite: show edge + stake.

**Effort:** 2-3 days (route + auto-generation cron + SEO metadata)

---

## Phase 3 — Differentiation (Months 2-3)

### ENG-15: Market Inefficiency Index Per League

**What:** Per league: "Eredivisie: HIGH inefficiency (model edge +4.8%). Premier League: LOW inefficiency (+1.2%)."

**Why:** Unique idea from Reply 3. Helps users focus their attention on leagues where your model finds the most edge. No competitor does this. Turns your model performance data into a user-facing feature.

**Implementation:** Aggregate pseudo_clv and model edge by league over rolling 30 days. Rank leagues by average edge magnitude.

**Tier:** Free: league names + high/medium/low label. Pro: edge percentages. Elite: per-market breakdown.

**Effort:** 1 day (query + frontend component on value bets page)

---

### ENG-16: "Ask AI About This Match" (Expanded)

**What:** Per-match AI chat button. User asks: "How does Team A perform when coming off a midweek European away game?" Gemini answers using your signal data, H2H history, and match context.

**Why:** Reply 1's highest-impact idea. ParlaySavant charges $30/mo for this. You already have Gemini + all the data. The difference between your current BET-EXPLAIN (static explanation) and this (interactive Q&A) is the difference between a dictionary and a conversation.

**Implementation:** Expand `/api/bet-explain` to accept freeform questions. Feed match context + all signals as system prompt. Rate limit by tier.

**Tier:** Free: 3 questions/day. Pro: 20/day. Elite: unlimited.

**Effort:** 2-3 days (prompt engineering + rate limiter + UI)

---

### ENG-17: Season-End Review ("Your Year in Betting")

**What:** End-of-season summary: "You tracked 312 bets across 8 leagues. Best month: October (+12.3u). You consistently find value in BTTS markets. Top league: Bundesliga."

**Why:** Strava's "Year in Review" is one of the most viral features in fitness. Shareable, personal, data-driven. Creates a milestone moment that reinforces platform value.

**Implementation:** Build after one full season of user data. Auto-generate using bet tracker data + model performance.

**Effort:** 2-3 days (when ready, needs a season of data)

---

## Key Decisions Made in This Analysis

| Decision | Rationale |
|----------|-----------|
| **No forums or chat** | Moderation is a full-time job. Social proof through aggregate data instead. |
| **No gamification** | Reaffirmed by all 4 replies. Badges/streaks repel serious bettors. |
| **AI previews > manual articles** | 3x leverage (site + email + social). Manual content doesn't scale at our stage. |
| **Email digest is #1 priority** | Unanimous across all replies. Single highest-retention feature in SaaS. |
| **Glossary before blog** | Evergreen SEO > temporal content. Write once, rank forever. |
| **Promote F10 (bet tracker)** | All 4 replies said this is the #1 switching-cost creator. Move from Tier 3 to Phase 2. |
| **Bot consensus, not bot personalities** | "7/9 agree" is data. Bot narratives/characters conflict with serious tone. |

---

## Competitive Landscape (Reference)

| Competitor | Strength We Steal | Gap We Exploit |
|------------|-------------------|----------------|
| **Action Network** | Public betting %s, email digest, bet sync | No model transparency, no CLV education |
| **Covers.com** | Massive community (618K members), streak contests | Unverified tipster records, forum noise |
| **Rithmm** | AI match previews, customizable models | $30/mo price point, black-box model |
| **SofaScore** | Real-time viewer counts, AI player ratings | No betting intelligence, no predictions |
| **Flashscore** | Match previews, win probability | No model, no value bets, no education |
| **Pinnacle Resources** | Deep educational content, authority | Not a product — just content |
| **DeepBetting** | Timestamped prediction audit trail | No signal explanation, no CLV |

**Our unique combination:** Transparent model + signal explanation + CLV tracking + AI analysis + betting education. Nobody else has all five.

---

## Measurement (How We Know It's Working)

| Metric | Baseline | Phase 1 Target | Phase 2 Target |
|--------|----------|---------------|---------------|
| Daily active users | — | 50+ | 200+ |
| Email open rate | — | 30%+ | 35%+ |
| Return visit rate (7-day) | — | 20%+ | 35%+ |
| Free → Pro conversion | — | 3%+ | 5%+ |
| Pro → Elite conversion | — | 10%+ | 15%+ |
| Avg tracked bets per user | — | 5+ | 20+ |
| Organic search traffic/mo | 0 | 500+ | 2,000+ |

---

## Source Legend

| Source | Details |
|--------|---------|
| Reply 1 | ChatGPT brainstorm — strong on competitive analysis, Elite consensus idea |
| Reply 2 | Gemini brainstorm — research-heavy, citations, shareable cards idea |
| Reply 3 | Claude brainstorm — market inefficiency index, time-decay, "what changed today" |
| Reply 4 | Perplexity/Grok brainstorm — triple-duty AI previews, bot consensus, SEO prediction pages |
| Own Research | Web search across Action Network, Covers, Rithmm, SofaScore, Reddit forums |
