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
selection: every leg clearing the edge floor; NO daily cap          [v4 amended]
           one selection per (match, market) — highest edge wins    [v4 amended]
           runaway breaker at 60/day is a FAULT guard, not a cap    [v4 amended]
cadence  : every 30 minutes at :05/:35                                [v4]
lead     : kickoff between now+45 min and now+14 h                    [v4 — LOCKED]
```

### v4 — `sharp_edge_v4_2026_09_15`, registered 2026-09-15, before its first pick

**AMENDED 2026-09-15, still before its first pick (v4 has published n=0).**
Two changes, both owner decisions taken on measurement:

* **The daily cap is removed.** `TOP_N = 8` was pre-registered and carried
  forward unexamined. Qualifying legs per day over the intact window were
  10, 10, 7, 4, **32**, 11, 2 — mean 10.9 — and the cap bound on 4 of 7 days,
  publishing **45 of 76 (59%)** and discarding 31 legs that had cleared the bar.
  Under a 30-minute cadence a daily cap is not a quality filter at all: a live
  feed cannot know the day's best in advance, so the cap fills with whichever
  legs qualify EARLIEST. It was dropping 41% of picks on a rule unrelated to
  their quality. What remains is a **runaway breaker at 60/day** — a guard
  against a data fault flooding the channel, which should never bind (measured
  max 32).
* **One selection per (match, market).** Four matches in seven days published
  two selections of the same 1x2 — once inside a single run. That reads as
  covering both ways.

Amending rather than starting v5 is legitimate **only** because v4 has published
nothing; the rule in this document is that a change AFTER the first pick
invalidates the test.

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

> **NOTE 2026-09-23 — a new book entered the universe, not a rule change.**
> Tonybet (our own sweep, `workers/automation/tonybet_feed.py`, #101) was verified
> against tonybet.com (14 of 15 1X2 prices identical; favourites agree with
> Epicbet/Coolbet 95–97%) and is not a phantom feed, so under the rule below it
> is part of "all books" from 2026-09-23 ~19:00 UTC, in both the live arm (price
> source) and the consensus arm (price source + consensus member). The v2
> price-ratio cap (≤ 20% over the anchor) and the consensus 8% edge ceiling are
> the guards against a mis-paired fixture. Rule versions are unchanged.

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

> ⚠️ **AMENDED 2026-09-25 ([[#156]]) — the n=200 and n=400 rows above are superseded** by
> *AMENDMENT 1* at the end of this document (instrument = sharp-anchor CLV, stop condition relative
> to the junk-anchor control). The text above is kept exactly as registered. The n=800 rows are unchanged.

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


## A second arm, and why this registration is unaffected (2026-09-22, [[#068]])

`arm='consensus_anchor'` now publishes alongside `arm='live'`. **Nothing in this
pre-registration changes**, and that is the whole reason it was built as a second
arm rather than an edit.

**What prompted it.** `MAX_ANCHOR_OVERROUND = 0.04` — registered in v3 to ensure
the anchor "must actually BE a sharp line" — admitted **0 of 173** Pinnacle-priced
markets on 2026-09-22. The live arm published 294 picks on 09-19 and zero on
09-22. That is not a fault: the gate has always admitted only ~10-17% of fixtures
and it admits them on big-liquidity weekend cards, so the channel is
weekend-shaped by construction.

**Why the gate was not relaxed.** Loosening a registered parameter mid-test
forfeits the registration, which is the entire basis of the honesty claim on
/picks. The registered arm keeps its rule, its constants and its start date.

**What the second arm changes.** Exactly one variable: the source of the
fair-value probability. Each book that prices the complete market is de-vigged on
its own and the resulting probabilities are averaged (≥5 books required). Every
downstream guard is shared and unchanged — the 3% edge floor, the 60-minute
alignment window, `MAX_ODDS`, `MAX_RATIO`, the lead time and the lookahead.

**One deliberate asymmetry.** The consensus arm has an 8% edge CEILING; the live
arm has none and must not gain one. The ceiling is [[#007]]'s finding applied
here: `edge = p·odds − 1` is maximised by a WRONG price, so the largest apparent
edges in any anchored feed are its data faults. On the first run, 5 of 15
qualifying legs cleared 8% against a 7-11 book consensus — including +14.7% on a
draw at 3.94 against ten books, which is a broken price, not an opportunity.

**Why a consensus is a defensible anchor**, measured rather than assumed: scoring
each book's own de-vigged 1x2 probabilities against realised results over 45 days,
n=11,419 matches, AF-"Pinnacle" alone gives log-loss 0.98401 while a consensus
*excluding* Pinnacle gives 0.98339 (t=+1.76). Our single-book anchor is
statistically indistinguishable from an average of the other books, and its median
closing overround is 10.24% against those books' 7.95%. This arm does not lower
the bar; it stops using a ruler that turned out not to be one.

**The two arms are separable** in the ledger by `arm`, by `rule_version` and by
`anchor_bookmaker` (`Pinnacle` vs `consensus:N`). They are deduplicated against
each other so no match/market can carry both, and the live arm claims first, so a
pre-registered pick always wins the tie. Pinned by smoke `PICKS-CONSENSUS-ARM`.


## AMENDMENT 1 — 2026-09-25 ([[#156]], owner-approved) — the n=200 / n=400 stopping rule

**Recorded before the live arm reached n=200** (it stood at 88 settled on
`sharp_edge_v4_2026_09_15`). Nothing above this section has been edited except the one
dated pointer under the criteria table. The rule, its constants, the arms and the n=800
ROI criteria are unchanged; only the instrument and the stop condition at the n=200 and
n=400 checkpoints change.

### Why the original instrument has to go

The registered instrument is `clv_margin_corrected` — the pick's odds against **the same
soft book's own close**, margin-corrected (`workers/jobs/settlement.py`, `get_closing_odds`
and the margin block after it). This rule selects a leg *because* that book misprices it
against the sharp line. A soft line that is wrong and is never corrected closes where it
opened, and its own close then scores the pick at roughly **minus the book's margin, by
construction** — whether or not the pick was good. The measure is therefore blind to the
one thing the test exists to measure:

| measure (v4, settled) | live − junk-anchor control |
|---|---|
| own-book margin-corrected CLV | **−0.4pp [−1.9, +1.2]** in the 2026-09-25 audit; **+0.4pp, one-sided p = 0.24** in the checkpoint script at amendment time — indistinguishable either way |
| sharp-anchor CLV | **+4.6pp (1X2), +3.7pp (O/U)** in the audit; stratified **+4.3pp, 97.5% lower bound +3.3pp** in the script (1X2 +4.6pp, O/U +3.4pp) |

An instrument that cannot separate the pre-registered rule from a randomly-anchored copy
of itself cannot fail the rule for the right reason either: at n=200 the original "< −2%"
test would stop or continue on the soft books' margins, not on the rule. The junk control
reads −2.2% / −2.8% on the sharp close — it loses roughly the vig, exactly as registered
("Expected: loses roughly the vig"), which is also the evidence that the sharp-anchor
harness is sound.

### Against the lock — said plainly

The registration says any change to the stopping criterion after the first pick
"invalidates the test and starts a new one". This amendment is that kind of change, and
it is made anyway, openly, by the owner's decision (2026-09-25), for two reasons: the
SELECTION rule, its constants, the arms, the start date and the n are untouched, so the
picks being judged are exactly the registered ones; and the criterion being replaced
could not fail for the right reason (above). Two costs are recorded rather than hidden:
**(1)** the new criterion was chosen AFTER seeing interim data (n=88, where the live arm
already leads the control on the sharp close), which biases toward continuation — the
counterweight is that the new bar is stricter than the old one (a significant margin over
a control, not merely "not clearly negative"); **(2)** the ROI rules at n=800, which are
the only claim-making criteria, are not touched, so no amendment here can manufacture a
published claim. The v4 test is therefore NOT restarted; a reader can re-run the original
criterion from the same output at any time.

### The amended rule (pre-stated — do not tune)

* **Instrument — SHARP-ANCHOR CLV**, per settled (won/lost) leg, from `leg_clv_sharp`
  (`workers/jobs/clv_sharp.py`): `clv_sharp` (odds × Shin-de-vigged *fresh* Pinnacle close
  − 1) where `status = 'ok'`; otherwise `clv_cons` (the ≥5-book consensus close,
  `workers/utils/anchor.py`, Pinnacle and the pick's own book excluded) where
  `cons_status = 'ok'`. The source (Pinnacle / consensus) is recorded per leg and
  reported. **3–4-book thin consensus (`clv_cons_thin`) is excluded** — never pooled. A leg
  with neither is unscored and reported as such. `|clv| > 1` is excluded as a data fault
  (the `bot_scoreboard` guard).
* **Populations:** arm `live` vs arm `junk_anchor` on the **same `rule_version`** (the live
  arm's current one). The `+DEGENERATE_JUNK_DAY1` rows carry a different `rule_version`
  and never enter.
* **Statistic:** the market-stratified difference in mean sharp-anchor CLV,
  **Δ = Σₘ wₘ (mean_liveₘ − mean_controlₘ)**, with wₘ = the live arm's share of scored legs
  in market m. Stratified because the arms carry different market mixes (control ~61% 1X2,
  live ~71%) and the markets sit at different CLV levels.
* **Test:** one-sided bootstrap, **B = 10,000**, resampling legs with replacement within
  each arm × market cell, **seed 20260925**; p = share of replicates with Δ* ≤ 0.
* **Checkpoints** are unchanged in timing — the live arm's SETTLED count reaching **n = 200**
  and **n = 400**. At each: **CONTINUE only if p < 0.025** (one-sided; equivalently the 97.5%
  lower bound of Δ is above zero); **otherwise STOP.** A rule with no edge survives both looks
  with probability ≤ 0.025. This is deliberately stricter than the original, which continued
  unless the point estimate was clearly negative.
* **The original own-book figure keeps being computed and published beside it** — on the
  checkpoint output for both arms and on /performance as a labelled secondary ("vs the
  book's own close"). It decides nothing.
* **n = 800 / 1,600 ROI criteria: UNCHANGED.**

**The calculation is code, not prose:** `python3 -m scripts.picks_forward_test_checkpoint`
(read-only) prints both measures for both arms, per market and stratified, the source mix,
and the verdict under this amendment. Pinned by smoke `PICKS-FORWARD-TEST-AMENDED-CHECKPOINT`.

### What this does to /performance

The published CLV for every forward-test bot becomes the sharp-anchor figure with its n
and source mix ("vs sharp close +2.4% · 64 picks · 64 Pinnacle / 0 consensus"), with the
own-book figure shown beside it as the secondary. Each row is scored on its CURRENT
`rule_version` only (as `bot_scoreboard` does); earlier versions stay in the ledger and on
the row as "earlier rule" history, never pooled.

### 2026-09-25 note ([[#158]]) — consensus v1 picks re-classified under v2 for /performance only

The consensus arm's v2 (`consensus_edge_v2_2026_09_24`) differs from v1 in ONE gate: the edge
must clear 3% under ALL of Shin / additive / power. Owner decision: every SENT v1 pick was
re-checked against v2 using **pick-time data only** — its consensus rebuilt from
`odds_snapshots` as of `published_at` with the publisher's own functions, never its result
(`scripts/recheck_forward_test_picks.py`; verdicts in the private table `pick_rule_recheck`,
frozen once written). Result: **B 3 pass / 2 fail, C 31 / 3, D (sent before the re-tier) 14 / 2**;
the sharp arm's 8 v1 picks all fail v4 (anchor overround > 4%). Where the rebuild could not
reproduce the Shin probability the publisher recorded (intraday snapshots are thinned after the
fact), a pick passes only if its worst-method edge clears 3% by more than the discrepancy —
3 picks failed that way as `rebuild_mismatch_inconclusive`.

**This changes the /performance BOT RECORD only.** The pre-registered test's own counts are
NOT changed: `picks_forward_test.rule_version` is never rewritten, `picks_forward_test_summary`
still groups on the rule as published, and the stop-rule checkpoints keep counting v2-PUBLISHED
picks only. A re-checked pick is marked as such on the bot's detail view.

## TWIN ARMS — 2026-09-25 ([[#161]], owner-approved) — two recorded, never-published hypothesis arms

**Registered before either arm's first pick.** Nothing above this section is edited: the
live arm, the consensus arm, the junk control, their constants, their n and their
checkpoints are untouched. These are two NEW arms, each with its own `arm` and
`rule_version` in `picks_forward_test`, each testing ONE extra gate that the [[#156]] audit
pointed to. Both are hypotheses born on small cells (n 7–57), so they are judged forward
only — nothing measured before today counts toward them.

### Common to both arms

* **Recorded, never published.** Never sent to Telegram, never on /picks, never on
  /performance, never in the pre-registered live counts or the runaway breaker
  (`PUBLISHED_ARMS` does not contain them; every public view filters an explicit arm
  allow-list). Status **EXPERIMENTAL** under [[#155]] — admins only. Like the junk control.
* **Same cadence, same candidate pool, same room.** They run inside the same
  `job_publish_picks_forward_test` pass (:05/:35), over the exact pool the parent arm
  selects from, truncated at the same runaway room the parent was given.
* **Own-arm dedupe.** One selection per (match, market), seeded from the twin's OWN ledger
  rows only. A twin is by construction a subset of its parent's qualifying legs, so seeding
  it from the published arms would suppress every twin pick. Consequence, stated up front:
  the parent is deduped against the OTHER published arm and the twin is not, so a few twin
  picks may have no parent row.
* **Settled and scored by the same arm-agnostic code** — `settle_picks_forward_test` and
  `workers/jobs/clv_sharp.py` (`leg_clv_sharp`) have no branch on `arm`.
* **The evidence for the extra gate is stored per row** in `picks_forward_test.twin_gate`
  (jsonb, NULL on every other arm).

### (A) `arm = 'sharp_own_book_aligned'` · `rule_version = 'sharp_edge_v4_ownbook_align5_2026_09_25'`

**Rule:** the live v4 rule in every gate (3% floor, odds ≤ 4.0, 60-min alignment,
price ratio ≤ 0.20, anchor overround ≤ 4%, 45-min lead, 14 h lookahead, the same excluded
books, one selection per market, no daily cap) **PLUS**: when the leg's book is one of our
own direct books — **Coolbet, Unibet-Site, Epicbet, Tonybet** — the book's quote and the
Pinnacle anchor quote must be **≤ 5 minutes apart** (`alignment_gap_minutes`). API-Football
books arrive in the same fetch as Pinnacle (gap 0) and are unaffected. The gate applies to
the leg AS v4 SELECTED IT (the best aligned price): a failing leg is dropped, never
re-routed to the next-best book, so every twin pick is exactly the (book, price) v4 took.

**What it tests:** that our own books' stale quotes are what drags the sharp arm there —
the audit read sharp picks at our books at **+3.9% vs the sharp close when ≤ 5 min apart
and −0.6% at 5–60 min**. Expected: twin CLV above the live arm's.

### (B) `arm = 'consensus_pin_confirmed'` · `rule_version = 'consensus_edge_v2_pinconf_2026_09_25'`

**Rule:** consensus v2 in every gate (≥ 5-book consensus, 3% under Shin AND additive AND
power, 8% ceiling, odds ≤ 4.0, the shared alignment / ratio / lead / lookahead, grades
B / C / D labelled and recorded exactly as the parent) **PLUS**: where a fresh tight
Pinnacle anchor exists at the moment of the pass — `workers/utils/anchor.py`
`pinnacle_tight`: a complete Pinnacle set from one fetch, ≤ 60 min old, overround ≤ 4% —
the leg must ALSO have **EV ≥ 0% against that Shin-de-vigged Pinnacle price**. Where no
tight Pinnacle exists the leg passes unchanged, so on those fixtures the twin equals its
parent. `twin_gate` records `{pin_tight, pin_p, pin_ev}` per row.

**What it tests:** that consensus picks Pinnacle disagrees with are the drag — the audit
read consensus picks at our books at **−2.1% vs the sharp close**. Expected: twin CLV
above the consensus arm's, the gain concentrated on Pinnacle-priced fixtures.

### Readout (pre-stated — do not tune)

* **Instrument and method: AMENDMENT 1 verbatim** — sharp-anchor CLV (`clv_sharp`, else
  ≥ 5-book `clv_cons`; thin excluded; |clv| > 1 excluded), market-stratified Δ weighted by
  the TWIN's market shares, one-sided bootstrap B = 10,000 within arm × market cells, seed
  20260925. Each arm is read on its own current `rule_version`.
* **Two comparisons per twin:** twin − parent (A vs `live`, B vs `consensus_anchor`) and
  twin − `junk_anchor` (the v4 control).
* **Checkpoints:** the twin's SETTLED (won/lost) count reaching **n = 50** (interim —
  reported, decides nothing) and **n = 100** (the readout).
* **At n = 100 a twin is SUPPORTED only if** twin − junk has p < 0.025 **and** twin − parent
  has p < **0.0125** (0.025 Bonferroni-split across the two twins — two gates tested, two
  chances to find one by luck). Otherwise NOT SUPPORTED.
* **Known bias, stated:** twin legs are mostly a subset of the parent's, so the two samples
  are positively correlated; the bootstrap resamples them as independent, which overstates
  the variance of the difference. That makes SUPPORTED harder to reach, not easier.
* **What a result licenses.** SUPPORTED lets the owner adopt the gate into the parent as a
  NEW `rule_version` — a new test with a new start date, per this document's own rule —
  never retroactively. NOT SUPPORTED closes the twin at its n; its rows stay in the ledger.
  Neither result touches the live or consensus arms' own checkpoints.

### Expected volume — measured on the ledger before the first twin pick (pick-time data only)

* **(A)** of the live arm's 69 v4 picks in the 7 days to 2026-09-25, **35 would have
  been recorded**. All 34 drops are at our own books with gaps of 11–37 min: 27 of the
  arm's 29 Epicbet picks (mostly at 21–23 min — Epicbet's feed cadence, not an occasional
  stale quote), 4 of 8 Coolbet, 3 of 4 Unibet-Site. So (A) roughly halves the arm's
  volume and removes nearly all of its Epicbet picks.
* **(B)** of the consensus arm's 89 picks in the 4 days to 2026-09-25, **none had a tight
  Pinnacle anchor at pick time** (28 had no Pinnacle set within 60 min; 61 had one at
  4.2–15.4% overround — `pinnacle_wide`). Pinnacle tightens toward kickoff and these
  picks are made hours out, so **as specified, (B) will rarely differ from its parent** and
  will reach n = 100 with few or no legs where its gate bound. Recorded here, before the
  first pick, so a null readout is not later mistaken for a finding about Pinnacle
  confirmation: `twin_gate.pin_tight` counts how often the gate was even testable. A
  looser variant (e.g. any fresh Pinnacle) would be a DIFFERENT twin with its own arm and
  registration — the owner's call, not an edit to this one.

**The calculation is code:** `python3 -m scripts.picks_forward_test_checkpoint --twins`
(read-only). Pinned by smoke `FORWARD-TEST-TWIN-ARMS`.

## DEVIATION — 2026-09-25 ([[#164]], owner decision "VIP FIRST") — some picks publish at kickoff

**What changes.** A pick of a PUBLISHED arm (`live`, `consensus_anchor`) that a VIP bot holds (a VIP /
hide_pending bot has a pending pick on the same match + market + selection) or that is in VIP's range at its
price and claim time (1X2 NEW+ EV ≥ 5%; O/U 5–15% above Pinnacle with ≥ 12 h to kickoff) is **still claimed,
recorded and counted** exactly as before, but it is **not sent to the channel** and is hidden from /picks until
kickoff (`picks_forward_test.held_back_until` = kickoff, `held_back_reason`; `workers/utils/vip_guard.py`).
At kickoff it appears on /picks and in the record; nothing is sent after kickoff.

**What does NOT change.** The selection rule, the pricing, `daily_room()`, the junk control, the twin arms, the
stopping rule (Amendment 1) and n: a held-back pick counts in n and in every checkpoint like any other. The test
measures the RULE's picks, and every one of them is still in the ledger at its claim-time price.

**Why this is a deviation, stated plainly.** The pre-registration said qualifying picks are *published* before
kickoff. Held-back picks are recorded before kickoff (the claim timestamp is the proof they were not chosen
after the fact) but published at kickoff, so a reader could not have acted on them. Any readout that asks
"what could a subscriber have taken" must exclude them (`held_back_reason IS NOT NULL`); the rule's own result
includes them.

**Retroactive (2026-09-25).** Pending published-arm picks that broke the rule were held back from that moment;
ones already sent to the channel, or already settled, keep their record and carry `vip_rule_breach = true`
(never deleted, never unsent). Counts at the time: 13 pending picks held back (consensus 10 = B 1 / C 6 / D 3,
live 3); 10 of them had already been sent and are flagged `vip_rule_breach`; the 3 D picks were never sent. No
settled published-arm pick broke the rule. Smoke `VIP-FIRST-HOLD-BACK`.

## DEVIATION — 2026-09-26 ([[#174]], owner decision) — Telegram carries only TESTING picks at EV ≥ 5%

**What changes.** Only the TELEGRAM send. Every published-arm bot is TESTING, and the public channel now
carries a TESTING pick only when its EV ≥ 5% (`edge` = `fair_prob` (p_sharp) × odds − 1; the ONE rule
`workers/utils/bot_status.public_channel_skip_reason`, enforced in `pick_sender.send_pick`). A pick below that
is **still claimed, recorded, counted and shown on /picks** exactly as before; its `pick_sends` row reads
`skipped · testing_below_ev5` and `picks_forward_test.telegram_message_id` stays NULL.

**What does NOT change.** The selection rule, the pricing, `daily_room()`, the junk control, the twin arms, the
stopping rule, n and the public list `/picks` (every TESTING / BETA / CALIBRATED pick). The test measures the
RULE's picks; the channel is now a subset of them.

**Readout consequence.** "What a Telegram subscriber received" = rows with `telegram_message_id IS NOT NULL`;
"what a /picks reader could have taken" = rows not held back (`held_back_reason IS NULL`). The rule's own result
includes every claimed row. Smoke `PUBLIC-TELEGRAM-ONE-RULE-EV5`.
