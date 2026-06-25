# Tennis paper-bet soak protocol — 2026-06-25

> **Strategy**: accumulate tour-main paper bets for free, measure edge,
> only buy a paid settlement source if Pinnacle-anchored ROI proves positive.
> User-imposed discipline: "before we wanna pay for any subscription, we
> wana know if we have edge".

## The decision gate

| Condition | Action |
|---|---|
| **Pinnacle-anchored bots show settled ROI ≥ +2% AND avg CLV > 0 on n ≥ 100 settled actionable picks** | Pay for a settlement source (api-tennis.com Starter $40/mo OR Apify Flashscore $30-90/mo OR OddsPapi paid) to expand to Challenger/ITF. |
| **ROI is flat or negative after 100 settled picks OR 2026-08-15 (whichever first)** | Stop investing engineering in tennis. Park the pipeline as-is (it stays free). |
| **n < 100 by 2026-08-15** | Cycle is fundamentally too thin to justify paid expansion. Same as "stop investing" — Coolbet-only volume continues for free as a future training pond. |

**Why ROI ≥ +2%, not break-even?** Because the Coolbet-only expansion adds
~$40-90/mo recurring cost. Even at break-even on the free tier, paid
expansion only makes sense if there's real positive expected value to scale.

**Why CLV > 0?** ROI on small samples is noisy (variance dominates). CLV
(book-odds vs Pinnacle close) is a leading indicator of true edge that's
robust to short-term variance. Both gates need to pass.

## Free pipeline that's already accumulating

| Step | Source | Frequency | Cost |
|---|---|---|---|
| Discover tour-main fixtures | Odds API `/sports` + `/odds` | 2× daily (06:00 + 14:00 UTC) | Free |
| Compute edge vs Pinnacle de-vigged | `odds_api_scanner.py` | Same | $0 |
| Settle finished matches | Odds API `/scores` | 2× daily (02:00 + 14:15 UTC) | Free |
| Capture Pinnacle close-odds → CLV | `capture_closing_odds.py` | Every 30 min, 06-22 UTC | Free |

**Coolbet-only volume** (~260/day Challenger/ITF/doubles observations) keeps
accumulating in `fair_source='coolbet_only'` rows — useful as a future
training pond once we have a settlement source. **Does NOT factor into the
edge-validation decision** since those rows can't be settled for free.

## Current status (2026-06-25 11:00 UTC)

- 20 Pinnacle-anchored rows today (Wimbledon week, all sharp → 0 actionable
  edge ≥ 3%, ALL my manual test rows from before scanner was fixed)
- 260 Coolbet-only observations today (training-data pond growing as expected)
- 0 settled actionable picks yet
- Railway scheduler broken for `tennis_scanner` — 0 automated runs in 7 days.
  Workaround: GitHub Actions backup cron `.github/workflows/tennis_daily.yml`
  fires daily at 06:05 + 14:05 UTC (requires Railway secrets set as GH secrets)

## Tournaments that matter for the soak

Tour rotation through August 2026:
- **2026-06-30 to 07-13** Wimbledon — sharp main draw, low signal
- **2026-07-13 onwards** Hamburg / Bastad / Newport / Hopman Cup mix — softer,
  expect actionable picks here
- **2026-08-04 to 08-11** Canadian Open / Cincinnati Masters — sharp
- **2026-08-15** ← decision gate
- **2026-08-25 to 09-08** US Open — sharp

The soak's signal-rich window is **2026-07-13 to 2026-08-04** (3 weeks of
softer-tour tournaments where soft-book lag is most likely). If we don't
see actionable picks accumulate during that window, we likely don't have
edge.

## Operator checklist

| Task | When | Done? |
|---|---|---|
| Set GH Actions secrets (OA_KEY, COOLBET_COOKIE_REESE84, DATABASE_URL, etc.) | Before 2026-06-26 06:05 UTC first GH cron fire | ⬜ |
| Investigate Railway scheduler — why `tennis_scanner` cron not firing | When time permits; GH Actions backup covers the gap | ⬜ |
| Re-check after first 100 settled actionable picks land | ~late July if signal-rich window produces picks | ⬜ |
| Hard deadline review | 2026-08-15 | ⬜ |

## What we'll measure at the gate

```sql
-- Settled actionable picks count per bot
SELECT bot_id, COUNT(*) AS n, AVG(pnl/stake) AS roi, AVG(clv) AS avg_clv
  FROM tennis_value_bets
 WHERE fair_source = 'odds_api_pinnacle'
   AND result IN ('win','loss')
   AND bot_id LIKE 'bot_tennis_%'
 GROUP BY bot_id
 ORDER BY n DESC;
```

`/admin/tennis` already renders this aggregation. The decision is mechanical —
read the table, apply the gate, act.
