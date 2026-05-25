# META-BOT-PORTFOLIO — Plan

> **Status:** Waiting — do not start until Phase 4 pivot decision (~2026-06-30)
> **Trigger:** 200-bet cohort report identifies which bots have real positive ROI
> **Effort:** 1-2 days implementation once unblocked

---

## The Problem

Running bots independently has three flaws for a single-bankroll manual bettor:

1. **Overbetting** — each bot sizes stakes against the full bankroll, ignoring that other bots are also betting simultaneously. On a busy day you could have 10%+ bankroll at risk across bets that the individual Kelly fractions never intended to combine.
2. **Conflicting picks** — two bots recommend opposite sides of the same market. Placing both is paying the overround for zero expected value.
3. **Correlated picks** — two bots both fire on the same match (e.g. 1X2 home win + AH home -0.5). Stakes are not independent; treating them as independent inflates total exposure.

## The Principle

**Fewer bots with higher stakes beats more bots with diluted stakes**, once you know which bots have real edge. Adding a redundant or correlated bot doesn't increase long-run growth — it cannibalizes bankroll from the good bots. Academic work on Kelly portfolios finds 3-5 uncorrelated strategies captures most of the diversification benefit.

**Bot selection rule:** pick 2-3 bots with positive real ROI from the cohort report that bet on *different market types* (e.g. one 1X2 away-dog bot + one OU bot = low correlation). Don't combine two bots that mostly fire on the same markets.

---

## Algorithm (per betting window)

### Step 1 — Pool candidates
Collect all pending simulated_bets from selected bot IDs where match hasn't kicked off yet.

### Step 2 — Conflict resolution
For each (match_id, market, selection) group:
- If two bots recommend **opposite sides** of the same market: compute net Kelly fraction. If net ≤ 0, drop both. If net > 0, keep the net position under the winning bot's identity.
- If two bots recommend the **same side** of the same market: take the one with higher edge, drop the duplicate.

### Step 3 — Correlation discount
For each match_id that appears more than once after Step 2:
- Apply 0.5× multiplier to every bet on that match beyond the first (by descending edge order).
- This reflects that same-match bets are positively correlated — they tend to win and lose together.

### Step 4 — Simultaneous Kelly sizing
Use `scipy.optimize.minimize` to maximize E[log(wealth)] over the joint outcome space (2^N scenarios for N remaining bets). This is computationally trivial for N ≤ 15.

Reference implementation: `BettingIsCool/real_kelly-independent_concurrent_outcomes` (GitHub).

For each bet, inputs are:
- `p` — model's calibrated win probability
- `b` — net odds - 1 (decimal odds minus 1)
- The optimizer returns `f*` fractions for each bet jointly.

### Step 5 — Half-Kelly
Multiply all fractions by 0.5. Rationale: edge estimates from models are uncertain; overbetting Kelly is much more damaging than underbetting. Half-Kelly gives ~75% of full-Kelly growth with dramatically lower drawdown. Uhrín et al. 2021 (arxiv:2107.08827) validates fractional Kelly is best in practice for soccer.

### Step 6 — 20% bankroll cap
Sum all (fraction × bankroll) values. If total > 20% of bankroll, scale every bet down proportionally. This is a hard safety rail — prevents clustered picks on a busy match day from over-exposing.

### Step 7 — Round to practical stakes
Round each stake down to nearest €0.50. Drop any bet where rounded stake < €1 (Coolbet minimum).

---

## Implementation Plan

### Phase A — Bot selection (no code)
After generating the 200-bet cohort report (`dev/active/self-use-validation-results.md`):
- Rank bots by real ROI
- Pick top 2-3 with positive real ROI AND different primary market types
- Document chosen portfolio in this file

### Phase B — `scripts/meta_bot_picks.py`
New script (personal-use only, not wired into Railway):
- Accepts `--bots bot_id_1,bot_id_2,bot_id_3` flag (or reads from a config constant)
- Reads today's pending simulated_bets for those bots
- Runs Steps 1-7 above
- Outputs a table: match, market, selection, odds, model edge, meta-bot stake in €

This replaces running `daily_picks.py` manually. Morning ritual becomes: `python3 scripts/meta_bot_picks.py` → pick what to place at Coolbet.

### Phase C — `/admin/place` integration (optional, if the script proves useful)
- Add a "Meta-bot mode" toggle to the place page that filters to a preconfigured bot list and shows the portfolio-Kelly stakes instead of raw bot stakes
- Config stored as a superadmin-only setting (hardcoded list of bot IDs, not UI-configurable)

### Phase D — Stake scaling (post-pivot only)
After pivot decision, if ROI > 3%:
- Raise base stake from €1-3 to €5-15
- The same algorithm handles it — Kelly fractions scale with bankroll automatically

---

## Key References

| Resource | What it covers |
|----------|----------------|
| [BettingIsCool/real_kelly](https://github.com/BettingIsCool/real_kelly-independent_concurrent_outcomes-) | Python scipy implementation — use as base |
| [vegapit simultaneous Kelly](https://vegapit.com/article/numerically_solve_kelly_criterion_multiple_simultaneous_bets/) | Clear walkthrough + code |
| [Uhrín et al. 2021 — arxiv:2107.08827](https://arxiv.org/abs/2107.08827) | Validates adaptive fractional Kelly for soccer |
| [Thorp 2007 (PDF)](https://web.williams.edu/Mathematics/sjmiller/public_html/341/handouts/Thorpe_KellyCriterion2007.pdf) | Foundational Kelly in sports betting |
| [emiruz.com](https://emiruz.com/post/2025-01-05-sim-kelly/) | Clean worked example of simultaneous vs sequential fractions |

---

## What NOT to do

- Don't add more bots thinking diversification helps — beyond 3 uncorrelated bots the marginal gain is near zero and complexity increases.
- Don't run the full simultaneous Kelly on all 16 bots — most will get near-zero allocations and you're just adding noise.
- Don't skip the correlation discount step — two bots firing on the same match is the most common case and also the most dangerous for overbetting.
- Don't implement this before the 200-bet cohort report — you need real ROI data to know which bots deserve to be in the portfolio.
