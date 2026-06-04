# CSV-FULL-EXTRACT — Context

## Key files

| File | Role |
|---|---|
| `scripts/ingest_football_data_csvs.py` | Main-league ingest. `extract_odds_rows()` at L316–367 is the function to extend. |
| `scripts/ingest_football_data_extras.py` | `/new/` 3-letter extras ingest (ARG, BRA, MEX, etc.) |
| `scripts/ingest_football_data_extras_odds.py` | Odds-only path for extras CSVs (joins to existing DB matches) |
| `scripts/analyse_football_data_co_uk.py` | In-memory CSV analytics (not DB). Source of the 53K AH figure in fdco_analysis_findings.md |
| `data/raw/football_data_co_uk/main/<CODE>/<season>.csv` | 21 leagues × multi-season, 120 columns each |
| `data/raw/football_data_co_uk/extra/<COUNTRY>.csv` | 16 leagues, 25 columns each — only PSCH/D/A + MaxCH/D/A + AvgCH/D/A + BFECH/D/A + B365CH/D/A. No AH, no OU. |
| `supabase/migrations/066_ah_signals.sql` | Adds `handicap_line` numeric to odds_snapshots. AH rows need this set. |
| `supabase/migrations/096_is_opening_flag.sql` | Adds `is_opening` flag — populated for opening-line rows. |
| `workers/api_clients/db.py` | `execute_query` + `execute_write` + `_pool` connection mgmt |

## Current DB state (audited 2026-06-04)

| Bookmaker | 1X2 | OU 2.5 | AH | Notes |
|---|---|---|---|---|
| Pinnacle | 296K | 170K | 184K | 1X2 + OU from CSV ingest; AH only from AF live (post-Apr 2026) |
| Bet365 | 297K | 194K | 229K | Same — AH lacks pre-2026 depth |
| 1xBet | 276K | 177K | 250K | All from AF live feed |
| William Hill | 250K | 0 | 0 | 1X2 only via AF; CSVs have closing not loaded |
| Betfair Exchange | 0 | 0 | 0 | **Entirely unfilled** — CSVs have ~50K rows for top leagues |
| Max / Avg | 0 | 0 | 0 | **Entirely unfilled** — synthetic consensus from 9 books |
| Betfred / BetWin | 0 | 0 | 0 | CSVs have closing not loaded |

`odds_snapshots WHERE is_opening=true AND bookmaker IN ('Pinnacle','Bet365','Betfair Exchange')` → 0 rows. Opening odds entirely missing from historical period.

## Schema constraints

`odds_snapshots`:
- `(bookmaker, market, selection)` is free-text — no enum, no constraint. New bookmaker names like `'Max'`, `'Avg'`, `'Betfair Exchange'` won't violate anything.
- `handicap_line numeric` — nullable, set for AH rows
- `is_closing boolean` + `is_opening boolean` — both populated on new rows
- App-level dedup key in current ingest: `(match_id, bookmaker, market, selection, is_closing)`. **Must add `is_opening` to dedup** for the new opening rows — without it, opening-Pinnacle rows would dedupe against closing-Pinnacle on the same selection.

`match_stats`:
- Columns checked: `home_shots`, `away_shots`, `home_shots_on_target`, `away_shots_on_target`, `home_corners`, `away_corners`, `home_yellow_cards`, `away_yellow_cards`, `home_red_cards`, `away_red_cards`, `home_fouls`, `away_fouls`. All exist from AF integration. CSV ingest needs an UPSERT path that only fills NULLs.

`matches.referee`:
- Text column from migration 007. AF only fills it post-2022. CSV ingest should fill where NULL.

## Model context

- **Current shrinkage anchor**: Pinnacle implied prob (migration CAL-PIN-SHRINK, 2026-05-06). Soft-book avg is fallback.
- **Meta-model features**: `model_prob - pinnacle_implied`, `odds_at_pick`, `time_to_kickoff`. B-ML3 notes track `odds_drift` as collinear with `overnight_line_move`.
- **Bots that would benefit**:
  - `bot_aggressive`, `bot_v10_all` — 1X2 — better anchor → tighter shrinkage → fewer veto misses
  - No AH bot exists today — Phase 2B sanity check determines whether one's worth building
  - `bot_btts_all`, `bot_ou25_global`, `bot_ou15_defensive`, `bot_ou35_attacking` — OU markets, would benefit from broader consensus (Max/Avg)

## Decisions taken

- **No schema migration needed.** odds_snapshots accepts arbitrary bookmaker strings, `handicap_line` and `is_opening` already exist. Match-stats path UPSERTs into existing columns.
- **Synthetic bookmaker names**: `Max` and `Avg` written as bookmaker values (not stored in a separate table). These are CSV-derived aggregates, not real books — treat as virtual sources for query convenience.
- **Phase 2 anchor swap is measurement-only.** Even if Exchange-anchored calibration beats Pinnacle-anchored in Phase 2A, the actual model change is a separate task — this task only proves the data is useful.

## Next steps (live)

1. Extend `extract_odds_rows()` with all 11 (bookmaker, market, close/open) variants.
2. Add `extract_match_stats_row()` for the 12 stat fields + referee + HT goals.
3. Dry-run one league-season (E0 / 2425) — verify counts.
4. Full re-run across all 479 CSVs.
5. Phase 2 backtest script: `scripts/backtest_csv_full_extract.py` — three measurements.
6. Smoke + commit + doc update.
