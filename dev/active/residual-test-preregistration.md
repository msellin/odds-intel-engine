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

---

# AMENDMENT 1 (2026-09-14, still before any run) — two defects found by auditing the design

Both were found by checking the code rather than by looking at results. Recorded
here so the amendment is visibly pre-result.

## A. The object I originally named would have made the test CIRCULAR

The locked version said *"p_model is the clean bundle CALIBRATED, because that is
what the serving path emits"*. That is wrong **for this question**, and the reason
is visible in `improvements.calibrate_prob`:

```
shrunk = alpha * model_prob + (1 - alpha) * effective_anchor   # anchor = de-vigged Pinnacle
return _apply_stage2(shrunk, ...)
```

Production's `cal_prob` **already contains the market** — at the live
`shrinkage_alpha_t1_1x2 = 0.0085` it is ~99% Pinnacle. Blending that against
Pinnacle and asking whether it beats Pinnacle is testing Pinnacle against itself.
It would almost certainly have produced a small "improvement" that was pure
tautology.

**Corrected object:** `p_model` = raw bundle output, Platt-calibrated for LEVEL on
the train half, **with no shrinkage toward the market**. The model's independent
opinion.

⚠️ This is *not* the task-#3 mistake repeated, and the distinction is the whole
point. In #3 the question was "is the SERVED model better than a constant", so
the object had to be what is served. Here the question is "does the model add
anything TO the market", so the object must exclude the market. **The object
follows the question, not habit.**

## B. Eight features are available in training rows but NOT at serve time

Scoring on stored `match_feature_vectors` hands the model information it would
never have live:

| feature | NULL in training rows | NULL live |
|---|---|---|
| `season_progress` | 1.4% | **93.9%** |
| `league_draw_rate_ytd` | 29.7% | **95.9%** |
| `league_clv_efficiency` | 57.8% | **100%** |
| `line_velocity` | 67.3% | **100%** |
| `goals_for_avg_home` | 25.0% | 60.5% |
| `goals_against_avg_home` | 25.0% | 60.5% |
| `goals_for_avg_away` | 24.5% | 59.9% |
| `goals_against_avg_away` | 24.5% | 59.9% |

These are written by 23:05–23:45 UTC crons — i.e. after the match (master list
#7). A result that depends on them is not reproducible in production.

**Corrected design — the primary is now run TWICE, and both are reported:**

* **OPTIMISTIC** — stored MFV as-is. Upper bound.
* **REALISTIC** — the eight columns forced to NULL (with their `_missing`
  indicators set), simulating serve-time availability. **This is the arm that
  decides.**

A positive present only in the OPTIMISTIC arm is a finding *about task #7*, not
about the model, and must be reported that way.

---

# RESULTS (run 2026-09-14, design unchanged since the amendment)

Universe: 7,662 matches on/after 2026-08-20 with a pre-kickoff Pinnacle triple.
Home-win base rate 0.4376. Pinnacle overround **1.0849** (vig ≈ 8.49% — far above
the 2–3% Pinnacle shows on majors, because this universe is mostly low-tier
leagues; it is why the Shin robustness arm was pre-registered). Time-ordered
split, everything fitted on the first half, evaluated once on the second
(n=3,831).

| arm | fitted α | market LL | model LL | blend LL | blend vs market | residual AUC |
|---|---|---|---|---|---|---|
| OPTIMISTIC (stored features) | **0.0000** | 0.6221 | 0.6465 | 0.6221 | +0.000% | **0.4094** |
| **REALISTIC (post-hoc NULLed) — decides** | **0.0000** | 0.6221 | 0.6561 | 0.6221 | +0.000% | **0.3775** |
| REALISTIC + Shin de-vig (robustness) | **0.0000** | 0.6211 | 0.6561 | 0.6211 | +0.000% | **0.3676** |

Market AUC 0.6998–0.7000. Model AUC 0.6409–0.6634.

## VERDICT: PRIMARY FAILS in every arm. The model adds nothing to the market.

The optimiser was free to pick any weight in [0, 1] and chose **exactly zero** —
*use the market, ignore the model* — on 3,831 held-out matches, under both de-vig
methods, with and without the post-hoc features.

**Residual AUC is 0.368–0.409, i.e. BELOW 0.5.** Where the model disagrees with
the market, it is not merely uninformative — it is *wrong*. When it says "more
likely than the price implies", the outcome is *less* likely.

Three things this is **not**, each ruled out by construction rather than argued:

* **Not the leak.** The corpus was rebuilt strictly pre-match; `elo_diff` AUC
  0.7536 → 0.6134.
* **Not the inversion.** Class probabilities are read by label; this model scores
  AUC 0.64–0.66 standalone, not 0.42.
* **Not miscalibration.** Platt is fitted on the train half; the model alone
  beats a constant.

The model is, for the first time, genuinely fixed — and it still contributes
nothing against a sharp price. It ranks real (AUC 0.64 vs a 0.50 coin flip) and
the market simply ranks better (0.70) and already contains everything the model
knows.

**The economic gate was not reached.** With α = 0 there is no blend to price, so
"does the edge exceed the vig" is moot: there is no edge to compare. Recorded
rather than skipped.

## Consequence, per the locked stopping rule

> *"If the primary fails, the answer is the model adds nothing to the market,
> reported as such. That is a real result and it retires the model-anchored
> track — it does not become a search for a subgroup where it works."*

**The model-anchored track is retired.** Not paused pending a better model: the
instrument that would detect a better model — the fitted α — now has a clean,
correctly-oriented, properly-calibrated input and still reads zero.

This also explains, retrospectively and without needing any new theory, why
`shrinkage_alpha_t1_1x2` sat at 0.0085 from May and why two tiers read exactly
0.0000. That was never a symptom of the bugs. It was the correct answer all
along, arrived at by a fitter nobody was reading.

## What this does NOT retire

O/U 2.5 and BTTS are not tested here; this is the 1X2 head. The sharp/de-vig
anchor is untouched and remains the only thing in the system with a positive
non-circular CLV measurement (+9.21%, t=+5.1, n=89 — small, five days, and
unconfirmed, per `sharp-anchor-sweep-preregistration.md`).
