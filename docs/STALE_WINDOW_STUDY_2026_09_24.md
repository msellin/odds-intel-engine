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

*(appended after the run)*
