# API-Football Endpoint Frequencies vs Our Usage

> Source: `docs/API-Football_Documentation_v3.9.3.pdf` (v3.9.3, downloaded 2026-04-28)
> Compare against our usage in `WORKFLOWS.md` and `DATA_SOURCES.md`

## Real-Time Endpoints (15-second updates)

These endpoints update **every 15 seconds** during live matches. GitHub Actions cron (minimum 1 min, 30-60s jitter) cannot match this — would need a long-running process (Railway/Fly.io).

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/fixtures` (live) | **15 sec** | 1/min per league with live match | Every 5 min via live_tracker | **20x slower** — missing ~80% of in-play state changes |
| `/fixtures/headtohead` | **15 sec** | 1/min per live match | Once at enrichment (04:15) | Only pre-match, not live |
| `/fixtures/events` | **15 sec** | 1/min per live match | Every 5 min via live_tracker | **4x slower** — goals/cards detected late |
| `/fixtures/lineups` | **15 min** | 1/15min per live match | Every 5 min via live_tracker | OK (we poll more than needed) |

## Minute-Level Endpoints

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/fixtures/statistics` | **1 min** | 1/min per live match | Every 5 min via live_tracker (since 2026-04-30) | **5x slower** |
| `/fixtures/players` | **1 min** | 1/min per live match | Settlement only (post-match) | **Not used live** — missing live player xG, ratings |

## Hourly Endpoints

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/standings` | **1 hour** | 1/hour per league with live match | 3x/day (04:15, 12:00, 16:00) | Could increase during match days |
| `/predictions` | **1 hour** | 1/hour per live match | Once/day (05:30) | Missing updated predictions as kickoff approaches |
| `/leagues` | Several/day | 1/hour | Once/day (04:00 on Mondays) | Fine |

## Every-2-Hour Endpoints

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/odds` (pre-match) | ~2 hours (see odds section) | Per bookmaker | Every 2h (05-22 UTC) | **Roughly matched** |
| `/odds/live` | Real-time during match | Per live match | Not used | **Not fetching live odds from AF** (we use Kambi) |

## Every-4-Hour Endpoints

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/injuries` | **4 hours** | 1/day | 3x/day (04:15, 12:00, 16:00) | **Matched** |

## Daily/Weekly Endpoints

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/teams/statistics` | **2x/day** | 1/day per active team | Once/day (04:15) | Fine |
| `/teams` | Several/week | 1/day | Not regularly fetched | Low priority |
| `/coachs` | Daily | 1/day | Not used | Not relevant yet |
| `/players` | Several/week | 1/day | Not used | Could enrich match detail |
| `/sidelined` | — | — | Backfill only | Low priority |
| `/transfers` | — | — | Not used | Low priority |

## Endpoints We Don't Use At All

| Endpoint | What it offers | Potential value |
|----------|---------------|----------------|
| `/odds/live` | Real-time in-play odds | **High** — in-play model needs live odds |
| `/odds/live/bets` | Live bet types available | Medium — market discovery |
| `/fixtures/players` (live) | Per-player live stats (xG, rating) | **High** — player-level signals |
| `/players/topscorers` | League top scorers | Low — display feature |
| `/players/topassists` | League top assists | Low — display feature |
| `/players/topyellowcards` | Most carded players | Low — discipline signal |
| `/players/topredcards` | Most red-carded players | Low — discipline signal |
| `/venues` | Venue data | Low — display feature |

## Key Insight: The 15-Second Problem

The `/fixtures` endpoint updates every 15 seconds. Our live tracker runs every 5 minutes via GitHub Actions cron. This means:

1. **Score changes** are detected up to 5 min late (should be <30 sec)
2. **Red cards** (which shift odds ~10-15%) are detected late
3. **In-play betting windows** close before we see the state change

**GitHub Actions limitation:** Cron minimum is 1 minute, but actual execution has 30-60 second jitter. For 15-second polling, we'd need a long-running process on Railway/Fly.io (~$5/mo).

**API budget impact of 1-min polling:** ~10 live matches avg × 3 endpoints × 60 min/match × 90 min = ~2,700 calls/match-day. Well within 75K daily budget.

## Odds-Specific Notes

- AF `/odds` for pre-match: updates roughly every 2 hours — we match this
- AF `/odds` does NOT return data for completed fixtures (confirmed 2026-04-30)
- AF `/odds/live` (added v3.9.2): real-time in-play odds — we don't use this yet
- Historical odds need a separate source (The Odds API)
