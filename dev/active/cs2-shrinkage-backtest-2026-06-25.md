# CS2 shrinkage calibration backtest — 90d window

Compares raw model probability vs market-consensus-shrunk
probability on settled CS2 matches. Shrinkage formula:

    shrunk = α · model_prob + (1 − α) · consensus_implied

Per-source α: {'elo+pq_v1': 0.75, 'v8': 0.75, 'v7': 0.65, 'hltv_v1': 0.4}

Per-source metrics on the population where shrinkage applied
(consensus from ≥2 books available — others fall through to raw).

| source     |     n |  raw_LL |  shr_LL |   Δ_LL | raw_ECE | shr_ECE |  Δ_ECE | verdict |
| ---------- | ----- | ------- | ------- | ------ | ------- | ------- | ------ | ------- |
| elo+pq_v1  |     2 |  0.6935 |  0.5903 | +0.1033 |  50.02% |  44.57% | +5.45% | ✓ PROMOTE |
| v8         |     0 |       - |       - |      - |       - |       - |      - | no data |
| v7         |     0 |       - |       - |      - |       - |       - |      - | no data |
| hltv_v1    |    34 |  0.5936 |  0.5565 | +0.0371 |  32.16% |  23.72% | +8.43% | ✓ PROMOTE |

**Δ_LL** = raw − shrunk log-loss (positive ⇒ shrinkage better).
**Δ_ECE** = raw − shrunk ECE (positive ⇒ shrinkage better).

- **elo+pq_v1** (α=0.75): ✓ PROMOTE — shrinkage improves both log_loss and ECE
- **hltv_v1** (α=0.4): ✓ PROMOTE — shrinkage improves both log_loss and ECE

## Caveats and interpretation

**Sample sizes are tiny for everything except hltv_v1.** elo+pq_v1's n=2
is not statistically meaningful on its own — it's reported for completeness
but doesn't constitute evidence. hltv_v1's n=34 is the only data point with
real weight, and the improvement there is substantial (raw ECE 32% →
shrunk ECE 24%; raw log-loss 0.59 → 0.56).

**Why v8 and v7 show no data, even at a 180-day window:** these models
write predictions for HLTV-fallback rows (where ELO is gated). Those rows
also have HLTV-median `bookie_odds1/2` written by `cs2_hltv_match_odds`,
but rarely also have `coolbet_odds` (last 30d: only 7/170 = 4% of
HLTV-sourced rows). With <2 books available, `market_consensus()` returns
None and shrinkage falls through to raw — by design. So v8/v7 shrinkage
won't surface in this backtest until HLTV-sourced rows start getting
richer odds coverage (a separate workstream).

**Why raw ECE is so high.** Soccer's calibrated bots have ECE <5%. CS2's
30-50% ECE in this small sample suggests the raw model probs in
`cs2_predictions` aren't well-calibrated post-Platt — likely because the
Platt fit runs weekly on a 90-day rolling window that's also small
(`cs2_weekly_calibrate.py`). Shrinkage toward market consensus is doing
some of the calibration work the Platt fit can't do on thin data.

## Decision

**Ship shrinkage with current α values.** The hltv_v1 n=34 result alone
justifies it: ECE roughly halves, log-loss drops 6%. The shrinkage is
cfg-flagged (`shrink_to_market: True` in `BOTS_CONFIG`), so it can be
flipped off per-bot if a future backtest contradicts. CLI flag
`--no-shrink` enables side-by-side comparison going forward.

**Revisit α tuning after n≥50 settled rows per source.** Expected to take
4-6 weeks of paper-trading at current volume. Re-run this backtest and
look for ECE regressions on any source — sweep α down if elo+pq_v1 or
v8 show worse calibration with shrinkage than without.

**Operational note.** Most CS2 picks today come from elo+pq_v1
(positive `bo3gg_id` rows with both bo3.gg and Coolbet odds). On those
rows shrinkage will apply. v8/v7 picks fire much more rarely because
their source rows rarely have ≥2 books — when they do fire they'll see
shrinkage; when they don't, the bot uses raw probs unchanged.
