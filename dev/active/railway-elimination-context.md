# RAILWAY-ELIMINATION — Context

## Key decisions made

**Scheduler host: Mac (launchd), not a VPS**
The Mac already runs coolbet daemon 24/7. Moving the scheduler there is zero additional
cost and requires no new infrastructure. A VPS would cost the same $5/mo as Railway.

**No Docker on Mac for the scheduler**
Coolbet daemon runs as a native Python process via launchd. Same model for scheduler —
`python3 -m workers.scheduler`. No Dockerfile changes needed.

**load_dotenv() handles most credentials**
`workers/scheduler.py` already calls `load_dotenv()` at startup. All vars in `.env` are
automatically available. Only vars NOT in `.env` (feature flags, Railway-only overrides)
need to be explicitly listed in the plist EnvironmentVariables dict.

**TZ=UTC must be in plist**
APScheduler is initialized with `timezone="UTC"` in code, but OS-level TZ affects
startup logging and any code that calls `datetime.now()` (not `datetime.now(timezone.utc)`).
Set `TZ=UTC` in the plist EnvironmentVariables to match Railway's container.

**FLARESOLVERR_URL in plist overrides .env**
Mac's `.env` may have a Railway-hosted FS URL. Plist override pins `http://localhost:8191`
so the scheduler always uses the local FlareSolver (same as coolbet daemon plist).

**Memory limit env var rename**
`RAILWAY_POD_MEM_LIMIT_MB` → `SCHEDULER_PROCESS_MB_LIMIT`, default changed 1024 → 8192.
Reason: 1 GiB was calibrated for Railway's Hobby pod. Mac has 16+ GB. At 1 GiB default
the memory alert fires immediately on any warm scheduler run (psutil works on Mac,
unlike the old `/proc` fallback path that skipped silently).
Old env var name kept working during transition via old Railway (no code runs both paths).

**coolbet_state._default_set_by() needs no change**
Returns "local" when no RAILWAY_PROJECT_ID is set. That's correct for Mac. JWT set_by
tags will show "local" instead of "railway" — this is accurate, not a bug.

**Coolbet daily summary "Railway heartbeat" check still works**
`hb_age_s` is from `coolbet_session_state.last_heartbeat_at`, updated by
`job_healthcheck_ping` (healthchecks.io ping, every 5 min). That job moves to Mac with
the rest of the scheduler. The underlying check is fully portable — just rename the label.

**Railway auto-deploy replaced by manual kickstart**
After migration, scheduler-affecting code changes require:
```
launchctl kickstart -k gui/$(id -u)/com.oddsintel.scheduler
```
Non-scheduler changes (frontend, DB migrations, etc.) need no action.

**Cutover order: stop Railway first, then start Mac**
Avoid double-job execution. Railway paused → Mac plist loaded → verify → 24h soak → cancel.

## Key files

| File | Role |
|------|------|
| `workers/scheduler.py` | The process to migrate — 3423 lines, 114+ jobs |
| `workers/jobs/health_alerts.py:405` | `RAILWAY_POD_MEM_LIMIT_MB` rename target |
| `workers/jobs/coolbet_daily_summary.py:44,177,194` | `RAILWAY_HB_STALE_MIN` rename target |
| `local/launchd/com.oddsintel.coolbet-mac-daemon.plist` | Template for new plist |
| `local/launchd/com.oddsintel.scheduler.plist` | New file (Phase 3) |

## Group B feature flags to verify against Railway env panel
Before Phase 3, operator must check Railway dashboard → Variables and confirm current
live values for:
- `MODEL_VERSION`
- `MODEL_VERSION_OU_T1`
- `MODEL_VERSION_OU`
- `PIN_CROSS_DRIFT_VETO_ENABLED`
- `STAGE2_CALIBRATOR`
- `COOLBET_RECORD_ALLOWED_MATURITY`
- `META_B_ML3_ENABLED`, `META_B_ML3_VERSION`, `META_B_ML3_THRESHOLD`
- `INPLAY_E_PLATT_ENABLED`
- `ODDS_API_KEY` (or `OA_KEY`)
- `SHADOW_MODEL_VERSION` (if set — usually empty)
- Any others set to non-default values

These must be in Mac `.env` before the plist is loaded.

## Next steps
- Phase 1 can start immediately (no operator input needed)
- Phase 2 needs operator to read Railway env panel and diff against `.env`
- Phase 5 (cutover) needs operator to stop Railway service from dashboard

## State as of 2026-06-29
- Status: plan written, not started
- All findings verified against current codebase
- No blockers for Phase 1
