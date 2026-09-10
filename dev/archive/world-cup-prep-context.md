# World Cup 2026 Prep — Context for Future Sessions

> **For:** Future Claude sessions picking this up after context compression
> **Read first:** `world-cup-prep-plan.md` then `world-cup-prep-tasks.md`
> **Then read:** `pro-tier-rethink-findings.md` (Pro tier is post-WC work)

## TL;DR (90 seconds)

User wants OddsIntel to be ready for the 2026 World Cup (starts 2026-06-11). The spike on 2026-06-02 found:

1. **WC fixtures aren't in the DB** (pipeline only pulls today's). AF returns 72 group-stage matches via `get_fixtures_by_league_season(1, 2026)`. One-shot backfill needed.
2. **National-team data is thin in DB** (73 friendlies) but AF has plenty (~320 historical matches available across Euro 2024, WC 2022, WC qualifiers).
3. **The club prediction model won't work on national teams** — needs a separate ELO + Poisson predictor.
4. **No bookmaker odds available yet** for WC (AF says no, Odds API not configured).
5. **Pro tier needs a rework** but that's a separate concern, deferred until after WC.

## Key facts to anchor decisions

- WC league row exists: `id=108e7471-93af-42bb-81b6-841b9acfa985`, AF id=1, season 2026, `show_on_frontend=False`, `coverage_odds=False`, `coverage_predictions=True`
- First match: 2026-06-11 Mexico v South Africa
- 72 group-stage matches confirmed; knockout brackets seed after group results
- AF predictions endpoint returns flat 33/33/33 for WC fixtures today (will likely populate closer to kickoff, but don't depend on it)
- AF bookmaker odds endpoint returns **0 books** for the first WC fixture as of 2026-06-02

## Key files

| File | What it does |
|---|---|
| `workers/jobs/fetch_fixtures.py` | Currently pulls today's fixtures only. Phase 1 needs `--league` + `--season` CLI args. |
| `workers/api_clients/api_football.py:300` | `get_fixtures_by_league_season(league_id, season)` — already exists, used by historical backfill. |
| `workers/utils/pipeline_utils.py:241` | `FEATURED_WHEN_PLAYING` set, league 1 already included. |
| `workers/utils/pipeline_utils.py:254` | World Cup explicitly mapped to priority tier 1. |
| `workers/api_clients/odds_api.py:20-38` | SPORT_KEYS dict — **no WC entry**. Need to add `soccer_fifa_world_cup` if we want Odds API coverage. |
| `workers/model/features.py:10-36` | Feature set is league-centric, won't work for national teams as-is. |
| `workers/model/train.py:64-100` | Per-league imputation logic, undefined for national teams. |
| `supabase/migrations/005_data_quality_tables.sql` | `team_elo_daily` exists — can store national-team ELO if extended. |
| `TIER_ACCESS_MATRIX.md:86-92` | Current Pro vs Elite split for value bets (Pro sees "directional", Elite sees full). |

## What we already explicitly killed (so we don't reopen these)

1. **"Top-5 picks per day" as Pro pitch** — 3-year backtest on `dev/active/backtest-2023plus.csv` (45,955 rows, 2023-02 → 2026-05): all Top-N cohorts ranked by edge/prob/combo show **-10% to -12% ROI** vs +1.68% baseline. In 2026, baseline turned +0.73% but Top-5 was -9.12%. Per-day ranking is anti-selection — picks the model's most-wrong calls.

2. **"80%+ hit rate bankers" product** — our model is overconfident: when it says ≥90%, actual hit rate is 66%. WR caps at 66% regardless of how confident we get. Known issue (see `platt-overconfidence-deepdive-findings.md`). Cannot honestly market high-hit-rate picks.

3. **Reviving retired bots with high backtest ROI** — investigated. `bot_high_roi_global` (backtest +51% / live -49%), `bot_proven_leagues` (backtest +46% / live -67%), `bot_btts_all` (backtest +8% / live -5%), `bot_ou35_attacking` (backtest +35% / live -39%). The backtest CSV uses parameters that don't apply production calibration, so retired-bot backtest numbers are inflated. Retirements were correct. One exception: `bot_ou15_defensive` (live +30% / +50% CLV on n=40) was retired only for silence (no candidates passing thresholds) — could be revisited after June 8 calibration retrain.

## What's NOT decided yet

- Odds source strategy for WC (Odds API add / scrape Coolbet / accept no odds)
- Dedicated WC bot vs lifting league_filter on existing bots
- Marketing spend (organic-only vs paid)
- AF predictions blend ratio (when/if AF publishes WC predictions)

## Pipeline gotcha

`workers/jobs/fetch_fixtures.py` is invoked at 06:00 UTC daily and pulls **today's date only** (`get_fixtures_by_date`). It does NOT auto-backfill future fixtures. So even if WC league is included in the featured set, matches don't appear in DB until kickoff day unless we explicitly backfill via `get_fixtures_by_league_season`. **Phase 1 is essentially a one-time call.**

## What's been done

- **WC-PHASE-1 (2026-06-02)**: 72 WC 2026 group-stage fixtures backfilled. Migration 163 flips `show_on_frontend=true`. Committed `94eb355`. Smoke: WC-FIXTURES-IN-DB.
- **WC-PHASE-2 fixtures (2026-06-02)**: 6,921 international fixtures across 25 competitions, 6,651 finished — Friendlies 1,923, UEFA NL 668, WC Qual Europe 463, AFCON Qual 460, WC Qual Asia 459, WC Qual Africa 431, Euro+Quals 603, AFCON 358, WC 200, Asian Cup 102, Copa America 60. Smoke: INTL-BACKFILL.
- **WC-PHASE-2 enrichment**: partial (293 lineups, 1,576 stats, 25,759 events) and running in background ~25h ETA. Phase 3 doesn't depend on this.

## Critical caveats

- **Backtest CSV ≠ production reality.** Discovered during retired-bot investigation. The `backtest-2023plus.csv` file used by /tmp/top5_validate.py uses model output that doesn't apply production calibration. Any Pro-tier validation built on this CSV is suspect. **Use live `simulated_bets` table for honest validation** — not the backtest CSV.
- **Per-day ranking is anti-selection.** Confirmed across 3 years and every ranking metric tried (edge, prob, edge×prob). Do not propose "top N picks per day" again.
- **The model has a calibration ceiling at ~66% WR.** No high-confidence cohort exceeds this. Don't promise hit-rate-based products.
- **`backfill_internationals.py` enrichment is per-match-storage-bottlenecked.** Each match takes ~13 seconds for lineups + N events + stats + ~22 player_stats rows. With 6,628 matches, full enrichment is ~25 hours. Phase 3 (ELO + Poisson) doesn't need this — only scores + dates + teams + competition, all in DB from Phase A. Follow-up `OPT-BACKFILL-INTL`: parallelise via ThreadPoolExecutor + batch player_stats inserts via `execute_values` + add `--skip-players` flag. Deferred to post-WC.

## Snapshot of validation queries used

- `/tmp/top5_validate.py` — Top-N cohort analysis on 3-year backtest CSV
- `/tmp/pro_validation.py` — Pro-feed + high-prob intersection analysis
- `/tmp/wc_af_probe.py` — AF probe for WC fixtures / odds / predictions / historical depth

(These are temp scripts; reproduce as needed.)

## Where decisions on this work are tracked

- This file: standing context
- `world-cup-prep-plan.md`: phases + open questions
- `world-cup-prep-tasks.md`: checklist with phases
- `pro-tier-rethink-findings.md`: deferred Pro work
- `PRIORITY_QUEUE.md`: status of each task
