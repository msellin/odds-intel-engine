# Sport Expansion — Soccer Lessons Learned

What we built for soccer and what to do differently (faster, smarter) for any new sport.
Written 2026-06-07 from MODEL_WHITEPAPER.md, MODEL_ANALYSIS.md, WORKFLOWS.md.

---

## What We Built for Soccer (The Full Stack)

| Layer | What exists | Key file |
|-------|-------------|----------|
| Data ingestion | API-Football Ultra ($29/mo), 280+ leagues, 13 bookmakers | `workers/scheduler.py` |
| Feature store | `match_feature_vectors` (42 cols, 48K+ settled rows) | `supabase_client.py` |
| ELO | Global ratings, K=30, home +100 pts, goal-diff multiplier | `settlement.py` |
| Primary model | Poisson (Dixon-Coles) + XGBoost ensemble, 50/50 blend | `xgboost_ensemble.py` |
| Calibration | Stage 1 tier-shrinkage + Stage 2 Platt sigmoid, weekly refit | `improvements.py` |
| Edge detection | Kelly 0.15x, tier-gated thresholds, Pinnacle veto, odds drift filter | `improvements.py` |
| Meta-model | B-ML3 XGBoost on fired-bets only (AUC 0.67, activated 2026-06-07) | `train_b_ml3.py` |
| Bot strategies | 26 bots: pre-match + in-play, different markets/tiers/edges | `BOTS_CONFIG` in pipeline |
| Validation | CLV (primary), ECE, per-league ROI, shadow runs, cohort A/B testing | `check_calibration.py` |

---

## Lessons by Category

### 1. Data Architecture — Do This First

**Lesson: Pinnacle = anchor for everything. Without it, you can't devig, can't validate, can't calibrate.**
- Get Pinnacle odds from day 1. All our calibration relies on Pinnacle as the "true probability" anchor.
- Without Pinnacle, CLV is meaningless, Stage 1 shrinkage has no anchor, and the Pinnacle veto can't fire.
- For any new sport: find where to get Pinnacle odds first, before anything else.

**Lesson: Build the feature vector table early, not late.**
- We spent months building on the Kaggle v9a dataset before switching to live AF data.
- The live feature store (`match_feature_vectors`) was built in Stage 0 of ML-PIPELINE-UNIFY.
- For a new sport: design the MFV table schema in week 1 and start filling it immediately.

**Lesson: Missing data indicators carry real signal.**
- `h2h_win_pct_missing`, `pinnacle_implied_home_missing` etc are actual features, not just flags.
- Saar-Tsechansky & Provost (2007): matches KNN imputation at 1/100th the cost.
- For a new sport: always add `_missing` columns alongside imputed values.

**Lesson: Data quality gates at ingestion prevent downstream fires.**
- The `api-football` synthetic source had 100% invalid O/U pairs (implied sum 0.63).
- We caught it only after phantom P&L appeared in bots.
- For a new sport: add implied-sum sanity check on day 1 (`1/over + 1/under >= 1.02`).

### 2. Model Architecture — What Actually Works

**Lesson: Poisson + XGBoost ensemble beats either alone.**
- Poisson excels at goal-total markets (O/U, BTTS) by modeling the full score distribution.
- XGBoost excels at incorporating categorical signals (tier, trend, news).
- Blend weight starts at 50/50, then auto-learned weekly per `fit_blend_weights.py`.
- For a new sport: the equivalent is (outcome distribution model) + (classification model).
  - Tennis: Markov serve-point model + XGBoost on match-level features
  - Baseball: Poisson run model + XGBoost on pitcher/lineup features

**Lesson: Two-stage calibration is non-negotiable.**
- Raw model probabilities are 10-15% overconfident. Always.
- Stage 1: shrink toward Pinnacle implied (alpha = 0.20 top tier, 0.65 lower tier)
- Stage 2: Platt sigmoid fitted on settled bets. For O/U: add `log(odds)` as 2nd feature.
- For a new sport: deploy calibration in week 1, not when you notice it's wrong.

**Lesson: The 30-50% probability bin is where calibration fails hardest.**
- Systematic overconfidence of 12-16pp in this bin (observed in our live data).
- Root cause: double-counted home advantage (Poisson + XGBoost both encode it).
- For a new sport: audit the 30-50% bin first. This is where money gets lost.

**Lesson: ECE < 3% is the quality gate. Track it weekly.**
- Expected Calibration Error measures predicted vs actual across 20 bins.
- Don't trust ROI in first 500 bets. Trust ECE — it's calculable with 100+ samples.
- For a new sport: ship `check_calibration.py` equivalent in week 1.

### 3. Edge Detection — What the Thresholds Mean

**Lesson: The Pinnacle disagreement veto (gap > 0.12) prevents 80% of catastrophic losses.**
- If `model_prob - pinnacle_implied > 0.12` → don't bet. The gap is almost always phantom.
- Caught 22/34 losses while filtering only 6/40 wins in early live data.
- For a new sport: deploy this gate before you place a single real bet.

**Lesson: Lower-tier, less-covered leagues show more edge.**
- Soccer backtest (354K matches): Tier 4 ROI = -7.0% vs Tier 1 = -12.4%.
- 12 of our 22 consistently-profitable leagues are Tier 3-4.
- For a new sport: look for the equivalent of "lower tier" — challenger tennis, minor league sports.

**Lesson: Odds movement filter prevents betting against sharp money.**
- `drift < -10%` = hard veto (market moved strongly against our pick)
- This is even more important for sports where late injury news matters (baseball: pitcher scratch)
- For a new sport: implement odds movement tracking before betting goes live.

**Lesson: Lineup confirmation = +12pp ROI signal.**
- Bets on matches with confirmed lineups: +8.1% ROI. Without: -4.5%.
- This is the single strongest discovered signal in our whole stack.
- For a new sport equivalent: anything that confirms "the key player is playing" (pitcher confirmed for MLB, top seed present for tennis).

### 4. Bot Strategy — How to Find What Works

**Lesson: Run multiple bots with different parameters from day 1.**
- 26 bots running simultaneously revealed which markets/tiers/timings actually work.
- Shadow runs (no real money) let you evaluate 32 timing snapshots per day.
- For a new sport: design 4-6 bots with different edge thresholds from the start.

**Lesson: The meta-model (B-ML3) took too long to build.**
- B-ML3 was designed in month 2 but only activated in month 2.5 (June 2026) after multiple false starts.
- The critical mistake: training on all MFV rows, not on fired-bets-only. Inverted signal (Q5 < Q1).
- The fix: train ONLY on actual fired bets with real CLV. AUC 0.67 vs 0.57 prior.
- For a new sport: build the meta-model early, but TRAIN IT ON FIRED BETS ONLY.

**Lesson: CLV tracking from day 1 is more important than ROI.**
- ROI takes 500+ bets to be statistically meaningful. CLV signals quality after 50 bets.
- Positive CLV (beating Pinnacle closing line) = real edge. Negative = adjust model.
- For a new sport: deploy pseudo-CLV tracking in week 1.

**Lesson: Timing cohort matters.**
- Morning vs midday vs pre-KO bets have measurably different CLV profiles.
- We run 32 shadow snapshots per day to find optimal timing.
- For a new sport: A/B test 2-3 timing windows from the start.

### 5. Operational — What to Do on Day 1

**Lesson: Railway is the right scheduler, not GitHub Actions.**
- Railway ($5/mo) runs a long-running Python process. No cold-start delays.
- GitHub Actions crons had timing jitter and cold-start overhead.
- For a new sport: plug into the existing Railway scheduler from day 1.

**Lesson: Smoke tests before every commit. No exceptions.**
- Silent failures (InplayBot UUID bug) burned 11 days because "0 bets" looks normal.
- Every new job needs a smoke test in `scripts/smoke_test.py`.
- For a new sport: add smoke tests as you build, not at the end.

**Lesson: Document the data source architecture before writing code.**
- We lost time when Kambi was found to duplicate API-Football data (removed 2026-05-06).
- For a new sport: map all data flows in DATA_SOURCES.md first, then code.

**Lesson: The PRIORITY_QUEUE prevents two agents duplicating work.**
- Mark tasks `🔄 In Progress` before touching code.
- For a new sport: add the sport expansion tasks to PRIORITY_QUEUE.md before starting.

### 6. Things That Didn't Work (Skip These)

| Approach | Why it failed |
|----------|--------------|
| Kaggle-only training data | No live updating; stale quickly; no match-level feature granularity |
| Global static rho for Dixon-Coles | Should be per league tier (script: `fit_league_rho.py`) |
| Using all MFV rows for meta-model | Training distribution ≠ inference distribution → inverted signal |
| Manual Platt fitting | Needs weekly automation; manual = 4-8 week lag |
| Single global edge threshold | Per-market floors needed: 1X2=10%, O/U=3%, BTTS=10% |
| Trusting "0 bets" as normal | Check for silent failures — 0 bets can mean the bot is broken |
| Feature coverage < 30% | Can't learn reliably even with `_missing` indicators |
| Double Chance market | Losing at every edge threshold tested. Don't build it for new sports. |

---

## What to Do Differently for a New Sport

In priority order:

1. **Find Pinnacle odds source first** — everything else depends on it
2. **Design the feature vector table schema on day 1** — don't retrofit it later
3. **Deploy calibration (Platt + shrinkage) in week 1** — not when you notice miscalibration
4. **Track CLV from day 1** — don't wait for enough bets to trust ROI
5. **Deploy the Pinnacle disagreement veto before any real bets** — saves catastrophic losses
6. **Build 4-6 shadow bots with different strategies** — discover what works empirically
7. **Train meta-model on fired-bets-only** — not all MFV rows
8. **Add smoke tests as you build** — not at the end
9. **Audit the 30-50% probability bin weekly** — most likely calibration failure zone
10. **Look for lower-tier/smaller-event edges** — efficiency is always lower there

---

## Architecture Reuse for Tennis (Immediate)

Most of the soccer stack can be reused for tennis with these adaptations:

| Soccer component | Tennis adaptation |
|-----------------|-------------------|
| ELO (K=30, home +100) | ELO (K=16-40 by tourney tier, NO home advantage) |
| Poisson goals model | Markov serve-point model (per-point win → game → set → match) |
| XGBoost classifier | XGBoost on match-level features (rank ratio, surface Elo, serve stats) |
| Dixon-Coles low-score correction | Tie-break model (point-level model already handles this) |
| Tier shrinkage (A=0.20, D=0.65) | Same concept — Grand Slams more efficient, Challengers less |
| Pinnacle de-vig (PSW, PSL cols) | Already in tennis-data.co.uk xlsx (PSW/PSL columns) |
| CLV = `(odds_at_pick / closing) - 1` | Identical formula |
| B-ML3 meta-model | Same concept — train on fired bets with real CLV |

Key tennis-specific signals NOT in soccer stack:
- Surface Elo (separate ratings per surface: Hard, Clay, Grass)
- Serve win rate (rolling 30 matches, surface-specific)
- Break point save/conversion rate
- Head-to-head on same surface
- Tournament bracket position (seeding)
- Days since last match (fatigue — more important in tennis than soccer)
