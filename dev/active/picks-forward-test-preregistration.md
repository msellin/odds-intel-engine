# Pre-registration — PICKS forward test (sharp-edge rule)

> ## ⚠️ v2 CLOSED · v3 REGISTERED 2026-09-14 — anchor-quality gate
>
> **v3** (`sharp_edge_v3_2026_09_14`) adds **`MAX_ANCHOR_OVERROUND = 0.04`** and
> changes nothing else. v2's n is **not** carried forward.
>
> **Why.** The rule's own header tells readers the picks are *"priced directly
> against the sharpest line in the market."* On a third of the slate that was
> false, and `load_candidates()` was **already computing `anchor_overround` on
> every leg and discarding it** — a number computed but never surfaced, on a
> public Telegram feed. Measured on this rule's own population (90d, Shin,
> aligned ≤60 min, the three bettable books; n=326, ROI −13.54% overall):
>
> | anchor overround | n | ROI |
> |---|---|---|
> | **<4%** sharp-grade | 72 | **−3.36%** |
> | 4–6% | 76 | −22.07% |
> | 6–9% | 58 | −16.00% |
> | **≥9%** goodwill quote | 120 | −13.06% ← **34.1% of the slate** |
>
> A paired live-Pinnacle test the same day (n=92, 39 leagues) confirms the ≥9%
> band is **not a feed artefact**: where our stored row says 9.28%, real Pinnacle
> says **9.26%**, with $200 limits behind it. Pinnacle genuinely charges 9%+
> there. An "edge" against a quote with no size behind it is two soft prices
> disagreeing.
>
> **⚠️ THIS IS AN HONESTY FIX, NOT AN ALPHA FIX.** Gating does not make the rule
> profitable — the retained band is still **−3.36% at n=72**. It stops us
> publishing an edge computed against a price that is not a line. **If the honest
> answer remains "no demonstrable edge at any anchor quality", that is what goes
> on `/performance` and in the Telegram feed.** Publishing a positive figure while
> the honest number is negative is the precise pattern CLAUDE.md exists to prevent.
>
> **Volume cost — the owner's call, stated up front.** The <4% band is ~22% of
> legs (72/326) and ~12% of fixtures (11/92). At `TOP_N = 8` this will often
> publish fewer than 8 picks a day. Loosening to 0.06 roughly doubles volume and
> admits the **worst**-measured band (−22.07%). Per CLAUDE.md, restricting picks
> to a narrow band is good for 🤖 OWN and bad for 👥 PICKS, and that trade-off is
> the owner's, not an implementation detail.
>
> **Symmetry.** The gate is applied to the candidate **pool**, not in `select()`,
> so the junk-anchor control arm is gated identically. Whatever test the live arm
> gets, every control arm gets.
>
> ### Day-one measurement, and it is the sharpest statement of the thesis yet
>
> First gated dry run, 2026-09-14, on the live board (109 legs ungated):
>
> | gate | pool legs | legs ≥ MIN_EDGE | published |
> |---|---|---|---|
> | 4% | 28 | **0** | 0 |
> | 6% | 69 | **0** | 0 |
> | 8% | 88 | **0** | 0 |
> | 10% | 105 | 2 | 2 |
> | none | 109 | 2 | 2 |
>
> Ungated anchor-overround distribution: min 3.26%, median 5.46%, max 12.53%;
> **<4% is 25.7% of legs**, so the pool is not the constraint.
>
> **Both of the day's qualifying picks were anchored on ≥9% goodwill quotes.**
> Not one leg with a genuinely sharp anchor cleared a 3% edge.
>
> This is the thesis of `docs/ANCHOR_IS_NOT_SHARP_2026_09_14.md` stated at its
> strongest, and measured rather than argued: **when the anchor is actually
> sharp, the 3% edge does not exist. A ≥3% edge appears only when the anchor is
> soft — because the "edge" IS the anchor's own margin.** The rule is therefore
> structurally incapable of producing picks against a sharp line, and every pick
> it has ever published was, by construction, priced against a quote with no size
> behind it.
>
> **"No picks today" is the correct and honest output**, and the publisher already
> treats it as a valid outcome. If that persists, the finding to publish is not a
> thinner feed — it is that this rule has no demonstrable edge, which is exactly
> what `/performance` and the Telegram feed should then say.

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
anchor overround (sum(1/anchor_odds) − 1)     ≤ 4%     [v3]  ← the anchor must BE a line
alignment: anchor quote and bet quote within 60 minutes
markets : 1x2, over_under_25
excluded books: Max, Avg, Betfair Exchange, BetWin, Betfred,
                Unibet-Kambi (38% phantom-high), Unibet/AF (33.1% phantom-high)
selection: top 8 per day by edge, capped on published_at::date UTC   [v4]
cadence  : every 30 minutes at :05/:35                                [v4]
lead     : kickoff between now+45 min and now+14 h                    [v4 — LOCKED]
```

### v4 — `sharp_edge_v4_2026_09_15`, registered 2026-09-15, before its first pick

**One change: CADENCE.** v1–v3 published a single batch at 10:00 UTC. The
candidate window is `now+45 min .. now+14 h`, so a 10:00 run can never see a
kickoff before ~10:45 and can never see 00:00–03:00 kickoffs **at all**.
Measured: **47% of qualifying legs were structurally unreachable**, and an
independent two-day replay had the 10:00 slot catching **5 of 16**.

This is v4 rather than an edit to v3 because it changes **which bets are
selected**, not merely when they are looked at. Per this document's own rule that
starts a new test with a new start date. **The cost is zero: v2 and v3 published
nothing at all**, so no accumulated n is discarded — which is exactly why it is
being done now rather than later.

**Three things had to be true before a 30-minute cadence was safe, and are:**

1. **`claim`-before-send.** A qualifying leg re-qualifies in a median of **6**
   consecutive runs (mean 6.8, max 13). Sending before recording would have put
   the same pick in front of 62 subscribers ~6 times.
2. **A DB-backed daily cap.** `select()` caps per *call*; across 48 calls a day
   that is not a cap. `daily_room()` counts today's live rows and fails **closed**.
3. **Price persistence measured.** **94%** of qualifying prices still clear the
   floor at the same book after 30 minutes (**62%** after 60). A 30-minute
   cadence therefore publishes prices a reader can still get; a 60-minute one
   would not.

**The junk-anchor control runs at the same cadence and under the same daily
room**, and is seeded per `(date, run)` rather than by a constant — under one run
a day a fixed seed was merely reproducible, under 48 runs it makes every draw
identical and the control stops being an independent sample.

**Known boundary quirk, stated rather than discovered later:** the cap counts
`published_at::date` in UTC, not kickoff date, because with a 14 h lookahead one
run spans two kickoff dates. So 00:00–03:00 kickoffs are only ever in window from
~10:00–13:00 the previous day and consume the **previous** day's allowance.

**`MIN_LEAD_MIN` and `LOOKAHEAD_H` are now LOCKED constants.** They were live
rule parameters that existed only in the script and appeared in neither this
document nor the smoke test, so a schedule change could move them silently.

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
