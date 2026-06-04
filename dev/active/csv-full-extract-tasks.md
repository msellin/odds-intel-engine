# CSV-FULL-EXTRACT — Task checklist

## Phase 1 — ingest extension

- [ ] Extend `extract_odds_rows()` in `scripts/ingest_football_data_csvs.py` to emit all (bookmaker × market × close/open) variants
- [ ] Add Asian-Handicap path with `handicap_line` set from AHCh (closing) / AHh (opening)
- [ ] Add `extract_match_stats_row()` writing to `match_stats` + `matches.referee`
- [ ] Update dedup key to include `is_opening` so opening-rows don't collide with closing-rows
- [ ] Dry-run on EPL 24-25 — verify expected counts (380 matches × ~30 odds rows + 1 stats row)
- [ ] Full re-run across all 479 main+extras CSVs
- [ ] Audit DB: confirm Betfair Exchange and AH closing row counts post-run

## Phase 2 — backtest

- [ ] Write `scripts/backtest_csv_full_extract.py`
- [ ] **2A — Anchor swap**: Brier + LogLoss + per-bin calibration error, Pinnacle-anchored vs Exchange-anchored shrinkage, on 2024-25 holdout
- [ ] **2B — AH sanity**: flat-stake ROI + edge-threshold sweep on Pinnacle closing AH (CSV-loaded rows)
- [ ] **2C — Drift feature**: add `pinnacle_drift_pct` + `exchange_drift_pct` to meta-model feature set, 5-fold CV AUC vs control
- [ ] Write results to `dev/active/csv-full-extract-backtest-results.md`

## Phase 3 — ship

- [ ] Smoke test: CSV-FULL-EXTRACT — assert `extract_odds_rows()` returns rows for Exchange + Max + Avg + AH-close + AH-open from a synthetic row
- [ ] Update DATA_SOURCES.md — bookmaker rows now in DB, AH historical depth
- [ ] Update SIGNALS.md if drift features added
- [ ] Update MODEL_WHITEPAPER.md only if Phase 2A justifies anchor swap (separate followup task if so)
- [ ] Mark CSV-FULL-EXTRACT ✅ Done in PRIORITY_QUEUE.md with backtest summary
- [ ] Commit code + docs together
