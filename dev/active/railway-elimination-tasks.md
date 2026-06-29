# RAILWAY-ELIMINATION — Tasks

## Phase 1 — Code cleanup

- [ ] **1a** `workers/jobs/health_alerts.py` — rename `RAILWAY_POD_MEM_LIMIT_MB` →
  `SCHEDULER_PROCESS_MB_LIMIT`, change default from `1024` to `8192`, update alert body
  text to remove Railway-specific restart instructions → "kickstart the scheduler plist"
- [ ] **1b** `workers/jobs/coolbet_daily_summary.py` — rename env var
  `COOLBET_DAILY_RAILWAY_STALE_MIN` → `COOLBET_DAILY_SCHEDULER_STALE_MIN`, rename
  "Railway heartbeat" → "Scheduler heartbeat" (lines ~177 + ~194)
- [ ] **1c** `workers/scheduler.py` — rename startup banner from "OddsIntel Railway
  Scheduler" to "OddsIntel Pipeline Scheduler", remove Railway-specific comments
  (lines 5, 28, 2424, 2430, 2434, 2437)
- [ ] **1d** Smoke test `RAILWAY-ELIMINATION-CODE-CLEAN` — assert none of the
  three files contain the old Railway env var names, assert alert body text doesn't
  mention "Railway" in operator instructions

## Phase 2 — Env var audit (operator-assisted)

- [ ] **2a** Operator: open Railway dashboard → Variables → copy all vars to a scratch
  list. Specifically check Group B flags (see context.md) against Mac `.env`
- [ ] **2b** For any Group B flag missing from `.env`, add it now (correct live value
  from Railway panel). Commit the `.env` update (gitignored — just local edit).
- [ ] **2c** Verify `FLARESOLVERR_URL` in `.env` — if it points to Railway-hosted FS,
  note this (plist will override it to localhost anyway)
- [ ] **2d** Record final confirmed env delta in context.md "State" section

## Phase 3 — Create scheduler plist

- [ ] **3a** Create `local/launchd/com.oddsintel.scheduler.plist` based on coolbet
  daemon plist template. Key differences: module = `workers.scheduler`,
  ThrottleInterval = 60, log = `dev/active/scheduler.log`
- [ ] **3b** Include in plist EnvironmentVariables: `TZ=UTC`, `PYTHONUNBUFFERED=1`,
  `FLARESOLVERR_URL=http://localhost:8191`, `SCHEDULER_PROCESS_MB_LIMIT=8192`
- [ ] **3c** Add install/manage/tail instructions in plist comment header (mirror
  coolbet daemon plist style)
- [ ] **3d** Smoke test `RAILWAY-ELIMINATION-PLIST` — assert plist file exists,
  references `python3`, references `workers.scheduler`, contains `TZ=UTC`

## Phase 4 — Smoke tests

- [ ] **4a** Run `python3 scripts/smoke_test.py --filter RAILWAY-ELIMINATION` — all pass
- [ ] **4b** Run `python3 scripts/smoke_test.py --filter COOLBET-SELFHEAL-DOCKER-FS` —
  verify existing tests still pass (env var rename in coolbet_daily_summary is adjacent
  to Coolbet code)

## Phase 5 — Cutover (operator steps)

- [ ] **5a** Operator: verify Mac sleep disabled — `pmset -g | grep " sleep"` should
  show `sleep 0`. If not: `sudo pmset -a sleep 0`
- [ ] **5b** Operator: in Railway dashboard → service → Settings → pause or stop service
  (don't delete yet)
- [ ] **5c** Operator: install plist —
  ```
  cp local/launchd/com.oddsintel.scheduler.plist ~/Library/LaunchAgents/
  launchctl load ~/Library/LaunchAgents/com.oddsintel.scheduler.plist
  ```
- [ ] **5d** Operator: verify scheduler started — `launchctl list | grep oddsintel`
  should show `com.oddsintel.scheduler` with PID
- [ ] **5e** Operator: tail logs — `tail -f dev/active/scheduler.log` — verify jobs
  start firing (healthcheck ping at :00/:05/:10, ops_snapshot at :30)
- [ ] **5f** Operator: check pipeline_runs table for new scheduler runs (should see
  rows within 5 min)
- [ ] **5g** Monitor for 24h — watch Telegram for any unexpected alerts, check
  healthchecks.io stays green
- [ ] **5h** After clean 24h soak: cancel Railway subscription from dashboard

## Phase 6 — Doc updates

- [ ] **6a** `INFRASTRUCTURE.md` — remove Railway row from cost table, add Mac scheduler
  row (cost: $0, part of existing Mac), update "GitHub Actions Usage" section
- [ ] **6b** `WORKFLOWS.md` — rename "Railway Scheduler" → "Mac Scheduler (launchd)",
  update "Railway vs GitHub Actions" table, rename "Railway Environment Variables"
  section, add note about manual kickstart replacing auto-deploy
- [ ] **6c** `ROADMAP.md` — update Current System State scheduler row
- [ ] **6d** `PRIORITY_QUEUE.md` — mark RAILWAY-ELIMINATION ✅ Done with date
- [ ] **6e** Commit all code + docs changes in one commit

## Commit strategy
Phases 1+3+4: one commit (code cleanup + new plist + smoke tests)
Phase 6: same commit as above if cutover is the same day, or a follow-up doc commit

## Rollback plan
If scheduler fails on Mac during 5g soak:
1. `launchctl unload ~/Library/LaunchAgents/com.oddsintel.scheduler.plist`
2. Unpause Railway service from dashboard
3. Investigate scheduler.log for root cause
4. Fix and retry Phase 5
