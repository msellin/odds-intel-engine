# AI Consultation Prompt — In-Play Paper Trading Strategy Evaluation & New Ideas

Paste into Claude Opus / GPT-4o / Gemini 1.5 Pro. Ask each tool independently.

---

## Context

We are building a soccer betting intelligence product. We have a live data pipeline that
polls API-Football every 30 seconds during active matches and stores the following per match
per poll in `live_match_snapshots`:

**Available live data fields:**
- `minute` — current match minute
- `score_home`, `score_away` — current score
- `xg_home`, `xg_away` — cumulative live xG (expected goals)
- `shots_home`, `shots_away`, `shots_on_target_home/away`
- `corners_home`, `corners_away`
- `possession_home` (0-100)
- `ou_25_over`, `ou_25_under` — best live Over/Under 2.5 odds across 13 bookmakers
- `live_home_odds`, `live_draw_odds`, `live_away_odds` — live 1X2 odds

**Pre-match data we have per fixture:**
- `prematch_xg_home`, `prematch_xg_away` — model-predicted xG before KO
- `prematch_btts_prob` — pre-match BTTS probability from our model
- `prematch_o25_prob` — pre-match Over 2.5 probability
- Team ELO ratings (home + away)
- League tier (1-4), fixture importance score
- Pre-match 1X2 odds and implied probabilities (including Pinnacle)

**What we DON'T have live:**
- Live xG per shot (only cumulative total)
- Live O/U 1.5 or O/U 3.5 odds (only O/U 2.5 from AF)
- Substitution timestamps
- Live corners per 10-minute window (only cumulative)

**Infrastructure:** 30-second polling, paper bets logged to `simulated_bets` table,
settled at FT. No real money — pure paper trading to validate edge. Each strategy runs
as a separate named bot so we can compare ROI + CLV per strategy independently.

---

## Strategies already planned (from prior 4-AI review)

We have 6 strategies already designed. Brief summary:

- **Strategy A** — xG Divergence Over 2.5: min 20-35, score ≤ 1 combined goals, xG pace
  ratio > 1.0 (live xG/min ÷ prematch xG/90), live O2.5 odds ≥ 2.20, pre-match O2.5 > 52%.
  Edge hypothesis: market anchors on scoreline, lags on true chance quality.

- **Strategy B** — BTTS Momentum: min 15-40, score 1-0 or 0-1, trailing team xG ≥ 0.4
  AND shots on target ≥ 2, pre-match BTTS > 48%. Edge: trailing teams with real xG compress
  market's pessimism about a second goal.

- **Strategy C** — Favourite Comeback: min 25-60, pre-match favourite trailing by 1,
  favourite xG > underdog xG AND possession ≥ 60%. Market: 1X2 Favourite or Draw No Bet.
  Edge: market over-reacts to the scoreline vs the underlying dominance indicators.

- **Strategy D** — Late Goals Compression: min 55-75, score 0-0 or 1-0, combined xG ≥ 1.0,
  live odds > 2.50, pre-match expected goals > 2.3. Market: Over 1.5 total.
  Edge: final-30-min scoring rate ~65-70% regardless of prior score; market misprices this.

- **Strategy E** — Dead Game Unders: min 25-50, score 0-0 or 1-0, xG pace < 70% of expected,
  shots slowing, corners low. Market: Under 2.5. Edge: market assumes constant hazard rate,
  tempo collapse is real.

- **Strategy F** — Odds Momentum Reversal: any minute, odds move > 15% in < 10 min WITHOUT
  a goal AND contrary to xG trend. Bet against the move direction.
  Edge: sharp-looking moves without event sometimes overshoot and revert.

---

## Two new ideas from our team we want evaluated

### Idea 1: Over 3.5 High-xG Bot
**Concept:** When both teams are generating high xG early (combined xG > 1.0-1.2 by minute
20-35) but no goals have come yet, the Over 3.5 odds may drift to attractive levels because
the market anchors on 0-0 scoreline. Bet Over 3.5 when underlying xG suggests a high-scoring
game that just hasn't opened yet.

**Proposed conditions:**
- Minute 20-35
- Score 0-0
- Combined live xG ≥ 1.0
- xG pace ratio > 1.3 (significantly above pre-match expectation)
- Pre-match O2.5 implied prob > 60% (pre-match expected to be a goal-fest)
- Live O3.5 odds would need to be available (we only have O2.5 from AF — could we
  approximate O3.5 from O2.5 odds + score state? Or is this unfeasible without O3.5 data?)

**Questions:**
- Is this a real edge or does it just overlap with Strategy A?
- Is 0-0 at min 20-35 with high xG actually frequent enough to generate sample size?
- Is there a known Over 3.5 in-play mispricing in the literature?

### Idea 2: Home Favourite Comeback Bot
**Concept:** Home teams trailing by 1 goal have structural advantages (crowd pressure, familiar
pitch, substitution urgency) that the live 1X2 market may underestimate. Split Strategy C
specifically for home favourites (vs any favourite) to test if the home crowd factor creates
an additional signal on top of xG dominance.

**Proposed conditions:**
- Same as Strategy C but ONLY when home team is the pre-match favourite trailing by 1
- Add: possession ≥ 55% (slightly lower threshold — home crowd generates more set pieces)
- Add: minute ≤ 70 (home teams have time to push; late in game crowd panic can backfire)
- Market: Home Win or Draw (Double Chance) rather than DNB

**Questions:**
- Is there academic evidence that home favourite comeback has materially different hit rate
  than away favourite comeback in live markets?
- Does the crowd effect show up in xG data or only in win probability over longer time windows?
- Is Double Chance Home/Draw better than DNB for this specific scenario?

---

## What we want from this consultation

### Part 1 — Evaluate our two new ideas
For each idea (Over 3.5 Bot and Home Favourite Comeback):
1. Is the edge hypothesis sound? What could make it wrong?
2. Are the proposed conditions well-calibrated or do they need adjustment?
3. What's the expected hit rate and approximate edge if conditions are met?
4. Any practical problems (sample size, data requirements, odds availability)?

### Part 2 — Suggest additional paper trading strategies we haven't thought of
Given our exact data fields (listed above), suggest 3-5 additional in-play paper trading
strategies worth testing in Phase 1. Each suggestion should:
- Have a clear edge hypothesis (why is the market mispricing this?)
- Use only data fields we actually have (listed above)
- Be specific enough to implement as a rule-based bot (concrete conditions, not vague)
- Identify the market to bet (1X2, O/U 2.5, BTTS, or combination)
- Estimate expected hit rate and edge size if the hypothesis is correct
- Note any weaknesses or failure modes

Good candidates might include:
- Corner-based signals (we have cumulative corners, not per-10min — can this still work?)
- Shot-quality signals (shots on target as % of shots = on-target ratio)
- Possession efficiency signals (xG per possession unit)
- Half-time specific patterns (first 5 min after HT)
- Red card aftermath (we skip matches with red cards in current plan — is there value there?)
- Score volatility plays (a match that went 0-0 → 1-0 → 1-1 in 15 min)

### Part 3 — Prioritisation
Given we are running ALL strategies simultaneously as separate paper-trading bots to compare:
- Which 2-3 strategies would you prioritise for FASTEST edge validation? (i.e. highest
  trigger frequency so we accumulate 200+ bets quickest)
- Which strategies need the largest sample before we can trust the result?
- Which strategies are most likely to have real edge vs which are most speculative?

---

## Constraints to keep in mind
- We only have O/U 2.5 live odds from AF, not O/U 1.5 or O/U 3.5
- 30-second polling (not millisecond) — strategies must not depend on catching the exact
  moment odds change post-event
- Red cards: current plan skips all matches with red cards (simpler V1)
- Paper trading only — no real money execution risk at this stage
- Each strategy is a separate named bot so we compare independently
