# RAILWAY-ELIMINATION — Context

## Key decisions made

**Scheduler host: Hetzner VPS (systemd), not Mac**
Operator already has a Hetzner VPS: 2 vCPU / 4 GB RAM / 40 GB disk / €5.49/mo. Moving the
scheduler there costs the same as Railway but removes the Railway dependency entirely. The Mac
stays as the Coolbet placement daemon (CDP-Chrome, local FlareSolver for Coolbet) — it is NOT
the scheduler host.

**Architecture: two services on Hetzner**
1. `oddsintel-scheduler` systemd unit — long-running Python process (`python3 -m workers.scheduler`)
2. FlareSolverr Docker container — replaces Railway-hosted FS for HLTV/CS2 scraping

**Mac role unchanged**
Mac keeps: coolbet_mac_daemon (launchd), local FlareSolver for Coolbet (Docker on port 8191).
These are unaffected by this migration.

**Memory sizing: 4 GB is fine**
Heavy jobs (weekly ML retrain, CS2 jobs) all use `subprocess.run()` — they don't accumulate in
scheduler RSS. Normal in-process peak is ~400-800 MB. Alert threshold: SCHEDULER_PROCESS_MB_LIMIT=4096
(warns at 85% of 4 GB = ~3.4 GB). No VPS upgrade needed.

**No Docker for the scheduler itself**
Scheduler runs as a native Python process via systemd, same model as Mac launchd. Simpler and
avoids container-layer debugging.

**load_dotenv() handles most credentials**
`workers/scheduler.py` calls `load_dotenv()` at startup. All vars in `/opt/odds-intel-engine/.env`
are automatically available. Only vars NOT in `.env` (TZ, FLARESOLVERR_URL override, memory limit)
are set in the systemd unit's `Environment=` lines.

**TZ=UTC pinned in systemd unit**
APScheduler is initialized with `timezone="UTC"`. Set `TZ=UTC` in Environment= to match Railway.

**FLARESOLVERR_URL override in systemd unit**
.env may carry the old Railway FS URL. Systemd `Environment=FLARESOLVERR_URL=http://localhost:8191`
overrides it — processed after EnvironmentFile= so it always wins.

**FlareSolverr: no persistent profile on Hetzner**
Mac FlareSolver uses a persistent Chrome profile volume to preserve Coolbet device trust cookies.
Hetzner FlareSolver serves HLTV/CS2 only — sessions are torn down every 6h by FS-AUTO-RECOVER-HLTV,
so persistence would just accumulate stale state. No volume mount.

**coolbet_state._default_set_by() needs no change**
Returns "local" when no RAILWAY_PROJECT_ID is set. Correct for Hetzner. JWT set_by tags will
show "local" instead of "railway" — accurate, not a bug.

**Railway auto-deploy replaced by manual pull + restart**
After migration, scheduler-affecting code changes require on Hetzner:
```
cd /opt/odds-intel-engine && git pull && pip3 install -r requirements.txt -q
systemctl restart oddsintel-scheduler
```

**Cutover order: stop Railway first, then start Hetzner**
Avoid double-job execution. Railway paused → Hetzner systemctl start → verify → 24h soak → cancel.

## Railway variables audit (2026-06-29)

**Must carry to Hetzner .env (non-default feature flags / config):**
- `BOT_COHORT_OVERRIDES=bot_opt_away_british:morning,bot_opt_away_europe:morning`
- `COOLBET_MIN_EDGE=0.05`
- `COOLBET_RECORD_ALLOWED_MATURITY=calibrated`
- `COOLBET_STAKE=10.0`
- `COOLBET_USER=sellinmargus@gmail.com`
- `GATE_EVENTS_BY_COVERAGE=true`
- `LEAGUE_EFF_EDGE_BUMP_ENABLED=true`
- `META_B_ML3_ENABLED=true`
- `META_B_ML3_THRESHOLD=0.52`
- `META_B_ML3_VERSION=v_20260607_bets`
- `MODEL_VERSION=v20260621`
- `MODEL_VERSION_OU=v14_recreate_2026_05_11`
- `MODEL_VERSION_OU_T1=v20260607`
- `PIN_CROSS_DRIFT_VETO_ENABLED=true`
- `STAGE2_CALIBRATOR=isotonic`

**Already in .env (standard secrets) — verify values match:**
- `ADMIN_ALERT_EMAIL`, `API_FOOTBALL_KEY`, `DATABASE_URL`, `DIGEST_FROM_EMAIL`
- `FLARESOLVERR_URL` (systemd override will win regardless)
- `GEMINI_API_KEY`, `GRID_API_KEY`, `HEALTHCHECKS_IO_PING_URL`
- `OA_KEY`, `PANDASCORE_API_KEY`, `RESEND_API_KEY`
- `SITE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_URL`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_PUBLIC_CHANNEL`, `TELEGRAM_WEBHOOK_SECRET`

**Drop (Mac-only — not needed on Hetzner):**
- `COOLBET_COOKIE_*`, `COOLBET_IMPERVA_COOKIES`, `COOLBET_MANUAL_JWT` — browser automation cookies
- `COOLBET_PASS` — Coolbet login, used by mac daemon only
- `COOLBET_AUTO_EXECUTE` — not referenced in any Python code

**Drop (Railway-internal — meaningless outside Railway):**
- `RAILWAY_*`, `PORT`, `RAILWAY_PRIVATE_DOMAIN`

## Key files

| File | Role |
|------|------|
| `workers/scheduler.py` | The process to migrate — 3423 lines, 114+ jobs |
| `local/systemd/oddsintel-scheduler.service` | Systemd unit for Hetzner (Phase 3) |
| `local/systemd/docker-compose.yml` | FlareSolverr for Hetzner — no persistent profile (Phase 3) |
| `local/setup-hetzner.sh` | One-shot install script for Hetzner VPS (Phase 3) |
| `local/flaresolverr/docker-compose.yml` | Mac FlareSolver (Coolbet only — unchanged) |
| `local/launchd/com.oddsintel.coolbet-mac-daemon.plist` | Mac Coolbet daemon (unchanged) |

## State as of 2026-06-29

- Phase 1 ✅ complete (code cleanup committed)
- Phase 2 ✅ complete (Railway vars audited, Group B flags catalogued above)
- Phase 3 ✅ complete (systemd unit + docker-compose + setup script created)
- Phase 4: smoke tests pass (RAILWAY-ELIMINATION-CODE-CLEAN + RAILWAY-ELIMINATION-SERVICE)
- Phase 5: pending operator — needs Hetzner .env populated and Railway stopped
- Phase 6: pending cutover
