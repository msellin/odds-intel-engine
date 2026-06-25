# TENNIS-PAPER-BETS — Context

## Where things stand

- Plan accepted 2026-06-25, marked 🔄 In Progress in `PRIORITY_QUEUE.md`.
- Working from `dev/active/tennis-paper-bets-plan.md` (3 phases, ~7-9h total).
- Currently in **Phase 1, step 1**: probe OddsPapi v4 for a tennis results endpoint
  before committing to it as the settlement source.

## Key files

| File | Why it matters |
|---|---|
| `scripts/tennis/value_scanner.py` | Existing OddsPapi v4 client — copy the request-counter + auth pattern from here |
| `scripts/tennis/place_coolbet_tennis.py` | Existing Coolbet client — JWT-free public endpoints |
| `supabase/migrations/190_tennis_value_bets.sql` | Schema for the table we'll be settling into; `result`/`pnl`/`closing_odds`/`clv` columns already exist |
| `supabase/migrations/192_tennis_value_bets_unique.sql` | Current unique constraint is `(fixture_id, bookmaker, selection, scan_date)` — will need `bot_id` added in Phase 2 |
| `workers/scheduler.py` | Where new tennis jobs get wired (settlement, closing odds) |
| `workers/jobs/health_alerts.py` | Where tennis volume + settlement-staleness alerts go in Phase 3 |
| `workers/jobs/daily_pipeline_v2.py` | Soccer's `BOTS_CONFIG` — reference architecture only; we'll keep tennis bots in a separate `scripts/tennis/bots_config.py` |

## Decisions made

- **Pivoted to The Odds API (was OddsPapi)** on 2026-06-25 — OddsPapi free tier
  is 250 req/mo (busted by current cadence). Probe of The Odds API confirmed:
  100% Pinnacle coverage across 3 active tour tournaments; `/scores` endpoint
  returns the same `event_id` and a `completed: true` flag → clean settlement
  source; player names come as clean strings (no numeric-ID resolution). Cost
  budget: ~6 credits/day scanning + ~3/day settlement = ~270/mo of 500 free.
- **Dropped OddsPapi tennis scanner**: existing `scripts/tennis/value_scanner.py`
  + `job_tennis_scanner` will be replaced (NOT just patched). Keep OddsPapi
  client for the ad-hoc soccer CLV backfill (`ingest_oddspapi_pinnacle_closes.py`).
- **Retired WC odds sweep** in the same session (separate task ODDS-API-WC-DEACTIVATE)
  to free up The Odds API budget for tennis.
- **Coolbet stays soft-book-only**: it's the *target* of the scan, not the sharp
  reference. Cannot replace The Odds API / Pinnacle.
- **3 bots only at start**: `pin_broad` (3% edge), `pin_selective` (5% edge),
  `coolbet_only` (3% edge on Coolbet). All paper. Cap until ≥100 settled bets.
- **Separate `bots_config.py`** for tennis — don't pollute soccer's `BOTS_CONFIG`.
- **No public frontend surface** in this round — admin page only.
- **No real-money placement** — Coolbet daemon untouched.

## Next steps (immediate)

1. **Build the new tennis scanner** using `workers/api_clients/odds_api.py`:
   - List active tennis sport keys from `/sports`
   - For each: `/sports/{key}/odds?regions=eu&markets=h2h&bookmakers=pinnacle,<soft books>`
   - Compute Pinnacle de-vigged fair odds + edge per soft book per selection
   - Write to `tennis_value_bets` (same table, same schema, just different provider)
2. **Build settlement** using `/sports/{key}/scores?daysFrom=2`:
   - Filter `completed: true`, extract winner from `scores`
   - Match on `event_id` (stored as `fixture_id` in our table)
   - Update `result`, `pnl`
3. **Closing-odds capture**: same `/odds` endpoint, just run it once near kickoff
   to populate `closing_odds`. Quota-cheap: ~3 calls/day.

## Open questions

- Which soft books to scan via The Odds API? The probe showed Pinnacle is on all
  active tournaments — need to enumerate other available books per sport key.
- The current `tennis_value_bets.fixture_id` column holds OddsPapi numeric IDs;
  new rows will hold The Odds API string IDs (`0363040a8fcb6a76715262e9ef0e824a`).
  Schema is `text` so no migration needed, but the existing 0 rows are moot
  (table is empty per the read attempt today).

## What NOT to do

- Don't touch `workers/automation/coolbet_mac_daemon.py` — tennis is record-only.
- Don't add tennis to soccer's `BOTS_CONFIG`.
- Don't ship a Pro/Elite frontend surface — that's a separate task once we have
  ≥100 settled bets per bot.
- Don't use Sackmann tennis data anywhere in the production path (CC-BY-NC-SA
  license blocker — flagged in `dev/active/sport-expansion-plan.md`).
