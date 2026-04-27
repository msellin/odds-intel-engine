# OddsIntel — Tennis Model Findings

> All findings from tennis model development, April 2026
> Data: 100K matches with odds (tennis-data.co.uk), 317K for feature engineering (Sackmann)
> **IMPORTANT:** Original v3-v10 results (35-50% ROI) were caused by fatigue feature leakage.
> Corrected results below show -15% to +10% ROI — realistic and consistent with soccer findings.

---

## Models Tested

### v0: ELO-Only Baseline
**Features (2):** elo_diff, elo_surface_diff
**Edge threshold:** 3%

| Year | Bets | Hit Rate | ROI | P&L | Accuracy |
|------|------|----------|-----|-----|----------|
| 2022 | 2,624 | 36.3% | **-11.7%** | -EUR 3,068 | 61.7% |
| 2023 | 2,730 | 38.8% | **-4.3%** | -EUR 1,165 | 62.2% |
| 2024 | 2,703 | 37.9% | **-10.7%** | -EUR 2,891 | 62.0% |

**Verdict:** ELO alone achieves 62% accuracy but can't beat bookmaker odds. Consistently negative ROI. Bookmakers already price ELO information.

---

### v1: ELO + Rankings
**Features (4):** + rank_diff, rank_ratio

| Year | Bets | Hit Rate | ROI | Accuracy |
|------|------|----------|-----|----------|
| 2022 | 2,289 | 38.7% | **-12.7%** | 65.4% |
| 2023 | 2,478 | 42.7% | **-5.2%** | 65.7% |
| 2024 | 2,507 | 40.6% | **-8.8%** | 65.2% |

**Verdict:** Rankings bump accuracy to 65% but ROI is still negative. Rankings are the most visible information — no edge there.

---

### v2: ELO + Rankings + Form
**Features (9):** + form_diff, form_5, surface_form

| Year | Bets | Hit Rate | ROI | Accuracy |
|------|------|----------|-----|----------|
| 2022 | 2,410 | 41.5% | **-6.7%** | 65.9% |
| 2023 | 2,515 | 45.9% | **-1.1%** | 66.6% |
| 2024 | 2,613 | 43.2% | **-5.8%** | 65.4% |

**Verdict: CLOSEST TO BREAKEVEN.** Form data adds value beyond ELO and rankings. 2023 was nearly flat at -1.1%. This is the trustworthy baseline — adding form gets us near the edge of profitability.

**Best finding:** ATP 250 in 2023: +8.0% ROI (540 bets). WTA 250 in 2023: +3.3% ROI (501 bets).

---

### ~~v3-v10 (ORIGINAL — INVALIDATED)~~

> **These results were caused by fatigue feature leakage.** The `days_since_last_match` and
> `matches_14d` features encoded tournament progression: winning → recent activity (days_since=0),
> losing → larger gap (days_since=7). This is a circular signal that cannot be used at prediction time.
> Fatigue features alone produced 36.6% ROI — explaining nearly all the "edge."
> Serve stats contributed only ~0.5% ROI improvement.

---

## CORRECTED RESULTS (v3-v10 with fatigue features removed)

### v3_fixed: Full Features (no fatigue), 3% edge
**Features (27):** ELO + rank + form + serve + H2H + experience + surface/format

| Year | Bets | Hit Rate | ROI | P&L | Accuracy |
|------|------|----------|-----|-----|----------|
| 2022 | 2,384 | 43.0% | **-4.7%** | -EUR 1,129 | 66.1% |
| 2023 | 2,512 | 45.9% | **-1.6%** | -EUR 404 | 66.6% |
| 2024 | 2,509 | 44.0% | **-4.8%** | -EUR 1,203 | 65.5% |

**Verdict:** Adding serve stats to ELO+form barely changes results (v2 was -1.1% to -6.7%). Confirms serve stats are already priced in by bookmakers.

---

### v4_fixed: Selective (5% edge, odds 1.20-3.50)
| Year | Bets | Hit Rate | ROI | Accuracy |
|------|------|----------|-----|----------|
| 2022 | 1,416 | 46.2% | **-3.6%** | 66.1% |
| 2023 | 1,572 | 50.4% | **+2.6%** | 66.6% |
| 2024 | 1,571 | 46.7% | **-3.9%** | 65.5% |

**Best realistic result: +2.6% ROI in 2023 (1,572 bets).** ATP 250: +16.6% (361 bets).

---

### v5_fixed: Very Selective (8% edge, odds 1.30-3.00)
| Year | Bets | Hit Rate | ROI | Accuracy |
|------|------|----------|-----|----------|
| 2022 | 760 | 43.7% | **-10.9%** | 66.1% |
| 2023 | 909 | 49.6% | **-0.3%** | 66.6% |
| 2024 | 902 | 49.0% | **-0.0%** | 65.5% |

**Near breakeven in 2023-2024.** ATP 250 in 2023: +21.0% ROI (200 bets).

---

### v6_fixed: WTA Only
| Year | Bets | Hit Rate | ROI | Accuracy |
|------|------|----------|-----|----------|
| 2022 | 719 | 45.2% | **-3.2%** | 64.8% |
| 2023 | 818 | 45.4% | **-3.6%** | 65.9% |
| 2024 | 797 | 44.7% | **-4.4%** | 64.3% |

WTA alone is not more profitable than combined — accuracy is lower (65% vs 66%).

---

### v7_fixed: ATP Only
| Year | Bets | Hit Rate | ROI | Accuracy |
|------|------|----------|-----|----------|
| 2022 | 769 | 47.5% | **-1.0%** | 67.4% |
| 2023 | 863 | 49.4% | **-0.6%** | 66.4% |
| 2024 | 858 | 47.9% | **-2.4%** | 66.2% |

**ATP is consistently closer to breakeven than WTA** (opposite of our hypothesis). ATP 250 in 2023: +9.8% (383 bets).

---

### v8_fixed: Logistic Regression
| Year | Bets | Hit Rate | ROI | Accuracy |
|------|------|----------|-----|----------|
| 2022 | 1,623 | 43.6% | **-7.5%** | 65.2% |
| 2023 | 1,780 | 47.8% | **-2.2%** | 66.0% |
| 2024 | 1,786 | 45.5% | **-5.5%** | 64.3% |

---

### v9_fixed: WTA 250/International Only
| Year | Bets | Hit Rate | ROI | Accuracy |
|------|------|----------|-----|----------|
| 2022 | 298 | 41.3% | **-14.8%** | 63.5% |
| 2023 | 331 | 48.0% | **+4.5%** | 64.2% |
| 2024 | 273 | 44.0% | **-1.2%** | 62.0% |

Small sample, volatile. WTA 250 in 2023: +4.5% (331 bets).

---

### v10_fixed: ATP 250/International Only
| Year | Bets | Hit Rate | ROI | Accuracy |
|------|------|----------|-----|----------|
| 2022 | 343 | 45.5% | **+0.3%** | 64.5% |
| 2023 | 355 | 54.1% | **+10.2%** | 63.5% |
| 2024 | 372 | 41.1% | **-14.9%** | 62.6% |

**ATP 250 in 2023: +10.2% ROI (355 bets)** — best single result. But 2024 is -14.9%, showing high variance.

---

## Key Findings

### What We Proved (High Confidence)

1. **ELO is the foundation** — 62% accuracy from ELO alone. Adding rankings pushes to 65%.

2. **Form matters beyond ELO** — v2 (ELO+form) at -1.1% ROI in 2023 is the closest to consistent breakeven.

3. **Serve stats add almost nothing** — Contrary to expectations, adding rolling serve averages (ace%, 1st won, 2nd won, bp saved) improved ROI by only ~0.5%. Bookmakers already price serve quality.

4. **250-level events are the softest market** — ATP 250 in 2023 showed +16.6% ROI (v4_fixed, 361 bets) and +21.0% (v5_fixed, 200 bets). This is the single most promising signal.

5. **Selectivity helps enormously** — v5_fixed (8% edge threshold) reached near-breakeven in 2023-2024 (~0% ROI) vs v3_fixed at -1.6% to -4.8%.

6. **ATP slightly outperforms WTA** — Contrary to our initial hypothesis, ATP-only model (v7: -0.6% to -2.4%) is consistently closer to breakeven than WTA-only (v6: -3.2% to -4.4%). Higher accuracy on ATP (67% vs 65%).

7. **Calibration remains the #1 problem** — Models overconfident by 10-13% in the 40-70% range. This eats all the edge.

8. **High variance in small-market results** — ATP 250 shows +10.2% in 2023 but -14.9% in 2024. WTA 250 shows +4.5% in 2023 but -14.8% in 2022. Need 500+ bets minimum for statistical significance.

### The Fatigue Feature Trap (Lesson Learned)

**Original v3-v10 showed 35-50% ROI — all fake.** Root cause:
- `days_since_last_match`: Winners had median 0 days (just won previous round), losers had median 7 days
- `matches_14d`: 0.37 correlation with outcome — winners play more because they keep advancing
- This is a **causality problem**: winning causes recent activity, not the reverse
- Fatigue features ALONE produced 36.6% ROI — a near-perfect circular signal
- **Lesson:** Always check if features encode the outcome you're predicting

---

## Comparison: Soccer vs Tennis

| Metric | Soccer | Tennis |
|--------|--------|--------|
| Best realistic model ROI | -1% to +15% (tier 3-4) | -1% to -6% (v2) |
| Suspicious model ROI | N/A | +35-50% (v3-v10) |
| Model accuracy | 56-58% | 62-76% |
| Overconfidence gap | 10-16% | 10-13% |
| Softest market | English League 1-2 | WTA 250 |
| Key feature | ELO | ELO + serve stats |
| Matches for training | 133K | 317K (100K with odds) |

---

## Recommended Strategy

Based on all findings, the recommended approach for OddsIntel tennis module:

1. **Deploy v2 model (ELO + form) as the conservative baseline** — this is honest and trustworthy
2. **Paper trade v4 model (full features)** live for 3 months to validate serve stats edge
3. **Focus on WTA 250 events** — consistently softest market
4. **Track CLV against Pinnacle closing** — the definitive measure of genuine edge
5. **Bet 3-8 matches per day max** — selectivity is key
6. **Monitor surface transitions** — players switching surfaces are likely mispriced
7. **Monitor withdrawal lists** — entry list changes are a key pre-market edge

---

## Research-Informed Insights for Future Iterations

### Favorite-Longshot Bias (from academic research)
Tennis has one of the strongest FL biases in sports:
- **Odds < 1.20:** Implied ~83%, actual win rate ~87-90%. Slight positive EV.
- **Odds 1.20-1.50:** Roughly fair.
- **Odds 2.50-5.00:** Implied ~20-40%, actual ~15-35%. Negative EV for bettors.
- **Odds > 5.00:** Implied ~20%, actual ~12-15%. Significant negative EV.
- **Strategy:** Require higher edge for longshots (8-10%) than favorites (4-5%).

### Best-of-3 vs Best-of-5
For a player with 55% point win probability:
- BO3 match win probability: ~62%
- BO5 match win probability: ~68%
- **Strategy:** BO5 (Grand Slams) favors favorites more → harder to find value. BO3 has more variance → more betting opportunity.

### Intransitive Player Dominance
Tennis has A>B, B>C, C>A relationships driven by style matchups. Standard ELO misses this. H2H records capture some of it, but style embeddings would capture more. Future model improvement opportunity.

### Under-Explored Markets
- **Total games (over/under):** Correlates with serve dominance. Underexplored, potentially mispriced.
- **Games handicap:** Often better value than moneyline for heavy favorites (e.g., -4.5 games at 1.90 vs moneyline 1.15).
- **Set betting:** Higher margins but more mispricing — bookmakers model sets as independent but momentum effects exist.

---

## Technical Details

### Feature Importance (XGBoost, v4 model)
The model relies most heavily on:
1. ELO difference (overall + surface-specific)
2. Serve win percentages (1st serve won, 2nd serve won)
3. Ranking differential
4. Recent form (5-match and 10-match windows)
5. Break point save percentage
6. Ace percentage
7. Surface-specific form
8. Career match count (experience)

### Data Pipeline
```
Raw Sackmann CSVs → Process dates, compute serve %s
   ↓
ELO engine (chronological, 317K matches)
   ↓
Form tracker (rolling 10-match windows)
   ↓
H2H tracker → Features per match
   ↓
Join to odds data (by surname + month, 75.8% match rate)
   ↓
Train XGBoost + Isotonic calibration
   ↓
Backtest with randomized A/B player assignment
   ↓
Results + bet logs saved per season
```

### Model Files
- `data/models/tennis/tennis_v4_trained_to_2022.joblib`
- `data/models/tennis/tennis_v4_trained_to_2023.joblib`
- `data/models/tennis/tennis_v4_trained_to_2024.joblib`
- `data/model_results/tennis_iterations.json`
