# DeepBetting — API Payload Findings (2026-06-05)

Operator captured 3 network-tab payloads from deepbetting.com. Key intel below for
the upcoming GROWTH-COMPETITOR-RESEARCH session.

## Endpoints captured

1. **`/free`** — returns picks with `free_flag: "1"` only
   - 124 football + 212 NBA entries (lifetime, all sports combined)
   - Public-facing teaser

2. **`/ussports`** — same football block as `/free` + `ussports` array
   - Adds NBA/MLB/NHL/baseball under one key (renamed from `nba`)

3. **`/results`** ← MOST INTERESTING
   - 200 football + 200 multi-sport in 18-day window
   - **Mixes paid (`free_flag: null`) AND free picks**
   - If callable without auth, reveals full paid pick history

## Pick volume — much higher than visible UI

| Source | Picks/day |
|---|---|
| Operator's visible paste (UI scroll) | ~7 football/day |
| `/results` paid+free combined | **~11 football + ~11 US-sports = ~22/day total** |

The UI only shows a fraction. Their actual pick volume is 3× what we estimated.

## Pick structure (per row)

```json
{
  "forecast_type": "BTTS|Moneyline|Draw No Bet|Over-Under|Spread",
  "forecast_statement_en": "Both Teams To Score (Yes)",
  "odds": "1.91",
  "confidence": "1|2|3",                  // exposed publicly
  "forecast_status": "Won|Lost|Push|Postp.",
  "forecast_result": "0|1",
  "forecast_profit": "1.91|0|1",          // odds=Won, 0=Lost, 1=refund
  "free_flag": "1" | null,                // paywall mechanism
  "analysis_fr|en|es|pt": "..."           // multilingual
}
```

## Settlement math (clean, exploitable)

```python
net_pnl = sum(forecast_profit) - count(forecast_status in ["Won","Lost"])
# Push and Postp. return stake (forecast_profit=1), so they wash out
```

## Free-pick ROI snapshot (from operator's earlier paste, 92 settled picks)

- Hit rate: 63.0%
- Avg odds: 1.621
- **ROI: -0.99% (-0.91 units on 92u stake)**
- Edge vs implied: -0.2pp (basically zero edge)

This confirms operator's "deepbetting is not profitable" read.

## Strategic observations

1. **Multilingual content** (fr/en/es/pt) — target markets are France, Brazil,
   LatAm. Not English-first. Their SEO/distribution play is Romance languages.

2. **Volume + selectivity gap.** They publish ~22 picks/day across all sports;
   we publish 50-200/day value bets just for football. Different positioning —
   they sell "curated daily picks", we sell "all the edge we can find".

3. **Confidence tier exposed publicly.** Backtest opportunity: do their level-3
   picks beat their level-1 picks? If not, the confidence tag is marketing fluff.

4. **`/results` endpoint exposes 18 days of paid pick history without (apparent)
   auth.** If callable anonymously, full ROI backfill is one curl away. This is
   the lever for the competitor research session — get the real number, not just
   the free-teaser number.

## Next steps for competitor research session

- [ ] Test if `/results` is callable without auth headers (curl from clean session)
- [ ] If yes, scrape 90 days and compute true paid-pick ROI
- [ ] Compute per-confidence-tier ROI (does confidence=3 actually win more?)
- [ ] Compute per-market ROI (Moneyline vs Spread vs Over-Under vs BTTS vs DNB)
- [ ] Cross-reference visible pick count to /results count — confirms paywall logic
- [ ] Compare DeepBetting's verified Bet-Analytix ROI (-3.7%) against our scraped result
