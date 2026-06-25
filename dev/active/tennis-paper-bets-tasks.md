# TENNIS-PAPER-BETS — Tasks

Status legend: ⬜ not started · 🔄 in progress · ✅ done · ❌ blocked

## Phase 1 — Provider swap (OddsPapi → The Odds API) + settlement + closing odds

- [✅] **1.1** ~~Probe OddsPapi v4~~ → quota busted, pivoted to The Odds API. Probe `scripts/tennis/probe_odds_api_tennis.py` confirms 100% Pinnacle coverage + working `/scores` endpoint
- [✅] **1.2** Decision: The Odds API for both odds and settlement
- [✅] **1.3** `scripts/tennis/odds_api_scanner.py` — lists active tennis sport keys, fetches `/odds` per sport, computes edge vs Pinnacle, writes to `tennis_value_bets`. Verified live: 32 events, 100% Pinnacle, 3 credits/scan
- [✅] **1.4** `scripts/tennis/settle_value_bets.py` — calls `/sports/{key}/scores?daysFrom=2` per active sport, settles rows with `completed: true` (win/loss/void). Cheap pre-check skips /scores calls when no row past KO+2h
- [✅] **1.5** `scripts/tennis/capture_closing_odds.py` — every 30 min during tennis hours, captures Pinnacle h2h for value-bet rows with kickoff in next 45 min, overwrites `closing_odds` + computes `clv = book_odds/close - 1`. Re-captures on every cron until kickoff so final stored value is the closest-to-kickoff snap. Reads from `tennis_value_bets` (not `_fixtures_today`) so we only burn credits on sports with rows actually needing CLV. Pre-check skips API calls entirely when nothing's imminent
- [✅] **1.6a** Scheduler: `job_tennis_scanner` (existing 06:00 + 14:00 UTC slots) repointed to `odds_api_scanner.py`; env-var check switched OP_KEY → OA_KEY/ODDS_API_KEY
- [✅] **1.6b** Added `job_tennis_settlement` (02:00 + 14:15 UTC)
- [✅] **1.6c** Added `job_tennis_closing_odds` (every 30 min, 06-22 UTC)
- [✅] **1.7** Deleted `scripts/tennis/value_scanner.py` + obsolete `TENNIS-PLAYER-NAMES` smoke test. OddsPapi client kept for soccer CLV backfill
- [✅] **1.8a** Smoke `TENNIS-ODDS-API-SCANNER` (passing)
- [✅] **1.8b** Smoke `TENNIS-SETTLEMENT` (passing)
- [✅] **1.8c** Smoke `TENNIS-CLOSING-ODDS` (passing)
- [✅] **1.9** Admin overview rebuild — `/admin/tennis` now shows: system health (scanner/settlement status, pending settlement count), 30-day settled aggregates (hit rate, ROI, PnL), breakdowns by edge band + bookmaker + tournament, recent settled picks, pending-settlement debug table, today's value sheet (existing flow)

## Phase 2 — Bot segmentation

- [✅] **2.1** Migration 261 — `tennis_value_bets`: added `bot_id text`, `strategy_profile text`; widened unique index to include `bot_id`; backfilled existing rows with `legacy_unsegmented`
- [✅] **2.2** `scripts/tennis/bots_config.py` — 3 paper bots: `bot_tennis_pin_broad` (≥3% any book), `bot_tennis_pin_selective` (≥5% any book), `bot_tennis_coolbet_only` (≥3% Coolbet only)
- [✅] **2.3** Backfill: handled inside migration 261 (UPDATE WHERE bot_id IS NULL)
- [✅] **2.4** Refactored `odds_api_scanner.py` to route observations through `matching_bots()`; each qualifying (book, edge) produces one row PER matching bot
- [✅] **2.5** Refactored `place_coolbet_tennis.py` to route every observation through `matching_bots()` (so Coolbet rows land in coolbet_only AND pin_broad/selective lanes as edge dictates)
- [⬜] **2.6** Sanity guards: reject rows with `book_odds > 10.0` OR `pin_fair_odds > 8.0` (deferred — current scanner already drops `edge > 40%` as fixture mismatch; this would add a secondary belt-and-braces filter)
- [✅] **2.7** Smoke `TENNIS-BOTS-CONFIG` — pins bot registry, matcher boundary cases, scanner integration, migration shape

## Phase 3 — Visibility & guardrails

- [✅] **3.1** `/admin/tennis` — per-bot ROI/CLV table shipped in Phase 2 (ADMIN-TENNIS-PER-BOT)
- [✅] **3.2** `check_tennis_scanner_silent` — alerts via existing morning runner (09:35 UTC) when last `tennis_scanner` pipeline_runs success > 12h ago. Catches the OddsPapi silent-failure class
- [✅] **3.3** `check_tennis_settlement_stale` — alerts via existing settlement runner (21:30 UTC) when > 5 rows are past kickoff+6h with NULL result
- [✅] **3.4** Smoke `TENNIS-HEALTH-ALERTS` pinning thresholds + runner wiring
- [⬜] **3.5** Doc updates: WORKFLOWS.md (new tennis crons), DATA_SOURCES.md (provider swap), MEMORY for tennis pipeline state
- [⬜] **3.6** Mark `TENNIS-PAPER-BETS` ✅ Done in PRIORITY_QUEUE.md once 7-day soak completes and we have first settled-bet data
