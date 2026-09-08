# Coolbet OWN-betting — Unified Flow (epic plan)

## Goal (owner, 2026-09-08)
ONE unified, robust, monitorable flow for our OWN Coolbet real-money betting:
easy to monitor / pause / start / stop; self-verifies what it already placed
(via the Coolbet UI) so it never duplicates; full transparency grounded in an
actual map of what runs where and when.

## Why this is needed
Today there are TWO placers (see docs/COOLBET_OWN_BETTING.md):
- REAL money: place_coolbet_ui.py, bot_coolbet_value_v1 (line-shop, flat 3%),
  shadow_bets_unique, launchd coolbet-ui-placer --execute hourly.
- PAPER: coolbet_placer.py, simulated_bets/calibrated, per-market model edge
  floors (1x2 13%/OU 8%), execute=False.
They share only the per-market odds floor. Our edge-floor tuning does not gate
real money. No single monitor/control surface; state is spread across launchd
plists, a DB kill switch, Telegram marks, and lock files.

## Phases
1. **MAP** — full transparency: every Coolbet OWN process/daemon/script/launchd
   job, what it reads/writes, and when it runs. Output: dev/.../context.md map +
   a WORKFLOWS.md section. (codebase investigation)
2. **STRATEGY** — backtest line-shop vs model-edge at 500/1k/10k/104k games,
   fold-robust, priced at what we can actually take at Coolbet. Decide which
   instrument governs real placement (COOLBET-REALMONEY-EDGE-GATE-RECONCILE).
3. **REAL-MONEY AUDIT** — what the UI placer has actually done: real_bets rows,
   ROI, CLV, by market/bot; duplicate/self-verify gaps.
4. **DESIGN** — one unified flow + one monitor/control surface (start/stop/pause,
   up/down counters, self-verify placed bets against the Coolbet UI, dedup).
5. **BUILD** — implement, migrate off the two-placer split, smoke, docs.

## Guardrails
- Real money: never flip execute without explicit owner authorization.
- No floor/strategy change to the real-money path without a fold-robust,
  out-of-sample result (the 1x2-15%-overfit lesson).
- Push to main; docs in same commit; smoke per change.
