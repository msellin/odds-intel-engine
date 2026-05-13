# BET-TIMING-MONITOR — Plan

**Goal:** answer empirically, for every bot × market, *what time of day produces the highest ROI?* — without disrupting any user-facing data or placed bets.

**Why now:** Phase A timing analysis (2026-05-13) showed the cohort A/B test is confounded — different bots in different cohorts means we cannot tell whether timing or strategy drives the 4× ROI gap. Same-bot direct evidence (`bot_ou15_defensive`: morning −2.1%, midday +34.1% on OU n=33) confirms timing matters for at least one bot×market combination. We need clean factorial data to act on the other 20 bots.

## Approach: shadow bets

For every bot, at every refresh window (06 / 11 / 15 UTC), evaluate the same model with the same inputs and record what bet the bot *would* place — write these "shadow bets" to a separate table. Real bets continue as today (cohort-gated). After ~30 days of shadow data, each bot has ROI numbers for all 3 timing windows on the same market mix, removing the strategy confound.

## Components

| # | Component | Risk to existing pipeline |
|---|---|---|
| 1 | Migration: `shadow_bets` table | None (additive) |
| 2 | `run_morning(shadow_mode=True)` path | None (gated by flag, default off) |
| 3 | `bulk_store_shadow_bets` helper | None (new function) |
| 4 | Scheduler: 3 new jobs at 06:30 / 11:30 / 15:30 UTC | Small — 3× existing refresh compute on idle pod, no extra AF calls |
| 5 | Settlement: parallel pass over `shadow_bets` | Low — same logic, different table |
| 6 | Ops snapshot: `shadow_runs_today`, `shadow_bets_today` | None (new columns on ops_snapshots) |
| 7 | Smoke tests | None |

## Schema

`shadow_bets` mirrors `simulated_bets` minus bankroll-related fields:

| Column | Type | Note |
|---|---|---|
| id | uuid PK | |
| shadow_run_id | uuid | groups all bets from one evaluation pass |
| shadow_cohort | text | `'morning'` / `'midday'` / `'pre_ko'` — the window being shadowed |
| bot_id | uuid | |
| match_id | uuid | |
| market | text | |
| selection | text | |
| odds_at_pick | numeric | best accessible-book odds at shadow eval time |
| pick_time | timestamptz | when the shadow was generated |
| model_probability | numeric | |
| calibrated_prob | numeric | |
| edge_percent | numeric | |
| recommended_bookmaker | text | |
| closing_odds | numeric | set by settlement |
| clv | numeric | set by settlement |
| result | enum (won/lost/void/pending) | settlement settles all 3 cohorts |
| kelly_fraction | numeric | recorded for traceability |
| timing_cohort | text | bot's assigned cohort (constant per bot) |
| created_at | timestamptz | |

Indexes:
- `(bot_id, match_id, market, selection)` — primary cross-cohort join key
- `(shadow_cohort, pick_time)` — daily summaries
- `(result)` — partial index `WHERE result='pending'` for settlement

## Invariants (locked in by smoke tests)

1. `shadow_bets` is **never** queried by `simulated_bets`-aware code paths (frontend, daily_picks, bot_perf_report). Cross-check at commit time.
2. Shadow bets do **not** affect bankroll. `bulk_store_shadow_bets` is a write-only path.
3. Shadow bets are settled with the *exact same* `settle_bet_result()` function used for `simulated_bets` — outcome arithmetic stays in one place.
4. OU bots stay in their cohort (BOT-TIMING-OU-MIDDAY guard).

## Phases

| Phase | When | What |
|---|---|---|
| 1 | NOW (this session) | Build & deploy infrastructure |
| 2 | 2026-05-14 … 2026-06-12 | Collect ~30 days of shadow data; no action |
| 3 | ~2026-06-15 | Run `bot_timing_recommendation.py` (built later); decide cohort moves |
| 4 | Ongoing | Weekly report; optional auto-tune |

## Compute cost

Each shadow run does what `run_morning(skip_fetch=True)` already does, just for ALL 23 bots instead of one cohort. ~3× the refresh-run cost. No extra AF calls (data already in DB). Estimated +30–60s per shadow run × 3 runs = +1.5–3 min/day total pod time. Railway $5/mo plan absorbs trivially.

## Two-dimensional timing analysis (free with current data)

Each shadow row records both `pick_time` (when the shadow was generated) and
joins to `matches.date` (kickoff), so `hours_before_ko = EXTRACT(EPOCH FROM
(m.date - sb.pick_time))/3600` is available without changing the schema.
The Phase 3 analysis can cut by:

| Dimension | Values |
|---|---|
| bot | 23 strategies |
| market | 1X2 / O/U / BTTS / AH / DC / DNB |
| shadow_cohort | morning / midday / pre_ko (fixed UTC window) |
| hours_before_ko | 0-2h / 2-4h / 4-8h / 8-12h / 12h+ (derived) |

Same bot evaluated at 15:30 UTC has very different `hours_before_ko` depending
on the match's kickoff (e.g. 30 min before a 16:00 KO vs 2.5h before an 18:00
KO). This natural variance is what lets us answer "best time to bet" along
two axes: absolute time-of-day (shadow_cohort) and match-relative (hours
before KO). No extra cohorts needed for that — 3 fixed windows + derived
hours-before-KO gives ~15 cells in the grid.

## Out of scope (later)

- Auto-cohort-move when shadow data is conclusive (Phase 4)
- Hourly granularity (currently 3 windows; 12 windows would be 4× cost again)
- Pre-cutoff shadow backfill (can't reconstruct historical model state cleanly)

## Success criteria

- 23 bots × 3 cohorts × ~30 days = ~2000 shadow bets settled
- Per-bot ROI delta visible in a single query
- One bot reassigned based on shadow evidence by 2026-06-30
