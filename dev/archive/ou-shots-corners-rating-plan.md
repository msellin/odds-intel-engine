# #077 — O/U shots-and-corners rating: plan

Parent row: **[[#077]]** in `PRIORITY_QUEUE.md`.

## The hypothesis, and whose it is

Wheatcroft (2020, *IJF* 36(3)), n = **68,672 bets**: GAP ratings fed
**shots + corners** returned **+535 units**; the SAME ratings fed **goals**
returned **−631 units**, Bonferroni-corrected p < 0.001 for every input except
goals. **Past goals are a worse input for a goals model than past shots.**

Our O/U model is fed goals. This tests whether feeding it shots and corners
instead moves α off zero.

It is the only O/U input with a well-powered external result behind it, and we
already hold the data — so it is the cheapest remaining shot before O/U closes.

## Why this is the FOURTH attempt and what makes it different

| attempt | what it was | α |
|---|---|---|
| XGBoost O/U head | 52 shared features, 9 of them real | 0.0000 |
| "Dixon-Coles" | **actually double-Poisson** — the τ correction is a no-op for O/U 2.5 ([[#014]], and Petretta et al. publish the same) | 0.0000 |
| `residual_test_ou` re-run, both de-vig arms, 3 lines | same feature set | 0.0000 |
| **this** | **a different INPUT VARIABLE**, not a different model family | ? |

Every prior attempt varied the *model* over the same input: goals and Elo. This
varies the *input*. That is the one axis the published evidence says matters,
and the one we have never moved.

## Method

1. **Opponent-adjusted attack/defence rates** per team for each statistic
   (shots for/against, corners for/against), with exponential time decay and
   separate home/away terms. Built only from matches STRICTLY BEFORE the fixture
   being predicted — no leakage.
2. **Map predicted statistics → goal rates.** Fit λ_home, λ_away from predicted
   shots/corners on a training window, then P(total > 2.5) from independent
   Poisson. Independence is safe here specifically: the DC correlation term
   provably cannot move O/U 2.5 ([[#014]]).
3. **Score through the EXISTING harness**, reusing `residual_test_ou`'s own
   functions (`ll`, `auc`, `fit_platt`, `fit_alpha`, `devig_two_way`, `shin2`) by
   import, so α is comparable to the three zeros above rather than merely similar.
   Platt for LEVEL on the first half, α fitted on the first half, evaluated on the
   second, de-vigged Pinnacle as the market.
4. **Pre-registered pass condition, identical to the others:** blend log-loss <
   market log-loss **AND α > 0.02**, on the realistic arm.

## Stopping rule — pre-committed, before any result is seen

* **α > 0.02 and blend beats market** → the input matters. Then, and only then,
  #025 (calibrator refit) and a re-derived edge floor become worth doing, against
  THIS model rather than the old one.
* **α = 0** → the fourth zero, on the one axis the literature says should work.
  **O/U modelling closes on evidence.** The row says so, `docs/SYSTEM_MAP.md`
  says so, and `bot_v10_ou` is retired rather than left dormant.

Never promote on ROI (§8).

## The caveat that survives whatever this returns

Wheatcroft's edge is **+0.8%/bet at MAX odds across books (overround 1.57%)** and
a **substantial loss at AVERAGE odds (6.8%)**. He states the strategy still
identifies value at average odds and simply cannot clear the vig. With two
Estonian books we are on the losing side of that split.

So even a PASS here is a 👥 PICKS result (an honest published probability), not a
🤖 OWN one (money staked). Nothing in this task reopens automated betting — that
is closed on market access, not on model quality.

## Risks

| risk | guard |
|---|---|
| **Coverage** — shots/corners on 31% of finished matches | measure the usable n BEFORE building; if the harness population collapses below ~1,500 the test is underpowered and the honest output is "cannot be answered on our data", not a weak α |
| **Leakage** — using a match's own stats to predict it | ratings built strictly from matches before the fixture date; assert it |
| **Coverage is not random** — stats exist for bigger leagues | report the league mix of the test set against the full population; a result that only holds where coverage is good is a narrower claim |
| Comparing α across a different population than the prior zeros | re-run the SHIPPED model on the identical restricted population as a control, so the comparison is like-for-like |
