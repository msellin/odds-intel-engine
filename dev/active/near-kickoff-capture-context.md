# NEAR-KICKOFF-CAPTURE + DIRECT-BOOK-CLV — context

## Key files
- `workers/jobs/settlement.py` — `_VENUE_SNAPSHOT_BOOK`, `DIRECT_CLOSE_MAX_MIN`,
  `get_book_close`, `real_bet_closing`, `_settle_real_bets_for_matches`.
- `supabase/migrations/332_real_bets_direct_book_clv.sql`, `333_book_event_map.sql`.
- `workers/api_clients/supabase_client.py` — `record_book_events`.
- Sweeps writing the map: `coolbet_explorer.run_board_sweep` (Mac),
  `epicbet_explorer._run_bulk_inner` (VPS), `unibet_odds_feed._async_run_bulk` (Mac).
- `workers/jobs/near_kickoff_capture.py` + `local/launchd/com.oddsintel.near-kickoff-capture.plist`.
- `scripts/backfill_real_bets_direct_clv.py`, `scripts/direct_book_clv_report.py`.

## Facts measured 2026-09-11
- Coolbet evening board sweep: 60-75 min end to end (log dev/active/coolbet-odds-snapshot.log).
- 3-day closing coverage: Coolbet 23 matches, Epicbet 82, Unibet-Site 139, Pinnacle 539.
- 130 settled real bets: vs Coolbet's own later price median 0.0%; vs Pinnacle
  close median +6.4%, 77% beat. Stored (old) clv was +4..+17% — arbitrary book.

## Next steps
1. Push → migrations 332/333 apply → run backfill → run report.
2. Owner approval to load the launchd plist on the Mac.
3. After ~3 days of capture: re-run report; tighten DIRECT_CLOSE_MAX_MIN to 20.
