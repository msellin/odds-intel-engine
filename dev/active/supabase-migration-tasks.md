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
- [ ] **SM-3.8 Cloudflare DNS** — needs Margus: add A record `api.oddsintel.app` → 204.168.199.8, Proxied, SSL Flexible
- [x] SM-3.9 ~~Smoke external via anon~~ — verified via Host-header override; needs DNS for real hostname test
- [x] SM-3.10 ~~Smoke service_role~~ — bypasses RLS, reads `profiles` correctly

**PostgREST schema cache**: 134 Relations, 72 Relationships, 43 Functions, 4 Media Type Handlers

## Phase 4 — Frontend two-client refactor

*In odds-intel-web repo. Deploy against **Supabase** first (POSTGREST_URL = SUPABASE_URL). No cutover yet.*

- [ ] SM-4.1 Add env vars — initial values from Supabase so refactor doesn't cutover:
      ```
      NEXT_PUBLIC_POSTGREST_URL=$NEXT_PUBLIC_SUPABASE_URL
      NEXT_PUBLIC_POSTGREST_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY
      POSTGREST_SERVICE_KEY=$SUPABASE_SERVICE_ROLE_KEY
      ```
- [ ] SM-4.2 Refactor `src/lib/supabase-browser.ts` → export `createBrowserAuthClient()` + `createBrowserDataClient()`
- [ ] SM-4.3 Refactor `src/lib/supabase-server.ts` → export `createServerAuthClient()` + `createServerDataClient()` + `createServerServiceClient()`
- [ ] SM-4.4 Refactor `src/lib/supabase-public.ts` → export `createPublicDataClient()`
- [ ] SM-4.5 **Big edit**: `src/lib/engine-data.ts` (101 call sites) — swap module-level client to `createServerDataClient()` / `createPublicDataClient()` per function. Single file, ~30 min.
- [ ] SM-4.6 Update ~15 route handlers + 3 admin pages to use `createServerDataClient()` for reads, `createServerServiceClient()` for writes/admin
- [ ] SM-4.7 **Per-user data audit**: every server-side query touching `profiles`, `user_match_favorites`, `wc_bracket_predictions`, `wc_group_predictions`, `wc_user_picks`, `wc_email_log`, `weekly_digest_log`, `email_digests`, `real_bets`, `watchlist_alerts`, `inplay_bot_stats`, `accessible_bookmakers` must include explicit `.eq('user_id', session.user.id)` — service_role bypasses RLS, so the app is now the enforcement layer
- [ ] SM-4.8 Preserve `auth-provider.tsx`, `login/page.tsx`, `auth/callback/route.ts`, `proxy.ts`, `forgot-password`, `reset-password` unchanged
- [ ] SM-4.9 `npm run build` clean, `npm run typecheck` clean
- [ ] SM-4.10 Deploy to VPS (pm2 restart odds-intel-web) with POSTGREST_URL still = Supabase URL
- [ ] SM-4.11 Manual regression: sign in, sign out, magic link, OAuth (Google + Discord), password reset, tier gating (free/pro/elite), `/value-bets`, `/matches/[id]`, `/track-record`, admin CS2/LoL/Tennis pages, Stripe checkout end-to-end

## Phase 5 — Engine + operator daemon re-point (staging config, no cutover yet)

- [ ] SM-5.1 Update VPS `/opt/odds-intel-engine/.env` `DATABASE_URL` → `postgres://oddsintel_owner:pw@localhost:5432/oddsintel` — **do NOT restart scheduler yet**
- [ ] SM-5.2 On operator Mac: `brew install autossh` if not present
- [ ] SM-5.3 Create launchd plist for autossh tunnel: `-L 5433:localhost:5432 root@204.168.199.8` with ServerAliveInterval 30, load with `launchctl load`
- [ ] SM-5.4 Verify tunnel: `psql postgres://oddsintel_owner:pw@localhost:5433/oddsintel -c 'SELECT 1'` from Mac
- [ ] SM-5.5 Update Mac `.env` `DATABASE_URL` → `postgres://oddsintel_owner:pw@localhost:5433/oddsintel` — **do NOT restart daemons yet**
- [ ] SM-5.6 With Mac `.env` pointing at VPS, run `python3 scripts/smoke_test.py --filter DB` locally
- [ ] SM-5.7 LISTEN/NOTIFY roundtrip test via SSH tunnel: from Mac tunnel-psql `LISTEN inplay_bet_fired`, from VPS psql `NOTIFY inplay_bet_fired, 'test'` — should print notification on Mac within 1s

## Phase 6 — Cutover night (03:00 UTC, ~30–45 min window)

- [ ] SM-6.1 Announce cutover start
- [ ] SM-6.2 Stop VPS scheduler: `systemctl stop odds-scheduler.service`
- [ ] SM-6.3 Stop operator Mac daemons: `launchctl unload …` for Coolbet daemon + HLTV crawlers
- [ ] SM-6.4 Verify no writes hitting Supabase for 2 min (`SELECT max(created_at) FROM odds_snapshots`, `pipeline_runs`)
- [ ] SM-6.5 `DROP DATABASE oddsintel` then `CREATE DATABASE oddsintel OWNER oddsintel_owner` (clean slate)
- [ ] SM-6.6 Re-install extensions in fresh `oddsintel` DB
- [ ] SM-6.7 Final dump from Supabase (same command as SM-2.1)
- [ ] SM-6.8 scp to VPS
- [ ] SM-6.9 pg_restore
- [ ] SM-6.10 Row-count parity check (must match Supabase to the row)
- [ ] SM-6.11 Update VPS `/opt/odds-intel-web/.env.production.local`: `NEXT_PUBLIC_POSTGREST_URL=https://api.<oddsintel-domain>`, `NEXT_PUBLIC_POSTGREST_ANON_KEY=<Phase 3 JWT>`, `POSTGREST_SERVICE_KEY=<Phase 3 service JWT>`
- [ ] SM-6.12 `pm2 restart odds-intel-web`
- [ ] SM-6.13 Start scheduler: `systemctl start odds-scheduler.service`
- [ ] SM-6.14 Start operator Mac daemons: `launchctl load …`
- [ ] SM-6.15 Smoke: site loads, `/value-bets` loads, sign-in works, one new `odds_snapshots` row appears in VPS DB within 15 min, `pipeline_runs` row inserted
- [ ] SM-6.16 Verify LISTEN consumer received a real `inplay_bet_fired` notification (or wait for one)

## Phase 7 — Observation (1 week)

- [ ] SM-7.1 Add Uptime Kuma probe for `https://oi-api.<domain>/matches?limit=1`
- [ ] SM-7.2 Add Uptime Kuma TCP probe for `oi-db.<domain>:5432`
- [ ] SM-7.3 Daily check: `pg_stat_statements` top-10 slow queries; add indexes if needed
- [ ] SM-7.4 Daily check: VPS load avg + swap
- [ ] SM-7.5 Daily check: pm2 restart counts on all 3 apps
- [ ] SM-7.6 Daily check: scheduler completed a full cycle with no errors

## Phase 8 — Backups + observability

- [ ] SM-8.1 Provision Hetzner Storage Box if not already (shared with CrossRank)
- [ ] SM-8.2 Copy CrossRank's `backup-cron.sh` → `/opt/oddsintel/backup.sh`, edit for `oddsintel` DB
- [ ] SM-8.3 Add to root crontab: `0 3 * * * /opt/oddsintel/backup.sh >> /var/log/oddsintel-backup.log 2>&1`
- [ ] SM-8.4 Verify first backup lands on Storage Box
- [ ] SM-8.5 Test restore from Storage Box to a scratch DB (do this once to prove backups work)
- [ ] SM-8.6 Configure Netdata Postgres plugin for VPS Postgres

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
