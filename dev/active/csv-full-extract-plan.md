# CSV-FULL-EXTRACT — Plan

> Status: 🔄 In Progress 2026-06-04
> Task ID: CSV-FULL-EXTRACT

## Problem

We have 479 football-data.co.uk CSVs on disk (`data/raw/football_data_co_uk/`). The main-league CSVs carry 120 columns/row — closing odds across 9 bookmakers for 1X2 / OU 2.5 / Asian Handicap, plus opening odds for the same set, plus secondary match stats (shots, corners, cards, fouls, referee).

`scripts/ingest_football_data_csvs.py:extract_odds_rows()` currently writes only **4 of those fields** to `odds_snapshots`:

| Stored today | Source columns |
|---|---|
| Pinnacle 1X2 closing | PSCH / PSCD / PSCA |
| Bet365 1X2 closing | B365CH / B365CD / B365CA |
| Pinnacle OU 2.5 closing | PC>2.5 / PC<2.5 |
| Bet365 OU 2.5 closing | B365C>2.5 / B365C<2.5 |

DB audit (2026-06-04):
- `odds_snapshots WHERE bookmaker='Betfair Exchange'` → **0 rows**
- `odds_snapshots WHERE bookmaker IN ('Max','Avg','Betfred','BetWin')` → **0 rows**
- Pinnacle AH exists (~184K rows) — but from AF live feed post-April 2026, not historical CSV closing
- `odds_snapshots WHERE is_opening=true` → migration 096 added the flag but nothing from CSV ingest populates it

## Why this matters

Three concrete unlocks:

1. **Betfair Exchange closing as no-vig anchor.** Exchange closing has ~1% effective vig (back-lay midpoint), tighter than Pinnacle's 2–3%. Used as the shrinkage anchor in MODEL_WHITEPAPER's Stage 1 calibration, it should reduce conditional miscalibration on the 0.30–0.40 home-win bin (the issue Round 3 evaluators flagged). Quantifiable via Brier / LogLoss vs current Pinnacle-anchored calibration.

2. **Asian Handicap backtest universe.** Today we have zero AH historicals — every AH row in DB is from the live pipeline post-April 2026. CSV closing AH with `handicap_line` (PCAHH/A + AHCh) opens 13 years × top leagues = ~50K row backtest universe. Foundation for an AH bot strategy that doesn't exist today.

3. **Opening→closing drift feature.** `PSH/D/A` (opening) and `PSCH/D/A` (closing) on the same row give pre-kickoff Pinnacle drift. Drift is in the literature as one of the strongest pre-match signals; meta-model has `odds_drift` (live-time) but not pre-kickoff total drift. Add as feature, measure lift on holdout.

Secondary unlocks (lower priority): Max/Avg closing as consensus, other book closings for `bookmaker_disagreement` signal breadth, match stats labels for corners/cards prediction models.

## Approach

### Phase 1 — extend ingest (half day)

Rewrite `extract_odds_rows()` to walk all 120 columns. New `odds_snapshots` rows:

| Bookmaker | Market | Selection | is_closing | is_opening | handicap_line | Source col |
|---|---|---|---|---|---|---|
| Pinnacle | 1x2 | home/draw/away | true | false | NULL | PSCH/D/A |
| Pinnacle | 1x2 | home/draw/away | false | true | NULL | PSH/D/A |
| Pinnacle | over_under_25 | over/under | true | false | NULL | PC>2.5 / PC<2.5 |
| Pinnacle | over_under_25 | over/under | false | true | NULL | P>2.5 / P<2.5 |
| Pinnacle | asian_handicap | home/away | true | false | AHCh | PCAHH/A |
| Pinnacle | asian_handicap | home/away | false | true | AHh | PAHH/A |
| Bet365 | (same three markets, close+open) | | | | | B365CH... / B365H... |
| Betfair Exchange | (same three markets, close+open) | | | | | BFECH... / BFEH... |
| Max | (same three, close only) | | true | false | AHCh for AH | MaxCH... |
| Avg | (same three, close only) | | true | false | AHCh for AH | AvgCH... |
| BetWin / Betfred / WH / 1xBet | 1X2 close+open | | | | | BWCH/BWH etc |

Match-stats path → separate function writing to `match_stats` table (already exists): `home_shots`, `away_shots`, `home_shots_on_target`, `away_shots_on_target`, `home_corners`, `away_corners`, `home_yellow_cards`, `away_yellow_cards`, `home_red_cards`, `away_red_cards`, `home_fouls`, `away_fouls`, `referee` (on matches table), `ht_home_goals`, `ht_away_goals`.

### Phase 2 — backtest (half day)

Three measurable comparisons:

**A. Calibration anchor swap.** On 2024-25 holdout (~10K matches), compute Brier + LogLoss + bin-level calibration error for:
- Existing Pinnacle-anchored shrinkage (control)
- Same pipeline with Betfair Exchange closing as anchor (treatment)

Hypothesis: Exchange anchor reduces 0.30–0.40 bin error from -0.013 to ≤ -0.005.

**B. AH flat-stake sanity.** 50K CSV-loaded AH rows. Compute:
- Flat-stake ROI on home/away picks at Pinnacle closing (baseline = vig-bounded -2.4%)
- Threshold sweep: %-edge vs ROI to find break-even point
- If any threshold sustains >0% ROI on n ≥ 1K, that's a viable bot strategy

**C. Drift feature lift.** Add `pinnacle_drift_pct = (PSCH - PSH) / PSH` and `exchange_drift_pct` to meta-model features. Compare 5-fold CV AUC on 1X2 outcome prediction.

### Phase 3 — commit + docs (1 hour)

- Smoke tests for the extended ingest
- Update PRIORITY_QUEUE.md (mark Done with backtest summary in the entry)
- Update DATA_SOURCES.md (new bookmaker rows + AH closing depth)
- Update SIGNALS.md (drift features)
- Update MODEL_WHITEPAPER.md only if anchor swaps in Phase 2

## Risks

- **Existing CSV ingest is idempotent on `(match_id, bookmaker, market, selection, is_closing)`** — new bookmakers won't collide with old rows, but new opening rows for already-stored books will. Need to add `is_opening` to dedup key.
- **Match-stats overwrite risk** — AF already populates match_stats for 2022+ matches. Don't overwrite when AF row exists; only fill gaps.
- **Phase 2A risk: shrinkage anchor swap might hurt calibration on some bins while helping others.** Need to measure per-bin not just aggregate before recommending a model change. If results are mixed, leave the swap as a future MODEL_WHITEPAPER decision, not part of this task's commit.

## Out of scope

- Quarter AH lines (CSVs only have main line, this stays on The Odds API roadmap)
- Player props (not in CSVs)
- In-play / 5-min line movement (CSVs are kickoff snapshots only)
- Replacing the current model — this task ingests data + backtests; any anchor swap is a separate task on positive evidence
