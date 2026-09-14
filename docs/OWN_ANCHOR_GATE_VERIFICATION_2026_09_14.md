# Adversarial verification — the sharp-anchor gate, the last thread standing

**Date:** 2026-09-14 · **Direction:** 🤖 OWN — this is entirely about what we
should stake at Coolbet / Epicbet / Unibet-Site. Nothing here is publishable and
nothing here changes a published figure.

**Under test:** `docs/OWN_SEGMENT_SIGNAL_SEARCH_2026_09_14.md` §2–§3 and
`dev/active/own-book-split-gate-preregistration.md`.
**Script (committed):** `scripts/own_anchor_gate_adversarial.py`
**Smoke:** `OWN-ANCHOR-GATE-VERIFICATION-GUARDS`

Reproduce everything below with:

```bash
python3 scripts/own_book_clv_universe.py --out /tmp/own_clv_panel.json.gz
python3 scripts/own_anchor_gate_adversarial.py --panel /tmp/own_clv_panel.json.gz
```

The panel reproduced byte-for-byte: 46,547 leg-rows, 4,869 book-market series,
**2026-09-07 … 2026-09-13, 7 match days**. Headline arm (1x2, lead 3h):
**n=5,475 legs on 1,268 fixtures** — 1,268 is the effective n, the legs are not
independent. Every SE below is clustered on `match_id`.

---

## Verdicts

| # | Claim | Verdict |
|---|---|---|
| 1 | The sharp anchor is real information, not arithmetic | **CONFIRMED** — and it survives three controls stricter than the published placebo. But the published placebo is a weaker control than it reads as (§1b). |
| 2 | Break-even is ~**+6.0%** prob-edge | **REFUTED as a number.** The trim choice alone moves it +3.1% → +10.5%; the *price basis* moves it to **+18% … +47%**. There is no defensible point estimate. |
| 3 | The gate must differ per book, because Epicbet's line responds at half the rate | **REFUTED as a book property.** The gap does not survive controlling for the measurement window, which differs 2–3× by book because our Epicbet scrape is 3–6× denser. |
| 4 | Own-book CLV is only ~7 days deep, structurally | **CONFIRMED exactly.** 1.00 rows/series through 09-06, 4.9–20.6 from 09-07. |

**And the question nobody asked, answered first because it decides everything:**

> ### A +6% gate would fire **2.4 legs/day across all three books** — and **0.3/day at Coolbet**, the one book where we place real money. With the live odds cap ≤2.50 it is **0.9/day** pooled and **1 leg per week** at Coolbet. The Epicbet-specific +14.6% gate fires **0.6/day**, all of it at Epicbet, **0 legs in 7 days at Coolbet**. At that volume, resolving whether the gate is 2pp away from break-even takes **5.4 years** (9.0 years at the 60% own-book CLV coverage the ledger actually achieves), and **57 years** at +14.6%.

---

## 1. Claim 1 — the anchor is information · **CONFIRMED**

### 1a. It survives three controls the published one does not imply

| arm | slope | t | intercept | break-even |
|---|---|---|---|---|
| **REAL** — Pinnacle Shin de-vig | **+1.314** | **+4.86** | −4.10% | +3.12% |
| PUBLISHED placebo (odds-decile shuffle) | +0.007 | +0.22 | −7.63% | +1096% |
| **VARIANCE-MATCHED placebo** (new) | **−0.002** | **−0.06** | −7.66% | never |
| `y ~ p_sharp + q` — coefficient on **p_sharp**, soft price held fixed | **+1.397** | **+5.15** | | |
| within (selection × odds-40ile) demeaned | **+1.410** | **+5.71** | | |

*(1x2, lead 3h, 2026-09-07…09-13, n=5,475, 1,268 fixtures.)*

The decisive one is the second-to-last row. `prob_edge = p_sharp − 1/odds_soft(T)`
and `mc_clv` both contain `odds_soft(T)`, which is the whole worry. Free the two
coefficients: `y = a + b₁·p_sharp + b₂·q`. If the ladder were the shared soft
price, **b₁ would be ≈0**. It is **+1.397 (t=+5.15)**, and b₂ = −1.331 (t=−5.02)
— equal and opposite, i.e. the data do not reject the prob-edge restriction the
published specification imposes. Demeaning inside 40 quantiles of the soft
implied probability crossed with selection — which removes every between-price
channel non-parametrically — leaves **+1.410 (t=+5.71)**, slightly *larger*.

**The anchor carries fixture-specific information about where our own books
close. That part of the finding is real and I could not break it.** It is also
still there at an obtainable price, at about a quarter the size: +0.346
(t=+3.32) on fresh rows, against −0.063 (t=−1.35) for the variance-matched
placebo on the same rows.

### 1b. But the published placebo is weaker than it reads

The published placebo replaces `p_sharp` wholesale within an odds decile. Inside
a decile `p_sharp` and `q` are correlated at **0.987**, so their *difference* has
sd **0.0244** while the shuffled difference has sd **0.0454** — the placebo
regressor carries **3.46× the variance**, almost all of it injected noise.
Classical errors-in-variables then shrinks any fitted slope by
Var(signal)/Var(signal+noise) **whether or not a mechanical channel exists**. So
"+1.314 vs +0.007" is not a clean 4.9σ-vs-0 contrast; a mechanical slope of
+0.024 would have printed as +0.007 too.

The fix is to shuffle only the part of `p_sharp` that a fine function of the soft
price does not explain (project on selection × 20-quantile of `q`, permute the
residual). Variance is then preserved by construction, and the placebo still
returns **−0.002 (t=−0.06)**. **The published conclusion is right; the published
control is not strong enough to have established it.** Use the decomposition.

---

## 2. Claim 2 — break-even is "+6.0%" · **REFUTED as a number**

### 2a. The trim choice alone spans 7.4 percentage points

| estimator | slope | intercept | **break-even** |
|---|---|---|---|
| no trim | +1.314 | −4.10% | **+3.12%** |
| trim 0.5%/side | +0.924 | −5.19% | **+5.62%** |
| trim 1.0%/side | +0.835 | −5.44% | **+6.52%** |
| trim 2.0%/side | +0.732 | −5.71% | **+7.81%** |
| trim 5.0%/side | +0.579 | −6.10% | **+10.54%** |
| winsor 0.5 / 1 / 2 / 5% | +0.938 / +0.886 / +0.830 / +0.710 | | **+5.49 / +5.99 / +6.60 / +8.17%** |
| `|raw clv| ≤ 20%` (the bot's own price-ratio cap) | +0.782 | −5.54% | **+7.08%** |

*(The published "+5.99% at a 1% trim" is winsorisation, not trimming; trimming at
1% gives +6.52%. Both are in the table.)* The estimate does not converge as the
tail is cut — it walks monotonically outward. **"+6.0%" is one point on a ramp
that runs from +3.1% to +10.5%, chosen by a free parameter.**

### 2b. And the price basis moves it much further than the trim does

`odds_dec` is *the last quote we observed at or before the decision moment*, not
the price the book was showing then. The direct-book writers
(`store_coolbet_odds_snapshot`, `store_book_odds_snapshots`) `INSERT` one row per
poll with **no dedup-on-change**, so a gap in a series is an interval we did not
observe, not a market that stood still. Measured on the same window:

| gap between consecutive observed quotes | P(price unchanged) | mean \|move\| |
|---|---|---|
| Coolbet, 30–60 min | 0.82 | 0.92% |
| Coolbet, 4–12 h | 0.52 | 3.68% |
| **Coolbet, >12 h** | **0.28** | **5.35%** |
| Epicbet, >12 h | 0.04 | 6.55% |
| Unibet-Site, >12 h | 0.37 | 5.84% |

At the 3h arm the median decision quote is another **113–162 minutes** older than
the nominal decision moment, and **26–45% of legs are more than 4 hours older**.
Those rows are ANALYSIS_GOTCHAS 44 exactly: a phantom price, entering the
predictor and the target at once.

Restrict the decision quote to one we actually observed near the decision moment
and the whole calibration moves:

| decision quote observed within | n | fx | slope | t | intercept | **break-even** |
|---|---|---|---|---|---|---|
| 10 min | 133 | 45 | +0.524 | +1.22 | −7.24% | +13.8% |
| 30 min | 684 | 192 | +0.168 | +1.09 | −7.84% | **+46.6%** |
| 60 min | 2,142 | 592 | +0.346 | +3.32 | −7.38% | **+21.4%** |
| 120 min | 2,604 | 725 | +0.373 | +3.78 | −7.19% | +19.3% |
| 240 min | 3,405 | 903 | +0.568 | +3.43 | −6.34% | +11.2% |
| **any lag (as published)** | 5,475 | 1,268 | **+1.314** | +4.86 | **−4.10%** | **+3.12%** |

It is a clean dose-response in staleness. Note the **intercept**: on obtainable
prices it sits at **−7.2% to −7.8%**, which is the unselected baseline (−7.46%)
— i.e. at edge=0 the anchor buys nothing, exactly as it should. The published
intercept of −4.10% is not a discovery about the anchor; it is the pivot of a
steeper line about the same mean.

**The realised number at the live configuration flips sign the same way:**

| rows | n | untrimmed | 1% winsor | 2.5% winsor |
|---|---|---|---|---|
| all rows (as published) | 49 | **+1.66%** | +1.48% | +1.26% |
| decision quote observed within 60 min | 19 | **−5.87%** | −5.74% | −5.55% |

### 2c. And the panel is not one regime

| match day | n | median decision lag | slope |
|---|---|---|---|
| 2026-09-07 | 156 | 53 m | +0.337 (t=+2.30) |
| 2026-09-08 | 354 | 38 m | +0.314 (t=+0.88) |
| 2026-09-09 | 324 | 46 m | +0.563 (t=+1.87) |
| 2026-09-10 | 321 | 43 m | +0.665 (t=+2.22) |
| **2026-09-11** | 759 | **444 m** | **+1.584 (t=+7.71)** |
| **2026-09-12** | 2,523 | **218 m** | **+1.581 (t=+3.33)** |
| **2026-09-13** | 1,038 | **162 m** | **+1.676 (t=+9.99)** |

79% of the legs and the entire headline slope come from 09-11 onward — the days
on which the decision quote is 4–10× staler, and the same date as
`DIRECT-BOOK-ANCHORS-2026-09-11`. The published time-ordered split (train <09-11
≤ test) puts *all* of the confirmation on the far side of that change
(ANALYSIS_GOTCHAS 39). Coolbet, a constant book across the whole window, moves
+0.13 → +1.95 by itself, so this is not book mix.

**Verdict on claim 2: REFUTED.** The honest statement is that break-even at a
price we could actually have taken is **somewhere between +11% and +47%**, with
the best-powered single estimate **+21.4% (n=2,142, 592 fixtures, t=+3.32)** —
and that the published +6.0% is a number produced by prices that were not on the
screen.

**Residual uncertainty, stated plainly.** The freshness restriction selects
rows, and denser polling may correlate with bigger fixtures where the anchor is
genuinely worth less. Two things argue against reading it that way: the
monotone dose-response in the lag allowance, and the gap-vs-change table above,
which shows the mechanism directly. What would settle it is the §6 build, not
another cut of this week.

---

## 3. Claim 3 — the per-book split · **REFUTED as a book property**

### 3a. How many cells were actually available

The evidence doc reports four window×lead combinations. The panel supports
**20** (2 markets × 5 leads × 2 windows); all 20 are printed by the script.
**10 of 20 have |t|>1.96**, 19 of 20 have the same sign — but the effect is
**absent at lead 1h and lead 24h in both markets**, and absent in O/U at 1h and
3h. The four reported cells are the four strongest of twenty, and they share
rows: the 3h and 6h arms of the same week are not independent confirmations.

### 3b. Fixture mix is NOT the explanation — the window is

The three books quote only **55 fixtures in common of 1,268** at the 3h arm, so
the published contrast is almost entirely across different fixtures. On the
common set the gap does survive (d_Epic = −1.181, t=−2.90, n=495) — so fixture
mix is not it.

What does kill it is the **measurement window**: the minutes of price time
between the decision quote we hold and the close quote we hold. Epicbet's series
is **20.6–23.8 rows deep** against Coolbet's **3.6–9.3** and Unibet-Site's
**3.3–13.9**, so Epicbet's decision quote is captured much closer to its own
close. Less price time observed → less convergence → smaller slope, mechanically.

| market | lead | d_Epic raw | **d_Epic, window-controlled** | edge × log(window) | median window CB / EP / UB |
|---|---|---|---|---|---|
| 1x2 | 3h | −1.192 (t=−3.04) | **−0.484 (t=−1.49)** | +1.005 (t=+6.71) | 310 / 277 / 329 m |
| 1x2 | 6h | −1.209 (t=−3.91) | **−0.518 (t=−1.43)** | +1.005 (t=+3.93) | 672 / 391 / 1016 m |
| 1x2 | 12h | −0.806 (t=−2.28) | **−0.294 (t=−0.70)** | +1.282 (t=+2.50) | 1141 / 748 / 1137 m |
| O/U 2.5 | 3h | −0.189 (t=−0.28) | +0.502 (t=+0.78) | +0.746 (t=+4.37) | 313 / 272 / 329 m |
| O/U 2.5 | 6h | −1.129 (t=−4.28) | **−0.294 (t=−1.51)** | +1.038 (t=+6.41) | 780 / 391 / 1016 m |
| O/U 2.5 | 12h | −1.003 (t=−3.12) | **−0.034 (t=−0.13)** | +2.059 (t=+4.96) | 1141 / 748 / 1137 m |

**In every one of the six cells the Epicbet gap loses significance once the
window is controlled, and the window itself is a strong predictor of the slope
(t = +2.5 to +6.7).** The same thing shows in the per-book break-even once the
price is required to be fresh — every book's number blows out, and the ordering
is preserved because the density ordering is preserved:

| book | all rows, 1% winsor | **decision quote ≤60 min, 1% winsor** |
|---|---|---|
| Coolbet | +1.057 → break-even **+4.2%** | +0.363 (t=+2.22) → **+18.2%** |
| Epicbet | +0.443 → break-even **+14.6%** | +0.138 (t=+1.07) → **+62.6%** |
| Unibet-Site | +1.195 → break-even **+4.0%** | +0.592 (t=+3.22) → **+11.1%** |

**Verdict: REFUTED.** "Epicbet's line responds at half the rate" is not
separable from "our Epicbet scrape is 3–6× denser than our Coolbet scrape". The
pre-registration's own withdrawal clause — *"if the Epicbet gap is later found to
track a [something] confounded with the book rather than the book, the
hypothesis is withdrawn, not re-cut"* — applies. **Do not split the gate.**

---

## 4. Claim 4 — own-book CLV is 7 days deep · **CONFIRMED**

Rows per series, 1x2, pre-kickoff non-live, by match day:

| match day | Coolbet | Epicbet | Unibet-Site |
|---|---|---|---|
| 2026-08-28 … 2026-09-06 | **1.00–1.01** every day | **1.00–1.24** | — |
| 2026-09-07 | 4.92 | 20.56 | — |
| 2026-09-09 | 9.26 | 20.93 | 3.32 |
| 2026-09-11 | 8.13 | 22.66 | 13.71 |
| 2026-09-13 | 3.94 | 23.76 | 6.92 |

Exactly as described: one surviving pre-kickoff row per series outside the
retention window, and that row is the close, so CLV there is zero by
construction. The panel is **2026-09-07…09-13, 7 match days**, and claims 1–3
all rest on it. Effective n is the **fixture** count — 1,268 at the 3h arm, and
only **592** once the decision price must be real. And because 79% of the legs
sit after 09-11 (§2c), the effective span of the headline number is **three days**.

---

## 5. What the gate would actually produce · the number that decides this

Legs per day, panel 2026-09-07…09-13 (7 match days). Bracketed figure survives
the live odds cap ≤2.50.

**1x2**

| gate | Coolbet | Epicbet | Unibet-Site | ALL |
|---|---|---|---|---|
| +2.0% (live) | 4.7/d (2.3) | 5.0/d (1.9) | 5.4/d (2.9) | **15.1/d (7.0)** |
| +4.0% | 1.4/d (0.7) | 2.0/d (0.4) | 2.3/d (1.1) | 5.7/d (2.3) |
| **+6.0%** (the proposed gate) | **0.3/d (0.1)** | 1.1/d (0.3) | 1.0/d (0.4) | **2.4/d (0.9)** |
| +8.0% | **0.0/d** | 1.0/d (0.3) | 0.4/d (0.0) | 1.4/d (0.3) |
| **+14.6%** (proposed for Epicbet) | **0.0/d** | 0.6/d (0.3) | 0.1/d (0.0) | 0.7/d (0.3) |

**over/under 2.5** adds 1.0/d (0.7) at +6% and 0.3/d (0.0) at +14.6%.

Raw counts, because per-day rates on 7 days flatter small numbers: at **+6%**
over the whole week there were **2 Coolbet 1x2 legs** (1 under the odds cap), 8
Epicbet, 7 Unibet-Site. At **+8%**, **zero** Coolbet legs in seven days.

**Time to resolve a 2pp departure from break-even** (n = ((1.96+0.84)·sd/0.02)²,
at the measured volume, and again at the 46–71% own-book CLV coverage the live
sharp bots actually achieve):

| gate | legs/day | sd | n needed | years | years at 60% coverage |
|---|---|---|---|---|---|
| +2% | 15.1 | 23.1pp | 1,046 | 0.2 | 0.3 |
| +4% | 5.7 | 34.8pp | 2,368 | 1.1 | **1.9** |
| **+6%** | **2.4** | 49.4pp | 4,785 | 5.4 | **9.0** |
| +8% | 1.4 | 64.8pp | 8,225 | 15.8 | 26.3 |
| **+14.6%** | **0.7** | 87.0pp | 14,850 | 57.0 | **94.9** |

Raising the gate does not merely cost volume; it raises the variance of what
survives (sd 23pp → 87pp), so the n required rises **and** the rate of accrual
falls. **A +6% gate is not a slower experiment than the +2% one; it is a
different experiment, and it cannot be finished.**

For context, the live instrument `bot_trigger_1x2_sharp_tight_v1` currently has
**13 picks, all on 2026-09-14, own-book CLV on 6 of them.** Its stopping rule
needs n≥300.

---

## 6. Bottom line for the operator

> ### Do not raise the gate to +6%, and do not split it per book. Neither number survives contact with the price basis, and even if +6% were right it would fire 2.4 legs/day pooled and 1 leg per week at Coolbet — a gate that cannot be resolved this decade.
>
> ### The thread is not dead, but it is not a gate. What survived is narrower and more valuable than what was claimed: **the Pinnacle anchor carries genuine fixture-specific information about where our own books close (+1.40, t=+5.15 with the soft price held fixed; +0.35, t=+3.32 at a price we could actually have taken).** What died is every number built on top of it — the +3.1%/+6.0% crossing, the −3.5% prediction at the live gate, and the per-book split.

**Concretely:**

1. **Leave `bot_trigger_1x2_sharp_tight_v1` at 2% and leave it on paper.** Its
   pre-registration says changing the gate starts a new instrument; there is no
   evidence here worth spending that on. Its own realised number at 2% is
   +1.66% on all rows and −5.87% on the 19 legs where the decision price was
   real — n=19 decides nothing, and that is the point.
2. **Withdraw `dev/active/own-book-split-gate-preregistration.md`** under its own
   clause. The Epicbet effect tracks our scrape density, not Epicbet's line.
   (I have not edited it — that is the owner's call and `dev/` is outside this
   read-only brief.)
3. **The one BUILD worth doing is not the retention change the evidence doc
   proposes — it is a FRESHNESS stamp.** Retention (keep a decision-time row
   past 7 days) fixes the *span*; it does nothing about the fact that 26–45% of
   decision quotes are already >4h stale *inside* the window. Both are needed,
   and freshness is the one that is currently inverting conclusions. Minimum
   viable version: record, per own-book series, the observation timestamp we
   priced from, and make every own-book CLV number reject a leg whose decision
   quote was not observed within N minutes of the decision moment. Then re-run
   this script; the honest calibration will fall out of a panel that is not
   half phantom.
4. **When the gate question comes back, ask for legs/day before asking for
   break-even.** This report's §5 took twenty minutes and would have stopped the
   +14.6% proposal on its own.

---

## 7. What I did NOT test

- **The outcome side.** Everything here is CLV. ROI cannot resolve any of it
  (sd≈1.3 per bet, ≈15,600 bets for a +3% signal) and I did not try.
- **Markets beyond 1x2 and over_under_25**, and Asian handicap (ANALYSIS_GOTCHAS 16).
- **Whether a fresh-price gate could be made to work at all.** The fresh-price
  slope is real (+0.346, t=+3.32) but its break-even is +21%, and at +21% there
  are ~0 legs. I did not search for a different functional form that might land
  a reachable gate on the fresh rows; that is the only remaining live question
  on this thread and it needs the §6.3 build first.
- **`dev/active/` and `PRIORITY_QUEUE.md` were not edited** — read-only brief.

---

## 8. Ripple — docs this report makes stale (NOT edited; read-only brief)

I was scoped to scripts plus this one doc, so I have corrected nothing in place.
These assert numbers this report refutes and each needs a dated banner or a fix:

| doc | what is now wrong |
|---|---|
| `docs/OWN_SEGMENT_SIGNAL_SEARCH_2026_09_14.md` §2, §3, findings 7 and 8, and the one-paragraph answer | break-even "+6.0%", "predicted −3.5% at the live 2% gate", the per-book gates (+4.15 / +14.59 / +3.95%), and "Epicbet's line responds at half the rate". Also §0b's trap table should gain a row for the decision-price lag. |
| `dev/active/own-book-split-gate-preregistration.md` | the whole hypothesis. Its own withdrawal clause applies. |
| `dev/active/own-sharp-tight-preregistration.md` | nothing is refuted — it is already paper-only with a CLV stopping rule — but the evidence line it cites should point here too. |
| `docs/SYSTEM_MAP.md`, `docs/BETTING_GATE_DECISIONS.md` | only if either has already recorded a +6% or per-book gate. Grep before assuming. |
| `PRIORITY_QUEUE.md` | needs a row for the freshness stamp (§6.3) and closure of whatever row tracks the split-gate proposal. |

```bash
grep -rln "14.59\|+6.0%\|own-book-split-gate" docs/ dev/active/ *.md
```
