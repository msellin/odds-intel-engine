# CONFIG-SWEEP-2026-08-19 — Context

## Session state
- 2026-08-19: plan written. Building sweep engine next.

## Key files (existing)
- `scripts/rigorous_eval.py` — walk-forward eval framework (reuse the
  windowing logic).
- `scripts/backtest_bot_config.py` — if exists (didn't verify, check).
- `workers/api_clients/db.py` — DB helpers (`execute_query`).
- `workers/jobs/daily_pipeline_v2.py::ACCESSIBLE_BOOKMAKERS` — for
  best-odds aggregation.

## Data shape

Historical data available:
- `matches` table — one row per fixture; `score_home`, `score_away`,
  `status`, `date`, `league_id`
- `predictions` table — one row per (match, market, source); use
  `source='ensemble'` for the production model
- `odds_snapshots` — many rows per (match, market, selection); filter
  `is_closing=false` for pick-time odds, `is_closing=true` for close
- `leagues` — `tier` column

Data volume estimate:
- ~40k matches May-Aug
- ~200k prediction rows
- ~10M+ odds_snapshots rows (bookmakers × markets × time)

Sweep approach: load a pre-flattened DataFrame with one row per (match,
market, selection). Compute best accessible odds via GROUP BY once.
Then all sweep configs are pandas filter operations.

## Fantasy-price safety

Sweep rows are pre-filtered to drop `odds_at_pick / closing_odds ≥ 1.65`
(same rule as CLV-AUTOVOID). Otherwise the sweep would find configs that
"win" by picking Gremio-U20-style fantasy prices.

## Config → bot translation

Each sweep result needs to map back to a placeable bot config. Sweep
axes align to `BOTS_CONFIG` dict shape:
- `market` → `markets: [...]`
- `edge_threshold` → `edge_thresholds` (tier-adjusted or flat)
- `odds_min/max` → `odds_range: (min, max)`
- `min_prob` → `min_prob`
- `tier_filter` → `tier_filter: [...]`
- `require_pinnacle` → new field, gates whether Pinnacle must be
  present. Not in current BOTS_CONFIG — new gate to add if a winner
  needs it.

## Watch-outs

- Ensemble predictions aren't populated for every match — the sweep will
  skip matches with no `ensemble` row. That's fine.
- Some old bets have `pnl=NULL` because they were shadow_bets originally
  — sweep computes P&L directly from won/lost so this doesn't matter.
- Voided/pending bets excluded from sweep by construction (we compute
  won from `score_home`/`score_away`).

## After completion

If sweep finds worthy configs (survive all 4 acceptance criteria):
- Deploy as shadow bots (write to shadow_bets only)
- 4-6 weeks paper observation
- Then decide beta/paper promotion or retirement

If sweep finds nothing worthy: valuable negative result — confirms
current bots are already close to the frontier. Log lesson in
whitepaper.
