# TENNIS-PAPER-BETS — Plan

**Goal**: accumulate a soccer-style paper-bet track record for tennis so we can
later analyze ROI by surface, tour, edge band, bookmaker, and bot persona —
and decide whether to graduate any strategy to real-money placement.

**Status**: proposed, not started. No `PRIORITY_QUEUE.md` entry until accepted.

---

## What we have today

| Piece | State |
|---|---|
| `tennis_value_bets` table (mig 190 + 192 unique) | ✅ — has `result`, `pnl`, `clv` columns, ready to be filled |
| `tennis_fixtures_today` table (mig 191) | ✅ — daily fixture snapshot |
| `job_tennis_scanner` — OddsPapi, 06:00 + 14:00 UTC | ✅ — writes every (fixture, bookmaker, selection) with edge > 0 |
| `job_coolbet_tennis_scanner` — Coolbet, every 30 min | ✅ — `--record` only, no placement |
| RLS hardening (mig 235) | ✅ — `public_read` on both tables |
| Admin page `/admin/tennis` | ✅ — read-only, edges + fixtures |
| **Settlement (filling `result` / `pnl` / `clv`)** | ❌ — never happens, all rows stay NULL forever |
| **Bot taxonomy** (`bot_id` column, `BOTS_CONFIG`-style segmentation) | ❌ — every value-bet is undifferentiated |
| **Closing-odds capture** (`closing_odds` near kickoff) | ❌ — column exists, never populated |
| Health alerts / volume tripwires for tennis | ❌ |
| Smoke tests for any of the above | ❌ |

We are observing value daily and writing rows, but nothing closes the loop.
There's no way today to answer "did the +5% edge picks make money?"

---

## Approach (parallel to soccer, simplified)

### Phase 1 — settlement + closing odds (biggest unlock)
**Why first**: rows already accumulate; without settlement they're worthless.

1. **OddsPapi results probe** (small spike — ~30 min).
   - Verify the OddsPapi v4 `/events/{id}` (or equivalent) returns `status=finished` + a winner field for tennis. If yes → use it (we already pay, fixture_id joins exactly).
   - Fallback: ESPN tennis scoreboard scrape (fuzzy name match — painful, defer).
2. **`scripts/tennis/settle_value_bets.py`** — for every `tennis_value_bets`
   row where `result IS NULL AND kickoff_time < now() - interval '4h'`:
   - look up the fixture result, set `result ∈ {win,loss,void}`
   - compute `pnl = stake * (book_odds - 1)` on win, `-stake` on loss, `0` on void
   - log a row to `pipeline_runs` (`job_name='tennis_settlement'`) so health alerts see it
3. **`scripts/tennis/capture_closing_odds.py`** — one Pinnacle scan at
   kickoff − 5 min (or hourly between 06–22 UTC if per-fixture timing is too
   fiddly to start). Updates `closing_odds` for any row with that fixture_id +
   selection, then settlement computes `clv` from it.
4. **Scheduler hooks** in `workers/scheduler.py`:
   - `job_tennis_closing_odds` — hourly 06–22 UTC (cheap MVP; per-fixture later)
   - `job_tennis_settlement` — 02:00 + 14:00 UTC (catches morning + evening sessions)

### Phase 2 — bot segmentation
**Why**: soccer's bot-level granularity is what makes ROI analysis useful —
we need the same axis on tennis or all 200 daily picks look like one blob.

1. **Migration 261** `261_tennis_value_bets_bot_id.sql`:
   - `ALTER TABLE tennis_value_bets ADD COLUMN bot_id text` (text, not FK — match soccer's convention)
   - `ALTER TABLE tennis_value_bets ADD COLUMN strategy_profile text` (for multi-strategy bots)
   - drop + recreate the unique index to `(fixture_id, bookmaker, selection, scan_date, bot_id)` so one fixture can be claimed by multiple bots
2. **`scripts/tennis/bots_config.py`** — keep it parallel to soccer's
   `BOTS_CONFIG` but tennis-local (don't pollute the soccer dict). Start with
   3 paper bots:
   - `bot_tennis_pin_broad` — edge ≥ 3% any soft book, 1u flat
   - `bot_tennis_pin_selective` — edge ≥ 5% any soft book, 1u flat
   - `bot_tennis_coolbet_only` — edge ≥ 3% on Coolbet only, 1u flat
     (mirrors where we'd actually place; the bookmaker is the differentiator)
3. **Scanner refactor**: `value_scanner.py` + `place_coolbet_tennis.py` route
   every qualifying observation through the bots-config filter and write one
   row **per (bot, fixture, bookmaker, selection)**. A single fixture/edge that
   qualifies for both `pin_broad` and `pin_selective` produces two rows — same
   as soccer.
4. **Backfill**: existing rows get `bot_id = 'legacy_unsegmented'` so they
   don't pollute future analytics.

### Phase 3 — visibility & guardrails
1. **Admin page** `/admin/tennis` — add a "Bot performance" table
   (bot_id, settled count, hit_rate, ROI, CLV). One SQL query, no new infra.
2. **Health alerts** (`workers/jobs/health_alerts.py`):
   - tennis daily volume tripwire (alert if < N picks in 24h — pipeline broke)
   - tennis settlement freshness (alert if > X bets unsettled past kickoff+6h)
3. **Smoke tests** in `scripts/smoke_test.py`:
   - `TENNIS-SETTLEMENT` — pin script structure + the SQL query shape
   - `TENNIS-CLOSING-ODDS` — pin scheduler entry + the script
   - `TENNIS-BOTS-CONFIG` — pin the 3 bot keys, edge thresholds, and that scanner reads from this config

### Phase 4 — frontend surface (deferred)
Out of scope for this plan. Once we have ≥ 100 settled bets per bot and a
non-trivial ROI signal, decide whether to surface tennis picks to Pro/Elite
users (separate `TENNIS-FRONTEND-SURFACE` task).

---

## What this plan does NOT do

- **No real-money placement.** Coolbet daemon stays untouched. Per
  `feedback_coolbet_execute_safety` memory: `--execute` requires explicit
  authorization. Everything here is record-only.
- **No new prediction model.** We're using Pinnacle sharp-vs-soft as the
  signal, same as the existing scanner. The backtest scripts (`_markov`,
  `_elo`, `_advanced` — written Jun 7–8) can plug in later as additional bot
  personas, but not in this round.
- **No license resolution.** Sackmann data stays out of the production path
  (per `sport-expansion-plan.md` license note). Pinnacle + OddsPapi are
  commercial-safe.
- **No public frontend surface.** Admin-only until the data justifies it.

---

## Risks / open questions

| Risk | Mitigation |
|---|---|
| OddsPapi v4 may not expose tennis results | Phase 1 step 1 is a probe; if it fails, defer settlement to ESPN scrape or tennis-data.co.uk weekly join (lossy, but workable) |
| Hourly closing-odds capture misses the true close on fast-moving matches | Accept as MVP; CLV will be approximate but directionally useful. Per-fixture scheduling is a later improvement |
| Coolbet odds-quality issues (per `feedback_odds_quality_recurring`) — could feed garbage picks to `bot_tennis_coolbet_only` | The Pinnacle de-vig step already runs in `value_scanner.py`; tennis is 2-way (no quarter-line OU complication), so the soccer OU-quality trap doesn't apply directly. Still add a sanity guard: reject any row where `book_odds > 10.0` or `pin_fair_odds > 8.0` |
| Bot count creep (we have 16 soccer bots) | Cap at 3 for first 60 days; only add bots when one of the existing 3 has ≥ 100 settled bets with a clear pattern |

---

## Effort estimate

| Phase | Time |
|---|---|
| Phase 1 (settlement + closing odds) | 3–4 h |
| Phase 2 (bot segmentation) | 2–3 h |
| Phase 3 (visibility + smoke) | 1–2 h |
| **Total** | **~7–9 h** (one focused session or two shorter ones) |

---

## Acceptance criteria

- A row in `tennis_value_bets` from a finished match has non-NULL `result`,
  `pnl`, and (where available) `clv` within 24 h of kickoff.
- Every new row written by the scanners has a non-NULL `bot_id`.
- `/admin/tennis` shows a per-bot ROI table.
- `scripts/smoke_test.py -f TENNIS-` passes 3 new tests.
- After 30 days, we can answer: "what is the ROI of each tennis bot, broken
  down by surface / tour / edge band?"

---

## If accepted

1. Add to `PRIORITY_QUEUE.md` as `TENNIS-PAPER-BETS` (status `🔄 In Progress`).
2. Create `dev/active/tennis-paper-bets-context.md` and
   `dev/active/tennis-paper-bets-tasks.md` per global agent protocol.
3. Start with the Phase 1 OddsPapi results probe — the rest of the plan
   depends on what it returns.
