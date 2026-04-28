# Prompt: How to Surface Prediction Signals in a Sports Betting SaaS UI/UX

## Context

We run **OddsIntel** — a sports betting intelligence platform. We have a prediction engine that collects ~58 signals per match, runs them through ML models (Poisson + XGBoost blend), and produces predictions with edge calculations. We want to brainstorm how to surface these signals in the UI to create a better user experience AND drive conversions from free to paid tiers.

---

## Our Tier Structure

| Tier | Price | Target User | Current Content |
|------|-------|-------------|-----------------|
| **Free** | €0 | Casual football fan, curious browser | All fixtures, live scores, 2-3 bookmaker odds, match interest indicator |
| **Pro** | €4.99/mo | Does own research, wants better data | Full odds comparison, odds movement timeline, team form, H2H, goals stats, standings, AI injury alerts, directional model signal (Home lean / Away lean / Even — no raw %) |
| **Elite** | €14.99/mo | Serious bettor, wants model-backed picks | Exact model probability %, edge %, value bet list, CLV tracking, natural language bet explanations, tips from top bot |

**Key UX principle:** Everyone sees all matches. Depth of information varies by tier. We never hide which matches exist — only the analytical depth.

---

## Our Signal Groups (what the engine actually computes per match)

### Group 1 — Model Signals (the probability estimates)
Our engine runs multiple independent models and blends them:
- **Poisson model** — statistical goal-scoring model
- **XGBoost model** — machine learning model trained on historical features
- **API-Football prediction** — third-party prediction
- **Ensemble probability** — calibrated blend of all three
- **Model disagreement** — how much the models disagree with each other (high disagreement = uncertain match)

We have a data tier system based on available historical data:
- **Tier A** (top European leagues): Full Poisson + XGBoost — highest confidence
- **Tier B** (global leagues with ELO data): Poisson only — medium confidence
- **Tier C** (lesser leagues, limited data): Poisson with lower confidence
- **Tier D** (no historical data): Third-party prediction only — lowest confidence

### Group 2 — Market Signals (what the betting market thinks)
- **Opening implied probabilities** — what bookmaker odds imply about each outcome
- **Bookmaker disagreement** — how much bookmakers disagree on odds (big disagreement = market uncertainty, possible value)
- **Overnight line movement** — did odds shift overnight? (suggests new information entered the market)
- **Odds drift** — how much odds have moved from open to now
- **Steam move flag** — rapid >3% odds movement (sharp money or breaking news moving the line)
- **Odds volatility** — how unstable the odds have been over 24 hours
- **Closing Line Value (CLV)** — did we beat the closing line? (the gold standard of betting skill, computed after match)

### Group 3 — Team Quality Signals (form, strength, context)
- **ELO ratings** (home, away, differential) — chess-style power rating for teams
- **Form PPG** — points per game over last 10 matches (rolling)
- **Form slope** — is the team improving or declining? (last 5 vs prior 5)
- **Season goal averages** — goals scored/conceded per game (home and away splits)
- **Venue-specific stats** — how the home team performs AT HOME, how the away team performs AWAY
- **League position** — normalized ranking
- **Points to title / relegation** — motivation proxy (fighting for something vs. dead rubber)
- **H2H record** — historical matchup win percentage and number of meetings
- **Rest days** — days since last match for each team (fatigue/freshness)

### Group 4 — Information Signals (real-world events the market may not have priced in yet)
- **News impact score** — AI-generated score from scanning news 4x/day (team crisis, manager sacked, etc.)
- **Injury count** — how many players are injured per team
- **Players confirmed out** — count of definitely-missing players
- **Lineup confirmed** — have official lineups been published?
- **Lineup confidence** — how certain we are about the expected lineup

### Group 5 — Context Signals (situational factors)
- **Referee stats** — cards per game, home win %, over 2.5 goals % (referee tendencies)
- **Fixture importance** — 0 to 1 urgency score (title decider vs meaningless end-of-season)
- **Importance asymmetry** — one team fighting for survival, other team already safe (motivation mismatch)
- **League meta stats** — this league's average home win rate, draw rate, goals per game

### Group 6 — Live Signals (during the match, updated every 5 minutes)
- **Live score, minute**
- **Live shots, xG, possession**
- **Live odds movements**
- **Red cards, goals as events**

---

## Signal Timeline (when signals become available)

```
T-24h    Fixtures published
T-16h    Morning pipeline: Model predictions + all pre-match signals computed
T-14h    First odds snapshot
T-12h    Odds snapshot + news scan (news impact update)
T-8h     Odds snapshot
T-6h     Odds snapshot + news scan
T-4h     Odds snapshot
T-2h     Odds snapshot
T-1h     Lineups published → lineup confirmed signal
T-30m    Final news scan
T-0      Match kicks off
T+5min   Live signals start (every 5 min)
T+FT     Settlement: result + CLV computed
```

---

## Current State of the UI

Right now, signals are **invisible to users**. The engine computes all 58 signals, feeds them into the model, and produces predictions — but the UI only shows:
- **Free:** basic match info, a few odds, live scores
- **Pro:** detailed stats, form, H2H, injuries, odds comparison, odds chart — this IS signal data but presented as raw information, not labeled as "signals"
- **Elite:** AI predictions with probability %, edge %, and natural language reasoning that references the signals

The signal architecture itself (groups, names, confidence levels, how many signals are available per match) is completely hidden from the user experience.

---

## What We Want You to Think About

1. **Should we surface signals explicitly in the UI?** Not necessarily raw values, but the concept that our engine analyzes X signals per match. Would showing "42/58 signals analyzed" or signal group icons add perceived value?

2. **Signal-based conversion strategy:** How can we use signal visibility to drive Free → Pro → Elite upgrades? For example:
   - Free users see that signals exist but not what they say
   - Pro users see signal data but not the model's conclusions
   - Elite users see the full picture (signals + model output + reasoning)

3. **Signal confidence as a feature:** We have a natural data tier system (A/B/C/D) based on how many signals we can compute per match. High-signal matches are more reliable predictions. Should we surface this as "match analysis depth" or "prediction confidence" to help users understand which predictions to trust more?

4. **Signal timeline as engagement:** Signals arrive at different times (odds snapshots every 2h, lineups 1h before, live data during match). Could a "signal feed" or timeline view create engagement and return visits?

5. **Market signals as a hook:** Things like "steam move detected" (sharp money moving the line), "bookmaker disagreement is high" (market uncertainty), or "overnight line shift" are genuinely interesting even to casual fans. Are these good free-tier hooks?

6. **Live signals during matches:** We track live xG, shots, possession, odds — updated every 5 minutes. How should live signal data enhance the in-play experience?

7. **Signal groups as navigation:** We have 6 clear signal groups (Model, Market, Team Quality, Information, Context, Live). Could these become tabs, cards, or sections in the match detail page?

8. **"Why this pick" transparency:** Elite users get AI reasoning. Should we show WHICH signals drove a prediction? Like "Key factors: Form slope ↑, Bookmaker disagreement high, 2 key players out for opponent"

9. **Gamification / engagement:** Signal availability changes over time (more signals closer to kickoff). Could we create anticipation? "3 new signals arriving in 2 hours" or "Lineups expected soon — prediction will update"

10. **What NOT to show:** Are there signals that would confuse users or give away too much of our methodology? Where's the line between transparency and protecting our edge?

---

## Constraints

- We're a small team (1 person building everything)
- Frontend is Next.js + Tailwind, dark theme, card-based layout
- We want to avoid overwhelming users with data — progressive disclosure is key
- The product should feel premium and analytical, not like a gambling site
- We do NOT want to encourage irresponsible gambling — this is an intelligence tool
- Mobile-first (most sports betting users are on mobile)

---

## One Idea to Riff On (from our team)

**"Match Intelligence Score" — a single number that sells the depth**

Every match gets a visible score like "42/58 signals" or a simpler A/B/C/D grade based on data coverage. This appears on EVERY match card, even for Free users. It does three things:

1. **Anchors perceived value** — users see that our engine does serious analysis, not just scraping odds from one bookmaker
2. **Explains prediction confidence** — an A-grade match with 52/58 signals is a stronger prediction than a D-grade match with 12/58. Users learn to trust the system
3. **Creates natural conversion hooks:**
   - Free tier: sees the score + signal group icons (greyed out). "This match has high market uncertainty — upgrade to Pro to see odds comparison and movement"
   - Pro tier: sees the score + signal data expanded. Specific signal callouts like "Steam move detected 2h ago" or "Form divergence: Home ↑↑ Away ↓". But no model conclusion.
   - Elite tier: sees everything + "Our model finds 4.2% edge on Home win — here's why" with the reasoning referencing the signals Pro users already saw

The key insight: **Pro users who see the raw signals will naturally start forming their own opinions about the match. When Elite shows them the model's opinion alongside theirs, that's the "aha" moment that converts.** They want to know if the model agrees with their gut — and more importantly, what the model saw that they missed.

Additionally, the signal timeline creates a reason to come back. "2 new signals since you last checked" or "Lineups just confirmed — prediction updated" turns a static page into a living analysis that builds toward kickoff.

---

## Deliverable

Please propose a concrete UI/UX strategy for how signals should appear across our three tiers. Include:
- What each tier sees (specific signal visibility per tier)
- Conversion triggers (what makes a Free user want Pro, what makes Pro want Elite)
- Wireframe-level descriptions of new UI elements
- Which signals are most compelling to surface and why
- Any signal-based features that could differentiate us from competitors
- Risks or anti-patterns to avoid
