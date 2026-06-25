# TENNIS-PAPER-BETS — Tasks

Status legend: ⬜ not started · 🔄 in progress · ✅ done · ❌ blocked

## Phase 1 — Provider swap (OddsPapi → The Odds API) + settlement + closing odds

- [✅] **1.1** ~~Probe OddsPapi v4~~ → quota busted, pivoted to The Odds API. Probe `scripts/tennis/probe_odds_api_tennis.py` confirms 100% Pinnacle coverage + working `/scores` endpoint
- [✅] **1.2** Decision: The Odds API for both odds and settlement
- [✅] **1.3** `scripts/tennis/odds_api_scanner.py` — lists active tennis sport keys, fetches `/odds` per sport, computes edge vs Pinnacle, writes to `tennis_value_bets`. Verified live: 32 events, 100% Pinnacle, 3 credits/scan
- [✅] **1.4** `scripts/tennis/settle_value_bets.py` — calls `/sports/{key}/scores?daysFrom=2` per active sport, settles rows with `completed: true` (win/loss/void). Cheap pre-check skips /scores calls when no row past KO+2h
- [⬜] **1.5** `scripts/tennis/capture_closing_odds.py` — runs near each fixture's kickoff, updates `closing_odds` for that fixture's selections
- [✅] **1.6a** Scheduler: `job_tennis_scanner` (existing 06:00 + 14:00 UTC slots) repointed to `odds_api_scanner.py`; env-var check switched OP_KEY → OA_KEY/ODDS_API_KEY
- [✅] **1.6b** Added `job_tennis_settlement` (02:00 + 14:15 UTC)
- [⬜] **1.6c** Add `job_tennis_closing_odds` (hourly 06-22 UTC)
- [✅] **1.7** Deleted `scripts/tennis/value_scanner.py` + obsolete `TENNIS-PLAYER-NAMES` smoke test. OddsPapi client kept for soccer CLV backfill
- [✅] **1.8a** Smoke `TENNIS-ODDS-API-SCANNER` (passing)
- [✅] **1.8b** Smoke `TENNIS-SETTLEMENT` (passing)
- [⬜] **1.8c** Smoke `TENNIS-CLOSING-ODDS`
- [✅] **1.9** Admin overview rebuild — `/admin/tennis` now shows: system health (scanner/settlement status, pending settlement count), 30-day settled aggregates (hit rate, ROI, PnL), breakdowns by edge band + bookmaker + tournament, recent settled picks, pending-settlement debug table, today's value sheet (existing flow)

## Phase 2 — Bot segmentation

- [⬜] **2.1** Migration 261 — `tennis_value_bets`: add `bot_id text`, `strategy_profile text`; widen unique index to include `bot_id`
- [⬜] **2.2** `scripts/tennis/bots_config.py` — 3 paper bots: `bot_tennis_pin_broad`, `bot_tennis_pin_selective`, `bot_tennis_coolbet_only`
- [⬜] **2.3** Backfill existing rows: `UPDATE tennis_value_bets SET bot_id = 'legacy_unsegmented' WHERE bot_id IS NULL`
- [⬜] **2.4** Refactor `value_scanner.py` to route observations through bots config (1 row per bot × fixture × bookmaker × selection)
- [⬜] **2.5** Refactor `place_coolbet_tennis.py` to route via `bot_tennis_coolbet_only`
- [⬜] **2.6** Sanity guards: reject rows with `book_odds > 10.0` OR `pin_fair_odds > 8.0`
- [⬜] **2.7** Smoke `TENNIS-BOTS-CONFIG`

## Phase 3 — Visibility & guardrails

- [⬜] **3.1** `/admin/tennis` — add per-bot ROI table (bot_id, settled, hit_rate, ROI, CLV)
- [⬜] **3.2** Health alerts: tennis daily volume tripwire (< N picks in 24h → alert)
- [⬜] **3.3** Health alerts: tennis settlement freshness (unsettled past kickoff+6h → alert)
- [⬜] **3.4** Doc updates: WORKFLOWS.md (new crons), DATA_SOURCES.md (settlement source), SIGNALS.md (if signals change)
- [⬜] **3.5** Mark `TENNIS-PAPER-BETS` ✅ Done in PRIORITY_QUEUE.md
