# In-Play Paper Trading Bot — Task Checklist
> Task: P3.4 | Updated: 2026-05-06

## Phase 1A — Strategy A Bot

### Pre-build
- [ ] Check `simulated_bets` schema for `strategy_id` column
- [ ] Check `matches` schema for `prematch_xg_home`, `prematch_xg_away`, `prematch_o25_prob`
- [ ] Confirm `live_match_snapshots.captured_at` is populated by LivePoller
- [ ] Confirm `live_match_snapshots.ou_25_over` and `live_*_odds` populated since May 5 fix
- [ ] Add migration 053 if `strategy_id` column missing from `simulated_bets`
- [ ] Build league filter query: leagues with ≥ 20 rows in `live_match_snapshots` where `xg_home IS NOT NULL`

### Bot implementation
- [ ] Create `workers/jobs/inplay_bot.py`
  - [ ] `get_live_candidates()` — query latest snapshot per live match (last 30s)
  - [ ] `check_staleness(snapshot)` — `captured_at` within 60s
  - [ ] `check_score_recheck(match_id, expected_score)` — re-read latest snapshot
  - [ ] `compute_bayesian_posterior(prematch_xg, live_xg, minute)` — formula
  - [ ] `check_strategy_a(snapshot, prematch_data)` — all entry conditions
  - [ ] `log_paper_bet(match_id, strategy_id, market, selection, odds, model_prob, notes)` — insert to simulated_bets
  - [ ] Dry-run mode: `--dry-run` flag prints triggers without inserting
- [ ] Register in `workers/scheduler.py` as 30s interval job

### Bot registration
- [ ] Insert `inplay_a` row into `bots` table
- [ ] Insert `inplay_a2` row into `bots` table (Strategy A2 — 1-0 state)

### Testing
- [ ] Run dry-run for one live match day — check trigger count vs expected 8-12% rate
- [ ] Verify `simulated_bets` rows inserted with correct fields
- [ ] Verify settlement pipeline picks them up at FT
- [ ] Check `bots` page shows in-play bots correctly

## Phase 1B — All Week 1 Strategies

- [ ] Add Strategy A2 (score 1-0, same logic as A)
- [ ] Add Strategy B (BTTS Momentum)
- [ ] Add Strategy C (Favourite Comeback — DNB)
- [ ] Add Strategy C_home (Home Favourite Comeback)
- [ ] Add Strategy D (Late Goals Compression)
- [ ] Add Strategy E (Dead Game Unders)
- [ ] Add Strategy F (Odds Momentum Reversal — needs 20-snapshot lookback query)
- [ ] Register all in `bots` table

## Phase 1B — Week 2 Strategies

- [ ] Add Strategy G (Shot Quality Under)
- [ ] Add Strategy H (Corner Pressure Over)

## Phase 1B — Week 3 Strategies

- [ ] Add Strategy I (Possession Trap Under)
- [ ] Add Strategy J (Dominant Underdog Win)
- [ ] Add Strategy K (2H Kickoff Burst)

## Phase 1 Validation (gates to Phase 2)

- [ ] 200+ Strategy A paper bets logged
- [ ] CLV > 0 on ≥ 55% of settled A bets
- [ ] ROI > 0% on Strategy A
- [ ] Build CLV calculation for in-play: entry odds vs pre-KO closing odds (Pinnacle)

## Phase 2 — ML Model (June 2026)

- [ ] Feature pipeline: snapshots → training rows at minute 15/30/45/60/75 checkpoints
- [ ] Train LightGBM `objective='poisson'` on lambda_remaining
- [ ] Backtest all strategies on historical snapshots
- [ ] Gate: model CLV > 0% on 300+ paper bets, outperforms rules by ≥ 2% ROI
