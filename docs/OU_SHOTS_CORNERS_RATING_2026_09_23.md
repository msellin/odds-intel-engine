# Feeding the O/U model shots instead of goals — #077. Negative, and the control is the story

**2026-09-23 · 👥 PICKS · script `scripts/ou_shots_corners_rating.py` · row [[#077]]**

## The hypothesis

Wheatcroft (2020, *IJF* 36(3)), n = **68,672 bets**: GAP ratings fed
**shots + corners** returned **+535 units**; the SAME ratings fed **goals**
returned **−631 units**, Bonferroni-corrected p < 0.001 for every input except
goals. Past goals are a worse input for a goals model than past shots.

Our O/U model is fed goals. Every previous attempt varied the *model* over the
same input. This varied the *input* — the one axis the published evidence says
matters.

## Result

n = 2,811 out-of-sample fixtures (from the harness's 7,273, restricted to matches
where both teams carry ≥5 prior matches with shots and corners). Scored through
`residual_test_ou`'s own imported functions, so α is comparable to the earlier
zeros rather than merely similar.

| input | model AUC | model log-loss | residual AUC | α (de-vig) | α (Shin) | verdict |
|---|---|---|---|---|---|---|
| **shots + corners** | 0.5441 | 0.6841 | 0.3902 | **0.0000** | 0.0000 | FAIL |
| **goals — CONTROL** | **0.5499** | **0.6818** | **0.4008** | **0.0000** | 0.0350 | FAIL |
| *market (Pinnacle, de-vigged)* | *0.6184* | *0.6649* | — | — | — | — |

**α = 0. The fourth zero.**

## But the control beat the treatment, and that is the honest headline

Goals-fed is **better than shots+corners-fed on every metric** — AUC 0.5499 vs
0.5441, log-loss 0.6818 vs 0.6841, residual AUC 0.4008 vs 0.3902.

**Wheatcroft's specific claim did not replicate here.** Two readings, and this
experiment cannot separate them:

1. The finding does not transfer to our leagues and data, or
2. **this implementation extracts less from shots than from goals.**

(2) is a live possibility and must be stated. Wheatcroft's GAP is a rating system
purpose-built for match statistics, which predicts the statistic and then feeds
that prediction into a differently-shaped goals model. What is implemented here is
a generic opponent-adjusted multiplicative attack/defence rating with a Poisson
GLM mapping `log(λ) = b₀ + b₁·log(shots) + b₂·log(corners)`. It is a reasonable
construction, it is **not** his.

**What the control DOES establish** is that the result is not an artefact of a
broken pipeline: the identical machinery, on the identical population, with the
identical scoring, produces a working-if-weak model when fed goals. So the code
path is sound and the shots arm is a real measurement of *this* construction.

## Why O/U still closes on this

Not because shots+corners failed — that single arm is confounded with the
implementation. Because of what both arms show together:

* **The market is at AUC 0.6184 and nothing we build clears 0.55**, on either
  input, on a population where the market is if anything *sharper* than average
  (0.6184 here vs 0.6007 on the full 7,273 — so this is not a soft corner).
* **Residual AUC is 0.39–0.40 on both arms — far below 0.5.** Where our model
  disagrees with the market, the market is right substantially more often than
  chance. That is the same sign and a worse magnitude than the three earlier
  measurements.
* The optimiser, free to choose any weight in [0, 1], puts **nothing** on the
  model.

Four independent attempts, now across two different *inputs* as well as three
different model families, all return α = 0 with residual AUC below 0.5.

## One detail worth keeping — why the pass condition has two parts

The goals control's **Shin arm returned α = 0.0350**, which clears the α > 0.02
half of the bar. It still FAILED, because its blend was **worse** than the market
(−0.018%). A one-part rule reading "α > 0.02" would have called that a pass. Both
halves earn their place.

## Confounds and limits, stated rather than buried

* **Not a faithful GAP replication.** See above. A true replication is a separate
  piece of work and would be the only way to attribute the shots arm cleanly.
* **Training history is identical across arms** — `load_history` filters on stats
  presence regardless of `--input`, so both arms learn from the same ~50k matches.
  That confound is controlled.
* **The online update is not.** In the test window the goals arm updates on every
  match; the shots arm only where stats exist. A small asymmetry favouring goals.
* **Coverage is not random.** The usable set skews to MLS, English Championship
  and League One, Argentine Primera and Brazilian Série B. A result that holds
  there is not automatically a result everywhere.
* **n = 2,811**, below this repo's own n ≥ 334-per-cell CLV bar only in the sense
  that it is a single pooled cell; it is ample for an α this close to zero.

## What would change the answer

Only one thing, and it is not a model: **execution**. Wheatcroft's edge is
+0.8%/bet at **max odds across books** (overround 1.57%) and a substantial loss at
**average odds** (6.8%) — he states the strategy still identifies value at average
odds and simply cannot clear the vig. With two Estonian books we are on the losing
side of that split regardless of what any model returns.

---

## ⚠️ What this does NOT establish — scoping added 2026-09-23 after the owner asked

Owner: *"so you are saying there is no model on earth that can beat OU market?"*

**No. Nothing here supports that, and the published evidence points the other
way.** The claim this file makes is much narrower, and the difference is the most
important thing on this page.

### Two different questions that keep getting merged

| | question | how it is measured | our answer |
|---|---|---|---|
| **Forecast** | can a model out-predict the SHARP line? | α against de-vigged Pinnacle | **No — four zeros** |
| **Price** | can a model find books offering worse-than-consensus prices? | realised return at best available price | **Yes, measured, +2.2% on O/U** |

Everything in this document, and in the three measurements before it, answers the
**first** question only. α is fitted against **de-vigged Pinnacle** — one of the
sharpest references in the world. "We cannot out-forecast Pinnacle" is a real
finding and a narrow one.

### The one well-powered published O/U edge did NOT beat the forecast either

Wheatcroft (2020) **is** a model beating the O/U market — +0.8%/bet over 68,672
bets, Bonferroni-corrected. But read where the edge lives:

| price basis | mean overround | result |
|---|---|---|
| max across all bookmakers | **1.57%** | **+0.8%/bet, significant** |
| average across bookmakers | **6.8%** | **substantial loss** |

Same model, same bets, same period. He states the strategy still *identifies
value* at average odds and simply cannot clear the vig. **So the published edge is
dispersion, not forecasting.** He did not out-predict the consensus; he found
books priced away from it.

### Our own data says the same thing, from the other side

`docs/BOOK_SET_COUNTERFACTUAL_2026_09_22.md`: opening the accessible book set is
worth **+2.2% mean price uplift on O/U** and **+52% to +76% more O/U picks at an
identical mean price** — both marked robust. That is the same mechanism
Wheatcroft's profit came from, measured on our own fixtures.

So the coherent picture is not "O/U is unbeatable". It is: **the forecasting edge
is absent for us, and the profitable mechanism is execution — which is the thing
two Estonian books structurally cannot give us.**

### The honest limits of even the narrow claim

* **xG coverage is 4%.** The standard modern input is effectively absent. A model
  with full xG, full lineups and referee data has not been tried here, and this
  file is no evidence about it.
* **Coverage is not random** — MLS, English Championship and League One, Argentine
  Primera, Brazilian Série B.
* **n = 2,811**, one pooled cell.
* **Nobody has published an O/U test against CLOSING lines at all**, so the
  hardest version of the forecast question is untested in the literature as well
  as here.

**What is fair to say:** *we* cannot out-forecast Pinnacle on O/U, on our data,
with four attempts across three model families and two inputs. **What is not fair
to say:** that nobody can.
