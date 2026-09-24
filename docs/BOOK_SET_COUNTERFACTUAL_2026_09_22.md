# Book-set counterfactual — what PICKS would have looked like if the set had never been cut

**2026-09-22 · measurement only, nothing in production was changed.**
Script: `scripts/backtest_book_set_restriction.py` (re-runnable).
Parent row: `PRIORITY_QUEUE.md` **#005 ACCESSIBLE-BOOK-SET-SHRANK-UNMEASURED**; the
selection-effect half answers **#065 CALIBRATION-AFTER-THE-OU-BUG (a)**.

---

## The question and the short answer

`ACCESSIBLE_BOOKMAKERS` (`workers/jobs/daily_pipeline_v2.py:1072`) was cut on
**2026-09-05 00:20 EEST** (`bb39c6e8`, ACCESSIBLE-SET-VERIFY) on **EMTA blocked-domain**
grounds — a test of what is reachable *from Estonia*, i.e. a 🤖 OWN constraint. It gates
`_load_today_from_db`, the **shared** price loader that also feeds published 👥 PICKS. The
owner's decision is that PICKS should price off every book we collect while OWN keeps the
Estonian-reachable set. This measures that decision before it is built.

| question | answer | robust? |
|---|---|---|
| Does the cut change **coverage**? | Yes — **5.7% of 1x2 and 7.9% of O/U** modelled selections have no price at any accessible book, so they cannot be published at all | **Yes** |
| Does it change **price**? | Yes — **+2.1% (1x2) / +2.2% (O/U)** mean uplift from opening the set, confirming the +2.6% already on #005 | **Yes** |
| Does it change **volume**? | Yes, and this is the big one — opening the set adds **+52% to +76% more O/U picks**, at an identical mean price | **Yes** |
| Does it change **realised outcome / calibration**? | **Unknown.** The sign flips when the evaluation instant moves from kickoff −6h to −12h. Nothing reaches significance | **No** |
| Did the thin book set **select for the model's overconfident tail**, linking #005 to #065? | **No evidence, in either direction.** Sign flips with the evaluation instant; \|z\| ≤ 1.63 in all eight cells | **No — and that is the finding** |

The exercise was set up to test a specific causal claim. It cannot confirm or refute it:
**the effect, if any, is smaller than the noise introduced by an arbitrary methodological
choice.** That is a real result and it should stop the claim being repeated as if it were
established. What *is* solid is price, coverage and volume — and those are enough to decide
the product question without the causal one.

---

## Method, in one paragraph

`odds_snapshots` still holds every book, so for every settled fixture we re-derive the best
price under three book sets and re-run the gate stack. **`as_was`** is the set actually live
at the decision instant (reconstructed from git — Pinnacle dropped 09-04, the cut 09-05,
Kambi dropped 09-06, AF-`Unibet`→`Unibet-Site` 09-14). **`restricted`** is today's four-book
set applied across the whole window. **`all_books`** is everything minus the feeds that were
never a real offer (`api-football`, `api-football-live`, `William Hill` on O/U, the retired
`Unibet-Kambi`). Raw probabilities come from `predictions` (source `ensemble`) as of the
decision instant; **the Platt coefficients and shrinkage alphas are looked up by
`fitted_at`**, so each arm runs the calibrator actually in force — including the O/U curve
migration 335 deleted, recovered from `model_calibration_ou_domain_mismatch_backup`. Two gate
stacks are reported because they answer differently: **`v10`** = `BOTS_CONFIG['bot_v10_all']`
(the bot that publishes picks — per-tier thresholds, tier-3+ bump, data-tier bump, odds range
1.30–4.50, min prob 0.30, Pinnacle veto + mid-band), and **`placer`** = the
registry/`coolbet_placer` floors (edge 10%/8%, odds 2.80/1.80) that decide what is stakeable.
The whole study is run twice, at evaluation instants **kickoff − 6h** (the median lead of the
real `bot_v10_all` 1x2 picks) and **kickoff − 12h**.

**`calibrated_prob` is deliberately not held constant across arms.** `calibrate_prob` takes
the best price both as the shrinkage-anchor fallback and as the CAL-ALPHA-ODDS longshot step,
so a thinner book set moves the *probability* as well as the price. Holding it fixed would
have measured a model that does not exist — and would have made the selection-effect test
vacuous, since that mechanism runs precisely through this coupling.

---

## 1. Price and coverage — the only selection-free numbers

Same candidates, no gate. Everything downstream inherits these two numbers: a published edge
is a price, and a selection with no price in the restricted set cannot be published at all.

**At kickoff − 6h:**

| market | period | candidates | no restricted price at all | mean best price restricted → all | uplift | % strictly better |
|---|---|---|---|---|---|---|
| 1x2 | → 09-04 | 6,489 | 1,608 (24.8%) | 3.317 → 3.516 | +4.83% | 83.4% |
| 1x2 | 09-05 → | 6,501 | 370 (**5.7%**) | 3.486 → 3.579 | **+2.12%** | 49.2% |
| O/U 2.5 | → 09-04 | 3,702 | 886 (23.9%) | 1.935 → 1.989 | +2.59% | 71.3% |
| O/U 2.5 | 09-05 → | 4,796 | 378 (**7.9%**) | 1.960 → 2.004 | **+2.18%** | 69.1% |

**At kickoff − 12h** the same rows read 4.9% / +1.82% (1x2 post-cut) and 7.2% / +2.02% (O/U
post-cut) — i.e. **this table is stable and the conclusions below rest on it.**

The post-cut **+2.1 / +2.2%** corroborates the **+2.6%** already recorded on #005 from a
different sample, so that number stands. Where the better price comes from, post-cut (6h):
Pinnacle 1,166 · Bet365 557 · 1xBet 537 · Betfair 401 · BetVictor 175 (1x2), and
Pinnacle 1,402 · Bet365 1,191 · Betfair 207 (O/U).

**Coverage is the number nobody had.** Post-cut, **5.7% of 1x2 and 7.9% of O/U modelled
selections have no price at any accessible book.** They are invisible to a reader at any
edge, for a reason that is about the Estonian tax authority and not about the reader.

> ⚠️ The **pre-cut** rows read 24–25% no-price, but that is a **feed artefact, not policy**:
> `Epicbet` only starts writing 2026-08-27 and `Unibet-Site` 2026-09-09, so the `restricted`
> arm before those dates is effectively a two-book set. Read the two periods separately;
> the before/after difference in this table is **not** the cut's doing.

## 2. Volume, price, edge, calibration, flat-stake ROI

`v10` gate stack (the picks bot). `gap` = realised win rate − mean `calibrated_prob`;
negative = overconfident. ROI is **flat €10, never Kelly**.

### Post-cut (2026-09-05 → 09-21, 18 days) — both evaluation instants

| market | lead | arm | n | picks/day | mean odds | cal prob | win rate | gap | ROI |
|---|---|---|---|---|---|---|---|---|---|
| 1x2 | −6h | as_was | 14 | 0.78 | 3.202 | 41.46% | 42.86% | +1.40pp | +32.9% |
| 1x2 | −6h | all_books | 15 | 0.83 | 3.263 | 41.41% | 40.00% | −1.41pp | +27.1% |
| 1x2 | −12h | as_was | 12 | 0.67 | 3.160 | 41.56% | 25.00% | −16.56pp | −27.3% |
| 1x2 | −12h | all_books | 14 | 0.78 | 3.242 | 41.38% | 21.43% | −19.95pp | −33.6% |
| O/U | −6h | as_was | 95 | 5.28 | 2.892 | 44.26% | 34.74% | −9.52pp | +2.0% |
| O/U | −6h | all_books | **144** | **8.00** | 2.893 | 44.66% | 31.94% | −12.72pp | −6.9% |
| O/U | −12h | as_was | 51 | 2.83 | 2.848 | 44.44% | 27.45% | −16.99pp | −21.8% |
| O/U | −12h | all_books | **90** | **5.00** | 2.880 | 44.51% | 32.22% | **−12.28pp** | **−7.0%** |

Read the two blocks against each other:

* **Volume is robust.** Opening the set adds +52% (6h) / +76% (12h) more O/U picks, and
  ~+1 pick per 18 days on 1x2. The 1x2 half is floor-bound, not price-bound.
* **Price is robust, and it does NOT arrive as a better price on the picks we already had.**
  The wider arm's mean odds are within 0.04 of the narrow arm's on O/U (2.892 vs 2.893).
  The uplift shows up as **more candidates crossing the floor**, which is exactly the
  mechanism #005 predicted for the volume collapse, now measured from the other direction.
* **The outcome comparison is NOT robust.** At −6h the wider set is 3.2pp worse calibrated
  and 8.9pp worse on ROI (O/U); at −12h it is 4.7pp *better* calibrated and 14.8pp better on
  ROI. Same fixtures, same books, same gates — only the hour of the price changed. Nothing
  here supports a claim in either direction.
* **Every 1x2 cell is n ≤ 15 over 18 days.** Do not quote them (gotcha #39: if the segment is
  too small, say so rather than falling back to a pool).

### Pre-cut (→ 09-04, 21 days), for completeness

At −6h: 1x2 `as_was` 28 picks / +17.2% ROI vs `all_books` 34 / +6.5%; O/U `as_was` 28 /
+13.8% vs `all_books` 33 / +5.9%. At −12h the same cells read 1x2 24 / +50.9% vs 27 / +34.6%
and O/U 12 / +80.1% vs 11 / +74.5%. The `restricted` arm in this period is the feed artefact
described above and should be ignored.

### O/U split on the calibrator regimes (the #065 confound, handled)

The O/U window contains a regime change that has nothing to do with books: the
domain-mismatched Platt curve shipped 2026-09-03 10:50 UTC and migration 335 deleted it
2026-09-13 20:51. Pooling across it would attribute the curve's effect to the book set.
At −6h:

| regime | arm | n | mean odds | gap | ROI |
|---|---|---|---|---|---|
| pre-curve (→09-03) | as_was | 19 | 2.324 | −6.09pp | +2.7% |
| | all_books | 19 | 2.327 | −6.05pp | +3.0% |
| domain-mismatch curve | as_was | 43 | 2.920 | +0.04pp | +33.4% |
| | all_books | 70 | 2.904 | −10.26pp | +2.1% |
| curve deleted (09-13→) | as_was | 61 | 2.879 | **−14.74pp** | −14.9% |
| | all_books | 88 | 2.891 | −13.95pp | −11.4% |

Two things survive the sensitivity check here:

**(a) The book set and the calibrator are separable.** In the pre-curve regime the two arms
are indistinguishable — 19 picks each, gap −6.09 vs −6.05. The arms only diverge once a curve
exists to push more candidates over the floor. The book set is a volume multiplier on
whatever the calibrator produces; it is not itself a source of miscalibration.

**(b) The worst calibration in the whole study is in the regime where the curve was
*deleted*, not where it was wrong** (−14.7pp). That is consistent with #065's conclusion that
the O/U calibrator bug is not what explains the collapse.

### The `placer` gate stack, for completeness

Same data, registry floors (edge 10%/8%, odds 2.80/1.80) + Pinnacle veto. It produces 4–10×
the volume and a far worse record because it has **no upper odds bound** — post-cut 1x2 mean
odds come out at **8.95** against the v10 bot's 3.20. Post-cut at −6h: 1x2 `as_was` n=52 /
ROI −48.4% vs `all_books` n=76 / −19.5%; O/U `as_was` n=121 / −0.1% vs `all_books` n=204 /
−8.5%. Reported because the task asked for the real placer floors. **It is not the PICKS
product**, and on a population of extreme longshots its outcome numbers are noise.

---

## 3. The selection-effect hypothesis — no evidence, in either direction

> *Claim under test:* with a thinner book set the best price is lower, so
> `edge = calibrated_prob − 1/odds` is smaller, so the candidates that still clear are
> disproportionately those where `calibrated_prob` is unusually high relative to the market
> — the model's overconfident tail. If true, the cut would *select for* the region where the
> model is most wrong and would partly explain the accuracy collapse, linking #005 to #065.

Test: partition the **all_books** picks into those that cleared under the restricted set too
(**BOTH** — the picks we actually made) and those that cleared **ONLY** with the wider set
(the picks the cut destroyed), then compare the calibration gap in each group. The hypothesis
predicts **Δgap(BOTH − ONLY) < 0** — the survivors should be the *worse*-calibrated half.

| profile | market | lead | BOTH n / gap | ONLY n / gap | **Δgap** | z |
|---|---|---|---|---|---|---|
| v10 | 1x2 | −6h | 28 / +1.57pp | 21 / −17.22pp | **+18.78pp** | +1.32 |
| v10 | 1x2 | −12h | 24 / −3.94pp | 17 / −11.06pp | **+7.12pp** | +0.46 |
| v10 | O/U | −6h | 105 / −9.43pp | 72 / −14.87pp | **+5.44pp** | +0.71 |
| v10 | O/U | −12h | 55 / −12.87pp | 46 / −2.80pp | **−10.06pp** | −1.01 |
| placer | 1x2 | −6h | 199 / −16.44pp | 138 / −17.27pp | **+0.83pp** | +0.15 |
| placer | 1x2 | −12h | 209 / −18.09pp | 96 / −10.92pp | **−7.17pp** | −1.21 |
| placer | O/U | −6h | 135 / −11.61pp | 109 / −13.62pp | **+2.01pp** | +0.31 |
| placer | O/U | −12h | 76 / −17.92pp | 60 / −3.88pp | **−14.04pp** | −1.63 |

**Verdict: no.** Four cells point one way, four the other; the only thing that changed between
them is which hour's price we priced off. Not one of the eight reaches |z| = 1.96. Had this
been run at a single lead — the natural, defensible choice of the median real pick lead — it
would have produced a confident "firm negative" that a 6-hour change of assumption reverses.
**The honest statement is that the effect is smaller than the noise from an arbitrary
methodological choice, and the hypothesis can be neither confirmed nor refuted on this
window.**

The mechanism's *first* step is equally unsupported. If the restriction selected the
overconfident tail, the surviving picks would sit further above the sharp line. They do not,
consistently: at −6h three of four cells have the wide-set-only picks sitting **further**
above Pinnacle (v10 O/U +10.19 vs +9.68; placer 1x2 +10.80 vs +10.59; placer O/U +9.74 vs
+9.63), while at −12h all four have BOTH further above (e.g. v10 O/U +10.89 vs +10.14). The
differences are ≤ 2pp everywhere — far too small to drive a 43% → 18% win-rate collapse.

**Why the mechanism is weaker than it looks.** The hypothesis assumes the book set moves
`edge` only through `1/odds`. It does not: a worse price also raises `implied_prob`, and
`calibrate_prob` shrinks the model toward that anchor (harder still above odds 3.0). A
thinner set therefore lowers the price *and* lowers `cal_prob` in the same pass, and the two
effects largely cancel inside `edge`. What the wider set actually buys is **more candidates
crossing the floor at the same price level** — §2's +52%/+76% at an unchanged mean price.
It is a volume lever, not a shift along the overconfidence axis.

**So #005 and #065 are not linked by this mechanism.** The book-set shrink explains volume;
there is no measurable accuracy channel. **#065 (a) — the 1x2 accuracy collapse the O/U
calibrator bug cannot explain — stays open, and this rules the book set out as its
explanation rather than in.**

---

## 4. What this counterfactual cannot tell us

Read before quoting anything above.

1. **It reconstructs which candidates would have CLEARED, not what the bot would have
   become.** Bankroll, Kelly stake, per-league exposure caps, daily caps and dedup all depend
   on the picks that came before, and every one would have evolved differently. That is why
   every ROI here is flat €10, and why "+52% more picks" must not be read as "+52% more
   stake".
2. **It applies the price / probability / edge / odds gates and the Pinnacle veto, not the
   whole gate stack.** The odds-movement veto, PIN-CROSS-DRIFT, CAL-SHARP-GATE and the news
   path are not modelled. None is book-set dependent, so they should bias both arms alike —
   but "should" is an assumption, not a measurement.
3. **One evaluation instant per fixture stands in for a pipeline that re-prices hourly — and
   §3 shows this is not a detail.** At −6h the `v10` arm reproduces the real bot's character
   well (mean odds 3.20–3.29 vs the real 3.44; mean `cal_prob` 41.3–41.5% vs the real 42.0%)
   but only ~40% of its 1x2 volume (42 reconstructed vs 107 actual), because the real bot
   gets many more shots at a moving price. O/U volume is closer (123 vs 104). The arms share
   the instant, so **price, coverage and volume comparisons are unaffected; every outcome
   comparison is not.**
4. **The `restricted` arm before 2026-08-27 is a feed gap, not a policy.** Epicbet and
   Unibet-Site did not exist as feeds yet.
5. **The universe is fixtures we modelled AND that settled.** A book set cannot be judged on
   fixtures nobody priced.
6. **Every arm in every cell is drawn from a model that was overconfident throughout the
   window.** When the underlying edge is negative, *any* volume lever looks bad at one lead
   and fine at another — which is most of what §2's instability is.

---

## 5. Recommendation

**Open the book set for PICKS. The evidence supports it on coverage and price — not on
performance, and it must not be sold as a fix for anything.**

Supported by the measurement:

* **Coverage, the strongest argument.** 5.7% of 1x2 and 7.9% of O/U modelled selections have
  no price at any book in the restricted set, so they are unpublishable at any edge. For a
  mostly non-Estonian Telegram audience that is a plain 👥 PICKS defect, and it is the one
  number here that is both large and stable.
* **Price: +2.1% / +2.2% post-cut**, confirming #005's +2.6% on an independent sample. Real,
  directly useful for a published break-even price, and small.
* **Volume: +52% to +76% more O/U picks**, at an identical mean price. Robust in sign and
  large in size — this is what the cut actually did to the published feed.

**Not** supported, and not to be claimed when this ships:

* that opening the set would have improved the published record — §2 says +/− depending on an
  arbitrary hour, i.e. unknown;
* that the book-set cut contributed to the accuracy collapse — §3 is "no evidence", and the
  mechanism is ruled out on first principles as well as on data;
* any expectation of 1x2 volume: post-cut it is +1 pick in 18 days on the v10 stack.

**Two cautions on sequencing.**

1. **The binding constraint is the model, not the book list.** 13 of 18 cells show a negative
   calibration gap. Opening the set on a model with a −10pp gap publishes more losing picks,
   faster. Do the split loader because coverage is a correctness issue, not because it will
   move the record.
2. **Do not re-open O/U volume on the back of this while the calibrator is deleted.** The
   worst calibration measured anywhere in this study (−14.7pp) is in the post-migration-335
   regime, and a 52–76% volume increase on a market with no curve is 52–76% more of it.

---

## Reproducing

```bash
PYTHONPATH=. python3 scripts/backtest_book_set_restriction.py \
    --start 2026-08-15 --end 2026-09-21 --json /tmp/rows6.json          # ~12 min
PYTHONPATH=. python3 scripts/backtest_book_set_restriction.py \
    --start 2026-08-15 --end 2026-09-21 --lead-hours 12 --json /tmp/rows12.json
PYTHONPATH=. python3 scripts/backtest_book_set_restriction.py \
    --from-json /tmp/rows6.json --profile v10                            # re-report, instant
```

**Always run at least two `--lead-hours` values.** §3 is the reason: at a single lead this
study produces a confident answer to the selection-effect question that the second lead
reverses.

Pinned by smoke `BOOK-SET-BACKTEST-REAL-GATES`, which fails if the script re-types a floor
instead of importing it from `coolbet_placer` / `bot_registry`, if a known-bad feed gets back
into the all-books arm, or if the shared outlier ceiling stops being applied before the arms
split. Mutation-verified on all three.

## Follow-up 2026-09-24 — #129: the outlier anchor was never widened

#005 re-opened the PICKS **price** basis to every publishable book, but the ODDS-OUTLIER-FILTER
**anchor** in the same function still read the Estonian set + Pinnacle. A fixture with no
Pinnacle and fewer than three Estonian books therefore had no anchor, and every candidate on
it was rejected however many real books priced it. Replay over 447 settled model-bot picks:
127 dropped purely for want of an anchor; with the publishable set 202 kept vs 154 (volume
restored, not an edge — the bot is losing either way since 09-06). Fixed in the PICKS path
only. On the day's fixtures the filter's rejections fell 844 → 47. 1xBet, the largest
newcomer to the median, was checked against Pinnacle first (median ratio 1.000, 47% above —
the same profile as Betano/Epicbet/Coolbet); Dafabet, the other book the row named, has not
been sent by API-Football since 2026-09-13.
