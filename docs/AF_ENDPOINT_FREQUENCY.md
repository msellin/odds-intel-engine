# API-Football Endpoint Frequencies vs Our Usage

> Source: `docs/API-Football_Documentation_v3.9.3.pdf` (v3.9.3, downloaded 2026-04-28)
> Compare against our usage in `WORKFLOWS.md` and `DATA_SOURCES.md`

## Real-Time Endpoints (15-second updates)

These endpoints update **every 15 seconds** during live matches. ✅ Now matched via Railway LivePoller (30s polling, can go to 15s).

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/fixtures` (live) | **15 sec** | 1/min per league with live match | Every 30s via Railway LivePoller | ✅ **Matched** (was 20x slower at 5min) |
| `/fixtures/headtohead` | **15 sec** | 1/min per live match | Once at enrichment (04:15) | Only pre-match, not live |
| `/fixtures/events` | **15 sec** | 1/min per live match | Every 60s via Railway LivePoller | ✅ **Near-matched** (was 4x slower) |
| `/fixtures/lineups` | **15 min** | 1/15min per live match | Every 5 min via LivePoller slow tier | OK |

## Minute-Level Endpoints

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/fixtures/statistics` | **1 min** | 1/min per live match | Every 60s via Railway LivePoller | ✅ **Matched** (was 5x slower) |
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

## ✅ In-Play Strategy Polling Requirements — ANALYSED (2026-05-06)

Strategies A-K (§ INPLAY Plan) were evaluated against current polling tiers:

| Data needed | Current | Strategies requiring it | Verdict |
|-------------|---------|------------------------|---------|
| Score + minute | 30s fast | All (entry conditions, goal abort) | ✅ Sufficient |
| Live odds (O/U 2.5, 1X2) | 30s fast | All | ✅ Sufficient — staleness <60s guaranteed |
| xG, shots, corners, possession | 60s medium | A, B, C, D, E, G, H, I, J, K | ✅ Matched to AF update rate (1/min) |
| Odds history (10-min window) | 30s fast → DB | Strategy F (15% drift detection) | ✅ Bot queries last 20 snapshots — not a polling gap |
| 2H kickoff detection (min 46-54) | 30s fast | Strategy K | ✅ 15+ cycles in 8-minute window |

**Conclusion: No polling frequency changes needed for Phase 1 in-play strategies.**
The HIGH-priority escalation (30s stats for matches with active bets, `live_poller.py:233`) already provides enhanced refresh once a paper bet fires.

---

## ✅ The 15-Second Problem — SOLVED (2026-04-30)

The `/fixtures` endpoint updates every 15 seconds. Previously our live tracker ran every 5 minutes via GitHub Actions cron, missing ~80% of state changes.

**Solution:** Railway long-running process with `LivePoller` (tiered polling):
- **30s** (fast tier): bulk fixtures + live odds — scores/odds detected within 30s
- **60s** (medium tier): per-match stats + events — xG, shots, goals, cards within 60s
- **5min** (slow tier): lineups + match map refresh

**API budget:** ~10K-15K calls/day during live play. Well within AF Ultra 75K/day.

## Odds-Specific Notes

- AF `/odds` for pre-match: updates roughly every 2 hours — we match this
- AF `/odds` does NOT return data for completed fixtures (confirmed 2026-04-30)
- AF `/odds/live`: ✅ now polled every 30s via LivePoller fast tier
- Historical odds need a separate source (The Odds API)
