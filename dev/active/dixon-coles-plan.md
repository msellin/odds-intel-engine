# Dixon-Coles baseline for goals — plan
**Started 2026-09-16 · 🤖 OWN + 👥 PICKS · queue row `DIXON-COLES-OU-BASELINE`**

## Why this, and why first

Both shipped heads measured residual **α = 0.0000** against Pinnacle
(`residual_test.py`, `residual_test_ou.py`). They share ONE 52-feature set
engineered for match outcome, of which only nine clear 88% population
(`docs/MODELLING_DATA_AUDIT_2026_09_16.md`). Two zeros from one feature set is
one result about the feature set, not two about model families.

Dixon-Coles is the control that settles it, and it is first for one reason:
**it needs only `(home, away, score, date)` — 171,509 matches at 100%
coverage.** Every other candidate (xG, lineups, referee) is capped at 4–15% by
data we do not have. If a goals model built on complete data also returns α = 0,
O/U is closed on evidence. If it returns α > 0, we know the feature set was the
problem and the rest of the audit is worth funding.

It is also the model `MODEL_WHITEPAPER` §5.1 has claimed we use since May, to
justify weighting goal-line markets HIGHER than 1x2. We have never had one.
(Banner-corrected 2026-09-16.)

## The model

Bivariate-Poisson-with-correction, per Dixon & Coles (1997):

    λ = exp(atk_home − def_away + γ)      home expected goals
    μ = exp(atk_away − def_home)          away expected goals

    P(x, y) = τ(x, y, λ, μ, ρ) · Poisson(x; λ) · Poisson(y; μ)

    τ = 1 − λμρ  (0,0) │ 1 + λρ  (0,1) │ 1 + μρ  (1,0) │ 1 − ρ  (1,1) │ else 1

`τ` is the whole point: independent Poissons misprice 0-0, 1-0, 0-1 and 1-1,
which is precisely where O/U 1.5 and 2.5 are decided.

Fitted by maximum likelihood with exponential time decay
`w(t) = exp(−ξ·Δdays)`, and `mean(atk) = 0` for identifiability.

## Discipline (non-negotiable, this is why the last two tests were re-run)

1. **Strictly out-of-sample.** For every evaluated match, parameters come from
   matches with `date < that match's date`. Refit weekly; never fit on a match
   then score it.
2. **ξ and ρ are chosen on a VALIDATION window that ends before the OOS window
   starts.** Tuning decay on the test set would manufacture the result. The
   selected values get written into the script and not touched again.
3. **Per league.** Teams rarely cross leagues; one global fit would tie
   unrelated strength scales together. Leagues under a minimum match count are
   skipped rather than fitted on noise.
4. **Scored through the SAME harness as the shipped heads** —
   `residual_test_ou.py`'s method: Platt for level only, REALISTIC arm decides,
   Shin robustness arm, α fitted on the first half and evaluated on the second.
   A different harness would make the numbers incomparable, which is the only
   thing that makes this worth doing.

## Phases

| # | phase | output |
|---|---|---|
| 1 | `workers/model/dixon_coles.py` — fitter + score matrix, pure, no DB | unit-checkable in isolation |
| 2 | `scripts/dixon_coles_fit.py` — walk-forward fit over the corpus | per-match λ, μ out-of-sample |
| 3 | hyper-parameter selection on the validation window | ξ, ρ locked before any OOS scoring |
| 4 | score through the residual harness | **α, directly comparable to 0.0000** |
| 5 | smoke tests + docs + queue | |

## What would make this a PASS

The pre-registered bar, identical to the shipped heads: blend log-loss < market
log-loss **AND** α > 0.02, on the REALISTIC arm. ROI is not admissible and is
not computed here.

## What this is NOT

Not a betting strategy, and not a reason to reopen automated placement — that is
closed on market access (5.66% overround vs a 2% threshold), which no model
quality changes. A positive α here would mean the model can *predict*; it would
still have to clear the vig to be worth staking.
