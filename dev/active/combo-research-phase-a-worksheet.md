# COMBO-RESEARCH-PHASE-A — Coolbet Combo & SGM Pricing Audit

**Goal:** find out whether Coolbet mispriceds combo bets in a way we can exploit, before spending engineering time on Phases B-D.

**Method:** open Coolbet on a real device, pick 2-3 today's matches, write down the prices Coolbet offers. No code, ~2 hours.

**The two specific things we want to find out:**

1. Does Coolbet **compound margin on standard accumulators**? (i.e., is a 3-leg combo's odds ≤ leg1 × leg2 × leg3, or exactly equal?)
2. Does Coolbet price **Same-Game Multis** (SGMs) as the product of marginals (correlation = 0), or with a fair correlation model?

If both answers favour us, combos/SGMs are a real opportunity. If both go against us, we drop the topic.

---

## Test 1 — Acca margin compounding

Pick **3 unrelated single bets** from today's Coolbet menu. Different matches, ideally different leagues. Note the single-bet odds:

| Leg | Match | Selection | Single odds |
|-----|-------|-----------|-------------|
| 1   |       |           |             |
| 2   |       |           |             |
| 3   |       |           |             |

Now combine them on the Coolbet slip as a 3-fold acca. Note the offered combo odds:

| Calculation | Value |
|---|---|
| Product of single odds (leg1 × leg2 × leg3) |  |
| Coolbet's offered acca odds |  |
| Ratio (offered / product) |  |

**Interpretation:**
- Ratio = 1.00 → Coolbet doesn't compound margin. **Huge:** confirmed +EV singles → confirmed +EV combos. Pursue Phase D.
- Ratio 0.95-0.99 → mild extra margin (typical). Combos still likely net -EV.
- Ratio < 0.95 → heavy extra margin. Skip Phase D entirely.

Repeat the test with another 3-fold to confirm (sometimes one leg's market has weird quirks). Also test a 5-fold to see if the compounding ratio degrades further with leg count.

---

## Test 2 — Coolbet SGM availability + pricing

Pick **one match where Coolbet supports SGMs** (look for "Bet Builder" / "Same Game Multi" / "Combo" on the match page). The strongest correlated pair to test is **BTTS + Over 2.5**:

| Single-leg odds | Value |
|---|---|
| BTTS Yes |  |
| Over 2.5 |  |
| Product (BTTS × Over 2.5) |  |
| Coolbet's offered SGM odds (BTTS + Over 2.5) |  |

**Interpretation:**
- Coolbet SGM = product → they price these as independent → strong correlation = clear mispricing → **pursue Phase B-C**.
- Coolbet SGM < product but ≥ 0.85 × product → moderate correlation adjustment → smaller edge but possibly still +EV
- Coolbet SGM significantly < product (≤ 0.85 ×) → Coolbet has its own correlation model → small or zero edge

For the **strongest possible test**, take a heavy home favourite (e.g., home win at 1.30) and combine with "home team to score 2+ goals." These are massively correlated (if the heavy favourite wins, they usually score multiple goals). Note the SGM price vs the product — if Coolbet doesn't deflate the price meaningfully, we have a smoking gun.

| Strong-correlation pair | Value |
|---|---|
| Home win (heavy favourite) |  |
| Home team 2+ goals |  |
| Product |  |
| Coolbet SGM |  |
| Ratio (Coolbet / product) |  |

A ratio near 1.0 here = massive opportunity. A ratio near 0.65-0.75 means Coolbet is roughly modelling the correlation correctly.

---

## Test 3 — Acca / SGM bet boosts (optional)

While on Coolbet, look for "Bet Boost" / "Acca Boost" / "Daily Offers":

- Are there standing bet boosts? (e.g., "all 4+ leg accas get +25% odds")
- Conditions (min stake, min odds, max payout)?

Note any standing offers — these can flip otherwise -EV combos to +EV. Marginal as a system but worth knowing.

---

## Verdict

After completing the worksheet:

- [ ] Test 1 ratio (3-leg): _______ (target: ≥ 0.99 to pursue acca strategy)
- [ ] Test 2 BTTS+O2.5 SGM ratio: _______ (target: ≥ 0.95 to pursue SGM strategy)
- [ ] Test 2 strong-correlation ratio: _______ (target: ≥ 0.90 = massive opportunity)
- [ ] Test 3: any standing bet boosts? _______

**Outcome decisions:**

| Test 1 result | Test 2 result | Decision |
|---|---|---|
| ≥ 0.99 | any | Build Phase D (cross-match acca bot) |
| any | ≥ 0.95 | Build Phase B-C (SGM bot) |
| Both above thresholds | | Build both — combo strategy is real |
| Both below | | Mark COMBO-RESEARCH closed-as-not-pursued |

Fill this worksheet in, paste the verdict back, and I'll act on whichever phases survived.
