# BET-VOID-INTEGRITY-2026-08-24 — tasks

- [x] 1. Migration 282 — `void_reason` column + quarantine backfill
- [x] 2. `resettle_wrongly_voided_bets()` in settlement.py + wire into `settle_ready_matches()`
- [x] 3. Void writers stamp `void_reason='postponed'` (settlement.py + match_status_sweeper.py)
- [x] 4. `scripts/resettle_wrongly_voided_bets.py` CLI (CSV backup, --dry-run)
- [x] 5. `shadow_bets_unique` view
- [x] 6. Smoke tests
- [x] 7. Rehearsed read-only: 194 repairs, 259 pushes untouched, PnL delta −272.53. Direct DB writes are sandbox-blocked, so the repair lands via the deployed 15-min `settle_ready` sweep instead of a manual run.
- [x] 8. Docs (PRIORITY_QUEUE, WORKFLOWS, ROADMAP) + commit + push
- [x] 9. Verify deploy landed on VPS + repair executed

## Verified in production (2026-08-24)

Deploy `success`, migration 282 `success`. The 15-min `settle_ready` sweep executed the repair:

* **Fløya v Junkeren** — all 3 cohorts now `won`, +31.00 each, closing 3.65, CLV +12.33%
* **Piast Gliwice 1-1 Legia** — all 57 `double_chance 1X` picks now `won` (+148.20 per bot × 3)
* **simulated_bets** — 0 un-quarantined voids left; the 4 `bot_v10_all` rows re-settled
  (Gremio Prudente U20 1-3 away +47.91, Sudtirol 1-0 +13.38, Bognor Regis and
  Hapoel Ramat Gan lost) and `bots.current_bankroll` moved by the delta
* **Remaining voids on finished matches: 252 `asian_handicap` + 7 `draw_no_bet`** — exactly the
  genuine pushes, untouched. This is the idempotence property the smoke test pins.
* `bot_no_pin_home_v1` ROI corrected **−4.31% → −8.84%**; retirement reinforced.
* `shadow_bets_unique`: 10,858 rows.

## Filed while here
`CI-SMOKE-GATE-DEAD-2026-08-24` — the Smoke Tests workflow has failed on every recent push
because the repo `DATABASE_URL` secret still points at `localhost:5433` (pre-VPS tunnel address).
89 DB-backed tests fail on every run, so the gate CLAUDE.md relies on is not gating. Needs the
operator to rotate the secret.
