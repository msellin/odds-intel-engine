# Task 2 — Can we work toward those edges? An honest audit

**Date:** 2026-08-28 · **Input:** Task 1 findings + measurements against our own data.
**Task 3 (other sports) deliberately not started.**

---

## The good news first: we are already running the right strategy

Task 1's clearest finding was that the best-evidenced edge in soccer is **not**
forecasting — it is relative price discovery against a sharp benchmark, worth ~3–5%,
and it needs no model.

`bot_coolbet_value_v1` is exactly that strategy. It prices at Coolbet, values against
de-vigged Pinnacle, and fires at ≥3% true edge. We arrived at the published
professional approach independently. Nothing in Task 1 suggests we should pivot the
*concept*.

Everything below is about whether our **execution** of it can actually work.

---

## Finding 1 — Our fair-value anchor is not sharp. This is the big one.

The entire strategy rests on Pinnacle being a materially better price than the book we
bet at. Measured against our own data, that premise is weak.

**Pinnacle's margin in our feed is ~8%, not the ~2–3% real Pinnacle runs on 1X2:**

| book | 1X2 overround (same-timestamp rows) | n |
|---|---|---|
| Coolbet | 1.0766 | 103 |
| **Pinnacle** | **1.0821** | **15,129** |
| Bet365 | 1.1014 | 17,580 |
| Unibet | 1.1050 | 14,428 |

I checked this three ways, because today has produced three artifacts already:

* **Not a query artifact** — same-timestamp rows only, n=15,129.
* **Not a lower-league effect** — Pinnacle is wider than Coolbet at *every* tier,
  including tier 1 (1.0758 vs 1.0747).
* **Not compensated by better forecasting** — Brier score on **2,327 shared settled
  fixtures**: Bet365 **0.57745**, Pinnacle **0.57803**, Coolbet **0.58823**.

**Read carefully, because it cuts both ways.** Pinnacle *is* a better forecaster than
Coolbet (0.578 vs 0.588), so the anchor is not worthless and the edge is pointed in the
right direction. But it is **not the sharp benchmark the strategy assumes** — Bet365
matches it, and its margin is triple what real Pinnacle charges. De-vigging an 8%
margin as though it were genuine removes the wrong amount, distorting every fair
probability we compute.

**This plausibly explains a lot**: why AH and DC backtested negative, why our 3% "true
edge" threshold produces picks that lose, and why claimed edge (+7.4% on the AH
backtest) diverged so far from realised (−15.3%).

**Highest-value action available to us.** Either source genuine Pinnacle pricing, or
stop calling this a sharp anchor and build the fair value from a *consensus* of many
books instead — we already collect 15.

## Finding 2 — Single-book access costs us ~80% of the opportunity

Measured over 2 days, on match-markets where we hold a full Pinnacle line:

* **308** cases where *some* book beat de-vigged fair by ≥3%
* **60** where **Coolbet** did → **Coolbet captures 19%**

Which book actually offers the best qualifying price:

| book | share | Estonian-licensed? |
|---|---|---|
| Unibet | 21.8% | **yes** (EMTA licensed) |
| Epicbet | 14.0% | **yes** (already ingested) |
| Bet365 | 12.7% | **yes** (Licence HKL000036) |
| 10Bet | 12.7% | unclear |
| Coolbet | 12.0% | yes — our only placement venue |

**Unibet and Bet365 are both legally available in Estonia and we already collect their
prices.** Adding either as a placement venue is a bigger win than any modelling change:
Unibet alone offers the best price nearly twice as often as Coolbet.

Caveat from Task 1: more books means more accounts to get limited, and soft books limit
on CLV rather than results. That is a real cost, but it is the cost professionals
accept — and it argues for using soft books *while they last* rather than not at all.

## Finding 3 — Betfair Exchange is legal in Estonia and we are not using it

Estonia is one of the few EU countries where the Exchange is available (Germany,
France, Netherlands, Sweden, Denmark, Poland, Austria and others are excluded).

From Task 1, exchanges solve the structural problem bookmakers create:

* commission on **net winnings** (~2%) instead of margin on every price
* **winners are not limited** — a winner generates commission, so they are a good
  customer
* stake bounded by liquidity, not by a risk desk; positions can be traded out

Note our existing "Betfair" data is the **Sportsbook, not the Exchange** — its overround
is **1.0958**, wider than our Pinnacle. So we have no exchange prices today, and
exchange prices would also be a far better fair-value anchor than what we currently use
(Finding 1).

## Finding 4 — We are not close to knowing whether any of this works

Task 1's benchmark: **500–1,000+ bets** before an ROI claim means anything; under 100 is
"nearly meaningless".

We have **24 real bets** (17 on 27 Aug, 7 on 28 Aug). At ~7/day we reach 500 in about
ten weeks. The CLV gate is far more efficient (n≈78) — **but Finding 1 undermines it**,
because our CLV is measured against the same non-sharp Pinnacle. Fixing the anchor is a
prerequisite for trusting either metric.

## Finding 5 — Markets Task 1 says are softer, which we do not touch

Research points at corners, cards and player props as the least efficiently priced
soccer surfaces, and lower divisions as under-attended. We currently bet 1X2 and OU
only.

Tempering that: we *tested* the two adjacent markets this week and both failed — DC is
dead (margin wider than the edge) and AH backtested at −15.3%. Given Finding 1, those
results are partly confounded by the anchor. **They deserve a re-test after the anchor
is fixed, not before.**

---

## What I would actually do, in order

1. **Fix the fair-value anchor.** Highest value by a distance. Two options:
   (a) source genuine Pinnacle pricing (their API, or a feed that carries real margins),
   (b) build fair value from a de-vigged multi-book consensus using the 15 books we
   already have, which is standard practice and needs no new data. Until this is done,
   every edge number we compute is suspect — including the ones that made this week's
   bots look good or bad.
2. **Add a second placement venue.** Unibet or Bet365, both Estonian-licensed and
   already in our data. Roughly doubles reachable opportunities. The UI placer already
   proves we can automate a book we have no API for.
3. **Investigate Betfair Exchange access.** Legal here, no limiting, lower cost, and
   it doubles as a superior anchor. Biggest structural upgrade available, and the only
   one that survives being a consistent winner.
4. **Keep accumulating bets on the current bot** — but treat its numbers as provisional
   until (1) lands. Do not scale stakes on 24 bets against a compromised anchor.
5. **Re-test AH after (1).** Not DC — that one is dead on its own merits
   (ANALYSIS_GOTCHAS #15).

## What I would not do

* Chase a better forecasting model. Task 1 is unambiguous that bookmaker odds are
  better calibrated than published models, and our own bot does not use our model at
  all — it is pure line-shopping. Model work is the least promising direction available.
* Scale stakes on the current record. 24 bets is noise, and the anchor is suspect.
* Build the DC or AH bots yet.

---

*Task 3 (other sports and betting options) not started — awaiting sign-off on this one.*
