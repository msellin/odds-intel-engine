# RAILWAY-ELIMINATION — Plan

**Goal:** Cancel Railway ($5/mo) by moving `workers/scheduler.py` to a launchd service
on the operator's Mac — the same machine that already runs the Coolbet daemon 24/7.

**Why now:** Mac is already the single point of failure for placements. The scheduler
moving there costs nothing in additional reliability risk. healthchecks.io already
provides the external "total silence" alert regardless of where the scheduler lives.

---

## Audit findings

### What Railway currently does
- Runs `workers/scheduler.py` — 114+ APScheduler cron jobs (football, CS2, tennis, ML
  retrains, monitoring, live poller daemon thread, healthchecks.io ping)
- Hosts ~40 env vars including API keys + feature flags
- Auto-deploys from GitHub main on every push (restarts scheduler mid-job)
- Exposes health endpoint on :8080 (Railway health check)

### What already lives on the Mac
- Coolbet daemon (launchd `com.oddsintel.coolbet-mac-daemon`)
- FlareSolver Docker on port 8191
- CDP-Chrome on port 9222
- `.env` with most credentials

### Code that references Railway (full inventory)

| File | Line | Type | Action |
|------|------|------|--------|
| `workers/scheduler.py:5` | Docstring "Railway Scheduler" | Cosmetic | Rename banner to "Pipeline Scheduler" |
| `workers/scheduler.py:28` | Comment about Railway's 500/sec log limit | Cosmetic | Remove |
| `workers/scheduler.py:2424` | Comment "Railway health check window" | Cosmetic | Update |
| `workers/scheduler.py:2430` | Banner string "OddsIntel Railway Scheduler" | Cosmetic | Change |
| `workers/scheduler.py:2434` | `_cleanup_stale_runs` comment | Cosmetic | Update |
| `workers/scheduler.py:2437` | "every git push restarts Railway" comment | Cosmetic | Update |
| `workers/jobs/health_alerts.py:143` | Alert text "RAILWAY restart" | Cosmetic | Fix alert text |
| `workers/jobs/health_alerts.py:405` | `RAILWAY_POD_MEM_LIMIT_MB` env var | **Functional** | Rename + new default |
| `workers/jobs/health_alerts.py:415` | Alert text "restart on Railway" | Cosmetic | Fix alert text |
| `workers/jobs/coolbet_daily_summary.py:44` | `COOLBET_DAILY_RAILWAY_STALE_MIN` env var | **Functional** | Rename env var |
| `workers/jobs/coolbet_daily_summary.py:177` | "Railway heartbeat" string | Cosmetic | Rename |
| `workers/jobs/coolbet_daily_summary.py:194` | "🛰 Railway HB:" string | Cosmetic | Rename |
| `workers/automation/coolbet_state.py:384` | `RAILWAY_PROJECT_ID` check for set_by tag | Functional-safe | No change needed — returns "local" always on Mac, that's correct |

### Functional items detail

**`health_alerts.py` — memory check:**
psutil IS in requirements.txt and works on Mac. After migration the memory check will
actually fire (unlike on Mac dev runs which previously skipped via `/proc` fallback).
The default `RAILWAY_POD_MEM_LIMIT_MB=1024` is too low for a Mac with 16+ GB RAM — at
1 GiB the alert fires almost immediately for any non-trivial run.
Fix: rename to `SCHEDULER_PROCESS_MB_LIMIT`, default `8192` (8 GB — conservative for Mac).

**`coolbet_daily_summary.py` — scheduler heartbeat:**
`hb_age_s` is from `coolbet_session_state.last_heartbeat_at` — updated by
`job_healthcheck_ping` every 5 min. That job runs fine on Mac. The "Railway heartbeat"
label just needs renaming; the underlying check is fully portable.

**`coolbet_state._default_set_by()`:**
Checks `RAILWAY_ENVIRONMENT` / `RAILWAY_PROJECT_ID`. On Mac neither is set, so
`_default_set_by()` returns "local" — exactly correct. No change needed.

### Env vars: Railway → Mac .env delta

**Group A — likely already in Mac .env** (core credentials used by everything):
- `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SECRET_KEY`
- `DATABASE_URL`
- `API_FOOTBALL_KEY`
- `GEMINI_API_KEY`
- `RESEND_API_KEY`, `DIGEST_FROM_EMAIL`, `DIGEST_TO_EMAIL`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `SITE_URL`
- `PANDASCORE_API_KEY`
- `HEALTHCHECKS_IO_PING_URL`
- `ADMIN_ALERT_EMAIL`
- `HLTV_AUTH_COOKIES`
- `FLARESOLVERR_URL` (on Mac should be `http://localhost:8191`)

**Group B — feature flags, currently non-default on Railway, may NOT be in .env:**
These are the flags you flip on Railway without code deploys. Must verify each.
- `MODEL_VERSION=v20260607` (default codebase: `v14`)
- `MODEL_VERSION_OU_T1=v20260607` (no default in code — if unset, falls through to MODEL_VERSION)
- `MODEL_VERSION_OU=v14_recreate_2026_05_11` (no default in code)
- `PIN_CROSS_DRIFT_VETO_ENABLED=true` (default: `false`)
- `STAGE2_CALIBRATOR=isotonic` (default: `platt`)
- `COOLBET_RECORD_ALLOWED_MATURITY=calibrated` (default: not set — placer default is `calibrated,beta`)
- `AF_DAILY_QUOTA=150000` (default: `150000` — probably fine at default)
- `ODDS_API_KEY` (WC window closing 2026-07-19, but keep it)

**Group C — Railway-specific, rename or set new value in plist:**
- `RAILWAY_POD_MEM_LIMIT_MB` → replaced by `SCHEDULER_PROCESS_MB_LIMIT=8192` in plist

**Group D — env vars to skip (Mac-specific, daemon already handles):**
- `FLARESOLVERR_URL` — already overridden in coolbet daemon plist to localhost
  For the scheduler plist: also set to `http://localhost:8191`
- `COOLBET_MAC_POLL_S` — daemon plist only
- `COOLBET_AUTO_LOGIN_ON_HEAL` — daemon plist only

### Arch change: auto-deploy gone
Railway redeploys on every push, restarting the scheduler (good for env var changes,
bad for mid-job kills). After migration:
- Code changes: `git push` no longer restarts scheduler. Operator must `launchctl kickstart -k gui/$(id -u)/com.oddsintel.scheduler` after pushes that change scheduler logic.
- Env var changes: edit `.env`, then kickstart.
- This is strictly better for job stability — no more mid-job restarts during active dev.

### Mac sleep
Mac already runs coolbet daemon 24/7 without sleeping. Verify with:
```
pmset -g | grep " sleep"
```
Expected: `sleep 0` (or never). If not set, add to plist or run `sudo pmset -a sleep 0`.

### Monitoring independence after migration
Before: Railway monitors Mac (coolbet_daemon_healthcheck, etc.)
After: Mac monitors itself → all watchdog jobs still work, they just read from DB and
send Telegram alerts. The cross-machine redundancy for the watchdog layer is reduced.
Mitigation already in place:
- healthchecks.io emails if Mac goes totally silent (external to both)
- This is acceptable — the Mac already has single-point-of-failure status for placement

---

## Phases

### Phase 1 — Code cleanup (rename Railway references)
Files: `workers/scheduler.py`, `workers/jobs/health_alerts.py`,
`workers/jobs/coolbet_daily_summary.py`
Changes:
1. Rename `RAILWAY_POD_MEM_LIMIT_MB` → `SCHEDULER_PROCESS_MB_LIMIT` in health_alerts.py,
   change default to `8192`, update alert text
2. Rename `COOLBET_DAILY_RAILWAY_STALE_MIN` → `COOLBET_DAILY_SCHEDULER_STALE_MIN` in
   coolbet_daily_summary.py, relabel "Railway heartbeat" → "Scheduler heartbeat"
3. Rename scheduler.py startup banner, update Railway-specific comments

### Phase 2 — Env var audit
Compare Railway env vars panel against Mac `.env`. For Group B flags that are missing
from `.env`, add them now (before plist creation). This is operator-assisted — only the
operator can see the Railway env panel.

### Phase 3 — Create `local/launchd/com.oddsintel.scheduler.plist`
New launchd service modelled on coolbet daemon plist:
- ProgramArguments: `python3 -m workers.scheduler`
- WorkingDirectory: `/Users/margussellin/www/odds-intel-engine`
- EnvironmentVariables: `TZ=UTC`, `PYTHONUNBUFFERED=1`,
  `FLARESOLVERR_URL=http://localhost:8191`,
  `SCHEDULER_PROCESS_MB_LIMIT=8192`
  (all other vars come from `.env` via `load_dotenv()` in scheduler.py)
- KeepAlive: true, ThrottleInterval: 60
- Logs: `dev/active/scheduler.log`

### Phase 4 — Smoke tests
Add `RAILWAY-ELIMINATION-PLIST` and `RAILWAY-ELIMINATION-CODE-CLEAN` to smoke_test.py.

### Phase 5 — Cutover
1. Pause/stop Railway service from Railway dashboard (don't cancel yet)
2. Install plist on Mac: `cp local/launchd/com.oddsintel.scheduler.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.oddsintel.scheduler.plist`
3. Tail logs: `tail -f dev/active/scheduler.log`
4. Verify first few jobs fire (healthcheck ping, ops snapshot, etc.)
5. Monitor for 24h — watch Telegram alerts, pipeline_runs table, healthchecks.io
6. Cancel Railway subscription

### Phase 6 — Doc updates
Update INFRASTRUCTURE.md, WORKFLOWS.md, ROADMAP.md to reflect Mac scheduler.
Update the "Railway env vars" section of WORKFLOWS.md to "Mac scheduler env vars".

---

## Risks

| Risk | Mitigation |
|------|-----------|
| Group B feature flags missing from .env | Phase 2 audit catches this before cutover |
| Mac sleep kills scheduler | Already solved by coolbet daemon setup (Mac doesn't sleep) |
| Health endpoint :8080 port conflict | Not used by anything on Mac; no external traffic needed |
| Railway auto-deploy gone = stale code runs | Operator kickstarts after scheduler-affecting pushes |
| Memory alert fires too aggressively on Mac | Set `SCHEDULER_PROCESS_MB_LIMIT=8192` in plist |
| Watchdog monitoring loses Railway independence | healthchecks.io remains as external fallback |

---

## Time estimate
- Phase 1 (code): 1h
- Phase 2 (env audit): 30min (operator-assisted)
- Phase 3 (plist): 30min
- Phase 4 (smoke tests): 30min
- Phase 5 (cutover): 30min + 24h monitoring window
- Phase 6 (docs): 30min

Total active work: ~3.5h + 24h soak
