# First-half markets — can we price them? ([[#084]], 2026-09-23)

Owner: *"start with #084 (can we measure it? before and after to see the results)"*.

**What #084 is now.** Its first question — do half-time features help the FULL-TIME model? —
was answered FAIL (A/B, `dev/active/half-time-layer-baseline.md`). What remains is the market
it was filed for: we store first-half odds from up to 16 books and **price none of them**.

**"Before" and "after".** Today we have no first-half model, so there is no old model to beat.
The honest BEFORE is **the market alone** — Pinnacle's own de-vigged first-half price, and the
"bet where a book beats Pinnacle" strategy. AFTER is a model blended with that price. Both
measured on the same fixtures with the same harness.

## Pre-registration (written BEFORE the first run)

**Research answer (per CLAUDE.md "research before you train").** First-half markets are the
three full-time shapes on a scaled rate, λ_1H ≈ s·λ_FT (Dixon & Robinson 1998; Maia et al.
2023 imply s ≈ 0.44 — measured on our own HT scores, not assumed). 1x2_1h is dominated by
0-0, so low-score dependence matters more than at full time. **No literature on pre-match 1H
market efficiency** — recorded as the answer; the work proceeds knowing it is unprecedented.
Full review: `dev/active/per-market-feature-sets-design.md`, "Research landed".

**Data (checked).** Pinnacle first-half prices exist only 2026-08-29 → 2026-09-23 (26 days).

| market | Pinnacle matches | with our rating | priced ≥2 h before KO |
|---|---|---|---|
| `1x2_1h` | 3,421 | 3,272 | 2,182 |
| `team_total_1h_home_05` | 6,799 | 6,047 | 4,204 |
| `team_total_1h_away_05` | 6,548 | 5,874 | 4,033 |
| `over_under_1h_05` | 1,170 | 1,043 | **0** |
| `over_under_1h_15` | 1,788 | 1,560 | **0** |

⚠️ Pinnacle 1H O/U was collected only 08-29 → 09-04 and never ≥2 h before kickoff — a
collection gap, filed separately. Those two get the α test only.

**Two model arms, no market inputs, both leak-free:**
* **R — our ratings.** λ_1H,home / λ_1H,away recovered exactly from the stored walk-forward
  #084 columns (`ht_expected_total ± ht_expected_diff`)/2 → independent Poisson → P(market).
* **F — the full-time market, scaled.** From Pinnacle's FULL-TIME 1x2 + O/U 2.5 at the same
  snapshot, solve the Poisson λ_home, λ_away that reproduce them; λ_1H = s·λ with s = the
  share of goals scored in the first half over all matches before the universe starts. Asks
  whether the soft 1H market is priced consistently with the sharp FT one. *(Uses a market
  price, but a DIFFERENT market's — the α test against the 1H price stays meaningful.)*

**Harness.** Exactly `residual_test_ou.py`'s method: universe = matches with a complete
Pinnacle price in the market (last pre-KO snapshot), date-ordered; Platt on the model for
LEVEL and α (blend weight vs Pinnacle, Shin de-vig) fitted on the first half, evaluated on the
second. **PASS per (arm, market):** α > 0.02 AND blend log-loss < market log-loss AND Holm
p < 0.05 over **m = 10** (2 arms × 5 markets), one-sided on per-fixture log-loss improvement.

**Backtest (1x2_1h and the two team totals only):** decision at T-2h; executable = best price
across ALL books (👥 PICKS — any book is fair game for readers); judged on CLV vs de-vigged
Pinnacle close, with ROI and bets/day. Strategies: MARKET (the before), BLEND (the after),
floors 3/5/8%. A model strategy counts only if it beats MARKET on CLV at the same floor.

**Expected:** R α = 0 (our ratings failed at full time; a half is noisier). F is the real
question — if the 1H market lags the FT market anywhere, F finds it. Prior: small or none,
because Pinnacle itself prices both.

## RESULT (2026-09-23) — neither model beats Pinnacle's first-half price; one book looks mispriced

s (first-half share of goals, all matches before 2026-08-29) = **0.4460**.

**(a) Does a model know more than the market? No — 10 of 10 FAIL (Holm m=10).**

| market | arm | n | α | market LL | model LL | blend LL | p |
|---|---|---|---|---|---|---|---|
| 1x2_1h | R | 1,493 | 0.025 | 1.0413 | 1.0748 | 1.0410 | 0.067 |
| 1x2_1h | F | 1,514 | 0.000 | 1.0387 | 1.0384 | 1.0387 | 1.000 |
| TT 1H home 0.5 | R | 2,731 | 0.080 | 0.6727 | 0.6872 | 0.6729 | 0.818 |
| TT 1H home 0.5 | F | 2,915 | 0.405 | 0.6751 | 0.6747 | 0.6748 | 0.126 |
| TT 1H away 0.5 | R | 2,649 | 0.170 | 0.6737 | 0.6839 | 0.6734 | 0.318 |
| TT 1H away 0.5 | F | 2,767 | 0.945 | 0.6699 | 0.6701 | 0.6700 | 0.569 |
| O/U 1H 0.5 | R / F | 522 / 560 | 0.000 / 1.000 | 0.6386 / 0.6378 | 0.6468 / 0.6384 | = market | 1.0 / 0.62 |
| O/U 1H 1.5 | R / F | 780 / 805 | 0.020 / 0.735 | 0.6506 / 0.6482 | 0.6591 / 0.6504 | ≈ market | 0.45 / 0.64 |

* **R (our ratings)** is worse than the market alone in every market.
* **F (full-time market, scaled)** matches the 1H market almost exactly (model LL ≈ market LL,
  so α is undetermined, not informative): **Pinnacle's first-half prices are consistent with
  its full-time prices.** There is no lag between them to exploit. A clean negative.

**(b) Money — T-2h, best price across all books, CLV vs Pinnacle close (held-out half).**
BLEND-R and BLEND-F never beat MARKET by more than noise (1x2_1h 3%: MARKET +6.44%, BLEND-F
+7.06%); on the team totals, BLEND-F fires 70–120 bets/day at −2% to −6% CLV — the "hundreds of
bad picks" shape again. **The model adds nothing.**

**The one lead is the MARKET strategy itself on 1x2_1h:** books beating Pinnacle's fair 1H price
by 3%+ at T-2h — 148 bets (13.5/day), **CLV +6.4% (t=11.0)**, rising to +12.6% at an 8% floor.
**69% of those bets are at Epicbet** (108: price +8.5% above fair, CLV +7.1%, **ROI −16.8%**).
CLV and ROI disagree; at n=108 and ~3.0 odds the ROI noise is ±~13pp, so neither settles it.
Two explanations, and they point different ways:
* **Real softness:** Epicbet prices first-half 1x2 lazily. Epicbet is one of OUR placeable books
  — this would be 🤖 OWN value, not a model edge.
* **Data fault:** the scraper stores a stale or mis-mapped Epicbet 1H price. The CLV would then
  be phantom (a price nobody could take), and the negative ROI is the truth.
Filed as its own row: verify a sample of Epicbet 1H prices against the live site before any
further weight is put on it.

**Verdict for #084:** first-half markets cannot be priced better than Pinnacle by either model.
Part 2 closes FAIL. The Epicbet lead and the Pinnacle 1H O/U collection gap (nothing after
2026-09-04, never ≥2 h before kickoff) are separate rows.
