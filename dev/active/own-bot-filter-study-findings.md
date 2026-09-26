Parent row: [[#182]] OWN-BOT DESIGN RESEARCH (PRIORITY_QUEUE.md)

# #182 — Which pick-time filters make a per-book sharp trigger an OWN bot?

**2026-09-26 · read-only research, nothing in production changed.** Direction: 🤖 OWN.
Script: `scripts/analysis/own_bot_filter_study.py` (the pre-registration is its docstring, written before
the first run; outputs in `data/models/_research/own182/`, gitignored). Runtime ≈ 1 min.

## 1. Answer in one paragraph

**One filter carries the value: take the trigger only in the last 3 hours before kickoff.** Three of the
19 pre-registered cells survive Holm and the holdout: c1 (< 3 h to kickoff), a1 (the other books confirm
Pinnacle's price) and e1 (Pinnacle's price is ≤ 17 min old). Cross-cutting them afterwards (exploratory,
outside the family) shows that **a1 and e1 are worth ≈ 0 outside the last 3 h**. The timing is the signal,
and inside that window a1 drops a small set of bad legs. The proposed OWN rule is: per-book trigger **AND**
< 3 h to kickoff **AND** consensus-confirmed. On the independent close it scores **+5.7% [+3.9, +7.6]**
(n 134; discovery +5.6%, holdout +5.8%), at about 5–8 picks a day per book.

**This is not yet a money signal.** Flat ROI on the same legs is **−18% ± 10**: 52 wins against the 61.2
that the consensus close predicts. Most of the CLV comes from picks made < 1 h before kickoff, where
whether the price could actually be placed has not been checked. It is a rule to **paper-test forward**,
pre-registered, and to grade against the exchange close once that has history.

## 2. Data

| step | legs |
|---|---|
| settled pre-match legs, sharp-registry bots, bookmaker ∈ {Coolbet, Unibet-Site, Epicbet, Tonybet}, 1x2 + O/U 2.5 | 1,174 |
| de-duplicated on (match, market, selection, book), earliest pick kept | 1,010 |
| − data-fault (quarantine / DQ findings) | 8 |
| − no own-book snapshot ≤ 180 min before pick (cannot price at pick time) | 256 |
| − no ≥ 5-book consensus close | 284 |
| **graded** | **462** (1x2 402 legs / 306 matches; O/U 60) |

Where the 1x2 legs come from: tight 175, Unibet trigger 75, Coolbet trigger 69, trigger_1x2_sharp 54,
forward-test sharp arm 29. The legs **at** each book are Coolbet 122, Unibet-Site 131, Epicbet 147 and
Tonybet 2.

Split at 2026-09-20 05:45 UTC (discovery 201 / holdout 201). The outcome-blind thresholds are: book count
tertiles 8 / 11, Pinnacle age median 16.7 min, own-quote age median 10.1 min.

## 3. Pre-registered family — 1x2, pooled over books, Holm over 19 cells

CLV_ind = pick-time snapshot price × consensus-close probability − 1. The consensus close uses ≥ 5 books
and excludes Pinnacle and the leg's own book. The 95% CI is a bootstrap over matches. p is one-sided and
computed on the discovery half. "Move" = CLV_ind minus the edge against the other books at pick time, i.e.
how far the consensus moved between pick and close.

| cell | n | CLV_ind [95% CI] | disc | Holm p | hold | ROI | Pin-close CLV | move | verdict |
|---|---|---|---|---|---|---|---|---|---|
| 0 baseline | 402 | +1.83 [+0.5, +3.2] | +3.03 | 0.002 | +0.63 | −4.1 | +3.5 | −6.3 | fails holdout |
| **a1 confirmed** | 253 | **+3.15 [+1.7, +4.6]** | +3.43 | 0.007 | +2.96 | −3.2 | +3.5 | −6.6 | **SURVIVES** |
| a2 unconfirmed | 53 | −2.82 [−7.7, +1.9] | +2.92 | 1 | −5.30 | −39 | +5.8 | −5.0 | untested (16 disc) |
| b1 odds < 2.0 | 117 | +2.49 [+0.7, +4.5] | +1.96 | 0.36 | +3.20 | +6.9 | +2.5 | | — |
| b2 2.0–3.5 | 206 | −0.03 [−1.7, +1.5] | +1.46 | 0.47 | −1.54 | −6.8 | +0.8 | | — |
| b3 > 3.5 | 79 | +5.70 [+1.2, +10.2] | +10.86 | 1 | +2.55 | −13 | +11.9 | | untested (30 disc matches < 30) |
| **c1 < 3 h** | 190 | **+4.92 [+3.3, +6.6]** | +5.23 | <0.001 | +4.66 | −10.3 | +7.1 | −4.6 | **SURVIVES** |
| c2 3–12 h | 161 | −0.87 [−3.1, +1.5] | +1.77 | 1 | −3.29 | −1.1 | +0.4 | −9.5 | — |
| c3 > 12 h | 51 | −1.16 [−4.7, +2.6] | +0.21 | 1 | −4.15 | +9.3 | −0.4 | | untested |
| d1 books < 8 | 117 | +2.23 [−0.5, +5.1] | +3.80 | 1 | −4.57 | +15 | +3.5 | | untested (22 hold) |
| d2 books 8–10 | 151 | +2.55 [+0.5, +4.6] | +3.61 | 0.023 | +1.79 | −7.1 | +4.4 | | fails holdout (< 50% of disc) |
| d3 books ≥ 11 | 134 | +0.68 [−1.4, +2.7] | +0.47 | 1 | +0.78 | −17 | +2.3 | | — |
| **e1 Pinnacle age ≤ 17 m** | 155 | **+3.39 [+1.5, +5.4]** | +4.68 | 0.004 | +2.46 | +5.0 | +5.1 | −4.9 | **SURVIVES** |
| e2 Pinnacle age > 17 m | 155 | +0.90 [−1.1, +2.9] | +1.93 | 0.36 | +0.30 | −24 | +2.8 | | — |
| e3 own quote ≤ 10 m | 201 | +1.33 [−0.1, +2.8] | +1.66 | 0.34 | +1.00 | −1.8 | +2.7 | | — |
| e4 own quote > 10 m | 201 | +2.33 [+0.3, +4.4] | +4.41 | 0.002 | +0.27 | −6.4 | +4.3 | | fails holdout |
| f1 edge < 3% | 25 | −3.26 [−5.2, −1.2] | −2.34 | 1 | −3.62 | +17 | −3.8 | | untested |
| f2 edge 3–6% | 104 | −0.16 [−1.2, +0.9] | +0.18 | 1 | −0.54 | +6.1 | 0.0 | | — |
| f3 edge ≥ 6% | 181 | +4.21 [+1.8, +6.6] | +7.13 | <0.001 | +2.80 | −22 | +7.3 | | fails holdout (2.80 < 3.57) |

Measured against the pre-registered expectation:
* The baseline came out at +1.8%, as expected. It is positive at full sample but fails the holdout.
* a2 < a1, as expected. a2 is too thin to test.
* The odds band and liquidity showed no clean effect, as expected.
* **Timing, not expected to matter, is the strongest effect.**
* Bigger edges read better on CLV but fail the holdout, and their ROI is −22%. That is the §67 pattern:
  large "edges" include feed faults.

## 4. Exploratory decomposition (post-hoc, outside the family, no p-values)

| subset | n | CLV_ind [95% CI] | disc / hold | ROI | per day (Cool / Uni / Epic / Tony) |
|---|---|---|---|---|---|
| **c1 ∧ a1** | 134 | **+5.74 [+3.9, +7.6]** | +5.57 / +5.84 | −18.1 | 5.6 / 8.1 / 5.0 / 0.1 |
| c1 ∧ ¬a1 | 26 | +1.56 [−4.4, +6.6] | +6.21 / −0.15 | −65 | 1.3 / 1.9 / 0.1 / 0 |
| a1 ∧ ¬c1 | 119 | +0.23 [−1.8, +2.5] | +1.11 / −0.38 | +13.5 | 3.7 / 4.0 / 9.6 / 0.1 |
| e1 ∧ ¬c1 | 65 | +0.53 [−2.8, +4.1] | +3.41 / −2.10 | +32.6 | 2.9 / 2.4 / 5.3 / 0.1 |
| a1 ∧ c1 ∧ e1 | 77 | +5.32 [+3.1, +7.8] | +5.47 / +5.23 | −12.6 | 2.6 / 4.3 / 3.1 / 0.1 |

a1 and e1 add nothing outside the 3-hour window. Adding e1 to c1 ∧ a1 halves the volume and does not raise
CLV, so it is not recommended.

**Why timing matters.** The trigger selects on Pinnacle noise. The mean edge against Pinnacle at pick
time is +10.4%. Pinnacle's own fair probability for the backed side then falls 5.7% (relative) by the
close. Between pick and close, the other books' fair probability falls 7.3% (relative) for picks made more
than 3 h out, and only 2.3% for picks made in the last 75 min. Early picks have time to revert; late picks
do not.

**Where the late-pick CLV sits.** Inside c1, legs picked < 1 h out score +8.6% and legs picked 1–3 h out
score +1.8%.

## 5. Proposed OWN rule per book (for a forward paper test — not real money)

**Rule:**
* the book's 1x2 price is ≥ 3% above Pinnacle's Shin fair price (the existing per-book trigger);
* **AND** the pick is < 3 h before kickoff;
* **AND** at least 3 non-Estonian, non-Pinnacle books have a complete set within 180 min, and their median
  fair probability for the backed side is ≥ 0.97 × Pinnacle's.

The per-book numbers below are descriptive: small n, and the rule is a post-hoc combination of two
surviving cells.

| book | CLV_ind c1∧a1 (n) | picks / day (7 d to 09-25) | recommendation |
|---|---|---|---|
| Coolbet | +5.25 [+2.2, +8.4] (41) | ~5.6 | **paper-test first.** Executor exists. Best real-money candidate once the forward test confirms it. |
| Unibet-Site | +7.50 [+4.7, +10.6] (59) | ~8.1 | Paper-test. Real money is blocked: there is no Unibet account/bet-history reader (#172 §6). |
| Epicbet | +3.70 [+1.7, +5.8] (33) | ~5.0 | Paper-test only. There is no executor and no per-book Epicbet bot. These legs come from best-of-books bots (tight, forward-test), so a real per-book trigger could behave differently. |
| Tonybet | 1 graded leg | ~0.1 | **No bot.** There is no evidence, and the bot would almost never fire: Tonybet's price is never above its own fair price, and its margin is 8–12% (§87). |

Volume is taken from how the existing bots fired, de-duplicated across bots. A dedicated bot that scans
more often in the last 3 hours could fire more. The existing triggers mostly run on the :05/:35 refresh.

**What the forward test needs.** The per-leg SD is about 7–8%. Detecting +3% at 80% power needs
≈ 60–80 matches per book, which is about 2–3 weeks at Coolbet. The test must also log **ROI and hit rate
against consensus-implied**, because those are what currently disagree.

## 6. Red flags (why this is not a money rule yet)

1. **ROI disagrees with CLV.** c1 ∧ a1 has ROI −18.1% ± 9.8 SE and 52 wins against 61.2 expected from the
   consensus close (≈ −1.6 SD). One reading is noise at n 134. The other is that the near-kickoff CLV
   measures a price that no longer existed.
2. **Placeability near kickoff is untested.** The stale study found only 33–50% of Unibet/Coolbet stale
   prices still on the board at the next sweep (§8). A scraped quote 10 min old, a few minutes before
   kickoff, may already be gone or suspended. That would be phantom CLV, and it would concentrate exactly in
   c1.
3. **a1 is partly favoured by construction.** The judge is the close of largely the same books that a1
   reads at pick time (stated in the pre-registration). Its value appears only inside c1, which weakens
   this concern but does not remove it.
4. **The exchange close is still the only fully independent grader** (#121 §9). Nothing here uses it.

## 7. Data problems found

* **54% of de-duplicated settled legs cannot be graded at the pick-time price.** Of those, 256 (26% of all
  legs) have no own-book snapshot within 180 min before the pick. These are mostly pre-2026-09-11 legs:
  sparse scraping plus retention (§59). Another 284 have no ≥ 5-book consensus close.
* **96 of 402 graded 1x2 legs have no pick-time consensus or Pinnacle set.** The 7-day retention has
  pruned the other books' intraday rows. These are concentrated in the discovery half, which is why a1 and
  e1 have smaller discovery n.
* **The recorded edge does not always reproduce.** 25 legs (8% of those with a pick-time Pinnacle set)
  have an edge against Pinnacle **under 3%** when recomputed at the snapshot price, although every trigger
  requires ≥ 3%. The bot evaluated a different price or Pinnacle set from what `odds_snapshots` holds, which
  is consistent with the pre-W2.1 price rewrites (§85/#179). Those legs score −3.3%.
* **Tonybet has almost no legs** (2 graded 1x2), and O/U has only 60 graded legs across all books:
  Coolbet −3.3% (26), Unibet-Site +4.2% (22), Epicbet +3.0% (9). Nothing can be said about O/U.
* **Data-fault exclusions were small:** 8 legs.

## 8. Reproduce

    python3 scripts/analysis/own_bot_filter_study.py

The pre-registered table does not depend on the exploratory addendum: the addendum runs last, so the
seeded bootstrap is identical. Leg-level features are in `data/models/_research/own182/legs.jsonl`.
