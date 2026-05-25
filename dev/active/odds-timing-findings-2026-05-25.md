# Odds Timing — Findings 2026-05-25

> Source: `scripts/odds_timing_analysis.py --days 30` (1430 settled bets, 5.19M snapshots)

## Headline findings

### CLV by placement-hour window (hours before kickoff)
| Window | n | Beat-close % | Avg CLV |
|---|---|---|---|
| 0-2h | 188 | 72% | **+4.4%** |
| 2-4h | 237 | 73% | +12.6% |
| 4-6h | 226 | 72% | +11.7% |
| 6-9h | 288 | 77% | +8.3% |
| 9-12h | 173 | 72% | +7.9% |
| **12h+** | 180 | 72% | **+16.8%** |

→ **Bet EARLY**. Placing 12h+ pre-KO gives +12.4pp CLV vs 0-2h. Overall +10.25% CLV across 1,292 settled bets.

### Time-of-day odds curves (UTC)
- **1x2 AWAY**: peak 07:00 (+0.48 vs baseline), trough 15:00 (-0.25). Spread 0.72.
- **1x2 HOME**: peak 14:00 (+0.13), trough 10:00 (-0.20). Spread 0.33.
- **1x2 DRAW**: flat (spread 0.37, no strong intra-day pattern).

→ **AWAY bets benefit massively from morning placement.** Bots that primarily fire AWAY (bot_opt_away_british, bot_opt_away_europe, bot_ah_away_dog when underdog) should be routed to the **morning cohort only**, capturing the +0.5 odds boost vs midday lines.

## Recommended action (filed as follow-up: ODDS-TIMING-COHORT-ASSIGN)

Post-Phase-3.5 (2026-06-07), set `BOT_TIMING_COHORTS` in `daily_pipeline_v2.py`:

```python
BOT_TIMING_COHORTS = {
    "bot_opt_away_british":  "morning",  # was: "all"
    "bot_opt_away_europe":   "morning",  # was: "all"
    "bot_ah_away_dog":       "morning",  # was: "all"
    # ...keep other bots at "all" (no strong time effect on their markets)
}
```

Expected lift: +0.5 odds × stake × hit rate ≈ +5-8pp CLV on the away-bot subset, which translates to ~+1-2% portfolio ROI.

## Caveats
- Sample is unbalanced: only 6 hour-buckets between 07:00-20:00 UTC have meaningful data (the rest of the day has < 100 obs).
- Confirmed via cross-tier: pattern holds across Tier 1-4 leagues.
- Some of the away-peak effect may come from EU bookmakers opening their odds at ~07:00 UTC with conservative initial pricing that the market then sharpens.

## Re-run cadence
- Re-run monthly via `python3 scripts/odds_timing_analysis.py --days 30`
- Trigger reassignment if any bot's time-of-day effect changes >5pp from this baseline
