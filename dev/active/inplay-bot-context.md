# In-Play Paper Trading Bot — Context
> Task: P3.4 | Updated: 2026-05-06

## Key Files

| File | Purpose |
|------|---------|
| `workers/live_poller.py` | LivePoller — 30s/60s/5min tiered polling, already running |
| `workers/scheduler.py` | APScheduler — add in-play bot job here |
| `workers/jobs/live_tracker.py` | fetch_live_bulk(), fetch_match_stats_for() — reuse these |
| `workers/api_clients/db.py` | execute_query(), store_simulated_bet() — use for all DB writes |
| `supabase/migrations/` | NNN_description.sql — next migration is 053 |
| `PRIORITY_QUEUE.md` § INPLAY Plan | Full strategy conditions, staking, phases |

## DB Tables Used

- `live_match_snapshots` — source of truth for live state (score, xG, odds, minute, captured_at)
- `simulated_bets` — destination for paper bet logs
- `matches` — pre-match data (prematch_xg_home/away, prematch_o25_prob, prematch_btts_prob)
- `match_events` — red card detection
- `bots` — register each strategy here to appear on superadmin bot page

## Key Decisions Made

1. **Bot runs in scheduler, not as separate process** — zero API budget impact, shares Railway host
2. **Bayesian posterior formula** (not raw pace ratio): `(prematch_xg + live_xg) / (1 + minute/90)`
3. **Fixed 1-unit stake** in Phase 1 — no Kelly until ML model in Phase 2
4. **strategy_id in simulated_bets** — each of 11 strategies logs independently
5. **league filter ≥ 20 xG matches** — prevents mis-triggers in data-sparse leagues
6. **All 8 AI reviews confirmed**: 30s polling sufficient for all strategies, no frequency changes needed
7. **CLV proxy**: use pre-KO `odds_snapshots` closing line (Pinnacle where available)

## What Exists Already

- `live_match_snapshots` table with `xg_home`, `xg_away`, `shots_on_target_*`, `corners_*`, `possession_home`, `ou_25_over`, `live_home_odds`, `live_draw_odds`, `live_away_odds`, `captured_at`
- `simulated_bets` table — used by 16 pre-match bots, settlement pipeline handles FT
- `bots` table — renders on superadmin bot page automatically
- HIGH-priority bet escalation in LivePoller (30s stats once bet is active)
- Event-triggered odds snapshot on goal/red card (already in live_poller.py)

## Next Steps

1. Check `simulated_bets` schema — confirm `strategy_id` column exists or add migration 053
2. Write `workers/jobs/inplay_bot.py` — Phase 1A Strategy A logic
3. Register job in `workers/scheduler.py` (30s APScheduler interval)
4. Insert bot rows into `bots` table for each strategy
5. Test with dry-run mode (log without DB insert) for 1 match day
6. Switch to live — watch `simulated_bets` for first triggers
7. Add strategies A2, B, C, C_home, D, E, F (Week 1 complete)
