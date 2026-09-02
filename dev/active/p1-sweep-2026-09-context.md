# P1 Sweep — context

## State: task 1 DONE (2026-09-02). Next: task 2 (AF-STALE steps 2-4).

### Task 1 outcome
- migration 291 -> `odds_at_pick_live` on simulated_bets + shadow_bets (additive)
- backfilled: simulated_bets 2,357/4,305 settled (54.8%), shadow_bets
  115,855/139,612 (83.0%)
- six audits now share `scripts/_our_stats.py`; local copies deleted
- **published cohort restated +15.99% -> +10.65% (n=529, 76.3% coverage)**
- landing + all six comparison JSONs republished; COMP_FALLBACK synced
- smoke `STALE-ODDS-HISTORY-RESTATE` (+ COMPETITOR-PICKS-CSV updated to assert
  the inplay/maturity cohort against the shared module)
- gotcha: assert against the SQL, not the file — the first draft of the smoke
  test passed while the SELECT was gutted, because the docstring still held
  the column name

## Numbers established 2026-09-02 (do not re-derive)

Landing cohort (calibrated+beta+active, 1x2+o/u+over_under_25, non-inplay,
2026-05-04 -> 2026-09-03), settled only:
- as published, all 692 rows, stored odds:            **+13.96%**
- repriceable subset (528, 76%), stored odds:         +15.51%
- repriceable subset at best price LIVE at pick time: **+10.27%**
- 253/528 (47.9%) store odds above anything live at pick

Broader check (488 settled, 90d, all bots): +9.47% recorded vs +4.43% live.

Scheduled-fixture odds at fix time: 34.2% of 1X2 selections had a historical
max above latest-per-book, mean +6.3%, worst 2.27x.

Sweep bots (157 picks): >20% edge share 72.6% -> 72.0% when repriced. The
calibration fault is REAL, not an odds artifact.

## Key facts

- ACCESSIBLE_BOOKMAKERS = Unibet, Betano, Marathonbet, 10Bet, 888Sport,
  Pinnacle, Coolbet (`daily_pipeline_v2.py:1061`)
- "live at pick" = `DISTINCT ON (bookmaker) ... WHERE timestamp <= pick_time
  ORDER BY ... timestamp DESC`, then MAX across books
- `simulated_bets` uses `odds_at_pick` / `pick_time` (NOT odds/placed_at)
- shadow ledger is `shadow_bets`; per ANALYSIS_GOTCHAS #18 a bot uses ONE of
  the two tables, never both. Use `shadow_bets_deduped` for shadow aggregates.
- PostgREST db-max-rows = 10000. Paginate anything larger.
- `scripts/_competitor_reprice.py` already holds closing-price helpers built
  for FOREBET-REPRICE — reuse rather than re-implement.

## Gotchas written this session
#27 matched-book odds comparison, #28 competitor ROI mismatches,
#29 MAX-ever is reachability not execution, #30 best-odds means best-ever.

## Next steps
Migration for `odds_at_pick_live`, then backfill.
