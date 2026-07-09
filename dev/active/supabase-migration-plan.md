# Supabase → VPS Migration Plan (Option A — data only)

**Status:** Ready to execute
**Owner:** Margus + Claude
**Precedent:** CrossRank + BoxRank, migrated 2026-07-07/08 (see `/Users/margussellin/www/crossfit-ranking/deploy/hetzner-migration-runbook.md`)
**Target window:** July 2026 (low season)
**Est. effort:** ~3–4 focused days + 1-week observation
**Last audit:** 2026-07-08, live SSH into `204.168.199.8`

## Goal

Move all application data from Supabase Postgres to self-hosted Postgres 17 on the existing Hetzner VPS (`204.168.199.8`). Keep Supabase Auth and Supabase Storage in place. Rationale: DB is the expensive/limiting piece; auth works fine on the Supabase free tier; other projects share the same Supabase Auth.

## Non-goals

- Not migrating `auth.*` schema. Login, signup, OAuth (Google/Discord), magic links, password reset, cookie sessions stay on Supabase.
- Not migrating Supabase Storage (models bucket, 222 objects — used by `workers/model/storage.py` on VPS + Mac).
- Not building a NextAuth / Lucia / Clerk replacement.
- Not touching bot execution logic, model logic, or pipeline stages beyond DB re-pointing.

## Scope

### What moves

- Entire `public` schema — 126 tables, ~18 GB, 15 custom functions, 10 triggers, 80+ RLS policies
- Engine `DATABASE_URL` consumers (VPS + operator Mac daemons)
- Frontend data reads (currently `supabase.from(...)` via `@supabase/ssr` client with anon key + RLS)
- `pg_notify` on `simulated_bets` + LISTEN consumer (`workers/automation/coolbet_inplay.py`)

### What stays on Supabase

- `auth.*` schema (52 users, GoTrue tables, hashed passwords)
- `storage.*` schema (models bucket)
- Supabase Auth API (login, OAuth, password reset endpoints)
- `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` — repurposed for the **auth client only**
- `SUPABASE_URL` + `SUPABASE_SECRET_KEY` in engine — kept for `workers/model/storage.py` only

## VPS state (verified live 2026-07-08 20:44 UTC)

- Ubuntu 26.04 LTS, 8 vCPU, 15 GiB RAM, **193 GB disk free**
- Postgres 17.10, cluster running, **already tuned** to CrossRank template (shared_buffers=4GB, effective_cache_size=12GB, work_mem=32MB, maintenance_work_mem=1GB, max_connections=100)
- Only DB in cluster: `crossrank` (66 GB, 16 active connections). Adding `oddsintel` alongside = one cluster.
- Roles already exist: `anon`, `authenticated`, `service_role`, `web_anon`, `box_writer`, `crossrank_owner`, `postgres`. Need to add `oddsintel_owner` only.
- Extensions available: `pgcrypto`, `uuid-ossp`, `pg_stat_statements`, `hypopg`, `pg_trgm`, `unaccent`. Currently cluster-wide: only `plpgsql`. Install per-DB during Phase 1.
- Nginx serves 8 sites already; ports 3000/3001/3005/3020 taken by Next.js apps + uptime-kuma; **3002 free** for oddsintel PostgREST; 3010 used by CrossRank PostgREST; 3011 loopback nginx.
- No `cloudflared` — CrossRank exposes PostgREST externally via **nginx on port 80** (`api.crossrank.ee` → 127.0.0.1:3010) with Cloudflare Flexible SSL. **We copy this pattern exactly.**
- Scheduler runs as `odds-scheduler.service` (systemd, `/opt/odds-intel-engine/venv/bin/python workers/scheduler.py`). Also a duplicate `oddsintel-scheduler.service` in a crash-restart loop → disable it before starting.
- box-ranking pm2 has 28 restarts but starts cleanly every time ("Ready in 103ms" logs, no errors) — appears to be a hot-deploy artifact, non-blocking.

## Architecture — before / after

**Before**
```
Frontend  ─ NEXT_PUBLIC_SUPABASE_URL ──► Supabase (Auth + PostgREST + Storage + Data, RLS via auth.uid())
Engine (VPS)   ─ DATABASE_URL ─► Supabase pooler (5432/6543)
Engine (Mac)   ─ DATABASE_URL ─► Supabase pooler
```

**After (Option A, adopting CrossRank pattern)**
```
Frontend Auth Client  ─► Supabase Auth (unchanged: login, OAuth, session cookie via @supabase/ssr)
Frontend Data Client  ─► https://api.oddsintel.<domain> (nginx → PostgREST on 127.0.0.1:3012)
Engine (VPS)          ─► postgres://oddsintel_owner:pw@localhost:5432/oddsintel  (direct, on-box)
Engine (Mac daemons)  ─► postgres://oddsintel_owner:pw@localhost:5433/oddsintel  (via autossh -L)
Model bundle Storage  ─► Supabase Storage (unchanged)
```

Key change from initial draft: we adopt **CrossRank's mint-our-own-PostgREST-JWT pattern** rather than trying to pass Supabase Auth's JWT through. Details in "The tricky bits" below.

## The tricky bits (finalized)

### 1. JWT strategy — PostgREST has its own secret (CrossRank pattern)

**Not doing:** PGRST_JWT_SECRET = Supabase's JWT secret with passthrough. That would preserve `auth.uid()` RLS unchanged but requires fetching Supabase's JWT secret and coupling us to Supabase's rotation policy.

**Doing:** PostgREST has its own generated HS256 secret. We mint two long-lived JWTs baked into env:

- `NEXT_PUBLIC_POSTGREST_ANON_KEY` — role=`anon`, no expiry, embedded in browser bundle
- `POSTGREST_SERVICE_KEY` — role=`service_role`, no expiry, server-only

This matches how CrossRank / BoxRank ship. Verified live: their `PGRST_JWT_SECRET` is a fresh 64-char random string, not Supabase's.

### 2. RLS: 15+ tables use `auth.uid() = user_id` — how do we preserve them?

The 15+ tables with `USING (auth.uid() = user_id)` policies (user_match_favorites, wc_bracket_predictions, wc_group_predictions, weekly_digest_log, email_digests, real_bets, watchlist_alerts, inplay_bot_stats, profiles, accessible_bookmakers, wc_user_picks, wc_email_log, WC anon hardening in 235) can be handled two ways:

**Chosen approach — server-side filter with service_role JWT:**
- Every per-user data read happens in a Next.js server component or route handler.
- Server reads `session.user.id` from Supabase Auth cookie.
- Server calls PostgREST with `service_role` key + explicit `.eq('user_id', session.user.id)`.
- RLS still exists in the DB as defense-in-depth but is bypassed by service_role.

**Why this works for us:**
- Frontend audit confirmed: all per-user reads (`profiles`, `user_match_favorites`, WC picks) happen server-side already.
- We already use service-role bypass for `/api/stripe/webhook`, `/api/resend-webhook`, `/api/admin/*`. Same pattern extends.
- Client bundle never sees `service_role` key (server-only env).

**Cost:** One line audit at every server-side per-user query to ensure the `user_id` filter is explicit. 15 tables × maybe 3–5 sites each = ~50 sites to verify. Grep enforcement possible via ESLint rule.

### 3. Two-client frontend

Split the current combined clients into:
- `authClient` (`createBrowserAuthClient` / `createServerAuthClient`) — points at `NEXT_PUBLIC_SUPABASE_URL`, used only for `auth.*` calls, session cookies, OAuth
- `dataClient` (`createBrowserDataClient` / `createServerDataClient` / `createPublicDataClient` / `createServiceDataClient`) — points at `NEXT_PUBLIC_POSTGREST_URL`, uses `NEXT_PUBLIC_POSTGREST_ANON_KEY` by default; server routes can swap in `POSTGREST_SERVICE_KEY` for admin operations.

**Refactor size (measured):** 174 `.from(...)` call sites, but **101 in one file** (`src/lib/engine-data.ts`) — the data layer. Refactoring `engine-data.ts` to use a module-level `dataClient()` factory hits 58% of call sites in a single edit. Remaining 73 are in ~15 route handlers + admin pages, ~1 day mechanical work.

### 4. External exposure — nginx on port 80, not Cloudflare Tunnel

CrossRank pattern (verified on VPS):
- Nginx `api` site: `api.crossrank.ee` listen 80 → proxy_pass `127.0.0.1:3010` (their PostgREST)
- Cloudflare DNS A record → VPS public IP
- Cloudflare SSL/TLS in **Flexible** mode (Cloudflare⇔browser HTTPS, Cloudflare⇔VPS HTTP)
- Also a loopback-only site `postgrest-local` on `127.0.0.1:3011` for VPS-local apps (BoxRank Next.js)

**For OddsIntel:**
- New nginx site: `api.oddsintel.<domain>` listen 80 → proxy_pass `127.0.0.1:3012` (oddsintel PostgREST)
- Nginx rewrites `/rest/v1/*` → `/*` (mimics Supabase URL layout so `@supabase/supabase-js` client works unchanged)
- Loopback nginx (`postgrest-local` site) already exists; add an `upstream oddsintel_postgrest 127.0.0.1:3012` + a second server block on `127.0.0.1:3013` if we want VPS-local apps to hit it (optional; the frontend hits it externally anyway)

### 5. Operator Mac daemon access — SSH tunnel with autossh

VPS Postgres is `listen_addresses = localhost` and stays that way (security). Operator Mac daemons need external access:
- `autossh -M 0 -f -N -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -L 5433:localhost:5432 root@204.168.199.8`
- Runs as launchd service (canonical for Mac daemons per existing memory)
- Mac daemon `DATABASE_URL=postgres://oddsintel_owner:pw@localhost:5433/oddsintel`
- LISTEN/NOTIFY works fine through SSH tunnel (protocol-level, tunnel doesn't affect it)

### 6. `pg_notify inplay_bet_fired` LISTEN consumer

- Producer: trigger on `simulated_bets` (migration 115)
- Consumer: `workers/automation/coolbet_inplay.py` on operator Mac
- Currently connects to Supabase pooler which is known-flaky for LISTEN (Supabase pooler in "transaction" mode kills long-lived connections)
- Post-migration: direct SSH tunnel → VPS Postgres. Native LISTEN, no session pooling. **This will be more reliable, not less.**
- Test: from Mac tunnel `psql`, `LISTEN inplay_bet_fired;` in one shell; from VPS `psql`, `NOTIFY inplay_bet_fired, 'test';` — Mac shell should print notification within milliseconds.

### 7. Backfill window

Odds ingestion every 30 min (07–22 UTC). Live tracker 30s–5min (10–23 UTC). Cutover happens **03:00 UTC** — outside all cron schedules. Estimated total gap: **30–45 min**.

### 8. Two-phase deploy

Frontend refactor and data-source cutover are **decoupled**:

- **Phase 4** — refactor two-client frontend, deploy with `NEXT_PUBLIC_POSTGREST_URL` initially pointing at **Supabase**. This proves the refactor doesn't break anything. Site keeps running on Supabase.
- **Phase 6** — flip only `NEXT_PUBLIC_POSTGREST_URL` env to VPS PostgREST. One env change + pm2 restart. Rollback = revert env + pm2 restart (~2 min).

Two independent risk windows, one at a time.

## Phased plan

### Phase 0 — Prep (0.5 day)

- [x] Locate scheduler — DONE (`odds-scheduler.service`)
- [x] Investigate swap — DONE (not a leak; Postgres + apps just fit in 15 GiB)
- [x] Verify Postgres already tuned — DONE
- Disable duplicate `oddsintel-scheduler.service` (systemd unit in crash-restart loop): `systemctl disable --now oddsintel-scheduler.service`
- Investigate `box-ranking` pm2 28-restart mystery (non-blocking; logs show clean starts). Look at deploy workflow — likely a pm2 restart triggered by CI hook.
- Snapshot Supabase DB baseline: `pg_database_size` + row counts for 30 largest tables → `dev/active/supabase-migration-baseline.md`
- Snapshot all `.env` values (local, VPS engine + web, GitHub Actions secrets, operator Mac). Store in 1Password, not repo.
- Add PRIORITY_QUEUE.md entry

### Phase 1 — VPS Postgres setup (0.25 day)

- [x] Postgres 17 running, tuned — already done
- Create role `oddsintel_owner LOGIN PASSWORD '<strong random>' CREATEDB`; grant `anon`, `authenticated`, `service_role`, `web_anon` to `oddsintel_owner`
- Create database `oddsintel` owner=`oddsintel_owner` in existing cluster
- `CREATE EXTENSION` (in `oddsintel` DB): `pgcrypto`, `uuid-ossp`, `pg_stat_statements`, `hypopg`, `pg_trgm`, `unaccent` (skip `supabase_vault`, `index_advisor`)
- Local test: `psql postgres://oddsintel_owner:pw@localhost:5432/oddsintel -c '\l'` on VPS

### Phase 2 — Trial dump/restore (0.5 day, non-destructive)

- On local Mac (or wherever `pg_dump` reaches Supabase pooler):
  ```
  pg_dump "$SUPABASE_DB_URL" --format=custom --no-owner --no-acl \
    --exclude-schema=auth --exclude-schema=storage --exclude-schema=realtime \
    --exclude-schema=vault --exclude-schema=supabase_migrations \
    --exclude-schema=graphql --exclude-schema=graphql_public \
    --exclude-schema=extensions --exclude-schema=pgsodium --exclude-schema=pgsodium_masks \
    --jobs=4 --file=oddsintel-trial-$(date +%Y%m%d-%H%M).dump
  ```
- `scp` to VPS `/tmp/`
- Restore: `sudo -u postgres pg_restore --dbname=oddsintel --jobs=8 --no-owner --no-acl --role=oddsintel_owner /tmp/oddsintel-trial-*.dump`
- Row-count parity check (30 largest tables) → `dev/active/supabase-migration-parity.csv`
- Verify: 15 functions (`\df public.*`), 10 triggers (`information_schema.triggers`), 80+ RLS policies (`pg_policies`)
- Time it end-to-end (this sets the cutover window budget)
- Test LISTEN/NOTIFY roundtrip via SSH tunnel from Mac

### Phase 3 — PostgREST on VPS (0.25 day)

- Copy `/opt/crossrank/docker-compose.yml` structure → `/opt/oddsintel/docker-compose.yml`
- Configure:
  ```yaml
  services:
    postgrest:
      image: postgrest/postgrest:v12.2.3
      restart: unless-stopped
      network_mode: host
      environment:
        PGRST_DB_URI: "postgres://oddsintel_owner:pw@localhost:5432/oddsintel"
        PGRST_DB_SCHEMAS: public
        PGRST_DB_ANON_ROLE: anon
        PGRST_JWT_SECRET: "<generate: openssl rand -base64 48>"
        PGRST_DB_POOL: 20
        PGRST_DB_POOL_ACQUISITION_TIMEOUT: 10
        PGRST_LOG_LEVEL: info
        PGRST_DB_MAX_ROWS: 10000
        PGRST_SERVER_PORT: 3012
  ```
- Generate anon + service_role JWTs (long-lived, no expiry) using PGRST_JWT_SECRET. Node one-liner or `jwt-cli`.
- `docker compose up -d`, tail logs
- Write `/etc/nginx/sites-available/oddsintel-api`:
  ```nginx
  upstream oddsintel_postgrest {
      server 127.0.0.1:3012 max_fails=0;
      keepalive 64;
  }
  server {
      listen 80;
      server_name api.<oddsintel-domain>;
      proxy_buffer_size 128k;
      proxy_buffers 4 256k;
      client_max_body_size 25M;
      location /rest/v1/ {
          rewrite ^/rest/v1/(.*)$ /$1 break;
          proxy_pass http://oddsintel_postgrest;
          proxy_set_header Host $host;
      }
      location / {
          proxy_pass http://oddsintel_postgrest;
          proxy_set_header Host $host;
      }
  }
  ```
- Enable, `nginx -t && systemctl reload nginx`
- Cloudflare DNS: A record `api.<oddsintel-domain>` → 204.168.199.8, Proxied, SSL Flexible
- Smoke: `curl -H "Authorization: Bearer <anon_jwt>" https://api.<oddsintel-domain>/rest/v1/matches?limit=1` → JSON

### Phase 4 — Frontend two-client refactor (1 day)

**In odds-intel-web repo. No cutover — deploy against Supabase.**

- Add env vars (initial values point at Supabase to prove refactor):
  ```
  NEXT_PUBLIC_POSTGREST_URL=$NEXT_PUBLIC_SUPABASE_URL
  NEXT_PUBLIC_POSTGREST_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY
  POSTGREST_SERVICE_KEY=$SUPABASE_SERVICE_ROLE_KEY
  ```
- Rewrite `src/lib/supabase-browser.ts` → export `createBrowserAuthClient()` (uses `SUPABASE_URL/ANON_KEY`) + `createBrowserDataClient()` (uses `POSTGREST_URL/ANON_KEY`)
- Rewrite `src/lib/supabase-server.ts` → export `createServerAuthClient()` + `createServerDataClient()` + `createServerServiceClient()` (service_role)
- Rewrite `src/lib/supabase-public.ts` → export `createPublicDataClient()` (uses POSTGREST vars)
- **The big edit**: `src/lib/engine-data.ts` (101 call sites) — change the module-level client import to `createServerDataClient()` or `createPublicDataClient()` depending on function; all `.from(...)` calls flow through. Single-file diff.
- Update ~15 route handlers + 3 admin pages to use `createServerDataClient()` for reads, `createServerServiceClient()` for writes needing bypass
- Preserve `auth-provider.tsx`, `login/page.tsx`, `auth/callback/route.ts`, `proxy.ts` unchanged (still use auth clients)
- `npm run build` + `npm run typecheck` clean
- Deploy to VPS (pm2 restart odds-intel-web). Full manual regression: sign-in, magic link, OAuth, password reset, tier gating (free/pro/elite), `/value-bets`, `/matches/[id]`, admin pages, Stripe checkout
- **If green: refactor is validated. Cutover happens later in Phase 6.**

### Phase 5 — Engine + operator Mac daemon re-point (0.5 day)

- VPS engine: update `/opt/odds-intel-engine/.env` `DATABASE_URL` → `postgres://oddsintel_owner:pw@localhost:5432/oddsintel`. Do NOT restart scheduler yet (Phase 6).
- Operator Mac: install `autossh` (`brew install autossh`), create launchd plist for the tunnel, verify `localhost:5433` reaches VPS Postgres
- Update Mac `.env` `DATABASE_URL` → `postgres://oddsintel_owner:pw@localhost:5433/oddsintel`. Do NOT restart daemons yet.
- Run smoke: `python3 scripts/smoke_test.py --filter DB` locally (with Mac tunnel active + `.env` pointing at VPS)
- Verify LISTEN/NOTIFY roundtrip via tunnel (`workers/automation/coolbet_inplay.py` in test mode)

### Phase 6 — Cutover night (30–45 min window at 03:00 UTC)

1. Announce start
2. Stop scheduler on VPS: `systemctl stop odds-scheduler.service`
3. Stop Mac daemons: `launchctl unload ...`
4. Verify no writes hitting Supabase for 2 min (`SELECT max(created_at) FROM odds_snapshots`)
5. Drop and recreate VPS `oddsintel` DB (clean slate)
6. Final dump from Supabase (same command as Phase 2)
7. `scp` to VPS
8. `pg_restore`
9. Row-count parity (must match to the row)
10. Update `NEXT_PUBLIC_POSTGREST_URL` on VPS `/opt/odds-intel-web/.env.production.local` → `https://api.<oddsintel-domain>`
11. Update `NEXT_PUBLIC_POSTGREST_ANON_KEY` and `POSTGREST_SERVICE_KEY` env values to the JWTs generated in Phase 3
12. `pm2 restart odds-intel-web`
13. Start scheduler pointing at VPS DB: `systemctl start odds-scheduler.service`
14. Start Mac daemons pointing at tunnel
15. Smoke: site loads, `/value-bets` loads, sign-in works, one new `odds_snapshots` row in VPS DB within 15 min, `pipeline_runs` new row

### Phase 7 — Observation (1 week)

- Uptime Kuma HTTP probe for `https://api.<oddsintel-domain>/rest/v1/matches?limit=1`
- Daily check: `pg_stat_statements` top-10 slow queries; add indexes if needed
- Daily check: VPS load avg + swap
- Daily check: pm2 restart counts
- Daily check: scheduler cycle completed with no errors

### Phase 8 — Backups + observability (0.25 day)

- Copy CrossRank's `deploy/backup-cron.sh` → `/opt/oddsintel/backup.sh` (edit for `oddsintel` DB, separate rsync target)
- Add to root crontab: `0 3 * * * /opt/oddsintel/backup.sh >> /var/log/oddsintel-backup.log 2>&1`
- Test one restore from backup into a scratch DB
- Netdata Postgres plugin (already deployed for CrossRank — just extends)

### Phase 9 — Decommission (after 2 weeks stable)

- Downgrade Supabase compute to Nano/Free — Auth + Storage stay on free tier
- Keep `public` schema on Supabase read-only for 30 days as fallback
- After 30 days: drop `public` schema on Supabase
- Update `INFRASTRUCTURE.md`, `ROADMAP.md`, `CLAUDE.md` architecture diagram
- Update memory notes

## Rollback plan

| Phase | Rollback | Time |
|---|---|---|
| 4 | Revert frontend PR + pm2 restart | 5 min |
| 5 | Revert engine `.env` + restart scheduler | 5 min |
| 6 step 10–14 | Revert `NEXT_PUBLIC_POSTGREST_URL` + revert engine `DATABASE_URL` + pm2 restart + `systemctl restart odds-scheduler` | 10 min |
| 9 | Cannot recover after dropping Supabase `public` — but backups still exist on Hetzner Storage Box | N/A |

Supabase data preserved through Phase 8. We copy, not move.

## Cost impact

- **Now:** Supabase Pro DB compute (~$25/mo) + storage add-ons
- **After Phase 9:** Supabase Free tier (Auth + Storage) + Hetzner Storage Box shared with CrossRank €3.20/mo (already paying)
- **Net savings:** ~$25/mo. Real win: dedicated 8 vCPU / 15 GiB Postgres with predictable latency, no pooler surprises.

## Success criteria

- All 30 largest tables parity in row counts (final cutover)
- All 15 functions + 10 triggers present and callable in `oddsintel` DB
- All 80+ RLS policies applied (spot-check anon vs authenticated queries)
- Site loads in < 2s on cold path (measure)
- Scheduler completes 24h full cycle with no errors after cutover
- `pg_notify inplay_bet_fired` roundtrip through SSH tunnel succeeds
- 7 days of stable operation before Phase 9

## Ports + domains

| Port  | App             | Purpose                                 |
|-------|-----------------|-----------------------------------------|
| 3000  | odds-intel-web  | Next.js frontend                        |
| 3001  | box-ranking     | Next.js (unrelated project)             |
| 3002  | *available*     | (was reserved; use 3012 instead)        |
| 3005  | uptime-kuma     |                                         |
| 3010  | crossrank pgrst | existing PostgREST                      |
| 3011  | nginx loopback  | postgrest-local for VPS-local apps      |
| 3012  | **oddsintel pgrst** | **new PostgREST for this migration** |
| 3020  | crossrank-web?  | (another Next.js)                       |
| 5432  | postgres        | localhost only                          |
| 8080  | odds-scheduler  | health endpoint (blocked by duplicate)  |
| 8191  | flaresolverr    | docker                                  |
| 19999 | netdata         | localhost                               |

External domains:
- `api.<oddsintel-domain>` (choose name — probably `api.oddsintel.app`) → nginx → PostgREST 3012

## Done. Any remaining questions

All blockers resolved. Ready to execute on go-ahead.
