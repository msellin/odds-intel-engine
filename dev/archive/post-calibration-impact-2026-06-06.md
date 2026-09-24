# Post-calibration impact — 6 markets calibrated 2026-06-06 10:35 UTC

Generated: 2026-06-06 11:37 UTC

Pre window:  2026-05-23 10:35 → 2026-06-06 10:35 UTC
Post window: 2026-06-06 10:35 → 2026-06-06 11:37 UTC
Post elapsed: 1.0 hours

Note: `calibrated_prob` in PRE-cutoff bets reflects the OLD Platt params (or none for AH).
So a shift in mean calibrated_prob is the combined effect of new params + any natural drift in raw model output.

| Market | n pre | n post | raw pre→post | cal pre→post | shift Δ | edge pre→post |
|---|---:|---:|---|---|---|---|
| asian_handicap_away -0.5 | 49 | 0 | 0.6143 → — | 0.5812 → — | — | 12.80% → — |
| btts_no | 27 | 0 | 0.6295 → — | 0.5871 → — | — | 7.33% → — |
| btts_yes | 33 | 0 | 0.6070 → — | 0.5730 → — | — | 7.39% → — |
| double_chance_1x | 66 | 0 | 0.8088 → — | 0.7923 → — | — | 14.97% → — |
| double_chance_x2 | 172 | 1 | 0.7415 → 0.7195 | 0.7024 → 0.5297 | -0.1898 | 12.74% → 15.00% |
| inplay_e_under_25 | n/a | n/a | in-play only | in-play only | — | — |

## Interpretation cues

- **Count collapsed** (post << pre, accounting for window length): calibration deflated probabilities enough to kill edge — bots stopped picking. Investigate whether the new params over-correct.
- **Shift Δ near zero**: calibration is barely changing what the bots see. Probably fine; was it worth fitting?
- **Edge mean swung negative**: calibration is correctly catching previously over-confident picks. Healthy.
- **AH n=0 post**: expected if the cohort doesn't hit `away -0.5` lines often. Wait until the next bet lands.

Re-run any time: `PYTHONPATH=. python3 scripts/check_post_calibration_impact.py`