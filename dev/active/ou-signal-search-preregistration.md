# OU-SIGNAL-SEARCH — pre-registration (locked 2026-09-04, before any holdout run)

Committed **before** the holdout was touched. The point of writing it down is
that 2026-09-03 produced three findings that looked real and were sampling
artefacts; a search over 22,814 matches will manufacture more unless the
hypotheses and the stopping rule are fixed in advance.

## Data

- Universe: **22,814** settled matches since 2026-05-01 with paired pre-kickoff
  over/under 2.5 prices at an accessible book (best price across
  `ACCESSIBLE_BOOKMAKERS`, latest quote per book at or before kickoff).
- **TRAIN: 14,083** (May–Jul).  **HOLDOUT: 8,731** (Aug 1 – Sep 4).
- Over-2.5 base rate 0.5434. Mean best prices: over 1.929, under 2.202.
- **Overround at best prices: 1.0469.** Any rule must beat a 4.69% market edge;
  this is why "predicts goals" is not the same as "profitable".

## Design

The market price is the benchmark, not the base rate. De-vig the pair:

    p_mkt = (1/over) / (1/over + 1/under)

and define the residual `y = 1[total > 2.5] - p_mkt`. A feature is only useful
if it predicts **the residual** — i.e. tells us something the price does not
already contain. Testing against the raw outcome would rediscover the market.

## Pre-registered features (10, fixed)

1. `elo_diff` (absolute — mismatch vs even game)
2. `elo_home + elo_away` (overall quality)
3. combined attack: `goals_for_avg_home + goals_for_avg_away`
4. combined defence: `goals_against_avg_home + goals_against_avg_away`
5. attack-minus-defence: (3) − (4)
6. `season_progress`
7. `league_draw_rate_ytd`
8. `rest_days_home + rest_days_away`
9. `league tier`
10. `p_mkt` itself (is the market miscalibrated at particular price levels?)

## Stopping rule — fixed in advance

- Screen all 10 on **TRAIN only**, Bonferroni α = 0.05/10 = 0.005.
- Anything that survives becomes **one** betting rule, direction and threshold
  chosen on TRAIN, then evaluated **once** on the holdout. No re-tuning after
  seeing holdout numbers; a second look invalidates the first.
- A rule must fire on **≥2,000 holdout matches**. Below that the detectable
  effect (~7% ROI) exceeds any plausible edge, so a "win" would be noise.
- If nothing survives TRAIN, the answer is **no signal** and it gets reported as
  such. A null result here is a real result: it says the remaining O/U headroom
  is not in the features we hold.

## What would make me distrust a positive result

- Effect concentrated in <3 leagues or one month.
- Rule fires on a narrow price band (that is a market-microstructure artefact,
  not a football signal).
- Sign flips between TRAIN and HOLDOUT.
