# Pre-registration — PICKS forward test (sharp-edge rule)

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
edge    = P_shin × best_book_price − 1        ≥ 3%
odds    ≤ 4.0
alignment: anchor quote and bet quote within 60 minutes
markets : 1x2, over_under_25
excluded books: Max, Avg, Betfair Exchange, BetWin, Betfred,
                Unibet-Kambi (38% phantom-high), Unibet/AF (33.1% phantom-high)
selection: top 8 per day by edge
```

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

The same rule with the anchor replaced by a **junk anchor** (a shuffled
Pinnacle line from a different fixture). Expected: loses roughly the vig. If the
junk arm makes money, the harness is broken and the live arm means nothing.

## Recording

Picks are recorded at publish time with: match, market, selection, price, book,
edge, `P_shin`, the anchor quote and its timestamp, and the bet quote timestamp.
**The alignment gap is stored per pick** — it is the quantity that invalidated
the first version of this backtest and it must be auditable per row, not
recomputed later.
