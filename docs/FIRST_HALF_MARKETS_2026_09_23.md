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
