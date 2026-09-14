# In-play strategy candidates — 22 triggers, backtested

Written 2026-09-14 overnight. Companion to
`docs/INPLAY_BOOK_COMPARISON_2026_09_14.md` (which books, what prices) — this one
asks **what to bet**.

> ## ⚠️ CORRECTION, same night — B1 DID NOT SURVIVE. Read this before the rest.
>
> The "+9.0% ROI at minute 40" result below was a **narrow-bucket artefact** and is
> **withdrawn**. Widening the entry to a window a bot could actually use (38'–47'
> rather than a lucky 5-minute bucket) collapses it to **+2.3% ROI, CI [−2.8, +6.9]
> — includes zero**, on a *larger* sample (n=1,086).
>
> The neighbouring windows show a spike, not a plateau, which is the signature of noise:
>
> | entry window | n | ROI | 95% CI |
> |---|---|---|---|
> | 28'–37' | 2,650 | −4.9% | [−8.0, −1.7] |
> | 33'–42' | 1,699 | −1.1% | [−4.9, +3.0] |
> | **38'–47'** | 1,086 | **+2.3%** | **[−2.8, +6.9]** |
> | 43'–52' | 650 | −1.0% | [−7.0, +4.4] |
> | 48'–57' | 206 | −5.0% | [−14.5, +5.7] |
>
> Nor does it survive any other cut: first half of the sample +0.9% [−5.1,+8.2],
> second half +3.5% [−3.7,+10.4], non-top-5 +2.0% [−3.1,+7.3], strongest pre-match
> filter +3.1% [−2.8,+8.8]. Every interval contains zero.
>
> **One positive cell out of ~50 tested is exactly what multiple testing produces on
> its own.** A real effect does not switch off at minute 45 and back on at nothing.
> **There is currently NO validated positive in-play strategy in this document.**
> Parts 3 and 4 explain *why*, and narrow where a future one could live: the in-play
> long side carries ~14% relative margin against ~3.8% on the favourite, and the
> short side is near-break-even. **A future signal must be expressed through a short
> price or it cannot clear the vig.**
>
> What survives is the *negative* findings, which are the valuable part: they are
> monotone across many buckets and large samples, and they tell you what not to build.
>
> **Headline: the owner's seed idea is REFUTED, and its mirror does not replace it.**
> Backing OVER in a goalless high-total game loses at every entry minute and loses
> *more* the longer you wait (−2.9% ROI at 10', −24.4% at 40'). Backing the UNDER in
> the same spot is the side with the edge (+7.7% ROI at 40', CI [+1.2, +14.1]).

## Why any of this is testable without a sharp anchor

An in-play bet has two halves and **we already own one outright**:

| half | source | state |
|---|---|---|
| did the event happen? | `live_match_snapshots.minute` + `score` (fully populated, 2.2M rows / 35,493 matches, 45s cadence) + final score | ✅ clean |
| what price was offered? | in-play odds | ⚠️ only AF's, ~40s stale |

**In-play bets settle on the final score, which we have.** So realised ROI is ground
truth; a sharp anchor would only reduce variance. This is a correction to the earlier
claim in `INPLAY_BOOK_COMPARISON` §1 that in-play edge is "not measurable today" — it
is measurable, it just needs more samples than a CLV-based test would.

**The staleness workaround.** AF's live prices are a median 40s old. That is fatal
around a goal and *nearly harmless in a quiet state*: a game that has been 0-0 for
half an hour has a 40-second-old over/under price that is still approximately right.
So every edge number below is computed **only on quiet triggers** (goalless / level),
never post-goal. 714,827 priced snapshots qualify.

---

## Part 1 — hit rates and break-even prices (no odds involved)

Sample: **35,442 matches**, 132 distinct dates. "High-total" = pre-match over-2.5
priced ≤ 1.85, taken from **non-live** rows only. `/day` = how often the trigger fires.

### The owner's idea and its neighbours — goalless in a game built for goals

| entry min | n | P(over 2.5) | break-even | P(over 1.5) | BE | P(over 0.5) | BE |
|---|---|---|---|---|---|---|---|
| 10' | 11,585 | 54.4% | 1.84 | 76.7% | 1.30 | 93.3% | 1.07 |
| 20' | 9,088 | 46.6% | 2.15 | 70.8% | 1.41 | 91.0% | 1.10 |
| 30' | 7,016 | 39.2% | 2.55 | 64.6% | 1.55 | 88.4% | 1.13 |
| 40' | 5,337 | 30.9% | 3.24 | 56.7% | 1.76 | 84.9% | 1.18 |
| 50' | 3,655 | 19.5% | 5.13 | 44.0% | 2.27 | 77.9% | 1.28 |
| 60' | 2,693 | 12.4% | 8.06 | 33.7% | 2.97 | 70.1% | 1.43 |

**Read this before concluding anything.** The odds do get better as you wait — that
part of the intuition is right. But the true probability falls just as fast. Waiting
buys a longer price on a proportionally less likely event; it is not free value. The
whole question is whether the market's price falls *faster or slower* than that column.
Part 2 answers it.

### Other triggers worth a price (selected; full set in the scratch harness)

| trigger | entry | n | /day | hit | BE |
|---|---|---|---|---|---|
| level score → match ends a **draw** | 80' | 9,989 | 75.7 | 58.4% | 1.71 |
| leading by 1 → **holds the win** | 80' | 13,648 | 103.4 | 80.0% | 1.25 |
| 0-0 → **under 2.5** (any game) | 60' | 7,048 | 53.4 | 88.0% | 1.14 |
| 1-1 → a **3rd goal** arrives | 60' | 4,027 | 30.5 | 69.8% | 1.43 |
| 0-0 at HT → **a goal in 2H** | 45' | 10,134 | 76.8 | 75.1% | 1.33 |
| 1-0 at 60' → **both teams score** | 60' | 10,290 | 78.0 | 41.9% | 2.39 |
| favourite trailing → still **wins** | 30' | 1,321 | 10.0 | 30.1% | 3.33 |
| favourite trailing → **win or draw** | 30' | 1,321 | 10.0 | 54.4% | 1.84 |
| underdog leading → **holds on** | 60' | 2,520 | 19.1 | 63.5% | 1.58 |
| 2-goal lead → **no more goals** | 80' | 11,645 | 88.2 | 52.1% | 1.92 |
| 0-0 at 75' → **0-0 final** | 75' | 4,972 | 37.7 | 48.5% | 2.06 |
| home trailing at HT → **wins 2H** | 45' | 9,419 | 71.4 | 32.5% | 3.08 |
| big favourite level at HT → **wins 2H** | 45' | 2,766 | 21.0 | 54.1% | 1.85 |

Volume is not the constraint here: most of these fire 20–100× a day, so any of them
reaches a decidable sample in weeks, not the ~15 years the corners bot needed.

---

## Part 2 — the edge test: hit rate vs the price actually shown

`edge pp` = empirical hit rate − market-implied probability. **The vig is the
baseline, not zero**: AF's live overround is ~6.5%, so roughly **−3pp per side is
what an efficient market looks like**. Only an edge well beyond that magnitude is signal.

### A1 — goalless high-total game → BACK OVER 2.5 ❌ **REFUTED**

| entry | n | hit | mkt price | implied | edge | ROI | 95% CI |
|---|---|---|---|---|---|---|---|
| 10' | 3,532 | 51.6% | 1.91 | 54.2% | −2.6pp | −2.9% | [−6.5, +0.4] |
| 20' | 2,836 | 45.2% | 2.15 | 49.5% | −4.3pp | −4.8% | [−9.5, −0.1] |
| 30' | 1,662 | 41.2% | 2.31 | 45.5% | −4.3pp | −4.6% | [−12.3, +2.8] |
| 35' | 1,057 | 38.6% | 2.31 | 44.5% | −5.9pp | −11.0% | [−17.8, −2.3] |
| **40'** | 538 | 33.1% | 2.31 | 44.1% | **−11.1pp** | **−24.4%** | [−33.3, −15.3] |
| 45' | 332 | 35.8% | 2.29 | 45.1% | −9.3pp | −21.1% | [−31.9, −9.4] |

Negative at **every** entry minute, monotonically worsening, and by 35'+ far beyond
the vig. Reproduced on the unfiltered "any game" version (−26.8% at 40'). A monotone
trend across eight buckets is not a multiple-testing artefact.

**Why it fails.** Notice the market price *stops lengthening* after ~30' — it sits at
2.29–2.31 from 30' to 45' while the true probability keeps falling (41% → 33%). The
book stops paying you to wait, but the game keeps running out of time. **You are not
"waiting for better odds" — after about half an hour you are waiting for the same
odds on a worse bet.**

### B1 — the same spot, BACK THE UNDER ❌ **WITHDRAWN (see the correction at the top)**

| entry | n | hit | mkt price | implied | edge | ROI | 95% CI |
|---|---|---|---|---|---|---|---|
| 20' | 2,836 | 54.8% | 1.79 | 56.9% | −2.1pp | −3.0% | [−6.3, +0.4] |
| 30' | 1,659 | 58.9% | 1.66 | 61.1% | −2.2pp | −3.3% | [−6.8, +0.7] |
| 35' | 1,056 | 61.5% | 1.63 | 62.2% | −0.8pp | −0.9% | [−6.2, +3.3] |
| **40'** | 538 | 66.9% | 1.62 | 62.7% | **+4.2pp** | **+7.7%** | **[+1.2, +14.1]** |
| 45' | 332 | 64.2% | 1.65 | 61.7% | +2.5pp | +4.5% | [−4.2, +13.1] |

Unfiltered version agrees and is slightly stronger: **+5.1pp, ROI +9.0%, CI
[+3.5, +14.1]** at 40' on n=727.

The gradient is the encouraging part — edge climbs steadily (−3.9 → −2.8 → −1.4 →
−0.4 → +5.1) rather than one cell jumping out of noise. It is the exact complement of
why A1 fails: the book stops marking the total down around the half-hour, which
overprices the over and therefore **underprices the under**.

⚠️ **WITHDRAWN.** The gradient looked encouraging, but it does not survive a realistic
entry window (38'–47' → +2.3%, CI [−2.8, +6.9]) or any time/league/threshold split.
It was one significant cell out of ~50 tested. Do not build it.

### D5 — "the draw is always mispriced in-play" ❌ **folk wisdom, refuted**

Edge is −1.7 to −3.0pp at **every** minute from 10' to 85', on n = 3,400–8,800 per
bucket. That is the vig and nothing else. The draw is one of the better-priced things
on the board.

---

---

## Part 3 — the structural finding, and why every long-side idea lost

Four more triggers were tested, this time with **wide windows from the outset**
(≥15 minutes — something a bot could actually be told to do) and the neighbouring
windows always shown, so a spike could not be mistaken for an effect.

| strategy | windows tested | result |
|---|---|---|
| back the **trailing short favourite** | 15'–74', five windows | ❌ edge −3.3 to −4.1pp at **every** window; ROI −6.2% → −21.4%; CI excludes zero in 3 of 5 |
| **lay a leading long-shot** (back the field) | 20'–79', five windows | ❌ edge −2.9 to −4.6pp; ROI −12.9% → −22.4%; **CI excludes zero in all five** |
| back the **level short favourite** | 30'–84', four windows | ➖ edge −0.8 to −1.1pp; ROI ≈ −1%; CI includes zero throughout |

The "market over-reacts to a goal against the favourite" folklore is **wrong** —
if anything the comeback price is too short. Laying leading underdogs is worse.

### Why — and this is the useful part

Backing a *level* favourite loses only ~1% on a board carrying ~7.8% overround.
If the margin were spread proportionally that bet should cost 3–4%. So the vig is
not where you would assume. Measured on **7,539 level-score snapshots, minute 30–84**:

| outcome | implied | actual | vig charged | **relative cost** |
|---|---|---|---|---|
| favourite | 47.6% | 45.8% | +1.8pp | **3.8%** |
| draw | 34.4% | 32.1% | +2.3pp | **6.7%** |
| underdog | 25.8% | 22.0% | +3.7pp | **14.3%** |

**The long side carries nearly 4× the relative margin of the short side.**

That reframes every negative above. They did not fail on football — they failed on
*where they were shopping*. "Back the trailing favourite" buys at 2.9–7.5, "lay the
dog" buys at 3.6–10.8; both are paying the long-side tax before a ball is kicked.
A strategy that has to back a long price in-play starts roughly **14% behind**; one
that backs a short price starts about **4% behind**.

This converges with `INPLAY_BOOK_COMPARISON` from a completely different dataset,
which found all cross-book dispersion sits on the long side (draw, away DNB, far
totals) while favourites are priced near-identically everywhere. **Two independent
measurements, one conclusion: the in-play long side is where the books take their
money.**

### The design rule this implies

Any future in-play candidate should be **short-side by construction** — back things
priced roughly 1.10–2.20 — or it must clear a ~14% margin before it can win. Every
strategy refuted tonight violated that rule, which in hindsight is the single best
predictor of which ones failed.

---

## Part 4 — testing the rule: short-side only

Part 3 predicted that short-side bets should be near-break-even and long-side bets
should bleed ~14%. Tested directly, prices restricted to 1.05–2.30, wide windows:

| strategy | window | n | market | edge | ROI | 95% CI |
|---|---|---|---|---|---|---|
| **0-0 → back under 2.5** | 35'–54' | 1,815 | 1.63 | −0.1pp | **+0.4%** | [−3.2, +4.0] |
| 0-0 → back under 2.5 | 45'–64' | 647 | 1.62 | −0.1pp | −0.4% | [−6.4, +5.8] |
| **2-goal leader → holds on** | 70'–89' | 752 | 1.11 | +0.7pp | **+1.1%** | [−1.4, +3.4] |
| 2-goal leader → holds on | 60'–79' | 1,312 | 1.12 | +0.1pp | +0.5% | [−1.4, +2.4] |
| 1-goal leader → holds on | 55'–94', 4 windows | ~7,500 ea | 1.21–1.37 | −2.5 to −2.7pp | ≈ −3% | excludes 0 |
| 2+ goals scored → over 3.5 | 30'–69', 3 windows | ~4,000 ea | 1.72–1.79 | −2.1 to −4.5pp | −3.5% → −8.2% | excludes 0 |

**The rule is confirmed.** On the short side the house edge very nearly disappears:
backing the under at 1.63 costs **0.4% or less**, and a two-goal leader at 1.11 is
statistically indistinguishable from break-even across three windows. Compare that
with the same book charging **14% relative** on the underdog side.

**But nothing is positive.** Every interval that contains a gain also contains zero.
The best cell of the night (+1.1% at odds 1.11) would need enormous volume to matter
and is exactly the sort of price books suspend and limit.

### What this actually buys us

It changes the size of edge we would need to find:

* a real **2–3% signal deployed short-side would survive**, because the vig there is
  ~0–1%;
* the same signal deployed long-side would be **wiped out**, because it starts ~14%
  behind.

So the search is not hopeless — it is *constrained*. Any in-play bot worth building
must express its view through a short price. That is a far more useful conclusion
than a lucky cell, and it is the thing to carry into the Epicbet data.

⚠️ All of Part 3 and Part 4 are measured against AF's aggregate, whose overround
(7.8% on 1x2) is **wider than Epicbet's** (measured 6.4–6.6% on O/U the same day).
The *shape* of the finding — long side dearer than short side — should hold at a real
book, but the absolute costs at Epicbet should be lower. Re-run on the collected data.

---

## Part 5 — is AF's live price a usable proxy for Epicbet's? (1x2: yes)

This decides what the 2.2M-row history is worth. If AF's live price, once de-vigged,
tracks the book we can actually bet, then every edge number above carries. If not,
the history is only good for *state* and all pricing work waits on new collection.

**307 concurrent AF/Epicbet 1x2 snapshots, 7 fixtures, fetched in parallel threads so
neither feed is systematically later.**

| side | median gap | mean | p90 abs gap | max abs gap |
|---|---|---|---|---|
| home | **−0.01pp** | −0.54pp | 3.11pp | 3.52pp |
| draw | **+0.42pp** | +0.59pp | 1.73pp | 5.37pp |
| away | **−0.04pp** | −0.06pp | 1.38pp | 2.25pp |

**After de-vigging, AF's live 1x2 is an essentially unbiased proxy for Epicbet's** —
median gaps are within half a point of zero on all three outcomes. The noise is real
but modest: 18% of snapshots differ by ≥3pp on P(home), which is what a ~40s-stale
feed should look like.

The margins differ as expected: **AF 6.51% overround vs Epicbet 5.56%**.

### What that means for every number in Parts 2–4

They are measured against the *wider* book, so they are **conservative**. Proportionally,
the margin difference is worth about **+0.8pp of ROI** in Epicbet's favour on 1x2.
Applied to Part 4: "1-goal leader holds" goes from −3.0% to roughly −2.2% (still
clearly negative); "2-goal leader holds" from +1.1% to roughly +1.9% (still an interval
containing zero).

**No conclusion in this document flips.** The refutations get slightly stronger in
relative terms and no negative becomes a positive.

### Scope — do not over-extend this

* It is **1x2 only**. Most candidate strategies here are **over/under**, and O/U
  fidelity is *not* established by this test. A dedicated O/U comparison is running
  overnight (`<scratch>/oufid.jsonl`) — until it reports, the O/U-based ROI figures
  should not be margin-adjusted.
* n=307 on 7 fixtures in a single evening, all top/mid-tier. The Unibet and Kambi
  precedents in this repo are a warning: both looked fine until checked against the
  book's own site, and both were 33–38% divergent.

**Provisional read: the 2.2M-row history is usable for 1x2 strategy discovery.** That
is a much better position than "the prices in it are unusable", which is where this
document started.

## Ranked next actions

1. **Finish the fidelity work (Part 5).** 1x2 is done and AF proxies Epicbet well.
   **O/U is the gap that matters**, because most candidates here are O/U — the
   overnight `oufid` run answers it. Only then margin-adjust the O/U numbers.
2. ~~Pre-register B1.~~ **Withdrawn — it failed its robustness check the same night.**
   The lesson is procedural and worth keeping: *always widen the trigger to the window
   a bot would really use before believing a cell.* A 5-minute bucket that a bot would
   never restrict itself to is not a strategy, it is a slice of noise.
3. **Do not build A1.** It is refuted with a monotone trend on large samples.
4. **Collect the state we are missing.** `shots_on_target` is 1.2% filled and
   `model_xg` 0%. Every trigger above is (minute, score) only — shots and xG are the
   obvious next axis and we store neither.
5. **Constrain the search to the short side.** Part 3 shows the long side carries
   ~14% relative margin against ~4% on the favourite. Screening candidates for
   "does this back a price under ~2.20?" would have killed most of tonight's losers
   before they were tested.

## Provenance

| artefact | where |
|---|---|
| strategy harness (22 triggers) | `<scratch>/strategies.py`, `strategies2.py` |
| edge test | `<scratch>/edge2.py` → `edge2.out` |
| 35,442-match state matrix | `<scratch>/extract.py` → `matches.pkl` |
| collector | `workers/jobs/inplay_epicbet_collector.py` |

**Nothing here was written to the database and no bot placed anything.**
