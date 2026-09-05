# API-Football Endpoint Frequencies vs Our Usage

> Source: `docs/API-Football_Documentation_v3.9.3.pdf` (v3.9.3, downloaded 2026-04-28)
> Compare against our usage in `WORKFLOWS.md` and `DATA_SOURCES.md`
>
> **Last verified 2026-09-05** (AF-DOCS-STALE-2026-09-05). Plan and limits read
> from live `/status` response headers; polling cadences and env gates read from
> `workers/live_poller.py`, `workers/jobs/live_tracker.py` and
> `workers/scheduler.py`. AF's own *recommended* frequencies in the tables below
> come from the v3.9.3 PDF and have **not** been re-checked against a newer PDF —
> treat that column as of 2026-04-28.

## Our plan (verified 2026-09-05, live `/status` headers)

| Field | Value |
|---|---|
| Plan | **Mega** (not Ultra — the old "Ultra / 75K" text in this file and in `DATA_SOURCES.md` was wrong) |
| Active to | 2026-11-28 |
| `x-ratelimit-requests-limit` | **150,000 / day** |
| `x-ratelimit-limit` | **900 / minute** |
| Recent daily usage | 9–23% of the daily limit (~13,700–35,000 calls/day) |
| Endpoints we call | **18** of the ~37 the plan exposes (`workers/api_clients/api_football.py`) |

The **per-minute** limit, not the daily quota, is the binding constraint — see
`DATA_SOURCES.md` § "Per-minute limit is the binding constraint".

## The in-play product is retired (2026-08-21)

`AF-QUOTA-REALLOCATION-2026-08-21` gated the two biggest live consumers off by
default:

- `INPLAY_LIVE_ODDS_POLL_ENABLED` (default **false**) — gates `/odds/live` in
  `fetch_live_bulk` and `run_live_tracker` (`workers/jobs/live_tracker.py:82,403`)
- `INPLAY_STATS_EVENTS_POLL_ENABLED` (default **false**) — gates per-match live
  stats + events in the poller (`workers/live_poller.py:424`)

Consequences, both confirmed: the 18-bot in-play fleet has produced **no picks
since 2026-08-21**, and `odds_snapshots` has **no `is_live=true` rows after that
date**. That is the intended effect of the gates, not a break. Everything below
marked *gated off* is code that still exists and would resume if the env vars
were flipped.

## Real-Time Endpoints (15-second updates)

These endpoints update **every 15 seconds** during live matches. Our LivePoller
runs **inside the VPS `oddsintel-scheduler` process** (`workers/live_poller.py`,
started from `workers/scheduler.py`). The Railway poller this file used to
describe has not existed since RAILWAY-ELIMINATION on **2026-06-29**.

Current tiers (`workers/live_poller.py:46-56`): **45s** fast when live matches
exist, **120s** idle, stats every 4th fast cycle (**180s**), lineups every 10th
(**7.5 min**).

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/fixtures` (live) | **15 sec** | 1/min per league with live match | Every 45s (fast tier), 120s idle | Active — 3x slower than AF's update rate |
| `/fixtures/headtohead` | **15 sec** | 1/min per live match | Once at enrichment (04:15) | Only pre-match, not live |
| `/fixtures/events` | **15 sec** | 1/min per live match | **Gated off** since 2026-08-21; still fetched at settlement | Deliberate — in-play retired |
| `/fixtures/lineups` | **15 min** | 1/15min per live match | Every 7.5 min via slow tier | OK |

## Minute-Level Endpoints

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/fixtures/statistics` | **1 min** | 1/min per live match | **Gated off** live since 2026-08-21; still fetched post-match | Deliberate — in-play retired |
| `/fixtures/players` | **1 min** | 1/min per live match | Settlement only (post-match) | Not used live — no live player xG/ratings |

## Hourly Endpoints

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/standings` | **1 hour** | 1/hour per league with live match | 3x/day (04:15, 12:00, 16:00) | Could increase during match days |
| `/predictions` | **1 hour** | 1/hour per live match | Once/day (05:30) | Missing updated predictions as kickoff approaches |
| `/leagues` | Several/day | 1/hour | Once/day (04:00 on Mondays) | Fine |

## Odds Endpoints

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/odds` (pre-match) | ~2 hours | Per bookmaker | **Every 30 min, 24/7** (`workers/scheduler.py:2209-2214`) | We poll faster than AF refreshes |
| `/odds/live` | Real-time during match | Per live match | **Gated off** since 2026-08-21 (`INPLAY_LIVE_ODDS_POLL_ENABLED`) | Deliberate. Note: this file previously claimed we never fetched AF live odds "because we use Kambi" — both halves were wrong. T5 shipped and ran until the gate landed, and Kambi was removed on 2026-05-06 (a *pre-match* Unibet-via-Kambi feed came back 2026-09-04, unrelated to live odds). |

## Every-4-Hour Endpoints

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/injuries` | **4 hours** | 1/day | 3x/day (04:15, 12:00, 16:00) | **Matched** |

## Daily/Weekly Endpoints

| Endpoint | AF Update | AF Recommended | Our Usage | Gap |
|----------|-----------|---------------|-----------|-----|
| `/teams/statistics` | **2x/day** | 1/day per active team | Once/day (04:15) | Fine |
| `/teams` | Several/week | 1/day | Not regularly fetched | Low priority |
| `/coachs` | Daily | 1/day | Client method exists, not scheduled | Not relevant yet |
| `/players` | Several/week | 1/day | Not used | Could enrich match detail |
| `/sidelined` | — | — | Backfill only | Low priority |
| `/transfers` | — | — | Backfill (opt-in) | Low priority |

## Endpoints We Don't Use At All

| Endpoint | What it offers | Potential value |
|----------|---------------|----------------|
| `/odds/live/bets` | Live bet types available | Low while in-play is retired |
| `/fixtures/players` (live) | Per-player live stats (xG, rating) | Low while in-play is retired |
| `/players/topscorers` | League top scorers | Low — display feature |
| `/players/topassists` | League top assists | Low — display feature |
| `/players/topyellowcards` | Most carded players | Low — discipline signal |
| `/players/topredcards` | Most red-carded players | Low — discipline signal |
| `/venues` | Venue data | Low — display feature (client method exists, unscheduled) |

## In-Play Strategy Polling Requirements — HISTORICAL (analysed 2026-05-06)

**Superseded 2026-08-21.** Kept for the record: Strategies A–K were evaluated
against the polling tiers of the time and no frequency changes were needed. The
tiers those rows describe (30s fast / 60s medium) are no longer what runs — see
the current tiers above — and live odds, stats and events are gated off, so none
of these strategies can fire today. Reviving any of them means re-enabling the
env gates and re-accepting the quota cost that was deliberately cut.

| Data needed | Then-current | Strategies requiring it | Verdict (2026-05-06) |
|-------------|---------|------------------------|---------|
| Score + minute | 30s fast | All (entry conditions, goal abort) | Sufficient |
| Live odds (O/U 2.5, 1X2) | 30s fast | All | Sufficient — staleness <60s |
| xG, shots, corners, possession | 60s medium | A, B, C, D, E, G, H, I, J, K | Matched to AF update rate |
| Odds history (10-min window) | 30s fast → DB | Strategy F (15% drift detection) | Not a polling gap |
| 2H kickoff detection (min 46-54) | 30s fast | Strategy K | 15+ cycles in 8-minute window |

---

## The 15-Second Problem — history

The `/fixtures` endpoint updates every 15 seconds. Until 2026-04-30 our live
tracker ran every 5 minutes via a GitHub Actions cron and missed most state
changes. The fix was a long-running `LivePoller` process — **originally on
Railway, moved to the Hetzner VPS scheduler on 2026-06-29 (RAILWAY-ELIMINATION);
there is no Railway component left anywhere in the pipeline.**

Live-play API budget is now a fraction of the old ~10K–15K calls/day estimate,
because live odds, stats and events are gated off — only bulk live `/fixtures`
and lineups still poll. Measured total account usage is 9–23% of the 150,000/day
limit.

## Odds-Specific Notes

- AF `/odds` pre-match refreshes roughly every 2 hours; we poll every 30 min, 24/7
- AF `/odds` does NOT return data for completed fixtures (confirmed 2026-04-30)
- **AF retains odds for exactly 7 days, then drops them** — anything older has to
  come from our own `odds_snapshots` or a historical source
- `/odds/live` is gated off (see above), not absent
- **Pinnacle sends 19 bet types through the bulk `/odds` response, not 8; we parse
  15** — see `docs/ANALYSIS_GOTCHAS.md` § 45
- **AF partitions bookmakers per fixture** — "13 bookmakers" is an account-level
  ceiling, not a per-fixture guarantee. Unibet and Betano lost forward coverage
  for fixtures dated 2026-09-06 onward
- Historical odds need a separate source (football-data.co.uk; The Odds API)
