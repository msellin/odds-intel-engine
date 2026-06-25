# Tennis model backtest — 2026-06-25

Re-ran the three existing tennis backtest scripts on local data (tennis-data.co.uk
historical xlsx files, 2005-2025, commercial-OK license). Train 2005-2021,
test 2022-2024.

## Headline

**No simple model beats Pinnacle on tennis.** Pinnacle prices favourites at
~68% accuracy; our models top out at 64%. The 4pp gap translates to consistent
-6 to -10% ROI at every edge threshold across every surface and tier.

## Per-script results

### `backtest_tennis_elo.py` — surface-weighted Elo
- Verdict: ❌ unprofitable
- Best segment: Grass surface, -6.9% ROI (the least-bad loser)
- ROI degrading year-over-year: 2022 -6.3% → 2024 -15.4%
- Underdog-only betting (odds > 2.00, edge ≥ 5%): -100% ROI / 0% win rate (signal-free)
- ELO disagrees with Pinnacle on 1396 matches; ELO is correct on only 39.6% of those
- Accuracy gap: Pinnacle 68.0% vs ELO 64.3%

### `backtest_tennis_markov.py` — hierarchical serve-point Markov + ranking blend
- Verdict: ❌ unprofitable. Best blend (α=0.0 pure ranking, edge ≥3%): -10.6%
- Accuracy: Markov 58.7%, ranking-logistic 64.0%, Pinnacle 68.0%
- Best segment: Masters tier, -5.5% ROI
- Serve stats don't add edge vs Pinnacle in this setup

### `backtest_tennis_advanced.py` — serve-stats + ranking ensemble
- Verdict: ❌ unprofitable
- Serve-model in-sample accuracy peaks at Grand Slam tier (66.4%), Grass (63.7%)
- Confirms the literature: serve stats close ~2pp of the gap, rankings close another ~1pp,
  combining everything gets to 67-68% — still BELOW Pinnacle, marginally neutral ROI

## What works elsewhere (literature)

| Approach | Reported ROI | Notes |
|---|---|---|
| Knottenbelt (2012) hierarchical Markov on POINTS | +3.8% on 2,173 Grand Slam matches | Models each point (1st serve %, BP save%), not sets — different feature granularity |
| Weighted Elo on serve games | 65-66% accuracy | Bigger lift than match-result Elo |
| Surface-weighted Elo decay (recent form) | +1-2pp accuracy | Cheap to add |
| Combining serve + rankings + surface + form + H2H | 67-68% | Theoretical neutral ROI vs Pinnacle |

## Strategic takeaways for TENNIS-PAPER-BETS

1. **Building our own tennis predictor is not the lever.** A 2-3 week engineering
   sprint on a Knottenbelt-style points Markov might get us to +3-4% ROI on Grand
   Slams (best case). Not the highest-EV use of engineering time given soccer +
   CS2 already work.

2. **The Phase 4 "Coolbet sharp-vs-soft" strategy is the correct framing.** We're
   NOT trying to outpredict the market; we're looking for soft books that lag
   Pinnacle's pricing — and the slow books are most likely to lag on lower-tier
   tournaments (Challenger / ITF / Futures) where attention is thin.

3. **The real bottleneck is the SHARP REFERENCE for Challenger/ITF**, not
   prediction. The Odds API free tier excludes lower tiers. Pinnacle direct API
   closed July 2025. OddsPapi free tier (250 req/mo) is the only path that
   covered them and we busted that quota.

4. **If we want a tennis predictor anyway**, the Knottenbelt points-Markov on
   Grand Slams is the highest-EV next step (per literature). Estimated 2-3 weeks.
   ITF/Challenger don't have point-by-point data available, so this approach is
   bounded to Slams + Masters.

5. **License-clean training data is feasible** — tennis-data.co.uk historical
   xlsx files we already have locally cover 2005-2025 with PSW/PSL (Pinnacle)
   closes. Even though the site is currently down (ECONNREFUSED), the historical
   files are safe to use commercially per their original terms.

## Recommendation

**Don't build a tennis model now.** Accept that Pinnacle is sharper than what
we can build in days/weeks. Spend the engineering on:

- (Highest ROI) Find a paid sharp-odds source for Challenger/ITF (OddsPapi
  paid tier ~$10-30/mo configurator-based, or api-tennis.com Business $80/mo).
  This unblocks the Phase 4 volume directly.
- (Cheapest) Wait for the Coolbet-only training pond to accumulate 10-50K rows,
  THEN revisit modeling with N×30 the data and decide if Knottenbelt is worth
  the build.

## Files

- `scripts/backtest_tennis_elo.py` — Elo, 311 lines
- `scripts/backtest_tennis_markov.py` — Markov + ranking blend, 514 lines
- `scripts/backtest_tennis_advanced.py` — serve-stats ensemble, 430 lines
- All read `data/raw/tennis/tennis_odds_YYYY.xlsx` (commercial-OK license)
- Sackmann `atp_matches_YYYY.csv` (CC-BY-NC-SA, research-only) is used by some
  scripts for serve-stat features — fine for analysis, not for production deployment
