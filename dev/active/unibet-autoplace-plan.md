# UNIBET-AUTOPLACE — bring the Unibet arm to unattended parity with Coolbet

**Direction:** 🤖 OWN — a second book we can stake at automatically, so the router's
"Unibet is better / Unibet only" findings stop needing a human to execute them.
No PICKS impact: nothing here changes a published figure.

## Why this exists

`best-price-router-monitor` already runs :20/:50, checks BOTH books on every
real-money candidate, and Telegram-alerts when a pick is better or ONLY at Unibet.
Today the operator then places those by hand. The decision layer is done; the
execution layer is one book short of automatic.

## What is ALREADY true (verified 2026-09-11 — do not rebuild)

- `unibet_placer.place_bet` places real money end-to-end (Derby €10, 2026-09-09).
- It already mirrors Coolbet's safety: €10 flat stake, `execute=False` default,
  live-site price re-check before placing, single-leg guarantee, balance
  before/after confirmation, auto-login via `unibet_browser_sync.cdp_auto_login`.
- Having NO pick table is BY DESIGN — it is a dumb executor arm; the router is the
  decider. Do not give it its own pick loader.
- `best_price_router.route(stage=True)` ALREADY dispatches to
  `resolve_event_url` → `unibet_placer.place_bet`, driving the real slip and
  stopping before the place click.
- The owner's routing invariant (2026-09-09) is already specified: place ONCE at the
  better clearing book; cross-book exposure check prevents double-betting.

## The actual remaining gaps

1. **Placer registry** — the router should dispatch per book through a registry
   rather than book-specific branches, so adding a book is data not code.
2. **Session liveness on the Unibet arm** — THE risk. Unibet does not rot today only
   because nothing runs it unattended; wiring execute creates that loop and it will
   rot exactly as Coolbet did. Needs `_ensure_session_live()`-equivalent per tick,
   BEFORE any no-candidate early return (the Coolbet lesson: the session was only
   checked when a pick was ready, so quiet days hid the lapse for days).
3. **A scheduled execute path** — the monitor is report-only by design.
4. **`ROUTER_ALLOW_REAL`** — OWNER GATE. Not mine to set. Everything above is built
   and testable with it unset; nothing moves money until the owner flips it.

## Risks

- Real money at a second book. Every step lands paper-first (`execute=False`).
- Session rot on the unattended arm (gap 2) — must ship WITH the heartbeat, not after.
- Cross-book double-bet — already guarded by the exposure check; must be covered by
  a smoke test before any execute path is scheduled.

## Status

🔄 In progress 2026-09-11.
