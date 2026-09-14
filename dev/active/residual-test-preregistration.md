# RESIDUAL-TEST — pre-registration (locked 2026-09-14, before any run)

Master task #4. The only question left on the model track: **does the model
predict anything the market price does not already contain?**

Written before the data is touched, and specifically because I got task #3 wrong
in a way that a pre-registration would have prevented. There I scored the RAW
XGBoost output against the base rate and reported a failure; production never
serves raw output, so I had tested an object that does not exist in the pipeline.
The lesson is not "be careful" — it is **name the object and the metric in
advance**.

---

## The object under test

`p_model` = the clean bundle `v20260914_clean_cut0820`, **calibrated**, because
that is what the serving path produces. Raw output is not a candidate.

`p_mkt` = **de-vigged Pinnacle** P(home). Pinnacle, not best-of-books: taking the
max across books re-creates the line-shop selection artefact (`2505c25`), which
would manufacture an apparent edge out of stale prices. De-vig is **proportional
across the 3-way**, and the result is reported alongside Shin as a robustness
check — proportional overstates longshots, which is exactly where a spurious
edge would appear.

## Universe

Matches with a settled 1X2 outcome and a pre-kickoff Pinnacle triple, **strictly
after the model's 2026-08-20 training cutoff**. Nothing before it is admissible:
the model saw it.

Split **time-ordered** in half. Everything fitted — the calibrator AND the blend
weight — is fitted on the FIRST half only and evaluated once on the second.

## PRIMARY test, and the one that decides

> **H: a blend of the model and the market beats the market alone,
> out-of-sample.**

Fit `alpha` minimising log-loss of `alpha·p_model + (1−alpha)·p_mkt` on the train
half. Evaluate on the test half:

| arm | meaning |
|---|---|
| market alone (`alpha=0`) | the benchmark |
| fitted blend | the model's claim |
| model alone (`alpha=1`) | context, not a criterion |

**Success requires BOTH:**
1. fitted blend log-loss **<** market-alone log-loss on the TEST half; and
2. fitted `alpha` **> 0.02** — a weight below that is indistinguishable from "use
   the market", and this project has watched that exact number sit at 0.0085 for
   four months.

This is deliberately the same instrument as `shrinkage_alpha`, which has been
answering this question since May on an inverted, leak-trained model. Running it
on a clean one is the point.

## SECONDARY — direction, not just fit

Residual AUC: does `p_model − p_mkt` predict the outcome? Previously **0.344**
(anti-predictive) on the broken model. Above 0.5 means our disagreement with the
market carries information. Reported regardless of the primary.

## ECONOMIC gate — statistical is not the same as bettable

Even a real edge is worthless below the vig. Report the measured Pinnacle
overround on the universe, and the ROI of backing every pick where the blend
beats the market price by ≥1%, ≥3%, ≥5%, **at Pinnacle's own price**. Betting at
best-of-books here would re-import the artefact the universe was chosen to avoid.

## Stopping rule

- One evaluation on the test half. No re-tuning after seeing it; a second look
  invalidates the first.
- If the primary fails, the answer is **the model adds nothing to the market**,
  reported as such. That is a real result and it retires the model-anchored
  track — it does not become a search for a subgroup where it works.
- Nothing here authorises deployment or staking.

## What would make me distrust a positive

- `alpha` large but log-loss improvement trivial (< 0.5%) → fitting noise.
- Effect present on proportional de-vig and absent on Shin → a longshot artefact.
- Effect concentrated in one league, one week, or the lowest-liquidity tier.
- Improvement that disappears when the calibrator is fitted on the train half
  rather than the whole period — that would mean I leaked calibration.
