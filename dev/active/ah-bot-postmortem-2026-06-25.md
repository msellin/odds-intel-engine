# AH Bot Post-Mortem — `bot_ah_home_fav` (2026-06-25)

Retired 2026-06-24 at **-13.63% ROI / 132 settled bets / -€94 PnL**.
Filed: PRIORITY_QUEUE.md → AH-BOT-RETIRE entry.

## TL;DR

The bot was systematically **overconfident on home favourites covering
AH lines**, specifically the -0.5 and -1.0 lines where 96 of 132 bets
(73%) landed. Diagnosis is not "AH lines are unprofitable in our
universe" — it's "our model treats the handicap line the same way it
treats moneyline, and ignores draw mass." A draw-aware AH probability
formula would likely revive the strategy.

## Per-selection breakdown

| Selection | n | hit% | ROI | avg odds | model p | cal p |
|---|---|---|---|---|---|---|
| **home -0.5** | **76** | 47.4% | **-15.81%** | 1.89 | 0.661 | 0.652 |
| **home -1** | **20** | 50.0% | **-5.02%** | 1.93 | 0.644 | 0.638 |
| **home +0 (DNB)** | 11 | 45.5% | **-28.78%** | 1.73 | 0.770 | 0.770 |
| home +0.5 | 8 | 50.0% | +6.06% | 1.82 | 0.695 | 0.695 |
| home -1.5 | 6 | 33.3% | **-35.19%** | 1.80 | 0.655 | 0.655 |
| home -2 | 4 | 0.0% | **-100.00%** | 1.98 | 0.632 | 0.632 |
| home +1 | 4 | 75.0% | +13.85% | 1.67 | 0.762 | 0.762 |
| home +1.5 | 3 | 100.0% | +69.22% | 1.63 | 0.771 | 0.761 |
| home +2 | 1 | 100.0% | +50.11% | 1.50 | 0.781 | 0.781 |

**96 of 132 bets are "home -X" (the favorite covering).** Together they
return -16.5% on €510 staked. The 24 "home +X" bets (taking the home
with handicap support) return +14% on €140 — those land at small n but
all positive directionally.

## Mechanism — why "home -X" bets lose

The model predicts moneyline probabilities, then maps them onto AH
selections via a simple "if p(home win) > p(implied AH)" check. The
problem is that this check **doesn't account for draw mass**:

```
P(home wins straight up) = P(home wins by 1+)
P(home covers -0.5)      = P(home wins by 1+)         ✓ (same)
P(home covers -1.0)      = P(home wins by 2+) + 0.5 × P(home wins by 1)
P(home covers -1.5)      = P(home wins by 2+)
P(home covers -0/+0)     = P(home wins by 1+) / (1 − P(draw))  (DNB)
```

In our model, `model_probability` for AH is being set to the
moneyline win probability — which is right for -0.5 but wrong for
-1.0, -1.5, -2.0 and the DNB +0 selection. Specifically:

- **home -1.0**: needs win by 2+ (or half-stake refund on win-by-1).
  Model uses p(win) = 0.644 → believes the bet has edge at 1.93 odds
  (implied 0.518). But actual probability of winning by 2+ is much
  lower (~0.40-0.45 in our cohort), and the half-stake refund only
  partially compensates. Net: hit rate 50% matches our prediction
  but the half-refund on wins-by-1 drags ROI to -5%.
- **home -1.5, -2.0**: same overconfidence, larger gap.
- **home +0 (DNB)**: needs to remove draw mass from the denominator.
  Model uses raw p(win) = 0.770; correct DNB probability is
  p(win) / (1 - p(draw)) — even higher numerator but should be
  compared to the DNB odds (1.73 implied 0.578). Model still says
  "edge!" but DNB picks consistently fall short — likely because the
  bot's matches are exactly the ones where draws are MORE likely than
  the prior (low-tier or balanced fixtures).

## Why "home +X" bets win

Symmetric mirror of the above. When the bot picks "home +0.5", it's
TAKING the home with half-goal handicap protection. Model says
p(win) = 0.695; actual outcome only needs the home team to win OR draw
(p ≈ 0.695 + p_draw ≈ 0.85). Same overconfidence on raw p(win) but
the AH selection happens to forgive the error. **The bet "wins by
accident"** — not because the model is right, but because the model's
overconfidence is on the side that benefits from draw mass.

## Calibration table mismatch

`clv_pinnacle` is NULL on all 132 settled AH bets. Looking at
`workers/jobs/settlement.py:get_pinnacle_closing_odds()`, the AH branch
DOES match on `handicap_line` — but our snapshots rarely have that
field populated for Pinnacle AH lines (Pinnacle publishes AH but our
fetch indexes by selection-string, e.g. "home -1.5" rather than
selection="home" + handicap_line=-1.5). Net: no CLV measurement for
AH at all. That's an orthogonal bug worth filing.

## Recommended fixes (when reviving AH)

1. **Per-handicap-line probability mapping.** Instead of using
   moneyline p(win), build a Poisson-based formula that produces
   p(home covers X) for each X. Same Poisson lambdas, different
   marginal — straightforward to compute in `workers/jobs/daily_pipeline_v2.py`
   AH-selection generation.

2. **Per-handicap-line Platt calibration.** Treat `home -0.5`,
   `home -1.0`, `home -1.5`, `home +0` (DNB) as **separate
   markets** in the Platt fit, not one aggregate "asian_handicap"
   key. The current Platt aggregate masks the per-line miscalibration.

3. **Fix Pinnacle CLV indexing for AH.** Either store
   `handicap_line` in odds_snapshots properly OR derive the line
   from the selection string. Without CLV measurement we can't tell
   whether a revived AH bot is improving or just running variance.

4. **Smaller-bankroll trial.** Re-launch with `bot_ah_home_fav_v2`
   in beta tier (paper-only) at lower stake until 60+ settled bets
   show ROI ≥ +3% AND CLV ≥ +1%. Same maturity-gate the calibrated
   AH bot would have needed.

## Decision

Stay retired. The fixes above are real work (~1-2 weeks for a
per-line Poisson + per-line Platt + AH-CLV-indexing). Worth doing
when product priorities allow but not the most impactful next move
— OU 2.5 underperformance vs Forebet (-25pp) is a bigger gap to
close on a market we're already in.

## Related items filed

- This document — `dev/active/ah-bot-postmortem-2026-06-25.md`
- PRIORITY_QUEUE entry — add AH-PER-LINE-CALIBRATION as a future P3
  task, gated on bandwidth.
- AH-CLV-INDEXING-BUG — `clv_pinnacle` NULL on all AH bets, file
  separately for the next CLV-data audit.
