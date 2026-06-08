# CS2 Overnight Summary — 2026-06-08 / 2026-06-09

## What shipped (commits in chronological order tonight)

1. **CS2-DATA-ACCUMULATION** — Migrations 199/200, scanner writes `cs2_predictions` history, `cs2_settlement.py`, scheduler crons for scanner+settlement
2. **CS2-GRID-EXPLORE** — corrected GRID OA URL, confirmed CS2 titleId=28, doc'd that OA has no rosters
3. **CS2-SCANNER-FIX** — naive datetime crash in roster transfers; semaphore for transfer concurrency
4. **CS2-COVERAGE-GATE** — Banger Gang 50/50 + fake VALUE badge fix. NULL odds when <10 matches/180d
5. **CS2-WINNER-BUG-FIX** — historical CSV's `team1_win` is 97.9% zeros; derive from scores. Top ELO went from UNiTY (1695) to Team Vitality (2157)
6. **CS2-UNIQUE-MODEL-VERSION** — Migration 202; widened constraint so multiple model versions can coexist on same match (the bug that kept elo+pq backfill at 0 rows)
7. **CS2-MULTI-BOOK-AND-BOT** — Migration 201 + Coolbet scanner + LAN/days_since_roster features + `bot_cs2_value_v1` + page redesign
8. **CS2-PANDASCORE-ROSTERS** — 55/61 teams resolved via PandaScore (88% vs Liquipedia's 7%); replaces Liquipedia for current 5-man lineups
9. **CS2-FEATURES** — 8 new walk-forward features (form_momentum, h2h_win_pct, h2h_count, days_since_match, opp_strength_avg) + migration 203
10. **CS2-SELF-LEARNING** — Weekly Platt cron + apply-at-scan + bot anomaly guard (25pp divergence)
11. **CS2-PIPELINE-HEALTH** — admin/cs2 shows cron last-run green/orange/grey dots
12. **CS2-KELLY-SIZING** — half-Kelly stake replaces uniform 1u (cap 2u)

## Calibration results

Backfill comparison after the team1_win bug fix:

| Model | n | Accuracy | Log loss | ECE | Platt a/b |
|---|---|---|---|---|---|
| `elo_v1_backfill_v2` (ELO only) | 9,199 | **58.9%** | **0.6664** | **3.03%** | 0.846 / 0.109 |
| `elo+pq_v1_backfill` (ELO+PQ) | 7,163 (still running) | 58.4% | 0.6700 | 3.24% | 0.829 / 0.097 |
| `elo+pq_v1` (LIVE, seeded from PQ backfill) | 37 | n/a (no settled) | — | — | 0.829 / 0.097 |

**Headline:** PQ doesn't help on walk-forward (small loss vs ELO-only). Both are well-calibrated (ECE under 5%). The expected +4.1pp gain from the original backtest didn't transfer — likely because walk-forward uses stale lineups (the strict-less-than pointer means we use whatever lineup was last seen, even if many weeks old).

**Decision:** keep `elo+pq_v1` as the live model_version (it gracefully falls back to ELO when PQ unknown), but the meaningful calibration result is the 3% ECE on 9.2k history. Weekly recalibration is now scheduled.

## What's self-running

| Job | Schedule | What it does |
|---|---|---|
| `cs2_scanner` | every 4h at :12, 06–22 UTC | ELO+PQ+features → cs2_upcoming_matches + cs2_predictions, applies Platt |
| `cs2_settlement` | hourly 12–02 UTC at :22 | bo3.gg finished matches → cs2_results, settles bets |
| `cs2_coolbet_scanner` | every 30 min 07–22 UTC | Coolbet odds → coolbet_odds1/2 |
| `cs2_bot` | every 4h at :25 | Scans for value, fires `bot_cs2_value_v1` simulated_bets with Kelly stake + anomaly guard |
| `cs2_pandascore_rosters` | daily 04:30 UTC | Refresh PandaScore current 5-man lineups |
| `cs2_weekly_calibrate` | **Sunday 03:30 UTC** | Refit Platt on past 90d, promote if log_loss improves by ≥0.001 |

All registered in `workers/scheduler.py`. Pipeline health UI on `/admin/cs2` shows last-run dot per job.

## What's NOT in production yet (deferred)

- **Pinnacle CS2 odds** — geo-blocked from dev network. Should work from Railway prod IP — worth a one-time test.
- **HLTV player ratings scraper** — 403'd on every endpoint. Needs Playwright + proxies, separate project.
- **egamersworld team data** — accessible (200 OK) but parser non-trivial. Future work.
- **PandaScore Pro live odds** — paid, deferred until model proves positive CLV.
- **Liquipedia map-veto** — would be a real signal but requires per-series wikitext parsing.

## Free-tier env vars needed on Railway

User confirmed both are set:
- `PANDASCORE_API_KEY` ✓
- `GRID_API_KEY` ✓

bo3.gg / Coolbet / scanner / bot / settlement need no env vars beyond the standard Supabase ones.

## What to look at first thing in the morning

1. **`/admin/cs2`** — Pipeline-health row at top. All five dots should be green (overnight crons fired).
2. **Stats panel** — Live predictions count. Should have grown from 37 to ~60+ if the 22:12 + 02:12 scanner runs fired.
3. **Bot bets** — Probably still 0 (tier-1 CS2 fixtures don't lock 72h ahead this time of week). Will pick up Monday morning.
4. **Backfill state** — `SELECT model_version, COUNT(*) FROM cs2_predictions GROUP BY 1` should show elo+pq_v1_backfill at 9200 if the process finished.

## Open follow-ups (next session)

1. If elo+pq backfill < 9200 by morning, kill and accept the partial calibration we have.
2. Decide whether to switch the live model to ELO-only given PQ doesn't help on walk-forward.
3. Try Pinnacle from Railway shell — `python -c "import requests; print(requests.get('https://guest.api.arcadia.pinnacle.com/0.1/sports/12/matchups').status_code)"`. If 200, wire a Pinnacle scanner.
4. Manual sweep: more aliases for PandaScore (the 6 misses are obscure teams; could add them).
5. Build `/admin/cs2/performance` — chart bot ROI / CLV / accuracy over time once we have ≥50 settled bets.

## Smoke-test count

9 CS2 tests, all passing locally:
- CS2-ELO-SCANNER
- CS2-DATA-ACCUMULATION
- CS2-COOLBET-SCANNER
- CS2-BOT
- CS2-PANDASCORE-ROSTERS
- CS2-FEATURES
- CS2-WEEKLY-CALIBRATE + PLATT-AT-SCAN
- CS2-BOT-ANOMALY-GUARD
- CS2-KELLY-SIZING

CI runs them on every push.
