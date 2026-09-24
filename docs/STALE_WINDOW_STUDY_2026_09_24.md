# Stale windows at the Estonian books after a sharp move — [[#121]] Phase 2 (i)

**Status:** pre-registration committed BEFORE any outcome was computed (2026-09-24).
Results are appended below the line "RESULTS" in a later commit. Script:
`scripts/stale_window_study.py` (read-only).

Direction: **🤖 OWN** (a placement trigger we could stake on) and **👥 PICKS** (a
fresh-move pick is a publishable, explainable pick) — but only if it survives an
independent close.

## 1. Question

When Pinnacle's price moves sharply, and a book we can bet at (Coolbet, Epicbet,
Unibet-Site, Tonybet) has not re-priced, is the stale price worth taking? And is the
*fresh move* the thing that carries the value — or does any standing gap of the same
size do just as well?

Feasibility numbers from the brainstorm (8 d, 1X2, not results): after a ≥3% Pinnacle
move Coolbet had not moved in 81% of cases, Unibet-Site 68%, Epicbet 43%; ~5/day the
stale price sat above Pinnacle's new fair odds (median edge 3.6% / 2.0% / 6.3%) and ~93%
stayed positive vs **Pinnacle's** close. That last number is the trap this study exists
to get past (§67): the same AF-Pinnacle feed triggered the bet and graded it, so a
Pinnacle glitch that persists to the close looks like CLV.

## 2. Hypotheses

* **H1 (primary).** A stale Estonian price after a ≥3% sharp move that sits ≥X above
  the move's new fair price has **positive CLV against an INDEPENDENT close**.
* **H2 (primary).** That CLV is **higher than a CONTROL**: the same books and markets,
  a standing gap of the same size, **without** a recent sharp move.
* Secondary: the same two statements graded against **Pinnacle's own close**
  (circular by construction; reported so the gap between the two graders is visible).

## 3. Definitions (fixed before running)

**Window.** Matches that kicked off in the last 7 days and are `finished` (full
intraday history exists only for matches finished < 7 days ago, §59; own books are
exempt for 60 d but Pinnacle and the consensus books are not). Pre-kickoff, non-live
rows only. Markets: `1x2` and `over_under_25` with `handicap_line` NULL or 2.5 (drops
the 1xBet 0.25-line rows, [[#121]] Phase 1).

**A complete set** (§62): per book, the legs of one fetch — rows sorted by time, a new
set starts when a row is > 120 s after the set's first row (Coolbet stamps each leg
with its own microsecond timestamp, so exact-timestamp pivots are wrong); a set counts
only if every side is present.

**Sharp move.** Two consecutive Pinnacle sets (t0, t1), ≤ 90 min apart, both before
kickoff. Shin de-vig both (`workers.model.devig.devig`, §78). A move on side s:
`p1[s] / p0[s] − 1 ≥ 3%` (the fair price of s shortened by ≥ ~2.9%). We back s — the
side the sharp market moved toward. "Moved" is defined by OUR poll times, not the books'
publish times: Pinnacle may have moved at any instant in (t0, t1].

**Stale.** Book B has a set in [t0 − 90 min, t0] (the pre-move price, q_before) and a
set in [t1, t1 + 30 min] and ≥ 1 min before kickoff (the decision set, q_T at time T).
B is stale on s if `|q_T[s] / q_before[s] − 1| < 1%`. Moved otherwise.

**Treatment candidate.** Stale, and `edge = q_T[s] · p1[s] − 1 ≥ X`, X ∈ {0%, 3%},
and the §9 guard `q_T[s] ≤ (1/p1[s]) × 1.25` (1X2) / `× 1.30` (O/U). First trigger per
(match, market, book, side).

**Control candidate.** A Pinnacle set at t_k where the previous Pinnacle set (any age)
and every Pinnacle set within the 120 min before t_k differ from it by < 1.5% relative on
every side (no recent sharp move); B's set in [t_k, t_k + 30 min], ≥ 1 min before KO;
`q_T[s] · p_k[s] − 1 ≥ X` and the same guard. First qualifying instant per (match,
market, book, side); units that are treatment units at the same X are removed.

**Wrong-fixture / mirror exclusion ([[#120]]).** On every set of an Estonian book, per
leg, compare with the median of the OTHER books' latest complete sets in
[ts − 60 min, ts + 10 min] (needs ≥ 4 other books): flag if the ratio is > 1.5625 AND
the implied probabilities differ by > 4 points. Any flag excludes that (fixture, book)
from BOTH markets. Counts are reported.

**Grading (CLV, break-even 0, §70).** `clv = q_T[s] · p_close[s] − 1`, where p_close is
de-vigged (Shin):
1. **Exchange close** (Betfair, `exchange_quotes`) — the reader went live
   2026-09-24 07:34 UTC; no finished fixture has any exchange row yet. **n = 0, not
   testable in this window.** Stated before running; the script counts it anyway.
2. **Independent consensus close (PRIMARY)** — `workers.utils.anchor.compute_anchor`
   at kickoff on `sets_from_rows`, `exclude_book='Pinnacle'`, min 5 books, with the four
   Estonian books also removed (the graded book never grades itself, and all four books
   are graded against the same close). Coolbet and the exchange are never members
   (`NEVER_IN_ANCHOR`). Caveat stated in advance (§74): 1xBet and Marathonbet track
   Pinnacle (residual r ≈ +0.88), so the consensus is *partly* independent; a
   descriptive sensitivity drops those two.
3. **Pinnacle close (SECONDARY)** — latest Pinnacle set ≤ kickoff, ≤ 180 min old.

**Survival.** Minutes from T until B's price on s changes by ≥ 1% at a later set, or
kickoff (censored). **Placeable** = B's next set after T exists within 45 min and before
kickoff and still offers ≥ 99% of q_T[s] — the price was still on the board at the next
sweep. Stake limits are NOT observable in `odds_snapshots`; said so, not guessed.

## 4. Tests and multiplicity

Family = 4 books × 2 markets × 2 thresholds × 2 graders (consensus, Pinnacle) × 2
statistics = **64 tests, Holm** over all 64 (a test with n < 10 matches enters at p = 1):
* **vs 0:** mean treatment CLV, clustered by match (per-match mean, then a two-sided
  one-sample t over matches).
* **T − C:** control re-weighted to the treatment's edge distribution (bins 0–1, 1–2,
  2–3, 3–5, 5–8, ≥8%), difference of bin-weighted means, SE from per-bin variances,
  two-sided normal p.

Everything else (moved/stale shares, survival, placeability, the 1xBet/Marathonbet
sensitivity, per-book edge distributions) is descriptive.

## 5. Power (stated in advance)

Per-bet CLV sd at these prices ≈ 4–5% (guess, recomputed on the data).
* vs 0, effect +2%: n ≈ ((1.96 + 0.84) · 4.5 / 2)² ≈ **40 matches per cell**.
* T − C, effect 1 pt: n ≈ 2 · (2.8 · 4.5 / 1)² ≈ **320 per arm**.
The brainstorm's ~5 stale-above-fair events/day across three books gives ~35 in 7 days
at X = 0 — **the vs-0 cells for Coolbet/Epicbet are borderline; every T − C test is
expected to be underpowered.** Tonybet has 1X2 only since 2026-09-23 14:00 → n ≈ 0.

## 6. Expected outcome (written before running)

* vs Pinnacle's close: strongly positive (circular — it re-measures the trigger).
* vs the consensus close: positive but smaller, ~+1–2%.
* T − C: point estimate positive, not significant after Holm.
* Exchange: untestable (n = 0). Tonybet: untestable.
* Verdict likely "promising, not enough data to act" — the honest next step would be a
  forward shadow log, graded vs the exchange once it has ~2 weeks of closes.

---

RESULTS

*Run 2026-09-24 ~09:00 UTC, `python3 scripts/stale_window_study.py` (7 d, 3,762 finished
matches). The pre-registration above was committed first (`5e853116`); nothing in it was
changed after the run.*

## 7. Verdict

**Do not build a fresh-move trigger on this evidence.** The two findings that matter:

1. **The brainstorm's "~93% positive" replicates, and it is the §67 circularity.** The
   same stale prices, graded against Pinnacle's close, are 89% positive (1X2, X=0, n=19,
   mean +5.6%). Graded against the leave-Pinnacle-out consensus close they are **37%
   positive, mean −2.4%** (t −1.29). The trigger and the Pinnacle grader share one feed,
   so a Pinnacle-only view that holds until kickoff looks like CLV.
2. **A fresh move is no better than a standing gap against the independent close.** The
   control (same books, same markets, standing gap ≥ X with no recent sharp move) reads
   −2.9% vs the consensus close (n=1,292). The treatment is +0.5 pt above that, on n=19.
   That gap cannot be told apart from zero. Detecting it would need ~3,800 per arm.

**Not enough data to act, and none of the pre-registered tests could run.** Every one of
the 32 cells has fewer than 10 matches, so all 64 tests enter Holm at p = 1.000. The
exchange close has **n = 0**, because no finished fixture has an `exchange_quotes` row
yet. Tonybet has 0 treatment units, because it has only one day of 1X2 data.

## 8. Numbers

**Data-fault exclusions** ((fixture, book) pairs dropped from both markets, >1.5625× AND
>4 pp from a ≥4-book median): Coolbet 14, Epicbet 78, Unibet-Site 29, Tonybet 0.

**How often the books were stale.** Counted when the book was observed both before and
after a ≥3% Pinnacle move. Moved means a change of ≥1% on the backed side.

| market | book | observed | stale | % |
|---|---|---|---|---|
| 1X2 | Coolbet | 127 | 80 | 63% |
| 1X2 | Epicbet | 671 | 427 | 64% |
| 1X2 | Unibet-Site | 257 | 136 | 53% |
| 1X2 | Tonybet | 10 | 3 | 30% |
| O/U 2.5 | Coolbet | 47 | 27 | 57% |
| O/U 2.5 | Epicbet | 267 | 195 | 73% |
| O/U 2.5 | Unibet-Site | 63 | 36 | 57% |

These shares differ from the brainstorm's (81 / 43 / 68%). Here "observed" requires the
book to have a set within 90 min before t0 and within 30 min after t1, and a move of
under 1% counts as stale. That tolerance absorbs Epicbet's re-margining flicker.

**Treatment units by book** (stale, above the new fair price, §9 guard, first per unit):

| market | book | X | n | CLV vs consensus (mean / median / %pos) | CLV vs Pinnacle close (mean / %pos) | control n, vs consensus / vs Pinnacle |
|---|---|---|---|---|---|---|
| 1X2 | Coolbet | 0% | 6 | −2.59% / −1.91% / 33% | +9.09% / 100% | 268 −2.78% / 285 −0.05% |
| 1X2 | Coolbet | 3% | 5 | −3.11% / −3.04% / 20% | +10.60% / 100% | 140 −1.33% / 149 +1.96% |
| 1X2 | Epicbet | 0% | 7 | −0.75% / +0.19% / 57% | +1.09% / 86% | 625 −3.21% / 647 −2.33% |
| 1X2 | Epicbet | 3% | 0 | — | — | 317 −2.10% / 332 −0.84% |
| 1X2 | Unibet-Site | 0% | 6 | −4.14% / −9.15% / 17% | +7.23% / 83% | 362 −2.25% / 378 +0.11% |
| 1X2 | Unibet-Site | 3% | 3 | +1.36% / −3.63% / 33% | +17.85% / 100% | 195 −0.35% / 205 +2.90% |
| O/U 2.5 | Coolbet | both | 0 | — | — | 45 −1.75% / 70 −0.30% |
| O/U 2.5 | Epicbet | 0% | 3 (2 graded) | −3.04% | −0.66% / 67% | 192 −1.55% / 276 −1.86% |
| O/U 2.5 | Unibet-Site | 0% | 1 | +6.49% | +6.36% | 61 −0.20% / 82 −0.47% |
| Tonybet | any | any | 0 | — | — | 1X2 19 −7.29% / +1.09% |

**Pooled across books.** Descriptive only, outside the Holm family.

| | treatment vs consensus | control vs consensus | treatment vs Pinnacle | control vs Pinnacle |
|---|---|---|---|---|
| 1X2, X=0 | n=19, −2.40%, 37% pos, t −1.29 | n=1,292, −2.88%, t −11.8 | +5.55%, 89% pos, t +2.33 | n=1,347, −0.98% |
| 1X2, X=3% | n=8, −1.43%, 25% pos | n=671, −1.46% | +13.32%, 100% pos, t +3.50 | n=705, +1.10% |
| O/U 2.5, X=0 | n=3, +0.14% | n=303, −1.40% | n=4, +1.09% | n=434, −1.28% |

Consensus with the two Pinnacle-followers dropped (1xBet, Marathonbet, §74), 1X2 X=0:
treatment −2.79% (n=14) against control −2.74% (n=926). Dropping them changes nothing.

**Where the graders disagree.** In the control, Pinnacle's close sits ~1.9 pts above the
consensus close. That matches the ~1 pt peer offset in §73, plus the selection effect.
In the treatment the gap is **~7.9 pts**. The triggers pick exactly the moves the rest of
the market did not follow by kickoff.

The worst example is Fernando de la Mora v Libertad (Copa Paraguay, 2026-09-23). AF-Pinnacle
priced the draw at 3.77–4.24 all day, while every other book had it at 5.45–6.6. A Pinnacle
draw move turned Coolbet's 5.75 into an "edge" of 19% (Unibet 13.9%). Against Pinnacle's
close that reads +30% (Unibet +24%). Against the consensus close it reads −9% (Unibet −13%).

These data cannot say which close is right. The only independent sharp grader is the
exchange (§78), and it has no closes yet.

**Survival and placeability** (X=0, pooled):
* Treatment: median survival **88 min** (1X2 Coolbet 98, Epicbet 112, Unibet-Site 30).
  50–71% of units never moved before kickoff. Median 2.8 h to kickoff at decision.
  **15 of 23 (65%) were still on the board at the next sweep** (Coolbet 50%, Epicbet
  100%, Unibet-Site 33%).
* Control: median survival 61 min; 1,090 of 1,783 (61%) placeable; 3.7 h to kickoff.
* Stake limits are not observable in `odds_snapshots`.

**Power, recomputed.** The observed per-unit sd of consensus CLV is **7.8%**, not the
4–5% assumed.
* vs 0 at +2%: ~120 matches per cell. That is ~6 weeks for pooled 1X2 at ~2.7 units a
  day, and months per book.
* T − C at 1 pt: ~950 per arm.
* T − C at the observed +0.5 pt: ~3,800 per arm.

## 9. What would change the verdict

* **Exchange closes.** Log fresh-move events forward, as an observatory metric rather
  than a bot, and grade them against the Betfair close once `exchange_quotes` has 2+ weeks
  of finished fixtures. That grader is sharp and independent of AF-Pinnacle. It breaks the
  tie in §8.
* Pooled 1X2 needs ~120 matches before a vs-0 test can see +2%. There are no grounds to
  pre-register a per-book cell before then.
