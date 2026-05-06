# In-Play Paper Trading Bot — Context
> Task: P3.4 | Updated: 2026-05-06

## Key Files

| File | Purpose |
|------|---------|
| `workers/jobs/inplay_bot.py` | **NEW** — 8 strategies, safety checks, Bayesian posterior, paper bet logging |
| `workers/live_poller.py` | Integration point — calls `run_inplay_strategies()` after each cycle |
| `workers/api_clients/supabase_client.py` | `ensure_bots()`, `store_bet()` — reused for in-play |
| `workers/api_clients/db.py` | `execute_query()`, `store_live_snapshots_batch()` |
| `PRIORITY_QUEUE.md` § INPLAY Plan | Full strategy conditions, staking, phases |

## DB Tables Used

- `live_match_snapshots` — live state: score, xG, odds, minute, captured_at
- `simulated_bets` — paper bet destination (via `store_bet()`)
- `bots` — 8 rows auto-created by `ensure_bots()` (inplay_a through inplay_f)
- `matches` — `af_prediction` JSONB for prematch xG, `league_id`, `status`
- `predictions` — prematch O2.5, BTTS, 1X2 probabilities (source='ensemble')
- `leagues` — `tier` for league filter
- `match_events` — red card detection (`event_type IN ('red_card', 'yellow_red_card')`)

## Key Decisions Made

1. **Bot integrated into LivePoller.\_run_cycle()** — not a separate scheduler job. Runs naturally after snapshots stored, only when live matches exist.
2. **Prematch xG from `matches.af_prediction` JSONB** — `predictions.goals.home/away`. Our model's exp_home/exp_away isn't stored persistently.
3. **`ensure_bots()` at runtime** — no migration needed. Bots created on first call.
4. **Fixed 1-unit stake** — no Kelly in Phase 1.
5. **Sentry + Railway logs + heartbeat** for error detection.
6. **All 8 Week 1 strategies built at once** — AI tools consensus was parallel launch for faster data accumulation.

## Prematch Data Sources

| Data | Source | Query path |
|------|--------|-----------|
| Prematch xG home/away | `matches.af_prediction` JSONB | `af_prediction->'predictions'->'goals'->>'home'` |
| Prematch O2.5 prob | `predictions` table | `market='ou_25_over', source='ensemble'` |
| Prematch BTTS prob | `predictions` table | `market='btts_yes', source='ensemble'` |
| Prematch 1X2 probs | `predictions` table | `market='1x2_home'/'1x2_away', source='ensemble'` |
| League tier | `leagues` table | `tier` column |

## Next Steps

1. Commit and push — Railway auto-deploys
2. Watch Railway logs for "InplayBot: 8 bots registered" message
3. Monitor first heartbeat log (~5 min after first live match)
4. Watch for first paper bet trigger
5. After first day: query `simulated_bets` for in-play rows, verify settlement
6. Week 2: add strategies G, H
7. Week 3: add strategies I, J, K
