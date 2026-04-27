# Model Improvement Research — Key Findings

## What Actually Matters (ranked)

1. **ELO ratings** — highest single-feature predictive power for football. Champions League study: 88% accuracy, 52% ROI
2. **xG rolling averages** (5 and 10 match) — tier 1 feature, better than raw goals
3. **Dixon-Coles tau correction** — fixes Poisson's underestimation of draws/low-scoring
4. **Isotonic calibration on held-out data** — 22% ECE improvement, +34.69% ROI in NBA test
5. **Favorite-longshot bias correction** — require 10%+ edge on longshots, 5% on favorites
6. **Extreme selectivity** — SoccerPredictor: 33.4% ROI betting on only 32/150 matches (21%)
7. **Fractional Kelly (0.25x)** — full Kelly leads to bankruptcy 100% of the time

## Specific Numbers from Research

- XGBoost with good features: 56-58% match outcome accuracy
- CatBoost + pi-ratings won 2017 Soccer Prediction Challenge: 55.82% accuracy, 0.1925 RPS
- Wharton study: 0.05 edge threshold, 8.5% selectivity → $10K profit on $50 stakes
- Isotonic > Platt scaling by ~22% for sports betting calibration
- ELO K-factor: 30 for league, home advantage: +100, GD multiplier: sqrt(goal_diff)

## GitHub Models to Study

- jkrusina/SoccerPredictor — 33.4% ROI, 21% selectivity (most relevant)
- dashee87/blogScripts — Dixon-Coles Python implementation
- kochlisGit/ProphitBet-Soccer-Bets-Predictor — neural nets + ensembles

## Key Insight for Our Model

Our v2 iteration (-2.5% ROI) was the closest. The gap is:
- Missing ELO (biggest single improvement)
- Missing xG data
- Not correcting for favorite-longshot bias
- Not using Dixon-Coles for goal distributions
