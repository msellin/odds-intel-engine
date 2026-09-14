# 🤖 OWN — adversarial verification of the "no sharp configuration works" result

**Written 2026-09-14. Brief: try to BREAK `docs/OWN_SHARP_CONFIG_SWEEP_2026_09_14.md`,
not to confirm it.** A negative result of that size stops a product line, so it
gets attacked before it is acted on.

**Script:** `scripts/own_sweep_verification.py` — an **independent
re-implementation**. It deliberately does not import `own_sharp_config_sweep`,
so a bug there cannot propagate into its own verification.
**Smoke test:** `OWN-SWEEP-VERIFICATION`.

```bash
python3 scripts/own_sweep_verification.py --days 150 --tests 1,2,3,4,5   # replication + the two missing gates
python3 scripts/own_sweep_verification.py --days 150 --tests 7,8,9       # stress test, book attribution, sign-aware null
python3 scripts/own_sweep_verification.py --days 150 --tests 10          # era vs alignment
python3 scripts/own_sweep_verification.py --days 150 --lead 60 --tests 7 # the CLV test that decides it
python3 scripts/own_sweep_verification.py --tests 6                      # live bots (DB only, fast)
```

---

## Verdict in one paragraph

**The sweep's harness is sound and replicates to the digit. Two of its four
supporting claims are wrong, and its headline — "none of 70,200 configurations
clears the bar" — is wrong in a specific, checkable way: the grid never
contained the gate the live bots use.** The sweep swept an *expected-ROI* floor
(`P×odds − 1`); `pick_triggers` gates on a *probability-difference* floor
(`P − 1/odds`), and since `roi_edge = prob_edge × odds` the second is a **curve
in odds**, which no cell of a constant-floor grid can express. Its `ODDS_BANDS`
also has no band ending at 2.00 — the band the live bots' record favours. Swept
properly, a coherent family of short-priced cells appears with clustered CIs
excluding zero, same sign at all three books and both traded markets, against a
**matched junk-anchor control that reads ≈0 on the identical band**.

Claim 2 ("junk beats real") is refuted outright: it compared a 550-leg tail
selection against a 5,881-leg near-flat-back, and at a matched gate the real
anchor beats junk by 18–33 pp everywhere. Claim 4's null is refuted too: counted
**by sign**, the junk arm's false-**positive** rate is **0.0%**, not 23.1%.

**And then the candidate I found fails on the sweep's own strongest test.** Two
things kill it and I could not save either. **(i) It is a 12-day effect, not a
37-day one**: +1.22% (n=81) over 2026-08-07..09-01 against +24.68% (n=107) over
09-09..09-13, and it is *not* an alignment artefact (tight-gap legs in the old
era return +1.04%; loose-gap legs in the new era return +32.98% — the date does
the work, not the gap). **(ii) Its own closing line contradicts it.** At
`lead 60`, where own-book CLV is computable at all, **every one of these cells
is negative on margin-corrected own-book CLV: −5.36%, −5.65%, −7.05%, −7.56%**
— beside ROIs of +19% to +42%. CLV converges ~200× faster than ROI (§8), and
that pairing is the sweep's own description of "a small sample landing well".

**So the sweep's CONCLUSION survives, on better evidence than the sweep gave for
it — while three of the arguments it used to reach it do not.** There is no OWN
sharp configuration worth a euro today.

---

## Claim-by-claim

| # | Claim under test | Verdict |
|---|---|---|
| 1 | Pooled sharp rule at the three books: n=695, ROI −9.55%, CI [−18.8, −0.3] | **CONFIRMED as arithmetic, REFUTED as a conclusion** — replicates, but its significance depends on markets we do not trade, and the rule tested is not the rule we run |
| 2 | The junk anchor does BETTER than the real one (−5.80% vs −9.55%) | **REFUTED** |
| 3 | The vig dipstick validates the harness | **CONFIRMED, but it validates less than claimed** |
| 4 | cells excluding zero are no rarer than under a junk anchor (real 9.6% vs junk 23.1%), so any survivor is noise | **REFUTED** — the junk arm's false-**POSITIVE** rate is **0.0%** |
| — | "None of 70,200 configurations clears the bar" | **REFUTED** — the live gate was not among them, at any of the 70,200 |
| — | **The overall recommendation: do not build, promote or stake on this rule** | **CONFIRMED — on different evidence.** The configuration the grid missed is ROI-positive and junk-separated, and its own-book CLV is −5.4% to −7.6%. |

---

## Claim 1 — the pooled −9.55%: **CONFIRMED as arithmetic, REFUTED as a conclusion**

**Test.** Rebuilt the leg set from scratch (±2 min per-book assembly, nearest-in-time
Pinnacle anchor, ≤60 min alignment, production outlier guard, cluster-robust SEs).

**The harness replicates to the digit.** The vig dipstick is identical on every
book and market:

| book | market | my n | my ROI | sweep's n | sweep's ROI |
|---|---|---|---|---|---|
| Coolbet | 1x2 | 10,538 | −9.73% | 10,538 | −9.73% |
| Coolbet | O/U 2.5 | 6,411 | −7.23% | 6,411 | −7.23% |
| Epicbet | 1x2 | 5,465 | −10.61% | 5,465 | −10.61% |
| Epicbet | O/U 2.5 | 3,400 | −6.10% | 3,400 | −6.10% |
| Unibet-Site | 1x2 | 3,301 | −8.95% | 3,301 | −8.95% |
| Unibet-Site | O/U 2.5 | 1,566 | −5.70% | 1,566 | −5.70% |

Two independently written harnesses landing on six identical figures settles
that the leg construction and settlement are right. **The sweep's mechanics are
not the problem.**

**But the headline row changes when the market set is restricted to what we
actually trade.** `pick_triggers` emits **1x2 and O/U 2.5 only**. On those two
markets:

| construction | n | ROI | 95% CI (clustered) | date span |
|---|---|---|---|---|
| sweep, 4 markets (1x2 + O/U 1.5/2.5/3.5) | 695 | −9.55% | **[−18.8, −0.3]** | — |
| this run, the 2 markets we trade | 550 | −7.64% | **[−17.85, +2.58]** | 2026-08-07..09-14 (37d) |

**The only reason the pooled figure excludes zero is the inclusion of O/U 1.5
and O/U 3.5 — markets no bot trades and no placer prices.** On the traded
markets the same rule is indistinguishable from zero. The report's own
confidence note ("the pooled −9.55% has a CI that barely excludes zero") is
therefore an overstatement: at the traded market set it does not exclude zero
at all.

Per book, publish rule (ROI-edge ≥3%, odds ≤4.0), traded markets:

| book | n | ROI | 95% CI | date span |
|---|---|---|---|---|
| Coolbet | 350 | −9.21% | [−22.40, +3.98] | 2026-08-07..09-14 (37d) |
| Epicbet | 189 | +1.96% | [−14.57, +18.49] | **2026-09-02..09-13 (11d)** |
| Unibet-Site | 93 | −21.58% | [−44.94, +1.78] | **2026-09-09..09-13 (4d)** |
| POOLED | 550 | −7.64% | [−17.85, +2.58] | 37d |

Note the spans, per the rule that retracted the +16%: Epicbet's usable aligned
history starts **2026-09-02**, not the 2026-08-27 the sweep's §7 table states,
and Unibet-Site has **four days**.

---

## Claim 2 — "the junk anchor does BETTER than the real one": **REFUTED**

This is the report's most damaging claim and it does not survive.

### (a) The two arms are not the same population — they are not close

Both arms were built in **one pass over the identical legs**, so each row
carries `p_real` and `p_junk` side by side and the comparison is exact.

| covariate (p10/25/50/75/90) | REAL arm | JUNK arm |
|---|---|---|
| **n** | **550** | **5,881** |
| `book_odds / pinnacle_odds − 1` | 0.08 / 0.11 / **0.15** / 0.20 / 0.25 | −0.06 / −0.03 / **−0.00** / 0.02 / 0.06 |
| selected anchor probability | 0.29 / 0.34 / **0.43** / 0.52 / 0.62 | 0.33 / 0.44 / **0.54** / 0.62 / 0.69 |
| lead, minutes to kickoff | 8.7 / 14.8 / **40.7** / 56.4 / 143.4 | 11.6 / 25.3 / **50.7** / 81.1 / 458.6 |
| market mix | 1x2 **76%** / O/U 2.5 24% | 1x2 **50%** / O/U 2.5 50% |
| selection mix | home 39% away 25% under 15% draw 12% over 9% | over 26% home 25% under 25% away 15% draw 10% |

**The real arm selects legs priced a median +15% above the sharp line. The junk
arm selects legs priced *at* the sharp line.** The junk arm is not a null for
the real strategy; it is a slightly-filtered flat-back. Its ROI confirms that:
**junk −5.56%** against **flat-back −7.88%** on the same pooled legs. Comparing
a 550-leg tail selection with a 5,881-leg near-flat-back and concluding "junk
beats real" is a statement about two different populations.

### (b) Even taken at face value, the difference is unmeasurable

Observed gap 2.07 pp; per-bet sd 1.23. Per §60, `n = 2((1.96+0.84)·sd/diff)²`
→ **54,900 per arm**. We have 550 in the binding arm. And the junk point
estimate (−5.56%) sits **inside** the real arm's own CI [−17.85, +2.58]. The
arms are indistinguishable; the report reported the sign of a difference it had
no power to see — the exact failure §60 was written after.

### (c) At a *matched* gate and odds band, the real anchor wins everywhere

Same floor, same band, same legs, real vs junk edges:

| cell | REAL | JUNK |
|---|---|---|
| POOLED, prob-edge ≥2%, odds 1.01–2.00 | n=109 **+19.67%** [+4.58, +34.77] | n=1,111 −3.02% [−8.33, +2.30] |
| POOLED, prob-edge ≥2%, odds 1.01–2.50 | n=225 **+17.07%** [+4.18, +29.95] | n=2,935 −1.48% [−5.25, +2.28] |
| POOLED, prob-edge ≥3%, odds 1.01–2.00 | n=67 **+23.92%** [+4.45, +43.39] | n=979 −2.02% [−7.72, +3.68] |
| Coolbet, prob-edge ≥2%, odds 1.01–2.50 | n=125 **+15.35%** [−1.81, +32.52] | n=2,402 −2.88% [−7.00, +1.24] |
| Epicbet, prob-edge ≥2%, odds 1.01–2.50 | n=84 **+32.69%** [+12.36, +53.02] | n=1,248 +0.81% [−5.00, +6.61] |

**Real beats junk by 18–33 pp in every cell.** The report's ordering exists only
at its own unmatched gate.

### (d) The anchor is demonstrably informative — a dipstick the report never ran

The vig baseline is **anchor-independent by construction** (the sweep's own
comment says it "must print the same numbers in both arms"), so it cannot test
the de-vig path at all: a completely broken de-vig would pass it. Binning 19,304
1x2 legs by Shin-de-vigged Pinnacle probability against realised outcomes:

| P bucket | n | mean P | realised | error |
|---|---|---|---|---|
| 0.10–0.20 | 2,344 | 0.161 | 0.147 | −1.4 pp |
| 0.20–0.30 | 7,370 | 0.254 | 0.243 | −1.1 pp |
| 0.30–0.40 | 3,812 | 0.343 | 0.344 | +0.2 pp |
| 0.40–0.50 | 2,477 | 0.446 | 0.461 | +1.5 pp |
| 0.50–0.65 | 1,926 | 0.565 | 0.594 | +2.9 pp |
| 0.65–1.00 | 895 | 0.743 | 0.756 | +1.3 pp |

Well calibrated, with the residual favourite–longshot tilt in the expected
direction. The junk anchor, on the same legs, is flat at ~30–45% realised
regardless of its stated probability (+23.5 pp error at the bottom, −27.9 pp at
the top) — exactly what a null should look like. **The sharp anchor carries real
information; "selecting on a real anchor did not beat selecting on a permuted
one" is false as a statement about the anchor.**

---

## Claim 3 — the vig dipstick: **CONFIRMED, but it proves less than claimed**

It replicates exactly (table under Claim 1) and it does establish that leg
construction, assembly, settlement and clustering are right. That is worth
having.

**What it cannot establish is the thing §3 of the report uses it for.** The
baseline applies no edge filter, so it is identical in the real and junk arms —
by the script's own design. It therefore validates everything *except* the
anchor and the edge computation, which is where the entire question lives. The
calibration table above is the missing half of that check, and it passes.

---

## Claim 4 — "23.1% of junk cells exclude zero, so any survivor is noise": **REFUTED**

The rate is real; the inference from it is not. Counted **by sign** on my grid
(139 real cells and 192 junk cells at n≥60):

| arm | cells | CI excludes zero, **positive** | CI excludes zero, **negative** |
|---|---|---|---|
| REAL anchor | 139 | **16 (11.5%)** | 16 |
| JUNK anchor | 192 | **0 (0.0%)** | 94 |

**Not one junk cell is significantly positive.** The junk arm's rejections are
all on the negative side, and they are not "noise by construction" — they are
the vig, measured precisely because the junk anchor passes ~10× as many legs
through the same nominal floor and so buys far tighter intervals. Offering that
49% (my grid) or 23.1% (the sweep's) as the null rate for a **positive** finding
compares against the wrong tail — and the sweep's own §1 already splits the REAL
arm by sign (207 of 248 negative, 41 positive) without doing the same for the
junk arm it is being compared against. **The correct null is the junk arm's
false-positive rate, and it is zero.**

---

## The thing the sweep could not have found: the gate the live bots actually use

`workers/jobs/pick_triggers.py::_window`:

```python
min_odds = max(1.0 / (cal - edge_floor), odds_floor)   # cal = P_shin for sharp_*
max_odds = min_odds * OUTLIER_MULT                     # 1.6
```

Solving `cal − 1/odds ≥ floor` is a **probability-difference** floor.
`cell_rows` in the sweep filters on `lg["edge"] >= edge_floor` where
`edge = probs[i] * o - 1.0` — an **expected-ROI** floor. This is
ANALYSIS_GOTCHAS **§42** ("our `edge` is probability points, not EV") appearing
for the fourth time. Since `roi_edge = prob_edge × odds`, a 3% probability floor
is a 4.5% ROI floor at 1.50 and a 12% ROI floor at 4.00 — **a curve, not a
constant, so no cell of a constant-floor grid can express it.** The sweep's
`ODDS_BANDS` also has no band ending at **2.00**, which is the band the live
bots' settled record favours; its narrowest ceiling is 2.50. Its 70,200 configurations therefore never contained the
live rule — and adding dimensions cannot fix it, because the missing gate is a
different FUNCTIONAL FORM, not a missing value of a swept parameter.

Swept properly, at lead 0, alignment ≤60 min, traded markets only:

| cell | n | ROI | 95% CI (clustered) | span |
|---|---|---|---|---|
| POOLED prob-edge ≥2%, odds 1.01–2.00 | 109 | **+19.67%** | [+4.58, +34.77] | 37d |
| POOLED prob-edge ≥2%, odds 1.01–2.50 | **225** | **+17.07%** | [+4.18, +29.95] | 37d |
| POOLED prob-edge ≥3%, odds 1.01–2.00 | 67 | +23.92% | [+4.45, +43.39] | 37d |
| POOLED prob-edge ≥3%, odds 1.01–2.50 | 143 | +16.41% | [+0.33, +32.50] | 37d |
| Coolbet prob-edge ≥2%, odds 1.01–2.50 | 125 | +15.35% | [−1.81, +32.52] | 37d |
| Epicbet prob-edge ≥2%, odds 1.01–2.50 | 84 | +32.69% | [+12.36, +53.02] | **11d** |
| Unibet-Site prob-edge ≥2%, odds 1.01–2.50 | 48 | +2.19% | [−25.98, +30.35] | **4d** |

And the live gate reproduced **exactly** on backtest legs
(`prob_edge ≥ 3%`, `odds ∈ [min_odds, 1.6 × min_odds]`):

| | n | ROI | 95% CI | span |
|---|---|---|---|---|
| POOLED, all bands | 242 | +3.24% | [−12.16, +18.64] | 37d |
| POOLED, odds 1.0–2.0 | 64 | **+20.35%** | [+0.35, +40.36] | 37d |
| POOLED, odds 2.0–3.0 | 116 | +7.71% | [−13.61, +29.03] | 37d |
| POOLED, odds 3.0–4.0 | 43 | −10.88% | [−56.87, +35.11] | 35d |

**The backtest reproduces the live bots' odds-band shape** — short prices good,
middle band worse — on 37 days of data rather than their five. That is the
agreement the sweep's odds-dimension section says does not exist ("the
short-odds pattern reproduces … but the short-odds cells are exactly the ones
with negative margin-corrected CLV"). It did not exist **in the grid** because
the grid could not express the gate — see the CLV caveat below, which is the
sweep's strongest surviving counter-argument and is not dismissed here.

### Robustness of the best cell — POOLED, prob-edge ≥2%, odds 1.01–2.50

| split | n | ROI | 95% CI | span |
|---|---|---|---|---|
| full | 225 | +17.07% | [+4.18, +29.95] | 2026-08-07..09-13 |
| fold 1 | 72 | +2.41% | [−20.62, +25.45] | 08-07..08-29 |
| fold 2 | 56 | +32.20% | [+8.21, +56.18] | 08-29..09-09 |
| fold 3 | 97 | +19.21% | [−0.72, +39.14] | 09-09..09-13 |
| in-sample (per-book 70%) | 137 | +13.00% | [−3.45, +29.45] | 08-07..09-10 |
| **out-of-sample (30%)** | 88 | **+23.40%** | **[+2.84, +43.96]** | 09-11..09-13 |
| 1x2 only | 142 | +19.15% | [+3.17, +35.12] | 37d |
| O/U 2.5 only | 83 | +13.51% | [−7.37, +34.39] | 37d |

**No losing fold. Positive out-of-sample. Positive in both traded markets.**
That is more than the sweep's own survivor table managed. Its single best cell
of 70,200 (`POOLED 1x2 home, edge ≥1%, odds 1.01–8.00, ratio ≤15%, lead ≥60`,
+31.67%, n=104) is also positive in all three folds — but it sits at `lead 60`,
which the sweep itself shows is an era selection, and at `lead 0` the same rule
reads +7.29% with fold 1 negative. **The cell here is at `lead 0`**, so it does
not inherit that defect. **But see the era split immediately below — the folds are not
equally weighted in time, and fold 1 is 25 of the 37 days.**

### The finding's biggest weakness: it is a 12-day effect, not a 37-day one

| window | n | ROI | 95% CI | days |
|---|---|---|---|---|
| 2026-08-07..09-01 (Coolbet is the only book) | 81 | **+1.22%** | [−20.21, +22.64] | 25 |
| 2026-09-02..09-08 (Epicbet joins) | 37 | +29.76% | [−0.33, +59.85] | 7 |
| 2026-09-09..09-13 (Unibet-Site joins) | 107 | **+24.68%** | [+5.95, +43.41] | 5 |

**The first 25 days contribute nothing.** And it is *not* explained by
alignment, which was the obvious benign mechanism (the old era's median anchor
gap is 30 min against 5 min after, because retention leaves one surviving quote
per series):

| | n | ROI | 95% CI |
|---|---|---|---|
| old era, anchor gap 0–15 min | 27 | **+1.04%** | [−36.60, +38.68] |
| old era, anchor gap 15–61 min | 54 | +1.31% | [−24.74, +27.35] |
| new era, anchor gap 0–15 min | 97 | +22.59% | [+2.84, +42.35] |
| new era, anchor gap 15–61 min | 47 | **+32.98%** | [+6.45, +59.50] |

A tight gap does nothing in the old era; a loose gap is fine in the new one.
**The date is doing the work, not the alignment.** The flat-back baseline on the
same band shows no comparable gap effect (−3.85% vs −5.19%), so this is not the
harness reacting to gap.

Three things changed around 2026-09-02 and they are perfectly confounded here:
Epicbet's aligned history begins; retention stops pruning the price path
(~7 days back from the run date); and on 2026-09-11 the direct-book `is_closing`
window widened from ≤5 to ≤15 min (§59), which is when our books first got
closing anchors at all. **Nothing in 37 days of data can separate them.** The
only instrument that can is forward time.

### Where it is weaker than it looks

* **POOLED is a best-of-three-books selection (§52, §55).** It is defensible
  here only because all three books are EMTA-legal and self-scraped, so the
  winning price is one we could actually take — but only Coolbet and
  Unibet-Site have placers; **Epicbet has an explorer, not a placer**, so a
  third of the pooled winners are manual today.
* **Attribution of the pooled winners** (prob-edge ≥2%, 1.01–2.50, n=225):
  Coolbet 119 (52.9%) at **+10.74%** [−6.88, +28.36] over 37d; Epicbet 75
  (33.3%) at **+31.51%** [+10.20, +52.82] over **11d**; Unibet-Site 31 (13.8%)
  at +6.42% over **4d**. **The pooled significance leans on Epicbet's 11 days.**
  Coolbet alone — the only book with a 37-day history — is positive but does not
  exclude zero.
* **Coolbet's own folds are backloaded**: +1.29% / +4.09% / +48.88%, IS +4.40%
  → OOS +55.10% at n=27. That is not a stable effect; it is a good last week.
* **Epicbet's OOS is −1.18% at n=17.** Its IS +41.28% is 10 days of picks.
* **THE DECISIVE ONE — the closing line contradicts it.** Own-book CLV is 0 by
  construction on `lead 0` legs (the leg *is* the last surviving pre-kickoff
  row), so I re-ran the identical cells at `lead 60`, where a strictly later
  own-book quote exists and CLV is computable:

  | cell at lead 60 | n | ROI | own-book CLV, raw | **margin-corrected EV** | CLV n |
  |---|---|---|---|---|---|
  | POOLED prob-edge ≥2%, 1.01–2.50 | 90 | +26.68% | +2.33% | **−5.36%** | 35 |
  | POOLED prob-edge ≥3%, 1.01–2.50 | 58 | +19.80% | +1.87% | **−5.65%** | 24 |
  | Epicbet prob-edge ≥2%, 1.01–2.50 | 49 | +42.18% | +0.19% | **−7.05%** | 26 |
  | Coolbet prob-edge ≥2%, 1.01–2.50 | 37 | +20.40% | +0.32% | **−7.56%** | 4 |

  **Every one is negative, at the same magnitude the sweep reports (−5.05% to
  −5.63%), beside ROIs of +19% to +42% on the same legs.** A random leg's
  margin-corrected own-book CLV is ≈ `−m/(1+m)` ≈ **−7.2%**, so the selection
  buys roughly **1.9 pp** of closing-line value. That is consistent with the
  anchor being genuinely informative (T5) and simultaneously nowhere near enough
  to cover a 7–8% margin. **On the metric that converges 200× faster than ROI,
  the rule loses. The sweep's central argument is correct and it applies to my
  cells too.**
* **Power is exactly as bad as the report says.** Detecting a true +3% ROI in
  this cell needs **n≈8,220**; at 6.0 picks/day that is **3.7 years**. The
  +17% point estimate is not a forecast.
* **Multiple comparisons apply to me too.** 16 of my 139 cells are
  significantly positive. What distinguishes them from a fishing expedition is
  that they form one coherent family (short prices, probability floor, same sign
  at all three books and both markets), that the matched junk control reads ≈0
  on the identical band, and that the junk arm's false-positive rate is 0.0%.
  That is suggestive, not decisive.

---

## What the data structurally cannot tell either of us

**This is a CLOSING-price backtest for 33 of its 37 days, and the live bots do
not bet at the close.** `prune_old_simple` keeps one pre-kickoff row per series
after 7 days, and at our books that is literally one row:

| date | rows per (match, selection) series, 1x2 |
|---|---|
| 2026-09-13 | Coolbet 3.9, Epicbet 23.8, Unibet-Site 6.9 |
| 2026-09-11 | Coolbet 8.1, Epicbet 22.7, Unibet-Site 13.7 |
| 2026-09-06 and earlier | **Coolbet 1.00, Epicbet 1.00** |

Pinnacle keeps ~3–4 (median surviving lead **15 min** — the `is_closing` row).
Consequences neither report can escape:

1. **The alignment filter is a coverage selection outside the 7-day window.**
   With one surviving book row and a Pinnacle row at ~15 min, "aligned ≤60 min"
   means "the book's last write happened within ~75 min of kickoff". That is a
   property of the scraper's schedule, not of a betting rule.
2. **The backtest can only evaluate near-closing prices**, where a soft book has
   already moved. Any sharp-anchor edge that is largest *early* is structurally
   invisible to both harnesses. This cuts against the negative result, not for
   it.
3. **The live bots cannot be reproduced retrospectively.** They fire on
   transient intraday prices that retention has deleted. The backtest and the
   live record are measuring two different things over the same fixtures.

**The live bots' own record is not a third data point yet.** On
`shadow_bets_unique`, fixture-clustered:

| band | n | ROI | 95% CI | span |
|---|---|---|---|---|
| all sharp bots | 254 | −1.87% | [−18.25, +14.51] | 2026-09-08..09-12 (4d) |
| 1.0–2.0 | 87 | +10.79% | **[−9.34, +30.91]**, t=+1.05 | 4d |
| 2.0–3.0 | 90 | −10.22% | [−35.82, +15.39] | 4d |
| 3.0–4.0 | 35 | −9.95% | [−62.77, +42.86] | 4d |
| 4.0+ | 42 | −3.45% | [−64.25, +57.34] | 4d |

The short-band "+10.79%" **does not exclude zero** (t=1.05) and needs n≈496 to
detect even its own point estimate. **Four days.** It agrees in sign with the
backtest, which is why it was worth chasing — but on its own it is nothing, and
the brief's framing of it as a disagreeing third data point overstates it.

Also reproduced: the CLV-fallback artefact the report flagged. Rows where
`closing_bookmaker IS NULL` (an arbitrary book's close) read **+14.31%** raw CLV
against **+8.28%** for own-book-anchored rows, n=87 vs 167. **The report's
highest-value recommendation — stop writing an arbitrary-book close into `clv` —
is confirmed and should be done.**

---

## Bottom line

**Is there a defensible OWN sharp configuration we could run as a paper shadow
bot today? Only as an INSTRUMENT, and its own CLV already predicts it loses.
There is certainly none worth money.** I set out to break the negative result
and broke three of its four arguments; the conclusion still stands.

The one cell family the sweep's grid could not express:

```
anchor : Shin-de-vigged Pinnacle, aligned within 60 min of the book quote
gate   : P_shin − 1/book_odds ≥ 0.02        (probability difference, NOT P×odds−1)
band   : book_odds ≤ 2.50                    (no lower floor beyond 1.01)
markets: 1x2 and O/U 2.5
books  : best aligned price of Coolbet / Epicbet / Unibet-Site
guard  : keep the production outlier filter (Pinnacle × 1.35 / × 1.30)
```

n=225, ROI +17.07%, clustered CI [+4.18, +29.95], no losing fold, OOS
+23.40% [+2.84, +43.96], both traded markets positive, matched junk control
−1.48%, 6 picks/day.

**Stated with its two worst numbers attached.** (1) 25 of those 37 days
contribute +1.22% at n=81; the whole effect sits in the last 12 days (n=144). It
is not an alignment artefact — that was tested and rejected — but it cannot be
separated from Epicbet joining, from retention not yet having pruned, or from
the 2026-09-11 closing-anchor change. (2) **At `lead 60`, the same rule's
margin-corrected own-book CLV is −5.36% (n=35) pooled, −5.65% at the 3% floor,
−7.05% at Epicbet, −7.56% at Coolbet — against ROIs of +19% to +42% on the same
legs.** A random leg's margin-corrected CLV is ≈ −7.2%, so the selection buys
about **1.9 pp** of closing-line value: real, informative (it matches the
anchor's measured calibration), and **nowhere near the 7–8% vig it has to
clear**. Anyone who reads +17.07% as the expected return of this rule has read
it wrong; the CLV says the expected return is negative.

**Do not stake a euro on it, and do not promote it.** Detecting the +3% that
would actually matter needs 3.7 years at this pick rate; the entire effect is
12 days old; a third of it comes from 11 days of Epicbet; and the fastest-
converging metric available says the sign is negative. **If a paper bot is run,
run it to settle the CLV question, not because the ROI looks good** — and
pre-commit to the stopping rule now: *promote only if margin-corrected own-book
CLV turns positive at n ≥ 300*, not if the ROI stays at +17%. The correct
action is a **paper shadow bot emitting exactly this rule, tagged so its CLV
can be read against its own book's close once NEAR-KICKOFF-CAPTURE has been
writing long enough to give it one.**

### What the prior agent got right, and should keep

* The harness. Six identical dipstick figures across two independent
  implementations.
* The retention limits, the era-selection lesson, and printing date spans.
* Refusing the +16.00% and refusing to move the 3% / 4.0 / 2.80 / 13% gates on
  a grid search.
* The CLV-fallback fix as the highest-value change in the document. Still true.
* "Keep collecting." Still right — and the CLV test above is the reason: the
  question is settled by own-book closing-line value, which needs
  NEAR-KICKOFF-CAPTURE to have been writing long enough to produce a close.
* **"Believe the CLV."** The sweep's central argument. It held when I pointed it
  at a cell the sweep never saw, which is the strongest thing that can be said
  for an argument.

### What must be withdrawn or restated

1. **"None of 70,200 configurations clears the bar"** → *none of the
   configurations **in that grid**; the grid did not contain the live
   probability-difference gate or any band ending at 2.00.*
2. **"The junk anchor does better than the real one"** → withdraw. The arms
   differ in price-ratio, market mix, selection mix, lead and n by a factor of
   10.7; at a matched gate the real anchor beats junk by 18–33 pp; and the
   observed 2.07 pp gap needs n=54,900 per arm to call.
3. **"23.1% of cells exclude zero under a junk anchor, so any survivor is
   noise"** → restate. The junk arm's false-**positive** rate is **0.0%**; its
   rejections are the vig.
4. **The pooled −9.55% CI [−18.8, −0.3]** → restate. On the two markets we
   actually trade it is −7.64% **[−17.85, +2.58]** and does not exclude zero.
5. **"The vig dipstick validates the harness"** → restate. It validates
   everything except the anchor, which is the part in question; the anchor
   calibration table is the check that was missing.
6. **Epicbet's first usable aligned row is 2026-09-02**, not 2026-08-27.

### Filed, not fixed here

* **`settle_shadow_bets` CLV fallback** — reproduced (+14.31% unanchored vs
  +8.28% own-book). `workers/jobs/settlement.py` is being edited by another
  agent; not touched.
* **`docs/OWN_SHARP_CONFIG_SWEEP_2026_09_14.md` and
  `docs/OWN_BOOK_UNIVERSE_2026_09_14.md`** both assert the withdrawn claims
  above and need the corrections in this section; `own_sharp_config_sweep.py`
  is being edited by another agent, so its grid was not changed.
