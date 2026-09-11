# UNIBET-AUTOPLACE — context

## Headline: the gap is MUCH smaller than first assessed

Two corrections made on 2026-09-11 after reading the code rather than the summaries:

1. **`unibet_placer` already meets Coolbet's safety standard.** €10 flat stake
   (matching Coolbet's FLAT_STAKE_EUR), `execute=False` default, live-site price
   re-check before placing, single-leg guarantee, balance before/after
   confirmation, auto-login. It has placed real money end-to-end (Derby €10).
2. **"No pick table" is BY DESIGN, not a gap.** Its docstring: *"This module does
   NOT decide WHICH bets to place — that is the Stage-B best-price router."*
   Executor arms must stay dumb. Do NOT give it a pick loader.

## Session liveness (was thought to be the big risk) — also already built

`unibet_browser_sync.ensure_logged_in(min_gap_min=30, max_wait_s=150)` exists and
is the documented Unibet analogue of Coolbet's auto-heal. Contract:

- returns `already` | `logged_in` | `rate_limited` | `failed` | `no_creds` | `error`
- **never raises**, idempotent
- rate-limited via a stamp file, and it **stamps BEFORE the attempt** so a hang
  still rate-limits the next tick (good — this is the failure mode that bites)

Already called by the odds feed at `unibet_odds_feed.py:525`. **Missing only from
the placement path.** So gap 2 is "call an existing, proven function at dispatch",
not "build a heartbeat".

## Key files

| File | Role |
|---|---|
| `workers/automation/unibet_placer.py` | dumb executor arm (place_bet) |
| `workers/automation/unibet_browser_sync.py` | session: `ensure_logged_in`, `cdp_auto_login`, `diagnose` |
| `workers/automation/coolbet_ui_placer.py` | the standard to match |
| `workers/automation/best_price_router.py` | the decider; `route()` modes report/stage/execute |

## Owner gate — do not cross

`ROUTER_ALLOW_REAL` is the env switch that lets the router move real money at a
second book. It is the OWNER's to set. Build and test everything with it unset.

## Next steps

1. Placer registry in `best_price_router` (dispatch as data, not branches).
2. Call `ensure_logged_in()` on the Unibet arm at dispatch, before placement.
3. Smoke tests: registry covers both books; cross-book exposure prevents
   double-bet; execute refuses without `ROUTER_ALLOW_REAL`.
