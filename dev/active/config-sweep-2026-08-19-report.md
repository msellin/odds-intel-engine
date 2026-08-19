# CONFIG-SWEEP-2026-08-19 — Report

## What ran

7,560 configs × 3 walk-forward windows over 32,290 finished matches
(2026-05-01 → 2026-08-12). 81,980 evaluable (match × market × selection)
rows after dropping fantasy-price outliers (313 rows with pick/close
ratio ≥ 1.65×).

Acceptance criteria: positive ROI in ALL three windows AND aggregate ROI
≥ 5% AND aggregate CLV ≥ 0 AND n ≥ 30 per window.

## Headline result: **71 configs passed. One structural insight dominates them all.**

### The signal: **the model has real edge specifically in tier 2-3 leagues, on three markets**

| Market | Winning configs | Avg ROI | Avg CLV | Total volume |
|---|---:|---:|---:|---:|
| 1X2 home | 51 | +9.4% | +7.3% | 18,259 bets |
| 1X2 draw | 18 | +6.4% | +1.8% | 13,488 bets |
| BTTS yes | 2 | +5.4% | +6.2% | 636 bets |

**Every single winning config was tier_filter = {2, 3}.** Not tier 1
(Big-5 European — too efficient), not tier 4 (too noisy). Middle-tier
leagues (Serbian Prva, Iranian Persian Gulf, Israeli Liga Leumit,
Polish II-tier, Nordic mid-tiers) are where the model beats the market.

Nothing worked for: 1X2 away, over 2.5, under 2.5, BTTS no. The edge
signal is asymmetric — the model finds value on home wins, draws, and
goal-scored games; it does not on the mirror bets.

### Edge threshold is a monotonic winner

| Threshold | Configs passing | Avg ROI | Avg CLV |
|---:|---:|---:|---:|
| 3% | 6 | +6.1% | +1.9% |
| 5% | 10 | +6.5% | +3.1% |
| 7% | 18 | +7.7% | +4.5% |
| **10%** | **37** | **+9.9%** | **+7.9%** |

Higher edge threshold = better performance. The model is
well-calibrated when it screams; noisy when it whispers.

## Top 3 winners — one per market, ready for shadow deployment

### 1. `bot_sweep_1x2_home_v1` (strongest signal)
- **Market**: 1x2_home
- **Tier**: {2, 3}
- **Edge ≥**: 10%
- **Odds range**: 2.00 – 5.00
- **min_prob**: 0.25
- **Require Pinnacle**: True (Pinnacle-required version slightly outperforms — +9.34% vs +7.56% ROI with only 5% fewer bets)
- **Performance**: 501 bets, +9.34% ROI, +10.16% CLV
- **W1 / W2 / W3 breakdown**: 258 @ +8.2%, 143 @ +15.6%, 100 @ +3.4%

### 2. `bot_sweep_1x2_draw_v1` (biggest volume)
- **Market**: 1x2_draw
- **Tier**: {2, 3}
- **Edge ≥**: 5%
- **Odds range**: 1.30 – 3.50 (draws typically 3-4)
- **min_prob**: 0.25
- **Require Pinnacle**: True
- **Performance**: 714 bets, +7.33% ROI, +2.74% CLV
- **W1 / W2 / W3**: 528 @ +4.8%, 116 @ +18.5%, 70 @ +8.0%
- **Note**: draws are an under-exploited market — existing bots rarely pick draws

### 3. `bot_sweep_btts_yes_v1` (smaller but consistent)
- **Market**: btts_yes
- **Tier**: {2, 3}
- **Edge ≥**: 5%
- **Odds range**: 2.00 – 2.50
- **min_prob**: 0.25
- **Require Pinnacle**: False
- **Performance**: 318 bets, +5.44% ROI, +6.15% CLV
- **W1 / W2 / W3**: 200 @ +0.9%, 80 @ +14.4%, 38 @ +10.2%

## Comparison with existing bots

Existing `bot_v10_all` (calibrated workhorse) does +13.1% ROI on 451
bets since May across ALL markets and tiers. Individual sweep winners
have lower per-bot ROI (7-9% vs 13%), but that's expected because
bot_v10_all pools variance across markets. The sweep's value is not
finding higher-ROI configs — it's finding **cleaner segmented signals**
that will:

- Reduce noise in headline numbers
- Enable targeted market/tier expansion (e.g. tier-2-3-only marketing)
- Provide 3 independent shadow experiments to validate the tier-2-3
  hypothesis on fresh data

## What this DOESN'T tell us

- **Overfitting is still possible.** 71 winners out of 7,560 configs
  passed the strict cross-window filter. That's 0.9%, well below the
  5% we'd expect from pure chance. But 4 months is not infinite data —
  the tier-2-3 signal could still be a seasonal artifact.
- **In-sample calibration.** The ensemble model was calibrated with
  data that partly overlaps this window. The BTTS signal in particular
  could be inflated by calibration lift.
- **CLV is small.** Best CLV of +10% is meaningful but not huge —
  sharpest bettors run at +2-3% CLV on average. This is consistent
  with "real but small edge", not "massive alpha."

## Recommended next step: shadow-deploy the 3 top winners

Each becomes a shadow bot (writes to shadow_bets only, no bankroll,
no simulated_bets impact) for 4-6 weeks of paper observation on FRESH
data. Winners at n≥50 with positive ROI + CLV then get promoted to
paper beta.

Files to touch (Phase D):
- `supabase/migrations/272_bot_sweep_shadows.sql` — three bot rows
- `workers/jobs/daily_pipeline_v2.py` — add `_run_sweep_shadow_pass`
  (analogous to `_run_no_pin_shadow_pass`)
- `scripts/smoke_test.py` — one guard test
- `PRIORITY_QUEUE.md` — mark task done + register Phase D revisit

Estimated effort: 2-3h.
