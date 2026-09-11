# NEAR-KICKOFF-CAPTURE + DIRECT-BOOK-CLV — plan (2026-09-11)

Owner ask: "do both — the CLV fix and NEAR-KICKOFF-CAPTURE; since we now sweep
Unibet, Coolbet and Epicbet we should make the most of it for CLV."

## Part A — DIRECT-BOOK-CLV (real bets)
- `real_bets.clv` was vs an arbitrary AF book (get_closing_odds, no book filter).
- New: CLV at the bet's OWN book, close = last pre-KO non-live price ≤60 min
  before kickoff (`DIRECT_CLOSE_MAX_MIN`), else NULL. Plus de-vigged Pinnacle
  CLV (`clv_pinnacle`), closing_bookmaker, closing_minutes_before_ko.
- Migration 332. Helper `settlement.real_bet_closing()` shared by settle loop +
  `scripts/backfill_real_bets_direct_clv.py`.
- Report: `scripts/direct_book_clv_report.py` — close at all 3 direct books,
  own-book CLV by lead time, drift-out vs Pinnacle.
- Web: `/admin/real-bets` table gets "Pin CLV" column + honest tooltip.
- NOT changed: shadow/simulated `clv` (other sessions are mid-measurement on
  shadow CLV; changing its definition would pool two definitions).

## Part B — NEAR-KICKOFF-CAPTURE (Option 1 of the queue spec)
- Migration 333 `book_event_map(match_id, bookmaker, book_event_id, book_start,
  match_score, matched_at)`, upserted by all 3 sweeps via `record_book_events`.
- `workers/jobs/near_kickoff_capture.py`: every 5 min, fixtures in (now, now+15]
  per book, skip if priced in last 6 min, fetch by event id, write with
  minutes_to_kickoff → is_closing.
- Runs on the operator's Mac (launchd `com.oddsintel.near-kickoff-capture`):
  Coolbet via COOLBET_NO_FS (never contend with sweep/placer FS), Unibet via
  CDP tab, Epicbet direct (own FS session id if it ever falls back).
- `closing_snap` (AF/Pinnacle) → 24/7.

## Risks
- Coolbet NO_FS reads depend on fresh watchdog cookies → capture fails soft.
- Mac asleep → no direct-book capture (same as the sweeps themselves).
- Out of scope, noted for owner: firing pick generation on near-KO prices
  (the "bet later" question) — touches PICK-GENERATOR, other session's area.
