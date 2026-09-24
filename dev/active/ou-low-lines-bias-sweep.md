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
