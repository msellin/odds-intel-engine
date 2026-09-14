# Pre-registration — PICKS forward test (sharp-edge rule)

> ## ⚠️ v1 CLOSED at n=8 · v2 REGISTERED 2026-09-15
>
> **v1** (`sharp_edge_v1_2026_09_14`) published 8 picks on 2026-09-14 and is
> **closed at that n**. It is not extended and its picks are not pooled with v2.
>
> **Why.** v1 omitted a filter production has carried since August
> (`ODDS-OUTLIER-FILTER-2026-08-18`): a cap on how far the book price may exceed
> the anchor. AF's Bet365 quotes run ~26.6% above contemporaneous Pinnacle —
> stale or shell prices nobody can take. **Six of v1's eight picks sat above 20%
> over anchor; the top one at +35.7%.**
>
> Measured on the time-aligned backtest, ROI by book/anchor price-ratio band:
>
> | ratio band | n | ROI |
> |---|---|---|
> | 0–10% | 202 | **+11.75%** |
> | 10–20% | 763 | +6.24% |
> | **20–35%** | 225 | **−12.13%** |
> | 35%+ | 44 | +7.32% (n too small) |
>
> The loss sits in **20–35%**, *below* the existing 35% production filter —
> exactly what the `BET365-EXECUTION-AUDIT` note predicted in August ("the
> ~20-30% band still leaks through and generates -20% ROI picks"). Capping at
> 20% moves the rule from **+3.83% to +7.39%** (n 1,234 → 965) and costs no
> volume on the day it was found (still 8 picks).
>
> **v2 adds `MAX_RATIO = 0.20` and changes NOTHING else.** Starting a new test
> rather than quietly tightening a running one is the entire point of this
> document — carrying v1's n forward would be the discipline failure it exists
> to prevent.


**Registered 2026-09-14, before the first post.** Locked. Any change to the
rule, the stopping criterion or the success criterion after the first pick is
published invalidates the test and starts a new one with a new start date.

## Why this exists

The O/U Platt calibrator manufactured ~8–9pp of published "edge" for months and
nobody caught it, because there was no pre-committed criterion that could fail.
Every figure was computed after the fact, and after-the-fact figures always find
a cut that works. This document exists so that this rule can be **wrong** in a
way we are forced to notice.

## The rule (LOCKED)

```
anchor  = Shin de-vig of the Pinnacle triple
edge    = P_shin × best_book_price − 1        ≥ 3%     [EXPECTED ROI, not P − 1/odds]
odds    ≤ 4.0
price ratio: book_price / anchor_price − 1    ≤ 20%    [v2]
alignment: anchor quote and bet quote within 60 minutes
markets : 1x2, over_under_25
excluded books: Max, Avg, Betfair Exchange, BetWin, Betfred,
                Unibet-Kambi (38% phantom-high), Unibet/AF (33.1% phantom-high)
selection: top 8 per day by edge
```

**On the word "edge".** This rule's `edge` is **expected ROI** (`P × odds − 1`).
The rest of the codebase — `pick_generator.py:239`, `pick_triggers.min_odds`,
`SYSTEM_MAP` §1 — uses `P − 1/odds`, a **probability difference**. They are
different quantities (`ROI_edge = prob_edge × odds`) and a 3% floor on one admits
roughly twice the picks of a 3% floor on the other. This test is pre-registered
on the **ROI** form. The public label should read "Expected return", not "Edge",
so the two never collide in a reader's head or ours.

Price basis is **best across all books** — the owner's ruling of 2026-09-14: for
PICKS a price that was capturable somewhere at some point is acceptable, because
we have no Estonian readers. EMTA legality constrains OWN only. A price no book
ever offered still fails this bar, which is why the phantom feeds are excluded.

## The prior — stated honestly, before we start

Backtest 2026-06-01 → 2026-09-13, time-aligned:

| alignment | n | ROI | 95% CI |
|---|---|---|---|
| none (**inflated — this is what a staleness artefact looks like**) | 4,339 | +8.47% | [+4.7, +12.2] |
| ≤180 min | 1,953 | +5.56% | [−0.1, +11.3] |
| ≤60 min (**the rule**) | 1,661 | +5.54% | [−0.7, +11.7] |
| ≤15 min | 1,611 | +5.55% | [−0.7, +11.9] |
| ≤5 min | 1,577 | +5.46% | [−0.9, +11.8] |

**The honest position is NO DEMONSTRATED EDGE.** Point estimate +5.5%, CI
includes zero. We are not publishing a claim; we are running a test.

What the backtest *does* establish, and what it does not:
* ✅ **Anti-selection control passes.** edge≥3% → +11.7%; edge 0..3% → +0.6%;
  edge −3%..0 → −3.1%; edge < −3% → −6.8% (n=49,159). Monotone across the whole
  range. A pure price-basis artefact would make the rejected bucket profitable.
* ✅ **Phantom-book control passes.** Odds-capped, the edge is the same size on
  our own verified scrapes (+8.79%) as on all books (+8.47%).
* ✅ **Stable under tightening.** 5.56 → 5.54 → 5.55 → 5.46 as alignment goes
  180 → 60 → 15 → 5 min. A staleness artefact keeps bleeding; this does not.
* ❌ **Not significant.** CI includes zero at every alignment.
* ❌ **No out-of-sample period.** The whole window was used to choose the
  odds cap and the alignment tolerance. That is exactly what this forward test
  is for.

## Success / failure criteria (LOCKED)

Evaluated on **settled picks published to @oddsintelpicks from 2026-09-14**,
priced at `odds_at_pick`, flat stake, no discretionary exclusions.

| Checkpoint | Criterion | Action |
|---|---|---|
| **n = 200** | CLV (margin-corrected, `EV ≈ (1+clv)/(1+m) − 1`, m ≈ 7.6%) < −2% | **STOP.** Negative CLV at n=200 is decisive; ROI is not yet. |
| **n = 400** | margin-corrected CLV < 0 | **STOP.** |
| **n = 800** | ROI 95% CI entirely below 0 | **STOP.** |
| **n = 800** | ROI 95% CI entirely above 0 | **PROMOTE** — publish the number, claim the record. |
| **n = 800** | CI straddles 0 | **CONTINUE to n = 1,600**, then decide. Do not re-cut the rule. |

**The primary instrument is margin-corrected CLV, not ROI.** Per-bet return
variance is ~1.32; confirming a true +3% ROI at 80% power needs ≈15,600 bets.
CLV captures ~95% of everything extractable from single-bet P&L (verified: a
10-bin oracle achieves R²=0.0057 against CLV's 0.0054). ROI is the *secondary*
check and will not resolve on any realistic timescale.

### Implementation note — `m` is per-row, not 7.6% (2026-09-14, before any pick settled)

The criteria above are stated on the CORRECTED number and quote `m ≈ 7.6%`. That
figure was a *measured average across 18,759 bets of a different mix*, written as
an illustration of the size of the correction — not as a constant to substitute
into the estimator. The settler
(`workers/jobs/settlement.py::closing_book_margin`) therefore computes `m` **per
row**: the closing book's own overround on that fixture and market, from that
book's full market at close. When that book's full market is unavailable the
column stays NULL, and the row is simply not counted — a NULL is honest, a
guessed margin biases the n=200 and n=400 stopping rules in a direction nobody
can see.

This matters more than it sounds. Median closing 1X2 margins measured 2026-09-14
over three days of finished fixtures:

| book | median closing margin |
|---|---|
| Coolbet | 7.8% |
| Epicbet | 8.0% |
| Pinnacle | 9.1% |
| Betfair | 10.4% |
| Unibet-Site | 10.5% |
| **Bet365** | **11.3%** |

Bet365 is where most of day one's picks landed. Using 7.6% there would overstate
EV by ~3.4pp **on the decision variable itself** — larger than the −2% threshold
it is being compared against. The thresholds (−2% at n=200, 0 at n=400) are
UNCHANGED; only the estimator of `m` is stated precisely. Recorded here rather
than done silently, per the lock.

**Break-even CLV is the closing book's margin, not zero.** `clv` in
`settlement.py:613` is a raw price ratio with no de-vig. At m = 7.6%, a +7.6%
raw CLV is 0.0% EV. Every criterion above is stated on the CORRECTED number.

## What would make me stop early, outside the schedule

* Any pick published at a price that did not exist at any book (phantom). One
  confirmed instance stops the test pending a provenance fix.
* Median anchor↔bet alignment on published picks drifting above 60 min — that
  is the staleness failure returning.
* The `ACCESSIBLE-BOOKMAKERS-FEEDS-ALIVE` guard firing, i.e. a book in the
  pricing set going dark.

## Negative control (runs alongside, not published)

The same rule — floor, odds cap, alignment, top-8 — with the anchor replaced by
a **junk anchor** (a shuffled Pinnacle line from a different fixture). Expected:
loses roughly the vig. If the junk arm makes money, the harness is broken and the
live arm means nothing.

**The junk anchor must change WHICH BETS ARE SELECTED.** Selection is the only
thing the anchor does in this rule, so an arm that re-labels the live picks with
a different `p_sharp` is not a control — it settles to identical outcomes by
construction.

> **Day one was degenerate (JUNK-ARM-DEGENERATE-2026-09-14, fixed same day).**
> `junk_anchor_arm()` shipped taking the eight live picks and overwriting their
> `p_sharp`, so all eight junk rows of 2026-09-14 duplicate a live row on
> (match_id, market, selection, odds, bookmaker). They are re-stamped
> `rule_version = 'sharp_edge_v1_2026_09_14+DEGENERATE_JUNK_DAY1'` by migration
> 343 and **must be excluded from any control analysis**. They are not deleted —
> removing rows from a pre-registered ledger is worse than annotating them. The
> publisher now re-runs the whole rule over a pool of shuffled anchors; on the
> same day's data that selects a set with zero overlap with the live picks.
> Pinned by smoke `PICKS-FORWARD-TEST-JUNK-ARM-SELECTS`.

## Recording

Picks are recorded at publish time with: match, market, selection, price, book,
edge, `P_shin`, the anchor quote and its timestamp, and the bet quote timestamp.
**The alignment gap is stored per pick** — it is the quantity that invalidated
the first version of this backtest and it must be auditable per row, not
recomputed later.
