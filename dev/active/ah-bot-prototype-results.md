# AH-BOT-PROTOTYPE — Backtest Results

Universe: **5,254 matches** with paired Pinnacle AH closing + ensemble 1X2 prediction, since 2024-01-01. Lines restricted to {-0.5, -0.25, 0, +0.25, +0.5} — the subset derivable from 1X2 probabilities alone (half + quarter straddling 0).

## Edge threshold sweep

| Edge threshold | N bets | N home | N away | P&L (units) | ROI |
|---:|---:|---:|---:|---:|---:|
| -0.050 | 5,254 | 1,858 | 3,396 | -139.19 | -2.65% |
| -0.020 | 5,254 | 1,858 | 3,396 | -139.19 | -2.65% |
| +0.000 | 5,254 | 1,858 | 3,396 | -139.19 | -2.65% |
| +0.010 | 4,918 | 1,696 | 3,222 | -123.32 | -2.51% |
| +0.020 | 4,597 | 1,557 | 3,040 | -120.19 | -2.61% |
| +0.030 | 4,256 | 1,399 | 2,857 | -130.55 | -3.07% |
| +0.050 | 3,605 | 1,121 | 2,484 | -109.88 | -3.05% |
| +0.080 | 2,666 | 755 | 1,911 | -113.47 | -4.26% |
| +0.100 | 2,118 | 546 | 1,572 | -106.22 | -5.01% |
| +0.150 | 1,010 | 218 | 792 | -45.17 | -4.47% |

## Interpretation

**No edge found.** ROI is negative across every threshold and gets *worse* as the edge filter tightens. This is the textbook signature of "edge" that is actually statistical noise rather than signal — a real signal would show ROI improving as we filter for higher-edge bets.

Observations:
- At zero edge filter (all 5,254 bets), ROI ≈ −2.65% — essentially the AH vig. The model takes both sides roughly proportionally to where they're priced.
- The home/away split is **lopsided**: 1,858 home bets vs 3,396 away bets. The ensemble systematically rates away sides higher than Pinnacle's AH closing implies — but acting on that disagreement loses money. Either the ensemble has an away bias the Platt calibration doesn't catch, or Pinnacle's AH market correctly anticipates it.
- At edge threshold +0.10 (substantial filter), ROI is **−5.01% on 2,118 bets** — significantly *worse* than baseline. Tightening the filter increases the noise concentration.

**Conclusion: the simple "ensemble 1X2 → derived AH probability" approach does not beat Pinnacle's AH closing.** A viable AH bot would need:
1. A dedicated **goals model** (Poisson / negative-binomial with team-form and venue effects) — not derived from 1X2 probs which compress information
2. **Signal layers absent from this baseline**: lineup strength, injury severity, line movement (the +8.76pp drift signal from CSV-FULL-EXTRACT could be a starting point), referee, weather
3. Edge over a **non-Pinnacle anchor** — e.g. detecting when soft-book AH disagrees with Pinnacle in a profitable direction

**Practical recommendation: shelve the AH bot for now.** The 8,868-match backtest universe is a real asset and a goals-model-based AH bot is worth a follow-up exploration, but the naive prototype confirms Pinnacle's AH market is efficient at the model's current level. This is also consistent with the parallel "anchor swap" finding (Pinnacle = Exchange to model precision).

The CSV-FULL-EXTRACT data unlock is still net-positive: even a negative AH result tells us where NOT to spend modelling effort, and the drift feature (+8.76pp WR spread) is a separate confirmed win.
