# CLV Analysis — Pinnacle Closing-Line Value per Bot

Source: 255 settled **pre-match** paper bets with real Pinnacle closing odds backfilled via OddsPapi `/historical-odds`. Bets from last 60 days where AF lacked a flagged Pinnacle close in `odds_snapshots`.

**Inplay bets excluded** — pre-match Pinnacle close is the wrong comparator for in-game bets (causes spurious +100%+ CLV). A separate inplay-CLV analysis would need Pinnacle live odds at bet timestamp, which this backfill doesn't include.

**CLV definition:** `(our_odds / pinnacle_close) − 1`. Positive = we got a better price than the close → +EV in expectation. Negative = the line moved against us → −EV.

**Why this matters:** CLV is the variance-free skill metric. At small n the win-rate / ROI ranking is noisy; CLV converges much faster.

## Bot ranking by CLV (descending)

| bot | n | win% | avg our_odds | avg pin_close | avg edge (reported) | **CLV close** | sd | t-stat | P(CLV>0) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bot_proven_leagues_v2 | 1 | 0% | 3.05 | 2.03 | 15.0% | **+50.32%** ✗ | 0.0% | +0.00 | 0% |
| bot_greek_turkish | 1 | 0% | 3.38 | 2.83 | 10.0% | **+19.43%** ✗ | 0.0% | +0.00 | 0% |
| bot_high_alignment | 36 | 42% | 2.52 | 2.78 | 13.4% | **+10.24%** ≈ | 60.7% | +1.01 | 84% |
| bot_ah_home_fav | 14 | 57% | 1.82 | 1.69 | 14.6% | **+8.89%** ✓ | 12.0% | +2.78 | 100% |
| bot_v10_all | 27 | 41% | 2.83 | 2.69 | 10.8% | **+6.73%** ✓ | 15.6% | +2.24 | 99% |
| bot_1x2_specialist | 1 | 0% | 2.34 | 2.25 | 8.0% | **+4.00%** ✗ | 0.0% | +0.00 | 0% |
| bot_high_roi_global_v2 | 1 | 0% | 2.34 | 2.25 | 9.0% | **+4.00%** ✗ | 0.0% | +0.00 | 0% |
| bot_aggressive_v2 | 21 | 33% | 2.72 | 2.67 | 8.9% | **+3.66%** ≈ | 15.4% | +1.09 | 86% |
| bot_proven_leagues | 9 | 11% | 3.58 | 3.50 | 11.6% | **+3.65%** ≈ | 11.9% | +0.92 | 82% |
| bot_ou15_defensive | 1 | 100% | 2.04 | 1.98 | 6.0% | **+3.03%** ✗ | 0.0% | +0.00 | 0% |
| bot_ou25_global | 9 | 44% | 2.11 | 2.06 | 8.8% | **+2.57%** ≈ | 7.3% | +1.05 | 85% |
| bot_high_roi_global | 8 | 12% | 3.92 | 3.95 | 13.4% | **+1.52%** ≈ | 11.9% | +0.36 | 64% |
| bot_aggressive | 102 | 41% | 3.25 | 3.32 | 7.9% | **+0.35%** ≈ | 12.9% | +0.27 | 61% |
| bot_ou35_attacking | 7 | 29% | 2.52 | 2.50 | 11.6% | **+0.16%** ≈ | 9.5% | +0.04 | 52% |
| bot_opt_home_lower | 5 | 60% | 3.66 | 3.74 | 14.0% | **-0.14%** ✗ | 16.2% | -0.02 | 49% |
| bot_lower_1x2 | 4 | 75% | 2.82 | 2.98 | 10.2% | **-1.75%** ✗ | 17.9% | -0.20 | 42% |
| bot_ah_away_dog | 8 | 25% | 2.05 | 3.51 | 10.9% | **-29.87%** ✗ | 33.8% | -2.50 | 1% |

Legend: `✓` = CLV > 0 at p<0.05; `≈` = directional positive but not significant; `✗` = negative CLV.

> ⚠ Rows with n < 5 are too small to interpret — they appear in the table for completeness but should be ignored for promotion decisions.


## Concrete recommendation for CHERRY-PICK-PLACER 2026-06-08 gate flip

**Promote to `calibrated` (CLV > 0 with n ≥ 5 and t > 1.0):**

- **bot_ah_home_fav** — n=14, CLV +8.89%, t=+2.78, win% 57%
- **bot_v10_all** — n=27, CLV +6.73%, t=+2.24, win% 41%
- **bot_aggressive_v2** — n=21, CLV +3.66%, t=+1.09, win% 33%
- **bot_ou25_global** — n=9, CLV +2.57%, t=+1.05, win% 44%
- **bot_high_alignment** — n=36, CLV +10.24%, t=+1.01, win% 42%

**Do NOT promote — significantly negative CLV (n ≥ 5):**

- **bot_ah_away_dog** — n=8, CLV -29.87%, t=-2.50, win% 25% — taking systematically bad prices
- **bot_opt_home_lower** — n=5, CLV -0.14%, t=-0.02, win% 60% — taking systematically bad prices

**Watch list (n ≥ 5 but not yet significant):** insufficient evidence; revisit after more bets accumulate or backfill more historical fixtures.


## Files

- Raw extracted snapshots: `/tmp/op_phase3_extracted.json` (219 fixtures)
- Per-bet CLV: `/tmp/clv_per_bet.csv` (255 rows)
- Raw OP historical responses: `dev/active/pinnacle-backfill-jsons/*.json.gz` (gitignored)