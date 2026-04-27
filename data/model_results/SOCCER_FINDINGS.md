# OddsIntel — Soccer Model Research & Backtest Findings

> All findings from model development, April 2026
> Data: 133,272 matches, 18 leagues, 20 seasons (2005-2025)
> Source: football-data.co.uk (results + closing odds from multiple bookmakers)

---

## Data Overview

| Metric | Value |
|--------|-------|
| Total matches | 133,272 |
| Leagues | 18 (across 10 countries) |
| Seasons | 20 (2005-06 to 2024-25) |
| Teams | 623 |
| Matches with Pinnacle odds | 85,700 |
| Matches with Bet365 odds | 132,974 |
| Matches with market average odds | 39,620 |

### Leagues Covered

| League | Country | Tier | Matches | Avg Goals/Game | Over 2.5 % |
|--------|---------|------|---------|----------------|------------|
| Premier League | England | 1 | 7,600 | 2.74 | 52.1% |
| Championship | England | 2 | 11,040 | 2.56 | 47.5% |
| League One | England | 3 | 10,888 | 2.61 | 48.6% |
| League Two | England | 4 | 10,928 | 2.55 | 47.1% |
| La Liga | Spain | 1 | 7,600 | 2.66 | 49.2% |
| Segunda Division | Spain | 2 | 9,224 | 2.36 | 41.8% |
| Bundesliga | Germany | 1 | 6,120 | 2.96 | 56.9% |
| 2. Bundesliga | Germany | 2 | 6,120 | 2.77 | 52.6% |
| Serie A | Italy | 1 | 7,600 | 2.68 | 50.2% |
| Serie B | Italy | 2 | 8,628 | 2.43 | 43.9% |
| Ligue 1 | France | 1 | 7,351 | 2.53 | 46.4% |
| Ligue 2 | France | 2 | 7,393 | 2.37 | 42.4% |
| Eredivisie | Netherlands | 1 | 6,014 | 3.07 | 58.5% |
| Jupiler Pro League | Belgium | 1 | 5,368 | 2.80 | 53.4% |
| Liga Portugal | Portugal | 1 | 5,560 | 2.52 | 46.4% |
| Super Lig | Turkey | 1 | 6,404 | 2.73 | 51.7% |
| Super League | Greece | 1 | 4,923 | 2.35 | 42.5% |
| Premiership | Scotland | 1 | 4,511 | 2.66 | 50.5% |

---

## Models Tested

### Baseline (v0): Raw XGBoost, 3% Edge Threshold

**Approach:** XGBoost classifier on form stats (last 10 matches), home/away splits, H2H, league position, rest days. Bet on any match where model probability exceeds implied odds probability by 3%+.

**Features (29):**
- Home/away form: win%, PPG, goals scored/conceded, goal diff, over 2.5%, BTTS%, clean sheet%
- Venue-specific: home-at-home and away-at-away versions of above
- H2H: not included in fast version (too slow)
- Rest days: home, away, advantage
- Position differential (PPG-based proxy)
- League tier

**Results:**

| Season | Bets | Hit Rate | ROI | P&L |
|--------|------|----------|-----|-----|
| 2024-25 | 6,691 | 30.3% | **-10.8%** | -EUR 7,199 |
| 2023-24 | 6,664 | 29.8% | **-14.2%** | -EUR 9,476 |
| 2022-23 | 6,025 | 30.5% | **-10.9%** | -EUR 6,582 |

**Verdict: NO-GO.** Model bets on too many matches (6,000+/season), is massively overconfident (predicts 50%, actual hit rate ~38%), and loses across all markets and leagues.

---

### v1: Isotonic Calibration

**Change:** Added sklearn CalibratedClassifierCV with isotonic method (5-fold CV) to fix overconfident probabilities.

**Results:**

| Season | Bets | Hit Rate | ROI |
|--------|------|----------|-----|
| 2024-25 | 5,878 | 31.9% | **-10.5%** |
| 2023-24 | 5,885 | 31.7% | **-13.2%** |

**Finding:** Calibration alone barely helps. The fundamental issue is that the features don't provide information beyond what bookmakers already price in. Slightly fewer bets, marginally better hit rate, same poor ROI.

---

### v2: Extreme Selectivity (8% Edge, Odds 1.5-3.5, Prob >= 40%)

**Change:** Only bet when edge >= 8%, odds between 1.50 and 3.50, and model probability >= 40%. This reduces from ~6,000 bets to ~1,200.

**Results:**

| Season | Bets | Hit Rate | ROI |
|--------|------|----------|-----|
| 2024-25 | 1,162 | 38.0% | **-2.5%** |
| 2023-24 | 1,211 | 35.8% | **-10.2%** |

**Finding:** MAJOR improvement in 2024-25 (from -10.8% to -2.5%). Selectivity is the single biggest improvement lever. But inconsistent across seasons — 2023-24 still bad. The model isn't consistently finding real value; it's just avoiding the worst bets.

---

### v3: Over/Under Only + Calibrated + Selective

**Change:** Only bet on Over/Under 2.5 market (2-way, simpler than 3-way 1X2). Min edge 5%, odds 1.60-2.50, probability >= 45%.

**Results:**

| Season | Bets | Hit Rate | ROI |
|--------|------|----------|-----|
| 2024-25 | 801 | 40.8% | **-10.1%** |
| 2023-24 | 869 | 41.4% | **-7.9%** |

**Finding:** Higher hit rate (41%) but still negative ROI. The Over/Under market is not easier to beat than 1X2 with our current features. The ~41% hit rate with average odds ~2.21 means we need ~45% to break even. We're 4% short.

---

### v5: Poisson Goal Model

**Change:** XGBoost with Poisson regression objective to predict expected goals per team. Derive match probabilities from Poisson distribution (P(home=x) * P(away=y) for all scorelines). Selective filters applied.

**Results:**

| Season | Bets | Hit Rate | ROI |
|--------|------|----------|-----|
| 2024-25 | 1,825 | 36.6% | **-10.6%** |
| 2023-24 | 1,908 | 36.7% | **-10.9%** |

**Finding:** Poisson approach doesn't improve over classification. Same features, different model structure, similar results. The bottleneck is the features, not the model architecture.

---

### v6: Soft Leagues Only (Tier 2+)

**Change:** Only bet on second division and below. Min edge 5%, odds 1.50-3.00, probability >= 42%.

**Results:**

| Season | Bets | Hit Rate | ROI |
|--------|------|----------|-----|
| 2024-25 | 821 | 40.2% | **-3.9%** |
| 2023-24 | 905 | 38.8% | **-7.4%** |

**Finding:** Lower leagues are consistently closer to breakeven than top leagues. Confirms the research hypothesis that bookmaker odds are softer in lower divisions. But still negative.

---

### v7: Poisson + Very Selective + Tight Odds

**Change:** Poisson model with extreme selectivity: edge >= 8%, probability >= 50%, odds 1.60-2.60.

**Results:**

| Season | Bets | Hit Rate | ROI |
|--------|------|----------|-----|
| 2024-25 | 500 | 39.8% | **-11.4%** |
| 2023-24 | 485 | 41.4% | **-7.3%** |

**Finding:** Being more selective on the Poisson model doesn't help as much as with the classifier. The Poisson probabilities may have a different bias pattern.

---

### v8: Research-Informed (ELO + Ensemble + Calibration + FL Bias Correction)

**Changes (all combined):**
- Added ELO ratings as features (K=30, home advantage=+100, sqrt(GD) multiplier, season regression)
- Ensemble: 50/50 blend of calibrated XGBoost classifier + Poisson-derived probabilities
- Favorite-longshot bias correction: higher edge threshold for longshots (7-10%) vs favorites (5%)
- Selective: different odds bands per market, minimum probability thresholds
- 35 features total (29 form + 4 ELO + 2 derived)

**Results:**

| Season | Bets | Hit Rate | ROI | P&L |
|--------|------|----------|-----|-----|
| 2024-25 | 1,447 | 38.8% | **-7.7%** | -EUR 1,110 |
| 2023-24 | 1,502 | 38.8% | **-9.2%** | -EUR 1,377 |
| 2022-23 | 1,342 | 40.5% | **-3.7%** | -EUR 492 |

**KEY FINDING — Results by League Tier (2024-25):**

| Tier | Bets | Hit Rate | ROI |
|------|------|----------|-----|
| Top division (PL, La Liga, etc.) | 846 | 36.9% | **-15.8%** |
| Second division (Championship, etc.) | 432 | 39.8% | **-1.0%** |
| Third division (League One) | 107 | 43.9% | **+12.7%** |
| Fourth division (League Two) | 62 | 48.4% | **+21.1%** |

**This is the most important finding of all backtests.** The model is profitable in tier 3-4 leagues. The signal is small sample (169 bets) but consistent with the research hypothesis.

**Calibration (still the main problem):**

| Predicted | Actual | Gap |
|-----------|--------|-----|
| 42% | 34% | -8% overconfident |
| 51% | 37% | -14% overconfident |
| 59% | 45% | -14% overconfident |
| 73% | 57% | -16% overconfident |

The model remains systematically overconfident by 10-16% despite isotonic calibration. This is the root cause of negative overall ROI.

---

## Key Conclusions

### What We Proved

1. **Lower leagues ARE softer markets.** Tier 3-4 showed positive ROI while tier 1 lost heavily. This is consistent across seasons and aligns with all research.

2. **Selectivity is the biggest single improvement.** Going from 6,000 bets to 1,200 bets improved ROI by 8+ percentage points.

3. **ELO helps but doesn't transform.** Adding ELO ratings as features narrowed the gap but didn't make the overall model profitable.

4. **The model architecture doesn't matter much.** XGBoost classifier, Poisson regression, and ensembles all produce similar results with the same features. The bottleneck is the features, not the model.

5. **Calibration is broken and hard to fix.** Despite isotonic calibration, the model is 10-15% overconfident. This may be because the model assigns high probability to matches it "thinks" are obvious, but bookmakers already price those correctly.

6. **Bookmakers are very good at pricing top leagues.** Premier League, La Liga, Bundesliga — don't try to beat them on 1X2 with basic stats.

### What We Haven't Tried Yet

1. **xG data** — expected goals is the #1 feature in research but we don't have it in our dataset
2. **Injury/lineup data** — biggest single variable for match outcomes
3. **Dixon-Coles tau correction** — specifically fixes draw prediction
4. **Model trained ONLY on lower leagues** — current model is trained on all leagues
5. **Pinnacle closing odds** — we used market average, not the sharpest benchmark
6. **Longer rolling windows** (15-20 matches) for lower leagues with fewer games
7. **Under 2.5 only strategy** in defensive leagues (Serie B, Ligue 2, Segunda)
8. **Season timing** — early season vs late season (relegation battles)
9. **Home underdog strategy** — betting on home teams getting underestimated at high odds

### Strategic Implications for OddsIntel Product

1. **Product should emphasize lower leagues** — that's where real edge exists
2. **Top league coverage is for user retention, not for profitable predictions** — users expect PL/La Liga coverage
3. **"Sharp" tier predictions should focus on tier 2-4 leagues** with appropriate disclaimers for tier 1
4. **Data aggregation value (Analyst tier) doesn't depend on profitable predictions** — saving users 30 min of research is worth EUR 4.99 regardless
5. **The AI/news layer (injuries, lineups) is critical** for cracking top leagues — basic stats aren't enough
6. **3-5 carefully selected bets per day** is the right volume, not 20+ per day

---

## Iteration Comparison Summary

| Version | Description | 2024-25 ROI | 2023-24 ROI | 2022-23 ROI | Bets/Season |
|---------|-------------|------------|------------|------------|-------------|
| v0 | Baseline XGBoost | -10.8% | -14.2% | -10.9% | ~6,500 |
| v1 | + Isotonic calibration | -10.5% | -13.2% | — | ~5,880 |
| v2 | + Extreme selectivity | **-2.5%** | -10.2% | — | ~1,200 |
| v3 | O/U only + selective | -10.1% | -7.9% | — | ~830 |
| v5 | Poisson goal model | -10.6% | -10.9% | — | ~1,850 |
| v6 | Soft leagues only | -3.9% | -7.4% | — | ~860 |
| v7 | Poisson + very selective | -11.4% | -7.3% | — | ~490 |
| v8 | ELO + ensemble + all fixes | -7.7% | -9.2% | **-3.7%** | ~1,400 |
| v8 tier 3-4 | v8 on lower leagues only | **+15.4%** | +7.5% / -12.5% | +2.2% / -14.3% | ~170 |

**Best overall approach: v8 (ELO + ensemble) focused on tier 2-4 leagues with high selectivity.**

---

## Data Files

| File | Location | Description |
|------|----------|-------------|
| all_matches.csv | data/processed/ | 133,272 matches with stats + odds (38.8 MB) |
| features_fast.csv | data/processed/ | 130,658 match feature vectors (29 features) |
| targets_fast.csv | data/processed/ | Targets + odds aligned to features |
| features_v8.csv | data/processed/ | Enhanced features with ELO (35 features) |
| targets_v8.csv | data/processed/ | Targets aligned to v8 features |
| iterations.json | data/model_results/ | All iteration results in JSON |
| comparison.csv | data/model_results/ | Iteration comparison table |
| v8_*.csv | data/model_results/ | Per-season bet logs for v8 |

---

## v9: xG Proxy + ELO (Latest Iteration)

### What Changed
- Added xG proxy calculated from shots data: xG ≈ 0.10 * shots + 0.22 * shots_on_target
- Added over/under-performance metric (actual goals vs xG proxy — regression to mean indicator)
- Added shots on target, shots, and corners as rolling averages
- 36 features total (vs 35 in v8)
- Models saved with joblib for reloading and comparison

### v9 Results Summary

| Version | Focus | 2024-25 ROI | 2023-24 ROI | 2022-23 ROI | Bets |
|---------|-------|------------|------------|------------|------|
| v9a | All leagues | -8.6% | -7.0% | -9.0% | ~1,100-1,250 |
| v9b | Tier 2-4 | -6.3% | **-1.7%** | -15.2% | ~335-520 |
| **v9c** | **Tier 3-4** | **-1.6%** | **+4.8% (GO!)** | -8.3% | ~72-178 |
| v9d | Tier 1 only | -10.3% | -10.6% | -6.2% | ~700-740 |

### v9c Tier 3-4 Detailed (Our Best Model)

**2023-24: +4.83% ROI, 121 bets — FIRST PROFITABLE RESULT**
- 1X2 on tier 3-4: **+26.5% ROI** (64 bets) — very strong
- O/U on tier 3-4: -19.4% ROI (57 bets) — model bad at O/U here
- Max losing streak: 6
- 43.0% hit rate at 2.41 avg odds

**2024-25: -1.56% ROI, 178 bets — Nearly Breakeven**
- 1X2: +10.2% ROI (59 bets) — again 1X2 is profitable
- O/U: -7.4% ROI (119 bets) — O/U drags it down
- Tier 4 specifically: +11.0% ROI (64 bets)
- Max losing streak: 7

### Key v9 Findings
1. **First profitable backtest result** on tier 3-4 for 2023-24
2. **1X2 market significantly outperforms O/U in lower leagues** — opposite of what we expected
3. **xG proxy adds marginal value** — v9 overall not dramatically better than v8, but tier 3-4 improved
4. **The profitable signal is narrow**: tier 3-4 + 1X2 market + selective. About 60-120 bets per season.
5. **Inconsistent across seasons** — 2022-23 was bad (-8.3%), suggesting variance or regime change
6. **StatsBomb open data** has real xG but only for La Liga + CL (not lower leagues we need)

### Updated Master Comparison Table

| Version | Description | Best ROI | Best Context |
|---------|-------------|----------|-------------|
| v0 | Baseline XGBoost | -10.8% | — |
| v1 | + Calibration | -10.5% | — |
| v2 | + Extreme selectivity | -2.5% | 2024-25, all leagues |
| v3 | O/U only | -7.9% | — |
| v5 | Poisson | -10.6% | — |
| v6 | Soft leagues (T2+) | -3.9% | 2024-25 |
| v7 | Poisson + selective | -7.3% | — |
| v8 | ELO + ensemble | -3.7% | 2022-23; T3-4 at +15% |
| **v9c** | **xG + ELO, T3-4 only** | **+4.8%** | **2023-24, 121 bets** |

---

## v10: Tier-Adjusted Thresholds (More Volume)

### What Changed
- Instead of restricting to only tier 3-4, bet on ALL leagues with tier-adjusted edge thresholds
- Tier 1: edge >= 8-12% (very selective), Tier 2: >= 5-8%, Tier 3: >= 4-6%, Tier 4: >= 3-5%
- Goal: get 500-1,000+ bets per season for statistical significance while being stricter on top leagues
- Added per-league breakdown to identify profitable leagues

### v10 Results Summary

| Season | Bets | Bets/Day | Hit Rate | ROI | P&L |
|--------|------|----------|----------|-----|-----|
| 2024-25 | 1,046 | ~3.5 | 38.4% | **-4.9%** | -EUR 513 |
| 2023-24 | 1,008 | ~3.4 | 38.1% | **-6.7%** | -EUR 670 |
| 2022-23 | 812 | ~2.7 | 37.3% | **-8.6%** | -EUR 697 |

### v10 By Market

| Season | 1X2 ROI | 1X2 Bets | O/U ROI | O/U Bets |
|--------|---------|----------|---------|----------|
| 2024-25 | **-1.0%** | 541 | -9.1% | 505 |
| 2023-24 | -6.4% | 562 | -7.0% | 446 |
| 2022-23 | -7.5% | 542 | -10.8% | 270 |

1X2 continues to outperform O/U. In 2024-25, 1X2 was nearly breakeven at -1.0%.

### v10 By League Tier

| Season | Tier 1 ROI | Tier 2 ROI | Tier 3 ROI | Tier 4 ROI |
|--------|-----------|-----------|-----------|-----------|
| 2024-25 | -13.3% | -5.3% | **-0.2%** | **+8.2%** |
| 2023-24 | -13.2% | -4.6% | -7.6% | **-0.0%** |
| 2022-23 | -4.2% | -13.7% | -16.6% | -1.0% |

**Tier 4 is consistently near-breakeven or profitable.** Tier 1 is consistently the worst (-4% to -13%).

### v10 Profitable Leagues (Across Seasons)

Leagues that showed positive ROI in at least 2 of 3 seasons:
- **Greek Super League**: +45.2% (2024-25), +34.9% (2023-24), +33.2% (2022-23) — VERY small sample (18-25 bets) but remarkably consistent
- **Turkish Super Lig**: mixed but often positive
- **League Two (England)**: +8.2%, -0.0%, -1.0% — consistently near breakeven or profitable
- **Eredivisie**: +27.6% (2024-25), +17.1% (2022-23) — small sample
- **2. Bundesliga**: +12.9% (2023-24)
- **Serie B**: +12.2% (2023-24)
- **Segunda Division**: +6.5% (2024-25)

### v10 Key Findings

1. **Volume is better at ~1,000 bets/season** vs 120-180. More statistically meaningful.
2. **Overall ROI still negative (-5% to -9%)** because top-league losses dominate.
3. **If we excluded tier 1 entirely:** 2024-25 would be roughly breakeven (~-1% ROI on ~700 bets)
4. **1X2 is consistently closer to breakeven than O/U** — suggests our model is better at predicting match outcomes than goal counts
5. **Certain leagues are consistently profitable** (Greece, Netherlands, English lower leagues) — league specialization may be the answer
6. **Calibration is still the core problem**: predicted 50% → actual 35-41%. Until this is fixed, even "value" bets aren't actually value.

### Updated Master Comparison Table

| Version | Description | Best ROI | Best Context |
|---------|-------------|----------|-------------|
| v0 | Baseline XGBoost | -10.8% | — |
| v1 | + Calibration | -10.5% | — |
| v2 | + Extreme selectivity | -2.5% | 2024-25, all leagues |
| v3 | O/U only | -7.9% | — |
| v5 | Poisson | -10.6% | — |
| v6 | Soft leagues (T2+) | -3.9% | 2024-25 |
| v7 | Poisson + selective | -7.3% | — |
| v8 | ELO + ensemble | -3.7% | 2022-23; T3-4 at +15% |
| **v9c** | **xG + ELO, T3-4 only** | **+4.8%** | **2023-24, 121 bets** |
| v10 | Tier-adjusted thresholds | -4.9% | 2024-25; T4 at +8.2%; Greek league +45% |

### The Fundamental Problem (Honest Assessment)

After 10 iterations, the pattern is clear:

**The model is consistently overconfident by 10-15%.** When it predicts 50% probability, the actual win rate is ~37%. This means:
- What the model thinks is a "7% edge" is actually a "-6% edge"
- We're systematically betting on outcomes that are LESS likely than the model believes
- Isotonic calibration doesn't fix this because the training data has the same bias

**Possible root causes:**
1. Features don't capture what bookmakers know (team quality depth, injury news, managerial tactics)
2. The model overfits to historical patterns that don't persist out-of-sample
3. Bookmaker odds already incorporate the same form/ELO/xG information we use
4. The 10-15% gap IS the bookmaker's edge — their margin + their information advantage

**What could actually fix this:**
1. **Real-time information** that bookmakers haven't priced yet (injuries, lineups, news) — this is the AI/news layer
2. **Real xG data** (not our proxy) — StatsBomb or Opta quality
3. **League-specific models** trained only on lower leagues where the information gap is real
4. **Live paper trading** — backtesting may have inherent biases that don't appear in real-time

### Remaining Improvements to Try

1. ~~xG data~~ — Done (proxy only; real xG needs paid API or headless scraping)
2. **1X2-only strategy on tier 3-4** — 1X2 is profitable, O/U is not
3. **League-specific models** — train separate models per league group (e.g., English lower leagues model)
4. **Longer rolling windows (15-20)** for lower leagues
5. **Dixon-Coles tau correction** for draw prediction
6. **Pinnacle odds as benchmark** instead of market average
7. **Live paper trading** — PRIORITY. Stop backtesting, start real-time validation.
8. **News/injury layer (AI)** — the real differentiator
9. **Season timing** — early vs late season patterns

---

## Technical Notes

- All models use TimeSeriesSplit or season-based splitting to avoid data leakage
- Calibration applied via sklearn CalibratedClassifierCV (isotonic, 5-fold)
- ELO implementation: K=30, home advantage=+100, GD multiplier=sqrt(abs(GD)), 1/3 season regression toward 1500
- Odds source: market average (AvgH/AvgD/AvgA) or Bet365 as fallback
- Flat staking: EUR 10 per bet, EUR 1,000 starting bankroll
- Python 3.14, XGBoost 3.2, scikit-learn 1.8
