# Coolbet Paper-Daemon Retirement + Ops-Channel Cleanup

## Why (the verdict)
`coolbet-mac-daemon` (PAPER) and `coolbet-daemon-keepalive` are LEGACY. Neither is
needed for:
- **paper simulation → model/strategy refinement** — that is `simulated_bets` +
  `shadow_bets`, written by the **pipeline** (`daily_pipeline_v2` + shadow/trigger
  jobs on the VPS), NOT the daemon.
- **real-money placement** — the **UI placer** (`place_coolbet_ui.py --execute`,
  launchd `coolbet-ui-placer`) does it, via the operator's logged-in CDP-Chrome.

The daemon's ONLY unique role is session-keep (`_ensure_session_live` JWT heal) +
operator Telegram control (`_drain_operator_commands`). That is PERIODIC, not a
continuous loop — it folds into the already-running `coolbet-feed-watchdog` (:20/:50)
and the existing webhook (pause/resume). This is exactly the **Unibet** architecture:
no daemon, periodic sweeps + `ensure_logged_in` heal + on-demand placer.

## Correct end-state architecture
- Pipeline (VPS) → `simulated_bets` + `shadow_bets` (paper sim for refinement).
- UI placer (Mac) → real money for `ui_place_enabled` bots.
- feed-watchdog (Mac, periodic) → odds-feed health + **session heal** (moved here).
- webhook (web) → operator pause/resume.
- ONE matcher: `coolbet_matching.match_event_to_af` (strong-anchor) for BOTH odds
  sweep and placement (converge `coolbet_placer` off its legacy search path).

## Stages (each tested + committed)
- [x] STAGE 0 — verdict + plan (this doc)
- [ ] STAGE 1 — ops-channel cleanup: NO_PICKS watchdog tracks a RETIRED bot
      (`bot_coolbet_value_v1`, retired 2026-09-08) → false-alarm spam every 20min.
      Point at a live bot + guard retired. Reduce "Pipeline complete — 0 new bets".
- [ ] STAGE 2 — converge `coolbet_placer` matching onto `coolbet_matching`.
- [x] STAGE 3 — move session-heal into `coolbet_feed_watchdog`.
- [x] STAGE 4 — retire launchd `coolbet-mac-daemon` + `coolbet-daemon-keepalive`;
      remove the now-moot `coolbet_daemon_healthcheck` alert.
- [x] STAGE 5 — docs (BETTING_ARCHITECTURE, COOLBET_RUNBOOK, WORKFLOWS, SYSTEM_MAP)
      + smoke tests + PRIORITY_QUEUE.

## Safety
UI placer is UNTOUCHED throughout → real-money placement never at risk. Heal is
MOVED before the daemon is removed (no session-keep gap).
