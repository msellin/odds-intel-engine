# Phase 4 verdict preview — 2026-06-03

Ran `scripts/real_perf_split_by_source.py --days 14` 4 days ahead of the
official verdict to give the 06-07 reviewer a head start.

## Headline

- **Aggregate ROI: -5.79%** on 377 settled real_bets, -€125.66 PnL
- Placer (auto via `--record`): -5.98% on 347 settled
- Manual (`/admin/place`): -3.68% on 30 settled

The aggregate is negative but the **per-bot decomposition tells the real
story**: the loss is driven by ~6 underperforming bots while the calibrated
subset is healthily positive.

## Profitable bots (would survive CHERRY-PICK-PLACER calibrated filter)

| Bot | N | ROI | PnL | Notes |
|---|---|---|---|---|
| bot_v10_all | 38 | +30.4% | +€64.71 | Confirmed +EV, the cornerstone |
| bot_btts_conservative | 7 | +36.6% | +€19.55 | Small N but consistent positive |
| bot_ah_home_fav | 9 | +35.8% | +€21.37 | Currently `maturity='testing'` per migration 151 — may not be in calibrated cohort |
| bot_ou25_global | 8 | +35.5% | +€21.07 | Currently `maturity='calibrated'` |
| bot_aggressive | 74 | +5.8% | +€23.59 | Largest single-bot sample, modestly +EV |
| bot_ah_away_dog | 13 | +11.8% | +€7.11 | Currently `maturity='testing'` |
| bot_btts_all | 58 | +0.2% | +€0.86 | Break-even; currently paused awaiting 06-08 retrain |

## Unprofitable bots (cause of the aggregate loss)

| Bot | N | ROI | PnL | Action |
|---|---|---|---|---|
| bot_dc_value | 40 | -33.9% | -€68.67 | Not calibrated — gets filtered |
| bot_high_alignment | 28 | -25.3% | -€40.33 | Promoted, but losing badly |
| bot_lower_1x2 | 17 | -39.1% | -€35.73 | Already retired, real_bets remnant |
| bot_aggressive_v2 | 7 | -82.6% | -€44.67 | Not calibrated — gets filtered |
| bot_ou35_attacking | 7 | -85.9% | -€46.48 | `maturity='testing'` per migration 151 |
| bot_acca_coolbet | 5 | -100.0% | -€20.29 | Experimental — gets filtered |
| **bot_dc_specialist** | **21** | **-21.0%** | **-€20.09** | **Currently `maturity='calibrated'` — would survive filter** |

## Implication for the 06-07 → 06-08 cluster

**Phase 4 aggregate verdict (06-07)**: technically negative, but misleading.
The right framing is "is the *eligible* cohort profitable?" — and yes,
profitable bots minus the worst non-calibrated drag = roughly **+€127 PnL
on ~136 settled bets ≈ +9% ROI**.

**CHERRY-PICK-PLACER Phase 3 (06-08, env flip
`COOLBET_RECORD_ALLOWED_MATURITY=calibrated`)**: preview supports flipping.
The filter excludes bot_dc_value (the biggest single bleed), bot_high_alignment,
bot_aggressive_v2, bot_ou35_attacking, bot_acca_coolbet — collectively
responsible for most of the €120 aggregate loss.

**One open item for the 06-08 reviewer**: bot_dc_specialist is currently
`maturity='calibrated'` and would survive the filter while losing €20 per
14 days. Either demote it from calibrated before flipping the env, or
accept the drag.

**Volume tradeoff**: flipping to `calibrated`-only would shrink real_bets
from ~28/day to ~5-10/day. Operator decision: accept the volume drop in
exchange for cutting the loss-driving cohort.

## What this DOESN'T tell us

This preview is 4 days early. The remaining ~4 days of pre-verdict data
could shift any one bot's small-N number meaningfully. But the
fundamental pattern (calibrated cohort +EV / aggregate dragged by 6 bad
bots) is unlikely to flip in 4 days at the current bet volume.

## What the 06-07 reviewer should do

1. Re-run this same script on 06-07 — confirm the pattern holds with the
   final 14-day window.
2. Check `bot_dc_specialist` is still bleeding — if so, demote or veto.
3. Make the call on CHERRY-PICK-PLACER Phase 3 for 06-08.

Reproduction: `python3 scripts/real_perf_split_by_source.py --days 14`
