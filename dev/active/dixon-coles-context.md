# Dixon-Coles — context

## State: phase 2 DONE (2026-09-16). Next: phase 3, select xi on a validation window.

## PHASE 2 RESULTS — walk-forward runs clean, but the model is 0.097 goals low

1,120 league fits, 8,505 matches scored, 310 leagues. Strictly out-of-sample.

    predicted mean total goals  2.9630
    ACTUAL   mean total goals   3.0602      gap -0.0972
    P(over 2.5) predicted 0.5300 vs actual 0.5819   gap -0.0519

DIAGNOSED, and it is NOT a fitter bug:

    fit window (prior 365d)  mean total goals  2.8601   n=50,180
    scored window (>=08-20)  mean total goals  3.0887   n=9,684
    regime shift                              +0.2286 goals per match

The model predicts 2.9630 — BETWEEN the two — because xi=0.0018 already pulls
toward recent matches but not far enough. Early-season European football scores
more than the annual average, and a history-fitted model cannot know that in
advance. The market can and does, which is a concrete illustration of what we
are up against.

=> PHASE 3 IS NOW THE INTERESTING ONE: a faster decay should track the regime.
   xi MUST be selected on a validation window ending before 2026-08-20.

## TWO BUGS FOUND AND FIXED IN PHASE 2 (both silent)

1. NO INTERCEPT + RIDGE = the league's goal LEVEL was being shrunk toward an
   arbitrary constant. With lambda = exp(atk - dfn + gamma), the level lives in
   mean(dfn), so penalising dfn^2 drags every league toward exp(gamma) home and
   1.0 away. Measured: predicted mean total fell to 2.952 vs actual 3.060.
   Fixed with an explicit intercept EXCLUDED from the penalty, and both atk and
   dfn now mean-zero constrained.
   *** The self-check was passing throughout: it generated data at an implicit
   intercept of 0, so it recovered the SPREAD and was structurally blind to the
   LEVEL. Section 3b now generates at three base rates and asserts both the mean
   total goals and the realised P(over 2.5). ***

2. UNREGULARISED FIT = thinly-observed teams ran to the parameter bounds;
   lambda ranged 0.003 to 227.4 goals. The MEAN was right the whole time, so
   only the distribution gave it away. Fixed with a ridge toward league average
   (RIDGE=0.02) and PARAM_BOUND tightened 3.0 -> 1.5. Range is now 0.064-19.3.

## A THIRD, CAUGHT BY MUTATION TESTING
The smoke assertion `"m[5] < g" in drv` matched the VERIFY block rather than the
training filter, so it passed on a driver whose training filter had been mutated
to `<=` — which would have let same-day fixtures into their own fit. The
assertion now targets the `train = [...]` line specifically. A failed
`git checkout` (the file was untracked) had left that mutation live in the file.

## ⚠️ FINDING FROM PHASE 1 THAT CHANGES EXPECTATIONS

**The Dixon-Coles tau correction has ZERO effect on O/U 2.5 — provably, for all
lambda/mu/rho.** The four corrected cells (0-0, 0-1, 1-0, 1-1) are all UNDER 2.5
goals, and their mass changes sum to exactly zero:

    d = rho * e^-lam * e^-mu * lam*mu * (-1 + 1 + 1 - 1) = 0

Verified algebraically and over a 48-point grid in the self-check. So at the 2.5
line this model IS independent Poisson with attack/defence strengths; tau only
moves O/U 0.5 / 1.5 and the DRAW probability (0-0 and 1-1 are draws, 0-1 and 1-0
are wins).

Consequences for this project:
- The value proposition at O/U 2.5 is entirely the ATTACK/DEFENCE STRUCTURE
  (opponent-adjusted, time-decayed, 100% coverage), not the famous correction.
- tau IS potentially valuable for 1x2, where it moves the draw — the outcome our
  current head is worst at.
- Do not claim "Dixon-Coles improves O/U 2.5 because of the low-score
  correction". It cannot. MODEL_WHITEPAPER §5.1 already over-claimed this model
  once (banner-corrected 2026-09-16); do not repeat it in the other direction.

## Key facts established before starting
- Corpus: 171,509 finished matches with scores, 880 leagues, 11,345 teams, 2017-03-22 → 2026-09-16.
- OOS window (>= 2026-08-20): 14,550 matches; ~7,273 have a Pinnacle O/U 2.5 pair.
- The bar to beat: market AUC 0.6011 (O/U 2.5), 0.6997 (1x2). Shipped heads: 0.5796 / 0.6632.
- scipy 1.17.1, numpy 2.4.4 available.

## Decisions
- Fit PER LEAGUE. Global fit would tie unrelated strength scales.
- ξ, ρ selected on a validation window ending BEFORE the OOS cutoff, then frozen.
- Scored through residual_test_ou.py's method so α is comparable to the shipped 0.0000.

## Next steps
1. Build the pure fitter + self-check.
2. Walk-forward driver.
3. Hyper-parameter selection (validation only).
4. Score.
