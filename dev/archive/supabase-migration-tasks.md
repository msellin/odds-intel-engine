# Supabase → VPS Migration — Tasks

Mark items `[x]` as they complete. Keep updated in every session.

## Phase 0 — Prep

- [x] SM-0.1 ~~Locate scheduler~~ — `odds-scheduler.service` systemd
- [x] SM-0.2 ~~Investigate swap~~ — not a leak
- [x] SM-0.3 ~~box-ranking restarts~~ — clean starts, driven by external deploy hook (git FETCH_HEAD activity). Non-blocking.
- [x] SM-0.4 ~~Baseline snapshot~~ — DONE 2026-07-09, see `supabase-migration-baseline.md`
- [x] SM-0.5 ~~Supabase JWT secret~~ — NOT NEEDED (CrossRank pattern, PostgREST mints own JWT)
- [x] SM-0.6 ~~Env snapshot~~ — done via local .env (VPS .env values match)
- [x] SM-0.7 ~~PRIORITY_QUEUE entry~~ — added under Infrastructure section, marked 🔄 In Progress
- [x] SM-0.8 ~~Disable duplicate scheduler~~ — DONE 2026-07-09. Load dropped from 4.5 → 2.5.
- [x] SM-0.9 ~~CrossRank external exposure~~ — nginx port 80 (verified live)

## Phase 1 — VPS Postgres setup

- [x] SM-1.1 ~~SSH to VPS, confirm postgres 17 healthy~~ — DONE
- [x] SM-1.2 ~~Apply tuning~~ — ALREADY APPLIED
- [x] SM-1.3 ~~Create roles~~ — `oddsintel_owner` created; `anon`/`authenticated`/`service_role`/`web_anon` already existed from CrossRank
- [x] SM-1.4 ~~Create database~~ — `oddsintel` created, owner `oddsintel_owner`
- [x] SM-1.5 ~~Install extensions~~ — 6 installed: pgcrypto, uuid-ossp, pg_stat_statements, hypopg, pg_trgm, unaccent
- [x] SM-1.6 ~~Tunnel setup~~ — No tunnel needed for Postgres (VPS engine uses localhost; Mac daemon uses SSH tunnel — Phase 5)
- [x] SM-1.7 ~~Verify tunnel~~ — Confirmed via loopback and via nginx

## Phase 2 — Trial dump/restore (non-destructive) — DONE 2026-07-09

- [x] SM-2.1 ~~Dump~~ — DONE in **1m37s** (18 GB → 1.5 GB directory format). pg_dump run on VPS (v17 matches Supabase; local Mac only had v14). DATABASE_URL delivered via mode-600 file at `/opt/oddsintel/.dump-env`.
- [x] SM-2.2 ~~scp~~ — N/A (dump created on VPS directly)
- [x] SM-2.3 ~~pg_restore~~ — DONE in **1m53s**, exit 0. 138 errors ignored (all 3 unique errors are Supabase-only event triggers pgrst_ddl_watch, pgrst_drop_watch, issue_pg_cron_access — need superuser, not needed anyway)
- [x] SM-2.4 ~~Row-count parity~~ — Top 15 tables verified. odds_snapshots 20,699,164 (baseline 20,673,171 + delta), match_signals 16,969,913 (matches baseline). Full parity check will re-run at Phase 6 cutover with authoritative COUNT(*).
- [x] SM-2.5 ~~Functions~~ — 110 in `public` (baseline said 15 custom + Supabase's helper set; total 110 is correct)
- [x] SM-2.6 ~~Triggers~~ — 10 present, matches baseline exactly ✓
- [x] SM-2.7 ~~RLS policies~~ — 99 policies present (baseline "80+"); 122 tables have RLS enabled
- [x] SM-2.8 ~~Timing~~ — dump 1m37s + restore 1m53s = **~3.5 min total for 18 GB**. Cutover window can be dramatically shorter than the 30-45 min budgeted; realistic ~10 min including scheduler stop/start.
- [ ] SM-2.9 pg_notify roundtrip test — deferred to Phase 5 (needs Mac tunnel)

## Phase 3 — PostgREST on VPS — DONE 2026-07-09 (except DNS)

- [x] SM-3.1 ~~docker-compose~~ — `/opt/oddsintel/docker-compose.yml`, PostgREST 12.2.3, port 3012, host network
- [x] SM-3.2 ~~JWT secret~~ — generated (64-char base64), saved to `supabase-migration-secrets.md` (gitignored)
- [x] SM-3.3 ~~Env~~ — PGRST_DB_URI + PGRST_DB_ANON_ROLE=anon + secret set
- [x] SM-3.4 ~~Mint JWTs~~ — anon + service_role JWTs generated, saved to secrets file
- [x] SM-3.5 ~~docker compose up -d~~ — container running, 134 relations loaded in schema cache
- [x] SM-3.6 ~~nginx config~~ — `/etc/nginx/sites-available/oddsintel-api` for `api.oddsintel.app` → 3012 with /rest/v1/ rewrite + Next.js-style proxy buffers
- [x] SM-3.7 ~~Enable nginx~~ — symlinked, `nginx -t` passed, reloaded
- [x] SM-3.8 ~~Cloudflare DNS~~ — DONE 2026-07-09. `api.oddsintel.app` resolves via Cloudflare (172.67.176.22, 104.21.56.25). `GET https://api.oddsintel.app/` returns 200 with PostgREST 12.2.3 OpenAPI JSON. **Phase 3 fully complete.**
- [x] SM-3.9 ~~Smoke external via anon~~ — verified via Host-header override; needs DNS for real hostname test
- [x] SM-3.10 ~~Smoke service_role~~ — bypasses RLS, reads `profiles` correctly

**PostgREST schema cache**: 134 Relations, 72 Relationships, 43 Functions, 4 Media Type Handlers

## Phase 4 — Frontend two-client refactor — DONE 2026-07-09

*Discovery: `supabase-public.ts` + engine-data.ts `createSupabaseAdmin` already
had the two-client fallback pattern (Phase 4 groundwork was pre-landed). The
real gap was that 13 admin routes/pages + `layout.tsx` + `get-user-tier.ts`
+ client-side `auth-provider.tsx.fetchProfile` were still reading `profiles`
via the Supabase auth client — those would go stale post-cutover.*

- [x] SM-4.1 ~~env vars~~ — added `NEXT_PUBLIC_POSTGREST_URL/ANON_KEY` + `POSTGREST_SERVICE_KEY` to VPS `.env.production.local` initially pointing at Supabase.
- [x] SM-4.2/4.3/4.4 ~~factory refactor~~ — added `createServerServiceClient()` in `supabase-server.ts`; kept `createSupabaseServer/Browser/Public` names to minimize diff.
- [x] SM-4.5 ~~engine-data.ts~~ — no per-call refactor needed; already flows through `createSupabasePublic` + `createSupabaseAdmin` factories.
- [x] SM-4.6 ~~routes + pages~~ — 13 files edited to keep auth on `createSupabaseServer` but redirect `profiles` reads to `createServerServiceClient`. Independent verification agent PASS on all 13.
- [x] SM-4.7 ~~per-user data audit~~ — swept 12 tables from the list; only `profiles`, `real_bets`, `accessible_bookmakers` are actually queried in frontend. All admin-scoped or already gated. `auth-provider.tsx` client-side profile fetch moved to new `/api/me/profile` server route.
- [x] SM-4.8 ~~preserve auth files~~ — untouched.
- [x] SM-4.9 ~~build+typecheck clean~~ — `npx tsc --noEmit` exit 0; `next build` exit 0.
- [x] SM-4.10 ~~deploy to VPS~~ — commit 3b61615, pm2 restart online.
- [ ] SM-4.11 Manual regression — user to test sign-in / OAuth / password reset / Stripe checkout end-to-end.

## Phase 5 — Engine + operator daemon repoint — DONE 2026-07-09

*Discovery: prior session had already cut over the VPS engine at 08:58 UTC
(scheduler restarted, `.env.pre-cutover-20260709-0858` backup on VPS).
5h of engine-side writes had been landing on VPS while frontend still read
Supabase. Phase 5 completed the split state.*

- [x] SM-5.1 ~~VPS engine DATABASE_URL~~ — already pointing at `oddsintel_owner@localhost:5432/oddsintel`. Added `DATABASE_URL_VPS` staged var + backed up.
- [x] SM-5.2 ~~autossh install~~ — `brew install autossh` on Mac (was missing).
- [x] SM-5.3 ~~launchd tunnel~~ — `~/Library/LaunchAgents/com.oddsintel.vps-postgres-tunnel.plist`, `-L 5433:localhost:5432 root@204.168.199.8`, KeepAlive + ThrottleInterval 30. Loaded, PID 24208, port 5433 listening.
- [x] SM-5.4 ~~verify tunnel~~ — `psql postgres://oddsintel_owner:<pw>@localhost:5433/oddsintel -c "SELECT COUNT(*) FROM matches"` → 144,726.
- [x] SM-5.5 ~~Mac .env DATABASE_URL~~ → `postgres://oddsintel_owner:<pw>@localhost:5433/oddsintel`. Backed up as `.env.pre-cutover-2026-07-09...`.
- [x] SM-5.6 ~~smoke test~~ — `python3 scripts/smoke_test.py --filter DB` → 15/16 pass. The one failure (WC-LEADERBOARD-AI) is pre-existing (`wc-bracket.ts` deleted in commit f6d3648 PRODUCT-COLLAPSE); unrelated to migration.
- [x] SM-5.7 ~~LISTEN/NOTIFY roundtrip~~ — Mac psql `LISTEN inplay_bet_fired` via tunnel, VPS psql `NOTIFY inplay_bet_fired, 'phase5-test'`. Payload received on Mac from server PID 936256.

## Phase 6 — Cutover — DONE 2026-07-09 (no maintenance window needed)

*Adapted from planned 03:00 UTC drop-and-redump: engine was already writing
to VPS since 08:58 UTC (5h of authoritative data). Cutover reduced to
flipping the frontend and the Mac daemon — no drop, no dump, no window.*

- [x] SM-6.1/6.2 ~~announce/stop scheduler~~ — skipped, scheduler already on VPS.
- [x] SM-6.3 ~~stop Mac daemons~~ — done via `launchctl kickstart -k` (restart in-place).
- [x] SM-6.4/6.5/6.6/6.7/6.8/6.9/6.10 ~~final dump + drop/recreate + restore + row-count parity~~ — SKIPPED. VPS oddsintel already had the Phase 2 dump + 5h of live engine writes. Dropping would discard authoritative data.
- [x] SM-6.11 ~~frontend env cutover~~ — `NEXT_PUBLIC_POSTGREST_URL=https://api.oddsintel.app`, VPS anon JWT, VPS service_role JWT.
- [x] SM-6.12 ~~pm2 restart odds-intel-web~~ — rebuilt (NEXT_PUBLIC_* are baked at build time), pm2 online, stderr clean.
- [x] SM-6.13 ~~start scheduler~~ — never stopped.
- [x] SM-6.14 ~~start Mac daemons~~ — restarted, log shows "DB pool created (2-20 connections)" via tunnel.
- [x] SM-6.15 ~~smoke~~ — `/`, `/performance`, `/track-record`, `/picks` all 200; VPS `odds_snapshots.max(timestamp)=2026-07-09 10:45:58` (fresh).
- [x] SM-6.16 ~~verify LISTEN~~ — proven in SM-5.7.

## Phase 7 — Observation (1 week)

- [ ] SM-7.1 **USER TO DO** — Add Uptime Kuma monitor via UI at http://204.168.199.8:3005: HTTP keyword monitor on `https://api.oddsintel.app/matches?limit=1`, expect string like `"id":"` in body. (API-add path requires session token; UI is faster.)
- [ ] SM-7.2 **USER TO DO** — Add Uptime Kuma TCP monitor to `204.168.199.8:5432` (Postgres). Or safer: a Push monitor triggered by a systemd timer running `psql SELECT 1`.
- [ ] SM-7.3 Daily check: `pg_stat_statements` top-10 slow queries; add indexes if needed
- [ ] SM-7.4 Daily check: VPS load avg + swap
- [ ] SM-7.5 Daily check: pm2 restart counts on all 3 apps
- [ ] SM-7.6 Daily check: scheduler completed a full cycle with no errors

## Phase 8 — Backups + observability — CORE DONE 2026-07-09

- [x] SM-8.1 ~~Storage Box~~ — already provisioned + used by CrossRank; reused. Credentials copied to `/opt/oddsintel/.storage-box.env`.
- [x] SM-8.2 ~~backup script~~ — `/opt/oddsintel/backup-oddsintel.sh` (adapted from CrossRank's; Storage Box shell is restricted so remote retention runs client-side on VPS).
- [x] SM-8.3 ~~crontab~~ — `30 3 * * * /opt/oddsintel/backup-oddsintel.sh >> /var/log/oddsintel-backup.log 2>&1` (offset from CrossRank at 03:00 to avoid disk contention).
- [x] SM-8.4 ~~verify first backup~~ — 2026-07-09 dump: 1526 MB local, 1.6 GB on Storage Box (`oddsintel/oddsintel-2026-07-09.dump`).
- [x] SM-8.5 ~~restore test~~ — pulled dump back from Storage Box, restored to scratch DB `oddsintel_restore_test`, tables present, dropped scratch. Round-trip proven.
- [ ] SM-8.6 Configure Netdata Postgres plugin for VPS Postgres — deferred; Uptime Kuma probes are the primary alerting channel.

## Phase 9 — Decommission (after 2 weeks stable)

- [ ] SM-9.1 Downgrade Supabase Postgres compute to Nano or Free
- [ ] SM-9.2 Keep `public` schema read-only on Supabase for 30 days
- [ ] SM-9.3 After 30 days: drop `public` schema on Supabase
- [ ] SM-9.4 Update `INFRASTRUCTURE.md` with new costs
- [ ] SM-9.5 Update `ROADMAP.md` current system state
- [ ] SM-9.6 Update memory: `/Users/margussellin/.claude/projects/-Users-margussellin-www-odds-intel-engine/memory/` — new project memory note about the migration outcome
- [ ] SM-9.7 Mark PRIORITY_QUEUE.md entry `✅ Done YYYY-MM-DD`
- [ ] SM-9.8 Update `CLAUDE.md` architecture diagram

## Blockers / questions — ALL RESOLVED

- [x] Q1 ~~Where does the scheduler run?~~ — `odds-scheduler.service` systemd unit, `/opt/odds-intel-engine/venv/bin/python workers/scheduler.py`
- [x] Q2 ~~What's holding VPS swap?~~ — Postgres (4GB shared_buffers × 8 workers) + scheduler 464MB swap + FlareSolverr + Next.js. Not a leak, just tight.
- [x] Q3 ~~box-ranking 28 restarts~~ — pm2 log inspection shows clean "Ready in 103ms" starts every time, no crashes. Something (probably a deploy workflow) is restarting it externally. **Non-blocking.**
- [x] Q4 ~~Supabase JWT secret~~ — **NOT NEEDED.** Adopted CrossRank pattern: PostgREST uses its own generated HS256 secret + our own minted anon/service_role JWTs. Supabase JWT never crosses to PostgREST. Per-user data access uses server-side service_role + explicit `user_id` filter.
- [x] Q5 ~~External exposure~~ — CrossRank exposes PostgREST via **nginx port 80** (`api.crossrank.ee` → 127.0.0.1:3010) with Cloudflare Flexible SSL. **NOT** a Cloudflare TCP tunnel. Copy this pattern for `api.oddsintel.<domain>` → 127.0.0.1:3012.

## Deferred (Option B — full auth migration)

Not doing now. If ever needed:
- Replace `@supabase/ssr` with Auth.js or Lucia
- Migrate 52 users from `auth.users` (bcrypt hashes portable between GoTrue and most Node auth libs)
- Rewire OAuth (new Google + Discord client IDs pointing at our callback)
- Rewire magic links via Resend directly
- Full frontend auth surface rewrite: login, signup, forgot-password, reset-password, callback, proxy middleware
- Est: 2–3 weeks
