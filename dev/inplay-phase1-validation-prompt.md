# AI Consultation Prompt — In-Play Phase 1 Readiness & Implementation

Paste into Claude Opus / GPT-4o / Gemini 1.5 Pro for independent validation.

---

## Context

We are building a soccer betting intelligence product with 16 paper-trading bots running since
2026-04-27. The pre-match model (Poisson + XGBoost ensemble, Pinnacle-anchored calibration,
CLV tracking) is live. We now want to add in-play (live) paper-trading as Phase 1 of a
longer-term in-play model.

We did 4 independent AI strategy reviews and synthesised a plan. Before building, we want
external validation on 3 specific questions.

---

## What we have today

**Infrastructure:**
- Live poller runs every 30 seconds during active matches
- API-Football `/odds/live` endpoint polled every 30s → stores live 1X2 + O/U odds
- `live_match_snapshots` table: one row per match per 30s poll with fields:
  - `match_id`, `minute`, `score_home`, `score_away`, `captured_at`
  - `xg_home`, `xg_away` (cumulative live xG from AF)
  - `shots_home`, `shots_away`, `shots_on_target_home/away`
  - `corners_home`, `corners_away`
  - `possession_home` (0-100)
  - `ou_25_over`, `ou_25_under` (best live O/U 2.5 odds across bookmakers)
  - `live_home_odds`, `live_draw_odds`, `live_away_odds`
- ~243 matches with xG data so far (live odds were broken before May 5, now fixed)
- Pre-match data: `prematch_xg_home/away`, `prematch_btts_prob`, team ELO, league tier

**What we don't have yet:**
- A feature pipeline that transforms snapshots → training rows
- An actual in-play paper-trading bot
- xG pace ratio computed anywhere (would need to derive from snapshots)

---

## The plan we've synthesised (seeking validation)

### Core hypothesis
The live O/U market anchors primarily on **time elapsed + scoreline** but lags on **xG pace**
(true chance quality). When realized goals < expected xG but the goal-generation process is
still above the pre-match rate, the Under odds drift too high — creating value for Overs.

### Phase 1: Paper-trade Strategy A while data accumulates

**Strategy A — xG Divergence Over:**
- Entry window: minute 20-35
- Conditions:
  - Score is 0-0 or 1-0 (combined goals ≤ 1)
  - Combined live xG ≥ 0.7
  - xG pace ratio > 1.0 (live xG/min ÷ prematch xG/90 > 1.0)
  - Pre-match O2.5 implied prob > 52%
  - Live O2.5 odds ≥ 2.20
- Market: Over 2.5 goals
- Skip if: xG per shot < 0.08 (low quality), red card active, odds < 2.20
- Stake: fixed 1% of paper bankroll (not Kelly yet — too few bets)
- Expected edge: 3-8% when all conditions align (per 4 AI reviews)

**Logging:** Each triggered bet logged to `simulated_bets` with:
- `market = 'ou_25'`, `selection = 'over'`
- `odds` = live O2.5 over odds at trigger moment
- `model_prob` = derived from xG pace model
- `stake` = 1% fixed
- Settlement at FT: `result = over/under`, `pnl` computed

### Phase 2 (when 500+ matches with xG): Train LightGBM
- Target: `lambda_remaining_home` + `lambda_remaining_away` (Poisson rates)
- Derive O/U, BTTS, 1X2 probabilities from that
- Test all 6 strategies (A-F), identify which have genuine edge

---

## Three questions we want validated

### Q1: Is xG pace ratio the right trigger for Phase 1?
Our plan uses `(live_xg_total / minutes_played) / (prematch_xg_total / 90)` as the primary
signal. Is this the right formulation? Are there known issues with xG accumulation early in
a match (low sample size at minute 20-25) that would make this noisy? Should we use a
Bayesian update on the prematch prior instead of a simple ratio?

### Q2: Is 30-second polling sufficient for this strategy?
Strategy A enters at minute 20-35. The bet is not a reaction to an event — it's a
pre-calculated position based on accumulated xG. After a goal is scored, we skip matches
that exceed the score threshold (>1 combined goals). Is 30s sufficient to catch the
entry window before odds move post-goal? Or do we need 5-10s polling at minimum?

### Q3: How do we validate edge on 200-500 paper bets?
With ~150 matches/day and Strategy A triggering on roughly 10-15% of them (those with
conditions met in the right window), we'd get ~15-20 paper bets/day. At what sample size
can we meaningfully distinguish real edge from noise? What statistical test is appropriate
(binomial test on hit rate? t-test on ROI? CLV analysis)? 

Minimum threshold to consider Phase 2 go-ahead: ROI > 0% on 200+ bets AND CLV (entry odds
vs closing odds) > 0 on 80%+ of bets.

---

## What we want from this consultation

1. **Validate or challenge** the xG pace ratio formulation (Q1)
2. **Validate or challenge** the polling frequency assumption (Q2)
3. **Recommend a statistical framework** for edge validation with small samples (Q3)
4. **Flag any blind spots** in the Phase 1 plan we haven't considered
5. **Suggest any changes** to Strategy A conditions based on known soccer live market research

Please be specific and direct. If the plan is sound, say so. If there are flaws, identify
them precisely. This team has a working ML pipeline and will implement your recommendations.
