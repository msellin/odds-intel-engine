# Dixon-Coles — context

## State: phase 1 DONE (2026-09-16). Next: phase 2, walk-forward driver.

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
