# Coolbet Automation Roadmap

> Single source of truth for everything Coolbet-related — what exists, what's
> broken, what's queued, what's idea-stage. Priority/effort/impact on every row.
> Update as tasks ship.
>
> Last updated: 2026-05-20

---

## What exists today

| Component | File | Status |
|---|---|---|
| Session manager (auth, JWT refresh, Imperva cookies, `keep_alive()`, `jwt_seconds_remaining`) | `workers/automation/coolbet_session.py` | ✅ Working |
| Odds ingester — new schema (markets + odds endpoints, parse_market) | `workers/automation/coolbet_explorer.py` | ✅ Working |
| Placer — `place_all_bets()` with dry/record/execute modes | `workers/automation/coolbet_placer.py` | ✅ Working on new schema (2026-05-20) |
| Foreground daemon — keepalive + odds + placement | `scripts/coolbet_daemon.py` | ✅ Working (placement loop runs but no-ops) |
| Audit — silent-bots diagnostic | `scripts/audit_silent_bots.py` | ✅ Working |
| Scheduler: `_coolbet_odds_snapshot_wrapper` every 30m | `workers/scheduler.py` | ⚠️ Likely 403s from Railway IP (Imperva tied to home IP). Error-isolated. |
| Scheduler: `_coolbet_keepalive_wrapper` every 20m | `workers/scheduler.py` | ⚠️ Same |

## Path to live auto-placement

✅ **COOLBET-PLACER-NEW-SCHEMA shipped 2026-05-20.** `--place-mode=execute` now physically capable of placing real bets via the new markets+odds schema. **Do not flip execute mode until COOLBET-SAFETY-GUARDRAILS lands** — that's the next P0.

---

## Tasks

### P0 — Critical path to live auto-placement

| ID | Effort | Impact | Description |
|---|---|---|---|
| ✅ **COOLBET-PLACER-NEW-SCHEMA** | Done 2026-05-20 | — | Placer per-bet loop in `place_all_bets` now uses `coolbet_explorer.fetch_match_markets` + `fetch_odds_for_markets` + `resolve_placement_target` (new function — maps our `(market, selection)` → Coolbet `(market_id, outcome_id, odds_id, current_odds)`). `fetch_odds_for_markets` extended to return `{outcome_id: {value, odds_id, market_id, status}}` so the placer has the UUID required for the bet payload. Legacy `find_market_outcome` / `fetch_sidebets` left in place as dead code (no callers). Smoke: COOLBET-PLACER-NEW-SCHEMA. |
| **COOLBET-PREFLIGHT** | 30m | Fails fast instead of silent dud runs | At daemon startup: verify Coolbet balance via `/s/user/balance`, decode JWT to confirm Imperva cookies aren't days from expiry, sanity-check bot universe. Refuse to start if any check fails (loud error). |
| **COOLBET-SAFETY-GUARDRAILS** | 1-2h | Difference between auto-placer and auto-bankroll-killer | Before flipping `--place-mode=execute` for real, add: `--max-bets-per-hour N`, `--max-stake-per-bet €X` override, `--bot-filter`, `--pause-after-loss €N`, `--max-edge-pct N` (refuse bets with absurd edge — model bug or odds error), and `--require-confirm` (y/n prompt per bet for first live runs). |

### P1 — Operational visibility (do before leaving daemon unattended)

| ID | Effort | Impact | Description |
|---|---|---|---|
| **COOLBET-IMPERVA-ALERT** | 30m | Biggest operational risk caught loud | Imperva cookies expire silently every few weeks. When login fails with 403, daemon must (a) stop the placement loop and (b) emit a loud notification (terminal beep + persistent log entry + Slack/email if wired). Currently a silent dud is the worst-case. |
| **COOLBET-DAILY-SUMMARY** | 1h | Operator visibility | At UTC end-of-day: print one summary line — bets placed today, total stake, paper-vs-real ROI delta, anomalies (skipped due to odds drop, no_market, etc.). Without it, daemon just hums silently and you forget it exists. |
| **COOLBET-PERSISTENT-LOG** | 30m | Diagnose after-the-fact | `logs/coolbet_daemon-YYYY-MM-DD.log` rotating file alongside stdout. Without it, a crash investigation needs reproducing the issue. |

### P2 — Reliability + state

| ID | Effort | Impact | Description |
|---|---|---|---|
| **COOLBET-STATE-PERSISTENCE** | 1h | Resume cleanly after restart | `~/.coolbet-daemon-state.json` holding last-keepalive timestamp, last odds-snapshot match count, set of bets seen. Restart picks up where it left off instead of re-flushing the placement loop. |
| **COOLBET-HEALTHCHECK** | 30m | External monitoring | HTTP `/healthz` on localhost:8765 returning JSON `{jwt_ttl, last_keepalive, last_odds, last_place, errors_last_hour}`. Lets a cron / Uptime Robot / `tmux` status bar surface daemon health. |
| **COOLBET-WIDER-ODDS-POLL** | 30m | More signal data | Add `--odds-mode=bets-only|wide|leagues` flag. `wide` = all upcoming matches in `--days` window. Useful when seeding historical Coolbet coverage for COOLBET-OR-PIN-REQUIRED-style analyses. |
| **COOLBET-BTTS-DC-AH-MTIDS** | 30m | Cleaner parsing | Once we observe Coolbet BTTS / DC / AH markets (didn't appear in the 3 small-league matches today), capture their `market_type_id` values and add to `_MTID_BTTS` / `_MTID_DC` / `_MTID_AH` in `coolbet_explorer.py`. Today's name-based fallback works but is locale-fragile. |
| **COOLBET-DEDUP-DUPES** | 30m | Storage hygiene | OU lines appear in both `fo-match` (main) and `sidebets` (depth) so each (match, market, selection) gets two rows per ingest cycle. De-dup by (market_id, outcome_id) inside `store_coolbet_snapshots_for_match` before insert. Not breaking — `odds_snapshots` is time-series — but unnecessary 2× write volume. |
| **COOLBET-ACTIVE-HOURS** | 30m | Quiet overnight | Add `--active-hours 6-23` to skip keepalive + polling overnight. Matches existing scheduler windows. Minor API/log noise reduction. |

### P3 — Speed + latency (only matters once placement is live)

| ID | Effort | Impact | Description |
|---|---|---|---|
| **COOLBET-FAST-PLACE** | 1h | React quicker to new bets | Tighten placement loop from 5m → 1m, OR adaptive: 1m for the first 5 min after each `betting_refresh` finishes, 10m otherwise. Useful only after live placement is on and we care about CLV. |
| **COOLBET-EVENT-DRIVEN-PLACE** | 2-3h | Zero-lag placement | Postgres `LISTEN`/`NOTIFY` from `betting_refresh` → daemon fires placement immediately on new bet. Real-time but requires scheduler-to-daemon plumbing. Defer until single-digit-minute latency proves insufficient. |
| **COOLBET-TWO-TIER-POLL** | 1h | Coverage + cost balance | Bets-only every 30 min + nightly wide sweep at 04:00 UTC. Better historical coverage without proportional cost. |
| **COOLBET-LEAGUE-FILTER** | 30m | Trim API calls | Limit odds polling to top-tier leagues we'd actually bet on. Niche if `--bets-only` is already the default. |
| **COOLBET-MULTI-MODES** | 30m | Testing + CI | `--once` (single cycle and exit), `--odds-only`, `--place-only`, `--no-keepalive` (for use alongside Railway scheduler). Mostly developer ergonomics. |
| **COOLBET-RAILWAY-KILL** | 5m | Reduce noise | If Railway-scheduled Coolbet jobs 403 every cycle (confirm in logs after deploy), remove them. Currently error-isolated so cost is just log noise. |

---

## Recommended sequence

Single ordered list, optimised for "least time to a safe live auto-placer running unattended":

1. ✅ ~~COOLBET-PLACER-NEW-SCHEMA~~ — done 2026-05-20
2. **COOLBET-PREFLIGHT** (P0, 30m) — fail-fast before anything else runs
3. **COOLBET-SAFETY-GUARDRAILS** (P0, 1-2h) — must precede first live `execute` run
4. **COOLBET-IMPERVA-ALERT** (P1, 30m) — only loud-failure operational mode
5. *Flip to `--place-mode=execute --require-confirm --max-bets-per-hour 5 --max-stake-per-bet 5`* — first live placements with training wheels on
6. **COOLBET-DAILY-SUMMARY** (P1, 1h) — visibility while observing first live cycles
7. **COOLBET-PERSISTENT-LOG** (P1, 30m) — close the observability loop
8. **COOLBET-STATE-PERSISTENCE** (P2, 1h) — first chance restart hygiene matters
9. *Loosen training-wheel limits as confidence grows*
10. Everything else (P2/P3) — as needs surface

Total to safe-live: ~3-5h of focused work, plus a paper-test cycle after step 5.

---

## Verification before any of this

Confirm the *existing* path actually does what's intended:

- [ ] `python3 scripts/coolbet_daemon.py` runs for ≥1 hour without crashing
- [ ] Keepalive log shows `JWT TTL ≈ <1820`, decreasing then refreshing
- [ ] Odds snapshot log shows non-zero rows stored per cycle
- [ ] Placement log shows `no qualifying bets` OR `no_market` for each candidate (no exceptions)
- [ ] `odds_snapshots` row count grows over time (sanity check via psql)

If any of these fail, stop and fix before touching the roadmap.
