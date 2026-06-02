# World Cup 2026 Preparedness — Plan

> **Status:** 🔄 In progress (planning, no implementation yet)
> **Owner:** margus
> **Created:** 2026-06-02
> **Deadline:** Group stage opens 2026-06-11 (9 days from start)
> **Tournament:** 2026-06-11 (Mexico v South Africa) → 2026-07-19 (Final)
> **Format:** 48 teams, 12 groups × 4, 104 total matches

## Why this matters

World Cup is the largest single-event traffic spike in soccer. One-shot opportunity for OddsIntel acquisition + organic SEO. Compounds if shipped on time; bounces if shipped late or sloppy.

## What we found in the spike (2026-06-02)

### Engine state vs WC

| Layer | Status | Note |
|---|---|---|
| WC league row | ✓ exists | `id=108e7471…`, `show_on_frontend=False` |
| WC fixtures in DB | ✗ 0 matches | Pipeline only pulls today's fixtures |
| WC fixtures available from AF | ✓ 72 group-stage | `get_fixtures_by_league_season(1, 2026)` |
| AF predictions for WC | ✗ flat 33/33/33 | "No predictions available" until closer to tournament |
| AF bookmaker odds for WC | ✗ 0 books | `coverage_odds=False` on league row |
| The Odds API WC sport_key | ✗ not configured | Need to add `soccer_fifa_world_cup` |
| National-team historical depth | partial | 73 friendlies in DB; AF has Euro 2024 (51), WC 2022 (64), WC Quals Europe 2024 (204) |
| Model fit for national teams | ✗ club-trained | Features assume league/season context |
| Bots open to national teams | ~3-4 | Most bots have country league_filter |

### What's clearly required to ship credibly

1. **Backfill WC 2026 fixtures** — one AF call, 72 group-stage matches. Flip `show_on_frontend=true`.
2. **Backfill historical internationals** — Euro 2024 + WC 2022 + WC qualifiers (~320 matches) to enable an ELO / Poisson model that has any signal.
3. **National-team prediction model** — separate from the club model. ELO + last-N internationals Poisson, no league features.
4. **Odds source for WC** — either add `soccer_fifa_world_cup` sport_key to The Odds API client, or accept "predictions only, no value bets" for WC.
5. **Frontend `/world-cup` landing page** — group stage table, advancement probabilities, our model's predicted bracket.

### What's nice-to-have but not blocking

- Bracket vs user bracket (free-tier hook)
- Underdog spotlight email
- Group-stage advancement probability calculator (live)
- AI live commentary (Elite, Gemini-powered)
- Public WC bot (paper, marketed publicly)
- Pre-tournament outright odds tracker

### What we explicitly killed

- "Top-5 picks per day" as the Pro pitch — 3-year backtest shows **-10.25% ROI** vs +1.68% baseline. Per-day ranking by edge/prob is anti-selection. See `pro-tier-rethink-findings.md`.
- "80%+ hit rate bankers" product — model is overconfident at the high end (says 90%, hits 66%). Can't honestly market it. Known issue per `platt-overconfidence-deepdive-findings.md`.

## Phased plan

### Phase 0 — Spike validation (DONE 2026-06-02)
- ✓ Confirm WC fixtures available from AF
- ✓ Audit national-team historical depth
- ✓ Backtest Top-N hypothesis (killed)
- ✓ Backtest high-hit-rate hypothesis (killed; calibration wall)
- ✓ Investigate retired-bot anomaly (explained; backtest CSV inflates retired bots)

### Phase 1 — Get WC fixtures visible (1-2 hours, ship ASAP)
1. Add `--league NN --season YYYY` CLI args to `workers/jobs/fetch_fixtures.py`
2. Run `python -m workers.jobs.fetch_fixtures --league 1 --season 2026` → 72 fixtures land in `matches`
3. Migration to flip `leagues.show_on_frontend = true WHERE api_football_id = 1`
4. Verify on frontend that WC matches surface
5. Smoke test: WC-FIXTURES-IN-DB

### Phase 2 — Historical internationals backfill (1 day)
1. Add national-team competition AF IDs to whitelist (Euro 2024, WC 2022, WC quals Europe 32 / S.America 29 / Asia 35 / Africa 36, Nations League 5)
2. One-off backfill script: pull `get_fixtures_by_league_season(lid, season)` for each, store via `bulk_store_matches`
3. Verify ~400+ national-team matches in DB with results

### Phase 3 — National-team predictor (2-3 days)
1. Compute team-level ELO from international match history (separate `team_elo_international` column or table)
2. Build simple Poisson on last-20-internationals scoring rates
3. Blend with AF predictions endpoint as secondary signal (once AF publishes WC predictions)
4. Persist predictions to `predictions` table with `model_source='national_team_v1'`
5. Calibration validation: backtest model on Euro 2024 + WC 2022 holdouts before going live
6. Higher confidence thresholds than club model — better to say "no bet" than place noise

### Phase 4 — WC frontend (2-3 days)
1. `/world-cup` landing page (group standings, all 104 fixtures, our predictions)
2. Live group-stage advancement probability calculator (recompute after each result)
3. Free-tier: predictions for group stage. Pro-gated: knockout stage predictions.
4. Outright odds + CLV tracker (if Odds API integration done)
5. Marketing pages: SEO-friendly per-team and per-match URLs

### Phase 5 — Marketing + retention (in parallel with Phase 3-4)
1. Reddit launch posts (use existing `docs/REDDIT_LAUNCH.md` playbook)
2. Daily prediction tweets (manual or scripted)
3. Email signup hook on `/world-cup` page
4. Underdog spotlight email per week
5. Consider: free public "WC bot" with public bankroll

### Phase 6 — Post-WC: Pro tier redesign
See `pro-tier-rethink-findings.md`. Deferred until after WC traffic spike.

## Open questions / unresolved

- **Odds source:** add `soccer_fifa_world_cup` to The Odds API client? Or rely on AF if/when they publish? Or scrape Coolbet? Decision needed before Phase 4.
- **AF predictions for WC:** AF returns "no predictions available" today; they may populate closer to kickoff. Plan A: rely on our own model. Plan B: blend with AF if/when available.
- **Bot strategy for WC:** lift league_filter on bot_aggressive_v2 + bot_v10_all for WC matches? Or build a dedicated `bot_world_cup`? Decision needed Phase 3.
- **Marketing budget:** does anything beyond organic Reddit/Twitter get spend? User to confirm.

## Risks

1. **National-team predictor produces low-quality picks.** Mitigation: high confidence threshold, "no bet" fallback, framed as predictions not value bets.
2. **No odds source materialises.** Mitigation: lead with "AI predictions" not "value bets" — predictions are the lead product, value bets a power-user feature.
3. **AF predictions endpoint stays empty.** Mitigation: blend is optional, we don't depend on it.
4. **Knockout bracket only seeds after group stage.** This is fine — we recompute predictions each day anyway.
5. **Calibration drift on national teams.** Mitigation: validate on Euro 2024 + WC 2022 before launch.

## Smoke tests required (per CLAUDE.md)

- WC-FIXTURES-IN-DB (Phase 1)
- INTL-BACKFILL (Phase 2)
- NATIONAL-TEAM-PREDICTIONS (Phase 3)
- WC-LANDING-PAGE (Phase 4)
- Each phase adds a single test to `scripts/smoke_test.py`
