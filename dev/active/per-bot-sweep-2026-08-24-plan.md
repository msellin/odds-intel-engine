# PER-BOT-SWEEP-2026-08-24 — Plan

Early execution of `PER-BOT-SWEEP-N500-2026-10-01` (queued for 2026-10-01),
pulled forward because the operator placed €1,270 of real money on these bots
over 2026-08-22→24 and lost €121.80 (his stakes) / €215.81 (flat-€10 basis).

## Question

The CONFIG-SWEEP-2026-08-19 backtest said all deployed configs were positive.
Live they are not. Why, and which bots genuinely need retiring?

## Findings that motivated this (audit 2026-08-24)

- Live deduped: 475 settled picks, −0.2% ROI overall.
- `bot_pin_1x2_draw_tier4_v1` −40.8% (n=27); 85% of its picks had NEGATIVE
  de-vigged edge (5% gate vs 12.2% Pinnacle overround).
- Tier 4 = −21.6% ROI on n=291 of INDEPENDENT historical data (t=−3.13).
- Line-shop edge formula never de-vigs (daily_pipeline_v2.py:4504, :4691).

## Key structural discovery

CONFIG-SWEEP-2026-08-19 only ever tested MODEL-driven configs and only these
markets: 1x2_home/draw/away, over_under_25_over/under, btts_yes/no.
It validated exactly 3 of the 8 deployed bots (the `bot_sweep_1x2_*` +
`bot_sweep_btts_yes_v1`). The other 5 were justified by an ad-hoc
"historical simulation" quoted in migrations 274/275/277 whose script was
NEVER COMMITTED — those numbers are unreproducible.

## Approach

Build `scripts/per_bot_backtest_sweep.py`: point-in-time replay harness.

- Snapshot per (match, book, market, selection) = LAST snapshot strictly
  before `kickoff - lead_hours`. No look-ahead.
- Two engines: LINESHOP (Pinnacle anchor, no model) and MODEL (ensemble prob).
- Sweep per bot: edge threshold × tier set × walk-forward window.
- Report raw AND de-vigged edge variants side by side.
- Compare each config against the live shadow_bets result for the same bot.

## Risks

- Ensemble predictions may be partly in-sample (model trained on overlapping
  data) → MODEL-engine backtests optimistic. Flag, don't hide.
- Multiple comparisons: report count of configs tested per bot.
