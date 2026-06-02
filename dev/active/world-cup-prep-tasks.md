# World Cup 2026 Prep — Tasks

> Update inline as work progresses. Mark `[x]` when done. See `world-cup-prep-plan.md` for phase context.

## Phase 0 — Spike (DONE 2026-06-02)

- [x] Confirm WC fixtures available via AF
- [x] Audit national-team historical depth (Euro 2024, WC 2022, qualifiers)
- [x] Backtest "Top-N per day" Pro pitch (KILLED — anti-selection, -10% ROI)
- [x] Backtest "high-hit-rate bankers" pitch (KILLED — calibration ceiling at 66%)
- [x] Investigate retired-bot anomaly (EXPLAINED — backtest CSV inflates retired bot numbers)
- [x] Write findings to `dev/active/` docs

## Phase 1 — Get WC fixtures visible (DONE 2026-06-02)

- [x] Add `--league` and `--season` CLI args to `workers/jobs/fetch_fixtures.py`
- [x] Wire `get_fixtures_by_league_season` path when args provided
- [x] Run `python -m workers.jobs.fetch_fixtures --league 1 --season 2026` (72 fixtures stored)
- [x] Verify 72 fixtures landed in `matches` table (June 11-28). Note: `season=2025` per our football-season convention (June months = previous year). Linked to WC league via league_id correctly.
- [x] Create migration 163 to flip `leagues.show_on_frontend = true WHERE api_football_id = 1`
- [ ] Verify on staging that WC matches appear in `/matches` and on homepage (after migration applies via GH Actions)
- [x] Smoke test: WC-FIXTURES-IN-DB (source-inspection of CLI args + migration file)
- [ ] Commit + push (this commit)

## Phase 2 — Historical internationals backfill (1 day)

- [ ] Decide which competitions to enable. Candidates: Euro 2024 (id=4), WC 2022 (id=1 s2022), WC Quals Europe (id=32), WC Quals S.America (id=29), WC Quals Asia (id=35), WC Quals Africa (id=36), Nations League (id=5)
- [ ] Confirm AF id list and fixture availability per `/tmp/wc_af_probe.py`
- [ ] Backfill script — one-off `scripts/backfill_internationals.py` calling `get_fixtures_by_league_season` for each (lid, season)
- [ ] Store via `bulk_store_matches`
- [ ] Run + verify ~400+ international matches now in DB with results
- [ ] Smoke test: INTL-BACKFILL (count + sanity row check)

## Phase 3 — National-team predictor (2-3 days)

- [ ] Schema decision: extend `team_elo_daily` with `is_international` flag, or new table `team_elo_international`
- [ ] Compute ELO from international match history (separate K-factor likely needed)
- [ ] Build simple Poisson predictor on last-20-internationals scoring rates (no league features)
- [ ] Optional: AF predictions endpoint blend (when/if AF publishes)
- [ ] Persist to `predictions` with `model_source='national_team_v1'`
- [ ] Validation: backtest on Euro 2024 holdout — does it predict knockout outcomes better than coin flip?
- [ ] Validation: backtest on WC 2022 holdout — same
- [ ] Confidence threshold higher than club model (lean toward "no bet")
- [ ] Smoke test: NATIONAL-TEAM-PREDICTIONS (predictions exist for upcoming WC fixtures with non-flat probabilities)

## Phase 4 — WC frontend (2-3 days, odds-intel-web repo)

- [ ] `/world-cup` route — group standings + 104 fixtures
- [ ] Group-stage advancement probability calculator (live, recompute after each result)
- [ ] Tier gating: free sees group stage predictions, Pro/Elite get knockout
- [ ] (optional) Bracket vs user bracket (free-tier signup hook)
- [ ] (optional) Outright odds + CLV tracker (depends on Phase 4b)
- [ ] SEO: per-team and per-match URLs with structured data
- [ ] Update `TIER_ACCESS_MATRIX.md` with WC-specific tier rules
- [ ] Smoke test: WC-LANDING-PAGE (route responds, key sections render)

## Phase 4b — Odds source (decision required first)

- [ ] DECIDE: add `soccer_fifa_world_cup` sport_key to Odds API client / scrape Coolbet / no odds
- [ ] If Odds API: add to `workers/api_clients/odds_api.py:20-38` SPORT_KEYS dict
- [ ] If Odds API: extend `coolbet_odds_snapshot` or equivalent to fetch + store
- [ ] If no odds: explicitly accept "predictions only, no value bets" for WC matches

## Phase 5 — Marketing + retention (in parallel)

- [ ] Reddit post(s) using existing `docs/REDDIT_LAUNCH.md` playbook adapted for WC
- [ ] Daily prediction tweets — manual or `scripts/wc_daily_tweet.py`
- [ ] Email signup hook on `/world-cup` page
- [ ] Underdog spotlight email (weekly during WC)
- [ ] (optional) Public WC bot — paper, bankroll log on `/track-record/wc-bot`
- [ ] (optional) Bracket challenge (legal/compliance check first if prizes involved — avoid prizes)

## Phase 6 — Post-WC Pro tier redesign (DEFERRED)

See `pro-tier-rethink-findings.md` for the validation work done so far + open questions.

## Doc updates required per CLAUDE.md (do these inside each phase's commit)

- `PRIORITY_QUEUE.md` — status updates per task
- `ROADMAP.md` (Current System State) — WC support added
- `WORKFLOWS.md` — new backfill scripts (Phase 2) + national-team predictor job (Phase 3)
- `DATA_SOURCES.md` — note WC odds gap, national-team competitions enabled
- `TIER_ACCESS_MATRIX.md` — WC-specific tier rules (Phase 4)
- `MODEL_WHITEPAPER.md` — national-team predictor architecture (Phase 3, mandatory per CLAUDE.md)
