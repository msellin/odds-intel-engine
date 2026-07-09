# Supabase → VPS Migration — Context

Living doc. Update after each session. New sessions can pick up by reading `plan.md` + this file.

## Current state

**✅ Migration complete 2026-07-09.** See `supabase-migration-status.md` for
the post-cutover runbook, monitoring, rollback plan, and decommission
timeline. Original plan + tasks preserved below and in `tasks.md`.

## Key facts (from 2026-07-08 audit)

### Supabase DB shape
- Total size: **18 GB** in `public`
- Dominant tables: `odds_snapshots` (10 GB / 20.7M rows), `match_signals` (4.8 GB / 17M rows)
- 126 tables, 15 custom functions, 10 triggers, 80+ RLS policies
- Extensions: `pgcrypto`, `uuid-ossp`, `pg_stat_statements`, `hypopg`, `index_advisor`, `supabase_vault` (skip), `plpgsql` (default)
- Supabase-specific schemas: `auth` (52 users), `storage` (1 bucket "models", 222 objects), `realtime` (unused — 0 subscriptions), `vault` (skip), `supabase_migrations` (skip)

### VPS state (`204.168.199.8`)
- Ubuntu 26.04 LTS, 8 vCPU, 15 GiB RAM, 205 GB free disk
- **Postgres 17 already running** on 127.0.0.1:5432 (used by CrossRank)
- `/var/lib/postgresql` at 62 GB — CrossRank data. 18 GB more fits easily.
- Nginx serves 8 sites already (multi-tenant proven)
- pm2 runs: `odds-intel-web` (port 3000), `uptime-kuma` (3005), `box-ranking` (3001 — **27 restarts, unstable**)
- Docker runs: `oi_local_flaresolverr` (8191), `crossrank-postgrest-1`
- **Load avg 2.5, swap 2/2 GiB used** — worth investigating before adding Postgres load

### Frontend Supabase footprint
- 3 client files: `src/lib/supabase-browser.ts`, `src/lib/supabase-server.ts`, `src/lib/supabase-public.ts`
- Auth surface: `login/page.tsx`, `forgot-password`, `reset-password`, `auth/callback/route.ts`, `proxy.ts`, `components/auth-provider.tsx`, `components/google-sign-in.tsx`
- Data queries centralized in `src/lib/engine-data.ts` (113 supabase-related lines) — good, keeps blast radius small
- Zero realtime (`.channel()` / `postgres_changes`) and zero storage usage on frontend
- Service-role bypass in `/api/stripe/webhook`, `/api/resend-webhook`, `/api/admin/*`

### Engine Supabase footprint
- `workers/api_clients/db.py` uses `psycopg2.pool.ThreadedConnectionPool` on `DATABASE_URL` — no PostgREST calls
- `workers/model/storage.py` uses Supabase Storage SDK (stays on Supabase — non-negotiable for Option A)
- `scripts/test_anon_auth_e2e.py` uses REST `/auth/v1` and `/rest/v1` (stays working; auth is unchanged)

### The one weird thing: pg_notify
- Migration `115_coolbet_inplay_snapshots.sql`: trigger on `simulated_bets` `pg_notify`s channel `inplay_bet_fired`
- Consumer: `workers/automation/coolbet_inplay.py` (Python, runs on operator Mac)
- Currently connects via Supabase pooler which is known-flaky for LISTEN
- Post-migration will connect via Cloudflare TCP tunnel to VPS Postgres — likely more reliable

## Reference material

- **CrossRank runbook (primary template)**: `/Users/margussellin/www/crossfit-ranking/deploy/hetzner-migration-runbook.md`
- **CrossRank templates ready to copy**:
  - `deploy/postgresql-tuning.conf`
  - `deploy/postgrest-compose.yml`
  - `deploy/backup-cron.sh`
  - `deploy/cutover-env-changes.md`
- **VPS Next.js runbook**: `docs/VPS_NEXTJS_MIGRATION_RUNBOOK.md` (already in repo)
- **PORT_REGISTRY**: 3000 = odds-intel-web, 3001 = box-ranking, 3002 = *available* → **grab 3002 for oddsintel PostgREST**, 3005 = uptime-kuma, 8191 = flaresolverr

## Decisions (finalized 2026-07-08 after deep VPS investigation)

- **Option A (data only)** — keep Supabase Auth + Storage. Confirmed 2026-07-08 by Margus. Reason: other projects share Supabase Auth; migrating auth would be 2–3 weeks of work with password-hash portability risk.
- **Env vars: `NEXT_PUBLIC_POSTGREST_URL`, `NEXT_PUBLIC_POSTGREST_ANON_KEY`, `POSTGREST_SERVICE_KEY`** — mirror CrossRank naming. Do not re-point `NEXT_PUBLIC_SUPABASE_URL`.
- **Two-client frontend** — `authClient` (Supabase) + `dataClient` (VPS PostgREST). **Not** JWT passthrough — PostgREST mints its own tokens (CrossRank pattern verified live).
- **Per-user data enforcement moves to app layer.** 15+ tables use `USING (auth.uid() = user_id)` RLS. Under CrossRank pattern, service_role bypasses RLS, so every server-side per-user query MUST add `.eq('user_id', session.user.id)` explicitly. RLS stays in the DB as defense-in-depth but is no longer the enforcement layer for us. Grep audit list in `tasks.md` SM-4.7.
- **External exposure: nginx port 80** (CrossRank pattern), not Cloudflare Tunnel. Cloudflare provides Flexible SSL. Verified: `/etc/nginx/sites-enabled/api` on VPS = `api.crossrank.ee` → 127.0.0.1:3010 with `/rest/v1/*` rewrite. Copy exactly for `api.<oddsintel-domain>` → 127.0.0.1:3012.
- **Operator Mac reaches Postgres via SSH tunnel** (`autossh -L 5433:localhost:5432 root@vps`), not Cloudflare TCP tunnel. Native LISTEN/NOTIFY works fine over SSH tunnel (protocol-level).
- **PostgREST port 3012** in the shared cluster (CrossRank uses 3010, 3011 is nginx loopback).
- **Postgres roles already exist**: `anon`, `authenticated`, `service_role`, `web_anon` — cluster-wide from CrossRank install. Only need to add `oddsintel_owner`.
- **Cutover window** — 03:00 UTC, ~30–45 min gap. Scheduler + live tracker + settlement all idle.

## Env vars inventory

| Var | Where used | Change? |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | frontend (auth client only, after refactor) | Unchanged |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | frontend (auth client only) | Unchanged |
| `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_SECRET_KEY` | frontend webhooks + admin, engine `workers/model/storage.py` | Unchanged |
| `NEXT_PUBLIC_DATA_API_URL` | **new** — frontend data client | New value: `https://oi-api.<domain>` |
| `DATABASE_URL` | engine (`workers/api_clients/db.py`), operator Mac daemons | New value: `postgres://oddsintel_owner:<pw>@oi-db.<domain>:5432/oddsintel` |
| `SUPABASE_URL` | engine (`workers/model/storage.py` — Storage only) | Unchanged |

## Next steps

1. Resolve open questions in `plan.md` (scheduler location, swap, box-ranking crashes)
2. Get Supabase project JWT secret from Dashboard → API → JWT Secret
3. Get sign-off on plan
4. Start Phase 0 tasks in `tasks.md`

## Verified on VPS (SSH 2026-07-08 20:44 UTC)

Post-plan-draft I logged into `204.168.199.8` directly to sanity-check the agent's audit numbers and answer Q1–Q4. Results:

**Q1 resolved — scheduler location**
- Runs as systemd unit `odds-scheduler.service` (enabled, active running 12h+)
- ExecStart: `/opt/odds-intel-engine/venv/bin/python workers/scheduler.py`
- Config file: `/etc/systemd/system/odds-scheduler.service`
- Memory: 16.6 MB (peak 746 MB), swap: 463.7 MB — **the swap holder is the scheduler**, not a leak (Q2 also mostly resolved)

**New blocker (SM-0.8) — duplicate scheduler in crash loop**
- A **second** unit `oddsintel-scheduler.service` (note the `intel-`) is also enabled and stuck in `activating (auto-restart)` — retries every ~10s
- Error: `OSError: [Errno 98] Address already in use` on the health port (2403 in `_start_health_server`), because the healthy `odds-scheduler.service` already binds it
- Both units point to the same code (`/opt/odds-intel-engine/workers/scheduler.py`). One is a leftover from a previous naming attempt.
- Impact: burns ~156ms CPU per restart, contributes to load avg 4.5, generates journal spam
- **Fix before migration**: `systemctl disable --now oddsintel-scheduler.service`

**Q2 partially resolved — swap 2/2 GiB**
- Not a leak. Distribution: 8 postgres worker RSS at 4.8 GB / 4.5 GB / 4.0 GB / 3.8 GB / 2.0 GB (these overlap in shared_buffers, so real cost is lower) + odds-scheduler 464 MB + FlareSolverr chromium 186 MB + next-server 351 MB
- With Postgres running `shared_buffers=4GB` + all other services, 15 GiB RAM is tight. Migration will add ~200 MB of PostgREST + a bit more Postgres cache pressure. **Fine but no headroom** — worth watching first weeks.

**Q3 RESOLVED — box-ranking 28 restarts are clean starts**
- `pm2 logs box-ranking --err` — empty. No errors.
- `pm2 logs box-ranking --out` — every restart logs `▲ Next.js 16.2.10 - Local: http://localhost:3001 - ✓ Ready in 103ms`. Each start is clean.
- Something external (probably a deploy workflow hook or `pm2 save`/resurrect triggered by another process) is restarting it. Not a crash loop.
- **Non-blocking for migration.**

**Q4 RESOLVED — Supabase JWT secret NOT NEEDED**
- Deep-dived into CrossRank's live PostgREST config on VPS. `PGRST_JWT_SECRET` there is a fresh random 64-char string, NOT Supabase's project JWT secret. CrossRank does not do JWT passthrough.
- Their pattern: PostgREST mints its own anon + service_role tokens with the fresh secret. Frontend sends anon JWT for public reads; server-side code uses service_role JWT for anything user-scoped and does app-level `user_id` filtering.
- Adopting this pattern eliminates the Supabase secret dependency entirely.
- Trade-off: 15+ tables' `auth.uid() = user_id` RLS becomes advisory (service_role bypasses). App must enforce user_id filter — see SM-4.7 audit list.

**Postgres already tuned to CrossRank template — skip Phase 1.2**
- `shared_buffers = 4 GB` ✓
- `effective_cache_size = 12 GB` ✓
- `work_mem = 32 MB` ✓
- `maintenance_work_mem = 1 GB` ✓
- `max_connections = 100` (16 in use by crossrank, huge headroom) ✓
- `listen_addresses = localhost` ✓
- **We reuse the existing cluster** — do NOT stand up a second Postgres instance. Add `oddsintel` DB alongside `crossrank` (66 GB) in the same cluster.

**Existing Postgres state**
- DBs: `crossrank 66 GB`, `template0/1`, `postgres` (system) — no `oddsintel` yet
- Extensions available: need to verify but pgcrypto/uuid-ossp/pg_stat_statements likely already loaded from CrossRank install
- Active connections: 16 on `crossrank`, 1 on `postgres`

**Disk / capacity**
- `/dev/sda1`: 301 GB total, 95 GB used (33%), **193 GB free**
- Adding 18 GB of `oddsintel` → 113 GB used. Room for ~3× growth over 2 years without concern.

**Other services on box (all healthy except duplicate scheduler)**
- pm2: `odds-intel-web` (3000, 7h, 2 restarts), `uptime-kuma` (3005, 12h, 0), `box-ranking` (3001, 4m, 28 — externally kicked, clean starts)
- Docker: `crossrank-postgrest-1` (port 3010, 8h up), `oi_local_flaresolverr` (8191, healthy)
- Nginx sites (8 total): `api` (=api.crossrank.ee → 3010), `boxranking`, `crossrank`, `crossrank-api-loopback` (127.0.0.1:3011 → 3010), `netdata`, `oddsintel-web` (→ 3000), `postgrest-local` (127.0.0.1:3011 → 3010, rewrites /rest/v1/*), `status-oddsintel`
- systemd timers: 4 crossrank timers already firing (refresh, gate-scorez, site-stats, triage)
- **No `cloudflared`** — CrossRank exposes PostgREST via nginx port 80 (verified from `/etc/nginx/sites-enabled/api`). We copy this exactly.

**Existing PostgREST config on VPS (for cloning)**
- Image `postgrest/postgrest:v12.2.3`
- Host networking, port 3010
- Env: `PGRST_DB_URI` → `postgres://crossrank_owner:pw@localhost:5432/crossrank`, `PGRST_DB_SCHEMAS=public,box`, `PGRST_DB_ANON_ROLE=web_anon`, `PGRST_JWT_SECRET=<64-char random, NOT Supabase's>`, `PGRST_DB_POOL=20`, `PGRST_DB_MAX_ROWS=10000`
- For oddsintel we clone with our own DB URI + generated JWT secret on port 3012

## Session log

### 2026-07-08 — Initial audit + plan draft + VPS verification + deep dive
- Ran 4-agent audit: engine coupling, frontend coupling, crossrank precedent, VPS + DB volume
- Drafted `plan.md`, `context.md`, `tasks.md` in `dev/active/`
- SSH'd into VPS directly, resolved Q1, resolved Q2, found new blocker SM-0.8 (duplicate scheduler crash loop)
- **Deep dive into CrossRank's live PostgREST + nginx config**:
  - Confirmed nginx-on-port-80 pattern (no Cloudflare tunnel)
  - Confirmed PostgREST mints its own JWT (not Supabase passthrough) → pivoted plan to same pattern
  - Cataloged existing Postgres roles (anon/authenticated/service_role/web_anon reusable)
- Measured frontend refactor scope: 174 `.from(...)` sites, 101 in one file (`engine-data.ts`)
- Identified 15 tables with `auth.uid() = user_id` RLS → require app-layer enforcement under CrossRank pattern
- **All blockers resolved. Plan is 100% ready to execute.**
- Not started implementation. Waiting on go-ahead.
