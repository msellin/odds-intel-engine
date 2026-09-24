Parent row: **#128 OU-LOW-LINES-BIAS-SWEEP-2026-09-24**

# O/U low-lines market-bias sweep — pre-registration (written 2026-09-24, BEFORE any outcome is computed)

## Question

Owner: *"predicting OU2.5 is difficult, what about over 0.5 … it's much harder to predict a winner
than to say there will be goals."* Most interest in **O/U 0.5**; also 1.5 and 3.5, 2.5 as control.

This is **not a model test.** Model α = 0 is already measured on O/U 1.5 / 2.5 / 3.5 against de-vigged
Pinnacle (#089 notes). "Easy to predict" is irrelevant to profit — the price already carries the
~92% over-0.5 rate, and at 1.07 break-even is 93.5%. The only question that can pay is: **is the price
wrong** — by line, by league scoring tier, by time before kickoff, or against a sharp-derived fair price.

## Literature / prior answer (per CLAUDE.md "research before")

* Totals markets carry the favourite–longshot bias: the margin is loaded onto the long side, so the
  short side is closest to fair but still below break-even (ANALYSIS_GOTCHAS §78 on de-vig: Shin
  still UNDER-states favourites). Expect the over-0.5 / over-1.5 / under-3.5 sides to lose least.
* No published work on FT O/U 0.5 efficiency vs a closing line was found in the 2026-09-23 reviews —
  recorded as the answer: unstudied.
* Our own: flat backing at clean rows (first look, 2026-09-24): every cell negative except Epicbet
  over 0.5 +1.9% (n=456), which is one cell of 22 at t≈1.6.

## Data rules (fixed now)

* Window: kickoffs 2026-05-15 → 2026-09-23, `matches.status='finished'`.
* Pre-match only: `timestamp <= kickoff`, `NOT is_live`.
* Books in the family: **Coolbet, Epicbet** (placeable, enough n). Tonybet/Unibet-Site too thin → descriptive only.
* **Contamination guards** (ANALYSIS_GOTCHAS §79, #128 first look):
  1. Ladder monotonicity per (fixture, book, snapshot-time): over-odds must strictly increase 0.5 < 1.5 < 2.5 < 3.5
     wherever two adjacent lines exist; a violating (fixture, book) is dropped entirely.
  2. Pinnacle `over_under_05` is NOT used (Apr–May rows are first-half contaminated; none after).
  3. Rows in `odds_snapshots_quarantined` are already excluded by construction.
  4. Anchor agreement (arm C only): |book implied over-0.5 − derived fair| ≤ 10 pp.
* Price times: **CLOSE** = last snapshot ≤ KO. **EARLY** = last snapshot in [KO−48h, KO−6h]. Coverage of
  EARLY is reported; retention may thin it.
* League scoring tier (walk-forward, fixed thresholds — not data-chosen terciles): mean total goals over the
  league's finished matches in the 365 days before kickoff, ≥30 matches, else excluded from tier cells:
  **low < 2.50, mid 2.50–2.90, high ≥ 2.90**.
* One bet per (fixture, book, line, side). Stake 1 unit flat. ROI = mean P&L per bet.
* **Split:** matches split 50/50 by `md5(match_id)` parity. **Discovery = even** half, where the family is
  tested; **Replication = odd** half, where any survivor must show the same sign with one-sided p < 0.05.
  (A date split is impossible: Epicbet O/U 0.5 exists only from 2026-08-27.)

## The family — fixed at 44 cells, Holm-corrected, two-sided α = 0.05

Short side per line: over 0.5, over 1.5, over 2.5 (control), under 3.5.

* **A (32):** flat short side at CLOSE × {Coolbet, Epicbet} × tier {all, low, mid, high} × 4 lines.
* **B (8):** flat short side at EARLY × {Coolbet, Epicbet} × 4 lines, tier = all.
* **C (4):** over 0.5 vs a **Pinnacle-derived fair price** — Dixon-Coles (ρ = −0.10) goal grid fitted to
  Shin-de-vigged Pinnacle CLOSE 1x2 + O/U 2.5 (+ O/U 1.5 where quoted); fair P(over 0.5) = 1 − P(0-0).
  Bet when `book_odds × P_fair − 1 ≥ {0, 2%}` × {Coolbet, Epicbet}, at CLOSE.

Test statistic per cell: t on per-bet P&L; cells with n < 100 enter with p = 1.

Reported but NOT in the family (descriptive): implied-vs-actual calibration curve per line at every book
(the favourite–longshot shape), per-month ROI for over 0.5, long-side ROI per line.

## Expected result, stated before running

* Every A/B cell negative or indistinguishable from 0; over 0.5 closest to break-even (−3%…+2%).
* C: edges fire rarely; CLV-free ROI ≈ 0 ± 2pp. **Nothing survives Holm.**
* A surviving cell is only a candidate: it needs the replication half AND a forward shadow test before
  any bot, because the 0.5 history is ~6 weeks long.

---

## RESULT (2026-09-24, `scripts/ou_low_lines_bias_sweep.py`, run after the pre-registration commit 5dfa2048)

**No positive cell survives. The expected result held.** 709 (fixture, book) pairs were dropped by the
ladder guard; the Pinnacle-derived fair over 0.5 was available on 15,554 matches.

**Family (discovery half, m = 44, Holm):** 9 cells survive, and **every one is NEGATIVE**. They are the
margin measured precisely, not an edge: O/U 1.5 over at Epicbet close −5.4%; O/U 2.5 over at Coolbet
in low-scoring leagues −13.4%; O/U 3.5 under −6% to −8% at both books, at both close and early; O/U 1.5
over early −5% to −6%.

**Over 0.5 — the owner's question.** It is the line closest to fair, as the favourite–longshot bias
predicts, and it is still not profitable:

| cell | discovery | replication |
|---|---|---|
| Coolbet close | −0.56% (n=1,219) | −0.15% (n=1,197) |
| Epicbet close | +2.09% (n=305, t=1.28) | +0.58% (n=291) |
| Coolbet early | +0.27% (n=466) | −0.97% (n=449) |
| Epicbet early | −0.12% (n=273) | +0.80% (n=271) |

Scoring tier doesn't rescue it: Coolbet low-scoring leagues −5.1% (p_holm 0.43), mid/high ≈ 0. The long
side (under 0.5) loses 39–42% — that is where the margin sits.

**Arm C (derived-fair over 0.5) — the one bright spot, explained.** Too few bets to test per half
(n = 51/49 < 100, so p = 1 by rule), but post hoc both halves read +8% to +11% at Coolbet (n = 100
combined, hit 99/100 vs a fair ~94%). This is **a real, temporary soft-book pricing lapse, not data error
and not a current edge**:

| Coolbet over 0.5 at close | corr(book implied, fair) | slope | share with EV ≥ 0 |
|---|---|---|---|
| 2026-08-01 → 08-23 (n = 677) | **0.39** | 0.78 | 21–29% per week |
| 2026-08-24 → 09-23 (n = 1,454) | **0.90** | 1.02 | 0–3% per week |

Until about 23 Aug, Coolbet quoted over 0.5 at a near-generic ~1.09–1.14 whatever the match, so in
high-scoring fixtures (fair ~0.95) it was too long. From 24 Aug it tracks the sharp-derived price almost
one-for-one and the edge is gone. Epicbet (from 27 Aug) tracks less tightly (corr 0.67, slope 0.71) but
positive-EV cases are 1–6% and the EV is small.

**Verdict:** no O/U line — 0.5, 1.5, 2.5 or 3.5 — is flat-bettable at Coolbet or Epicbet, by league
tier or price time. **CLOSED as a negative result.** Re-open trigger: a soft book's low-line price decouples
from the sharp-derived fair price again (corr < 0.6 over a week), which is what the August lapse looked
like. Monitoring for that belongs to [[#121]] (odds observatory), not to a bot.

---

## ADDENDUM — over 0.5 entered at minute 5–10 at 0-0 (owner follow-up, 2026-09-24)

Owner: *"what happens with the 0.5 odds 5-10 minutes into play — what if we placed the bet there?"*
**Stated before running:** the in-play price at 0-0 is fair minus the in-play margin (the 1x2 draw
calibration measured earlier the same day matched within 0.4pp), so waiting should not beat pre-match;
with ~100 bets only a glaring mispricing is detectable. Script `scripts/ou05_inplay_early_entry.py`.

**Data held:** Coolbet in-play = 26 matches on one day (unusable). Epicbet in-play boards since
2026-09-15 (1,540 matches, ~45 s cadence) — but Epicbet lists the 0.5 line early in only a minority of
games. AF live (not placeable, to 2026-08-21) for sample size.

**Epicbet (placeable):**

| strategy | n | avg odds | hit | ROI ± 95% |
|---|---|---|---|---|
| wait, bet over 0.5 at min 5–10 if 0-0 | 89 | 1.089 | 93.3% | +1.5% ± 5.8 |
| same matches, pre-match price | 75 | 1.099 | 93.3% | +2.6% ± 6.3 |
| pre-match on every in-play-covered match | 128 | 1.103 | 93.0% | +1.5% ± 4.9 |

The in-play price at minute 5–10 is **not longer** than the pre-match price on the same matches — median
ratio **0.991**, i.e. slightly SHORTER. Epicbet's in-play margin eats more than the ~8 minutes of goalless
time gives back. Waiting also loses 41% of the matches (a goal already, or no 0.5 line listed). Every
figure is inside noise. **Verdict: no reason to wait; nothing here either way at this n.**

**AF live `live_ou_05_*` is a FIRST-HALF market, not full-time** — at min 5–10 / 0-0 its average over
price is 1.41, its de-vigged implied probability 0.674 against a **half-time** goal rate of 0.671 (and a
full-time rate of 0.908). Settled on full-time goals it reads **+27% ROI at t = 9.6** — entirely fake.
The contamination is concentrated in the 0.5 line at early minutes; on the 2.5 line ~1% of rows in the
first 15 minutes look 1H-shaped and none after minute 45. See ANALYSIS_GOTCHAS §80.
