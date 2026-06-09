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

## Phase 2 — Historical internationals backfill (DONE 2026-06-02; enrichment continuing in background)

- [x] Probe AF for available comps (`/tmp/probe_intl.py`) — 79 senior men's national-team competitions found, 59 selected for backfill
- [x] Backfill script `scripts/backfill_internationals.py` with 59 (league, season) tuples (WC 2018/2022, Euro 2020/2024 + qual, Copa America 2021/2024, AFCON 2019/2021/2023/2025, Asian Cup 2019/2023 + qual, Gold Cup 2019/2021/2023/2025, CONCACAF Nations League, UEFA Nations League ×4, WC 2022 + 2026 qualifiers all 6 confederations, Friendlies 2022-25, regional comps)
- [x] Two-phase: (A) fixtures via `get_fixtures_by_league_season` + `bulk_store_matches`, (B) nested data (lineups, events, statistics, player stats) via `get_fixtures_batch` for finished matches
- [x] Idempotent: bulk_store_matches upserts on api_football_id; enrichment skips matches that already have `match_stats` rows
- [x] CLI: `--no-enrichment`, `--dry-run`, `--filter` for partial runs
- [x] **Phase A complete: 6,921 fixtures stored across 25 competitions (6,651 finished).** Key buckets: Friendlies 1,923 / UEFA NL 668 / WC Qual Europe 463 / AFCON Qual 460 / WC Qual Asia 459 / WC Qual Africa 431 / Euro+Quals 603 / AFCON 358 / WC 200 / Asian Cup 102 / Copa America 60. All confederations' WC qualifiers covered for 2022 + 2026 cycles.
- [~] **Phase B partial: enrichment runs at ~4 fixtures/min (per-match storage cost is the bottleneck: lineups + N events + stats + ~22 player_stats rows × 6,628 matches). Full enrichment ETA ~25 hours.** After ~38 min: 293 lineups, 1,576 match_stats, 25,759 match_events (most events pre-existing from prior live_tracker / settlement runs). Script is **left running in background (PID 4606)** — will keep adding data over the next hours. Phase 3 (ELO + Poisson) does NOT depend on lineups/stats; it only needs scores + dates + teams which Phase A already produced.
- [x] Smoke test: INTL-BACKFILL (source inspection)
- [x] Verification: counts per competition (see `dev/active/world-cup-prep-context.md`)
- [x] Commit + push

### Follow-up filed (post-WC)
- **OPT-BACKFILL-INTL** — optimise the enrichment phase: parallelise with ThreadPoolExecutor (settlement.py uses 2 workers), batch player_stats inserts via `execute_values`, optionally add `--skip-players` flag. Not blocking Phase 3 or any WC ship work.

## Phase 3 — National-team predictor (DONE 2026-06-02)

- [x] Schema decision: NEW table `team_elo_international` (separate from `team_elo_daily` because club ELO uses different K/home-adv and the trajectories shouldn't pollute each other). Migration 164.
- [x] Compute ELO from international match history — walked 6,651 finished international matches. K-factor: tournament 40 / qualifier+NL 25 / friendly 10. Home advantage +60 only on qualifier_nl matches (tournaments mostly neutral). Goal-diff multiplier `max(1, sqrt(gd+1))`. Script: `scripts/compute_international_elo.py` (idempotent — TRUNCATE + rebuild).
- [x] Build simple Poisson predictor on last-N-internationals weighted by competition (tournament 1.0 / qualifier 0.8 / friendly 0.3). Module: `workers/model/national_team_predictor.py`.
- [x] Persist to `predictions` with `source='national_team_v1'` (note: column is `source`, NOT `model_source` — frontend agent flagged this in v1).
- [x] Validation: backtest on 141 holdout matches (WC 2022 + Euro 2024 + Copa 2024 + AFCON 2023). 1X2 log-loss 1.0697 vs 1.0986 baseline (+2.6%), 45.4% top-pick accuracy. Per-tournament: Euro 2024 63.6%, WC 2022 48.4%, Copa 35.7%, AFCON 40.4%. Script: `scripts/backtest_national_team_model.py`.
- [x] **Goals model insufficient for value-bet claims** — O/U 2.5 log-loss 0.696 with smoothing (essentially tied with empirical-rate baseline 0.693). Ship 1X2 only; hide goals predictions or render as informational.
- [x] Parameter sweep — best 1X2 config: `softening_factor=1.3, draw_base=0.30`. Best combined config (with smoothing): `softening_factor=1.3, avg_goals_per_team=1.15, goals_smoothing=0.3`.
- [x] Smoke test: NATIONAL-TEAM-PREDICTOR-V1 (source inspection of K-factors + holdout pairs + migration schema)
- [ ] Wire predictor as a scheduled job — write predictions to `predictions` table for upcoming WC matches (TBD: morning cron after fixtures arrive)
- [ ] Frontend integration — `/world-cup` page reads from `predictions` source='national_team_v1' once predictions are written (v1 page already has the slots ready)

### Follow-up filed (post-WC)
- **GOALS-MODEL-V2** — current Poisson on team-level recent-goal-rates doesn't beat baseline. Try ELO-derived expected goals + Dixon-Coles rho + competition-tier league averages instead.

## Phase 4 — WC frontend (DONE 2026-06-06; full audit confirmed scope FAR exceeds original spec)

- [x] `/world-cup` route — group standings + 104 fixtures (`src/app/(app)/world-cup/page.tsx`, 7 tabs)
- [x] Group-stage advancement probability calculator (5,000-iter Monte Carlo, `computeAdvancement()`)
- [x] Tier gating: free sees group stage, Pro/Elite get knockout (KnockoutsPanel Pro lock footer)
- [x] Bracket vs user bracket — shipped as stage-gated WC-BRACKET + WC-AI-GHOSTS + WC-ACHIEVEMENTS
- [x] Group standings predictor (`/world-cup/groups-predictor`, 12 groups × 4 positions)
- [x] WC-PHASE-4b — Odds via The Odds API (`ODDS-API-WC` 2026-06-06), daily cron 06:30 UTC, gated to 06-11→07-19. 22 books incl. Pinnacle/Bet365/Unibet, ~5,800-6,200 rows per sweep.
- [x] SEO: structured data on `/world-cup`, /world-cup/teams/[name], /world-cup/predictions-record, /world-cup/who-can-win (SEO-EVENT-SCHEMA-COMPLETE 2026-06-06)
- [x] WC ad-landing OG cards (WC-AD-LANDING-OG 2026-06-09) — opengraph-image.tsx on /world-cup, /world-cup/bracket, /world-cup/groups-predictor; explicit openGraph + twitter metadata blocks
- [x] Tier matrix already documents productized version (`TIER_ACCESS_MATRIX.md:117 World Cup 2026 Games`)
- [x] Smoke: WC-AI-PREVIEW-FE, WC-BRACKET-*, WC-GROUP-PREDICTOR-*, WC-AI-GHOSTS-*, WC-AD-LANDING-OG

## Phase 4b — Odds source (DONE 2026-06-06)

- [x] DECIDED — The Odds API free tier (500 credits/mo), `soccer_fifa_world_cup` (ODDS-API-WC 2026-06-06)
- [x] `scripts/odds_api_wc_sweep.py` — bookmaker key→our-convention map, OU half-line filter, AH home-perspective
- [x] Daily Railway cron `job_wc_odds_sweep` 06:30 UTC, gated to 2026-06-11 → 2026-07-19 window
- [x] First sweep 5,858 rows; ongoing daily sweeps add ~6,200 rows. Pinnacle confirmed available on WC specifically.

## Phase 5 — Marketing + retention (in parallel)

- [x] **Paid Meta/IG ads — landing OG cards ready 2026-06-09** (WC-AD-LANDING-OG): /world-cup, /world-cup/bracket, /world-cup/groups-predictor all serve WC-themed 1200×630 cards. Recommended ad split: bracket ("Beat 5 AIs") + groups-predictor ("Predict 12 groups · 192 pts") + retargeting on /world-cup/teams/[name].
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
