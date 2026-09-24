# inplay_e ECE re-check — 2026-06-03

## Verdict

**Catastrophic miscalibration. ECE = 0.2193 (21.93%) on 216 settled bets.**
The 5% gate that the I/J/L bots were measured against is exceeded by 4.4×.

But — and this is the curious part — **ROI is still +7.64%** ($1,080 staked,
+$82.49 PnL, 126W/90L). The bot is wrong about its probabilities and
profitable simultaneously.

## Numbers

```
Settled:   216 (126W / 90L)
Pred mean: 0.797
Actual:    0.583
Gap:       +21.4pp (over-confident)
ECE:       0.2193  (gate = 0.05)
ROI:       +7.64%
```

Per-bucket breakdown:

| Bucket | N | Predicted | Actual | Gap |
|---|---|---|---|---|
| 0.50–0.60 | 18 | 0.554 | 0.278 | **+27.6pp** |
| 0.60–0.70 | 26 | 0.650 | 0.538 | +11.2pp |
| 0.70–0.80 | 50 | 0.755 | 0.540 | **+21.5pp** |
| 0.80–0.90 | 70 | 0.852 | 0.657 | +19.5pp |
| 0.90–1.00 | 51 | 0.933 | 0.647 | **+28.5pp** |

Every bucket ≥0.50 is over-confident by 11–29pp. The 0.90+ "high-conviction"
bucket (51 bets) the bot calls at 93% wins 65% of the time. This is the
exact pattern the 2026-05-24 post-mortem ran into ("the LLM flagged 43
high-conviction losses") — but the OU-UNDER-CAP audit dismissed it as
availability bias because it included the 75 wins. The wins exist; the
miscalibration is also real. Both can be true.

All 216 bets are on `under 2.5` (single-selection bot). Minute-window
breakdown unavailable — `match_minute_at_pick` was added to the schema
today by INPLAY-METADATA-STALENESS, so historical rows are NULL.

## Why ROI is positive despite the miscalibration

Bet math: avg odds ≈ 1.85 (back-solved from +7.64% ROI at 58.3% hit rate).

- The bot's model says "this is 80%, fair odds = 1.25" → at 1.85 it sees +48% edge
- Reality is 58.3% → fair odds 1.715 → at 1.85 there's +7.9% edge
- Bot is right that there IS edge, just wrong about how much

Flat $5 stake hides the calibration problem from sizing. If Kelly were
active (it isn't on inplay bots today), the bot would over-stake by
3–5× based on its overconfident probabilities, which would convert this
+7.64% modest-edge bot into something with much higher variance and
potentially negative bankroll trajectory.

## Why this matters operationally

1. **Downstream consumers get garbage**: anything that reads
   `simulated_bets.model_probability` for inplay_e (admin "confidence"
   displays, meta-model features, the "high-conviction bets" UX teaser)
   is showing meaningless numbers. The bot's 90% calls are really 65%.
2. **CHERRY-PICK-PLACER Phase 3 (2026-06-08)** plans to flip
   `COOLBET_RECORD_ALLOWED_MATURITY=calibrated`. inplay_e is currently
   in the `calibrated` cohort and would be routed real-money bets under
   that flag. The ROI is positive so this isn't a disaster, but we'd be
   sending real money to a bot whose stated probabilities are off by 20pp.
3. **Sample size**: 216 settled bets is enough to call this real, not noise.
   Confidence interval on ECE at this magnitude excludes "well-calibrated"
   with high certainty.

## Recommendation

**Don't demote.** The bot is +EV on a meaningful sample. Demoting would
remove a profitable bot from the real-money cohort.

**Do recalibrate.** Fit a Platt sigmoid to inplay_e's probabilities the
same way pre-match bots use. After fit, the bot's stored
`model_probability` should match its hit rate within ~5pp per bucket.
This unlocks accurate downstream features and unlocks Kelly-sized
sizing safely if we ever want to use it on inplay.

**Block on 2026-06-08**: confirm CHERRY-PICK-PLACER Phase 3 doesn't
expose inplay_e to any probability-based gating (it doesn't appear to —
the placer uses pre-computed edge_percent, not raw model_probability).
If the placer's sizing logic ever introduces Kelly-from-probability,
inplay_e should be EXCLUDED until recalibrated.

## Followup filed

`INPLAY-E-RECALIBRATE` (P1, ~2h) — fit Platt sigmoid on inplay_e's
historical settled bets, store as `model_calibration` row with market =
'inplay_e_under_25' (or similar). Update `apply_platt()` path or
introduce a per-bot calibration step in inplay_bot. Re-check ECE after
fit; target <5%.

Re-runnable: `python3 scripts/inplay_e_ece_recheck.py`.
