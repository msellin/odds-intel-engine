# RAILWAY-ELIMINATION — Tasks

## Phase 1 — Code cleanup ✅

- [x] **1a** `workers/jobs/health_alerts.py` — rename `RAILWAY_POD_MEM_LIMIT_MB` →
  `SCHEDULER_PROCESS_MB_LIMIT`, change default from `1024` to `4096`, update alert body
  text to remove Railway-specific restart instructions → systemctl/launchctl
- [x] **1b** `workers/jobs/coolbet_daily_summary.py` — rename env var
  `COOLBET_DAILY_RAILWAY_STALE_MIN` → `COOLBET_DAILY_SCHEDULER_STALE_MIN`, rename
  "Railway heartbeat" → "Scheduler heartbeat" (lines 44, 177, 194)
- [x] **1c** `workers/scheduler.py` — rename startup banner from "OddsIntel Railway
  Scheduler" to "OddsIntel Pipeline Scheduler", remove Railway-specific comments
- [x] **1d** Smoke test `RAILWAY-ELIMINATION-CODE-CLEAN` — asserts old Railway env var
  names absent, new names present, alert body doesn't say "Railway"

## Phase 2 — Env var audit ✅

- [x] **2a** `railway variables` CLI used to pull full variable list (2026-06-29)
- [x] **2b** Group B flags identified — see context.md "Railway variables audit" section
- [x] **2c** `FLARESOLVERR_URL=https://flaresolverr-cf-production.up.railway.app` confirmed
  in Railway — systemd unit overrides to `http://localhost:8191`
- [x] **2d** Full env delta recorded in context.md

## Phase 3 — Hetzner deployment artifacts ✅

- [x] **3a** `local/systemd/oddsintel-scheduler.service` — systemd unit pointing to
  `python3 -m workers.scheduler`, WorkingDirectory `/opt/odds-intel-engine`, User root,
  TZ=UTC, FLARESOLVERR_URL=http://localhost:8191, SCHEDULER_PROCESS_MB_LIMIT=4096,
  EnvironmentFile for .env, Restart=always RestartSec=30
- [x] **3b** `local/systemd/docker-compose.yml` — Hetzner FlareSolverr, port 8191,
  NO persistent profile volume (HLTV sessions are ephemeral), mem_limit 1536m
- [x] **3c** `local/setup-hetzner.sh` — one-shot install script (system packages, Docker,
  repo clone/pull, pip deps, FlareSolverr start, systemd unit install + enable)
- [x] **3d** Smoke test `RAILWAY-ELIMINATION-SERVICE` — asserts all three files exist,
  systemd unit has correct content (python3, workers.scheduler, TZ=UTC, localhost FS URL),
  docker-compose has 8191 but no persistent profile volume

## Phase 4 — Smoke tests ✅

- [x] **4a** `RAILWAY-ELIMINATION-CODE-CLEAN` passes
- [x] **4b** `RAILWAY-ELIMINATION-SERVICE` passes
- [x] **4c** No regressions in adjacent Coolbet tests

## Phase 5 — Cutover (operator steps) ⬜

### Before starting: populate Hetzner .env

Copy `/opt/odds-intel-engine/.env` to Hetzner with all standard secrets PLUS these
non-default flags from Railway vars audit (context.md):
```
BOT_COHORT_OVERRIDES=bot_opt_away_british:morning,bot_opt_away_europe:morning
COOLBET_MIN_EDGE=0.05
COOLBET_RECORD_ALLOWED_MATURITY=calibrated
COOLBET_STAKE=10.0
COOLBET_USER=sellinmargus@gmail.com
GATE_EVENTS_BY_COVERAGE=true
LEAGUE_EFF_EDGE_BUMP_ENABLED=true
META_B_ML3_ENABLED=true
META_B_ML3_THRESHOLD=0.52
META_B_ML3_VERSION=v_20260607_bets
MODEL_VERSION=v20260621
MODEL_VERSION_OU=v14_recreate_2026_05_11
MODEL_VERSION_OU_T1=v20260607
PIN_CROSS_DRIFT_VETO_ENABLED=true
STAGE2_CALIBRATOR=isotonic
```
Do NOT include: COOLBET_COOKIE_*, COOLBET_IMPERVA_COOKIES, COOLBET_MANUAL_JWT,
COOLBET_PASS, COOLBET_AUTO_EXECUTE, RAILWAY_* vars.

### Cutover steps

- [ ] **5a** Operator: on Hetzner, run `bash local/setup-hetzner.sh` to install all deps
- [ ] **5b** Operator: write `/opt/odds-intel-engine/.env` with the full var list above
- [ ] **5c** Verify FlareSolverr: `curl http://localhost:8191/` → should return JSON
- [ ] **5d** Operator: in Railway dashboard → `pipeline` service → Settings → pause/stop
  (don't delete yet — keep as rollback for 24h)
- [ ] **5e** Operator: `systemctl start oddsintel-scheduler`
- [ ] **5f** Verify scheduler started: `journalctl -u oddsintel-scheduler -f` — watch for
  job registration lines and first healthcheck ping within 5 min
- [ ] **5g** Verify DB activity: check `pipeline_runs` table for new scheduler rows within
  5 min of start
- [ ] **5h** Monitor for 24h — watch Telegram for unexpected alerts, healthchecks.io green
- [ ] **5i** After clean 24h soak: stop Railway FlareSolverr service too, cancel Railway subscription

## Phase 6 — Doc updates ⬜

- [ ] **6a** `INFRASTRUCTURE.md` — remove Railway row, add Hetzner scheduler row
  (€5.49/mo, replaces Railway $5/mo), update service list
- [ ] **6b** `WORKFLOWS.md` — rename "Railway Scheduler" → "Hetzner Scheduler (systemd)",
  update "GitHub Actions vs Railway" table, update env vars section
- [ ] **6c** `ROADMAP.md` — update Current System State scheduler row to "Hetzner VPS"
- [ ] **6d** `PRIORITY_QUEUE.md` — mark RAILWAY-ELIMINATION ✅ Done with date
- [ ] **6e** Delete `dev/active/railway-elimination-*.md` (this dir) after all docs updated
- [ ] **6f** Commit all doc updates

## Rollback plan
If scheduler fails during 5h soak:
1. `systemctl stop oddsintel-scheduler`
2. Unpause Railway `pipeline` service from dashboard
3. `journalctl -u oddsintel-scheduler -b` to get full boot logs
4. Fix root cause, `systemctl start oddsintel-scheduler`, repeat soak
