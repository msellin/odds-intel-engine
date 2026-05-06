# In-Play Paper Trading Bot — Task Checklist
> Task: P3.4 | Updated: 2026-05-06

## Phase 1A — 8 Week 1 Strategies (all at once)

### Pre-build
- [x] Check `simulated_bets` schema — uses `bot_id` FK to `bots` table, no strategy_id needed
- [x] Check `matches` schema — `af_prediction` JSONB has prematch xG via `predictions.goals.home/away`
- [x] Confirm `live_match_snapshots.captured_at` populated — yes, set to `now()` in `store_live_snapshots_batch()`
- [x] Confirm `live_match_snapshots.live_ou_25_over` etc populated — yes, since May 5 odds fix
- [x] No migration needed — `ensure_bots()` creates bot rows at runtime
- [x] League filter: query leagues with >= 20 rows where `xg_home IS NOT NULL`

### Bot implementation
- [x] Create `workers/jobs/inplay_bot.py`
  - [x] `_get_live_candidates()` — latest snapshot per live match (90s window)
  - [x] `_odds_age_seconds()` — staleness check (< 60s)
  - [x] `_score_recheck()` — re-read latest snapshot, verify score unchanged
  - [x] `_bayesian_posterior()` — `(prematch_xg + live_xg) / (1 + minute/90)`
  - [x] `_check_strategy_a()` — Strategy A/A2 conditions
  - [x] `_check_strategy_b()` — Strategy B (BTTS Momentum)
  - [x] `_check_strategy_c()` — Strategy C/C_home (Favourite Comeback)
  - [x] `_check_strategy_d()` — Strategy D (Late Goals Compression)
  - [x] `_check_strategy_e()` — Strategy E (Dead Game Unders)
  - [x] `_check_strategy_f()` — Strategy F (Odds Momentum Reversal — 10min lookback)
  - [x] Heartbeat log every ~5 min for Railway visibility
- [x] Integrate in `workers/live_poller.py` — called after snapshots stored

### Bot registration
- [x] 8 bots registered via `ensure_bots()` at first run:
  inplay_a, inplay_a2, inplay_b, inplay_c, inplay_c_home, inplay_d, inplay_e, inplay_f

### Error monitoring
- [x] Sentry `capture_exception()` on unhandled errors
- [x] Rich console logging to Railway logs
- [x] Health endpoint `/health` shows recent_errors
- [x] 5-minute heartbeat log with cycle count, candidates, bets placed

### Validation (after deploy)
- [ ] Watch Railway logs for first heartbeat (~5 min after deploy)
- [ ] Verify "InplayBot: 8 bots registered" appears on first live match
- [ ] Watch for first paper bet log (green bold message)
- [ ] Check Supabase: `SELECT * FROM simulated_bets WHERE bot_id IN (SELECT id FROM bots WHERE name LIKE 'inplay_%') ORDER BY created_at DESC`
- [ ] Verify settlement pipeline picks up in-play bets at FT
- [ ] Check superadmin bot page shows in-play bots

## Phase 1B — Week 2 Strategies (after Week 1 validated)

- [ ] Add Strategy G (Shot Quality Under)
- [ ] Add Strategy H (Corner Pressure Over)

## Phase 1B — Week 3 Strategies

- [ ] Add Strategy I (Possession Trap Under)
- [ ] Add Strategy J (Dominant Underdog Win)
- [ ] Add Strategy K (2H Kickoff Burst)

## Phase 1 Validation (gates to Phase 2)

- [ ] 200+ paper bets logged across strategies
- [ ] CLV > 0 on >= 55% of settled bets
- [ ] ROI > 0% on best-performing strategy
- [ ] Build CLV calculation for in-play: entry odds vs pre-KO closing odds (Pinnacle)
