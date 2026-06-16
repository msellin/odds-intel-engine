# COOLBET-DAEMON-ALERTS — Plan

_Created 2026-06-16. Strategic goal: make the Coolbet daemon "flawless enough for self-use" by eliminating silent failures._

## Why this exists

At 2026-06-16 19:00 UTC, the daemon has been failing every tick for ~24h. 70 ticks, 40+ consecutive errors, **zero alerts**. Manual JWT expired 2026-06-15 15:54 UTC and CDP-Chrome was in a logged-out state (Coolbet tab open, but `localStorage['cbauth']` missing). One calibrated-bot pick has been pending the entire window, missed.

The signaler sends Telegram for every qualified pick at pipeline time — that's the operator's safety net for "did I get a signal?", but it doesn't catch "is the daemon actually placing?".

## Scope

Tier-1 alerting only. Three pieces that together cover the silent-failure gap:

1. **In-daemon consecutive-fail alert** — on 2nd consecutive error tick, classify the failure and push Telegram. Dedup per hour.
2. **Pre-kickoff catch-net** — Railway-side job, independent of the Mac, runs every 5 min. If a calibrated-bot pick is <90min from KO, not placed, not skipped, and the Mac daemon is stale/erroring → urgent Telegram with placement details.
3. **CDP diagnosis helper** — `diagnose_cdp_jwt_state()` returns a structured classification (chrome_down / no_coolbet_tab / logged_out / jwt_expired / valid) so the alert can tell the operator exactly what to fix.

Out of scope for this PR (Tier 2+ deferred): proactive JWT refresh, CDP tab auto-navigate, self-pause after N hours, observability dashboard.

## Files touched

| File | Change |
|---|---|
| `workers/automation/coolbet_browser_sync.py` | Add `diagnose_cdp_jwt_state()` |
| `workers/automation/coolbet_mac_daemon.py` | Track consecutive errors, classify + alert on 2nd error |
| `workers/jobs/coolbet_prekickoff_alert.py` | NEW Railway job |
| `workers/scheduler.py` | Register `job_coolbet_prekickoff_alert` every 5 min |
| `scripts/smoke_test.py` | Add `COOLBET-DAEMON-ALERTS` + `COOLBET-PREKICKOFF-CATCHNET` |
| `PRIORITY_QUEUE.md` | Status entry |
| `COOLBET_ARCHITECTURE.md` | Update failure modes table |

## Risk

The new alerts will fire correctly the moment they ship — daemon is currently in a sustained failure. That's working as designed. Operator will receive 1-2 Telegram messages right after deploy (dedup per hour caps it).

No risk to existing placement path — additions only.
