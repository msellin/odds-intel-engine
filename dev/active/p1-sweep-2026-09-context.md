# P1 Sweep — context

## State: tasks 1-3 DONE. Queue sweep in progress. Remaining P1s: 4, 5, 6.

### Queue sweep, 2026-09-02 (42 open items)
Verified premises against code/DB rather than trusting ticket text, after six
tickets in one day turned out to describe work already shipped.

Closed / changed so far:
- TENNIS-COVERAGE-EXPAND      CLOSED STALE (tennis retired, 0 modules left)
- COOLBET-SQUAD-GUARD         DONE today (d3ab5f8)
- PERF-CONFIG-THRESHOLDS-REVIEW  DONE 2026-08-21 (proposal doc shipped)
- BET365-EXECUTION-AUDIT      DONE 2026-08-21 (b6cbee8)
- BOT-GRADUATION-GATES-TIERED CHANGED — t-stat gate replaced it 2026-08-26
- PICKS-DEDUPE / PICKS-COOLBET-COLUMN  PARTLY DONE
- AF-QUOTA-REALLOCATION       P1 -> P2 (quota now 8-35%%/day, was 99.9%%)

Key measurements taken (do not re-derive):
- AF quota: ceiling hit Aug 1/2/8/9 (149,800). Since Aug 18 peak 74,861;
  last 7d 8-35%%. Today 12,160 (8.1%%).
- In-play bots: ZERO picks since 2026-08-21 (12 days), yet LivePoller still
  starts by default. Establish WHY before cutting its data supply.
- xG coverage: 6.2%% of 14,414 MFV rows since 2026-08-01 (UNDERSTAT stands).
- Users: 52 profiles (51 free, 1 elite). Was ~30 in memory.
- Active bots: beta 13 / experimental 6 / calibrated 4 / active 1.
- PostgREST db-max-rows = 10000 (bites getAllBets, filed ALL-BETS-CEILING-DEAD).

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
