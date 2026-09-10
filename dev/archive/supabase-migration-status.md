# SUPABASE-TO-VPS — Post-Cutover Status & Runbook

Written 2026-07-09 after the same-day cutover. Living doc — update when
observation-window findings change the plan.

## TL;DR

Migration complete. All data reads + writes now go through Hetzner VPS
Postgres 17 (`204.168.199.8`). Supabase is still authoritative for **Auth**
and **Storage** only — nothing else.

Frontend commit `3b61615` (odds-intel-web). Engine commit `a49cf71`. Both on
`main`, deployed live.

---

## What lives where now

| System | Where | Notes |
|---|---|---|
| **Postgres 17 (`oddsintel` DB)** | VPS `204.168.199.8:5432` (localhost only) | 11 GB. Shared cluster with `crossrank`. |
| **PostgREST 12.2.3** | VPS docker, host network `:3012` | Behind nginx → `https://api.oddsintel.app` (Cloudflare Flexible SSL). |
| **Anon + service_role JWTs** | Minted by PostgREST with own secret | Not Supabase JWTs. Secrets in gitignored `dev/active/supabase-migration-secrets.md`. |
| **Frontend (`odds-intel-web`)** | VPS pm2 `:3000`, nginx TLS | Env `NEXT_PUBLIC_POSTGREST_URL=https://api.oddsintel.app` |
| **Engine scheduler** | VPS systemd `odds-scheduler.service` | `DATABASE_URL=localhost:5432/oddsintel` — up since 2026-07-09 08:58 UTC |
| **Mac Coolbet daemon** | Mac launchd `com.oddsintel.coolbet-mac-daemon` | Reads DB via SSH tunnel `localhost:5433` → VPS `5432` |
| **SSH tunnel** | Mac launchd `com.oddsintel.vps-postgres-tunnel` | autossh, ServerAliveInterval 30, KeepAlive true |
| **Nightly backup** | VPS root crontab `30 3 * * *` | `/opt/oddsintel/backup-oddsintel.sh` → Hetzner Storage Box `oddsintel/oddsintel-YYYY-MM-DD.dump` |
| **Auth (unchanged)** | Supabase | `auth.users` = 52 accounts; `NEXT_PUBLIC_SUPABASE_URL` unchanged in frontend env |
| **Model storage (unchanged)** | Supabase Storage | `models` bucket, 222 objects. Read by engine via `workers/model/storage.py`. |

VPS baseline at handoff: 8 vCPU, 15 GiB RAM (7.9 GiB used, 1.4 GiB swap), disk
77 GB / 301 GB used (26%), load avg 0.57. Room to grow.

---

## Frontend architecture (two-client split)

```
                     browser
                     │
              ┌──────┼───────┐
              │              │
    createSupabaseBrowser   fetch('/api/me/*')
    (auth cookies)          (server route)
              │              │
    Supabase Auth         Next.js server
    (auth.users)          │
                          │
                createSupabaseServer   ─── cookies (still Supabase auth session)
                createServerServiceClient ─── VPS PostgREST + service_role JWT
                          │              │
                  Supabase Auth        VPS PostgREST
                  (session refresh)    (per-user data via .eq('id', user.id))
```

**Two clients on the server side:**

- `createSupabaseServer()` (kept) — cookie-backed Supabase auth client. Used
  for `.auth.getUser()` / `.auth.getSession()`. Hits Supabase.
- `createServerServiceClient()` (**new**) — VPS PostgREST + service_role JWT.
  Used for ALL data reads. Bypasses RLS — callers MUST add explicit
  `.eq('id', session.user.id)` for per-user queries.

**Rule of thumb going forward:**

- New per-user client-side reads must route through a Next.js server route
  (`/api/me/*`), which pairs `createSupabaseServer` (identity) with
  `createServerServiceClient` (data + explicit user_id filter).
- Never `.from('profiles')` etc. on `createSupabaseServer` — it hits
  Supabase's stale `public` schema post-cutover.
- New public reads: use `createSupabasePublic()` (anon JWT).
- Service-role writes: use `createServerServiceClient()`.

---

## Files touched in Phase 4 (odds-intel-web)

| File | Change |
|---|---|
| `src/lib/supabase-server.ts` | Added `createServerServiceClient()` factory |
| `src/app/api/me/profile/route.ts` | **New** — server-side profile fetch for browser callers |
| `src/components/auth-provider.tsx` | `fetchProfile()` now `fetch('/api/me/profile')` — no direct browser query to `profiles` |
| `src/lib/get-user-tier.ts` | Uses `createServerServiceClient()` for `profiles` |
| `src/app/(app)/layout.tsx` | Uses `createServerServiceClient()` for the superadmin `profiles` check |
| `src/app/(app)/performance/page.tsx` | Dropped the now-unused `supabase` arg to `getUserTier` |
| `src/app/(app)/admin/{page,bots,lol,cs2,tennis,ops,place,real-bets}/page.tsx` | Superadmin `profiles` check redirected onto service client |
| `src/app/api/admin/{record-combo,real-bet,bot-book-odds}/route.ts` | Same |
| `src/app/api/{lol-bets,cs2-bets}/route.ts` | Same (POST + GET each) |

**Preserved unchanged:** `login/page.tsx`, `forgot-password`, `reset-password`,
`auth/callback/route.ts`, `proxy.ts`, `google-sign-in.tsx`, `google-one-tap.tsx`,
`login-modal.tsx`, `upgrade-modal.tsx`. All still use Supabase auth client.

---

## What's monitored + how to spot regressions

### Automatic
- **VPS pm2** watches `odds-intel-web`, restarts on crash. Currently stable.
- **systemd** watches `odds-scheduler.service`, auto-restarts. Currently active since 08:58 UTC.
- **launchd** on Mac watches the tunnel + daemon, `KeepAlive true` restarts on exit.
- **Uptime Kuma** at `http://204.168.199.8:3005` has 3 existing monitors on
  `oddsintel.app` (Homepage, Performance, `/api/v1/upcoming`). Green = frontend
  can reach VPS DB end-to-end.

### Not-yet-added (user action)
Add 2 monitors via the Kuma UI:
- HTTP keyword monitor on `https://api.oddsintel.app/matches?limit=1` — expect
  `"id":"` in body. Alerts if PostgREST or nginx die.
- TCP monitor on `204.168.199.8:5432`. Alerts if Postgres dies. (Or safer: a
  Push monitor triggered by a systemd timer running `psql SELECT 1`.)

### Manual daily-ish checks (first 1-2 days)
- Does `/performance` show a "last updated" within the last hour?
- Does `/picks` behave normally around European kickoff windows?
- Does `pg_stat_statements` show any query > 5s that wasn't slow on Supabase?
  ```
  ssh root@204.168.199.8
  sudo -u postgres psql -d oddsintel -c "SELECT mean_exec_time, calls, query FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10"
  ```
- Are VPS load avg + swap stable? (currently 0.57 / 1.4 GiB swap)
- pm2 restart count on `odds-intel-web` should stay flat past the migration churn.

---

## Answer to "will 1-2 days show if it works?"

**Mostly yes — but with caveats.**

### What 1-2 days WILL prove
- All 9 scheduled pipeline jobs complete a full daily cycle (fixtures 04:00,
  enrichment 04:15/12:00/16:00, odds every 30 min 07-22, predictions 05:30,
  betting cohorts across the day, live tracker, news checker, settlement
  21:00, betting refresh 7 cohorts). One full cycle = ~24 hours.
- Uptime Kuma + Sentry catch any 500s or crashes on the frontend.
- Mac daemon completes at least 48 poll cycles (every 30 min) through the
  tunnel and finds simulated_bets normally.
- Nightly backup runs at 03:30 UTC and lands on the Storage Box.
- Peak-hour traffic (European kickoff windows) doesn't overload the DB
  (VPS is single-node; Supabase was multi-node).

### What 1-2 days WON'T prove
- **Weekend + Sunday specifics.** Match volume is much higher on Sat/Sun;
  the migration was mid-week (Thursday). We won't see weekend peak until
  2026-07-11/12.
- **Mac laptop sleep/wake cycle.** The tunnel autossh restarts on network
  flap, but if you close the laptop overnight the tunnel drops and the
  daemon can't reach VPS until Mac wakes and autossh reconnects.
- **Postgres query plans stabilizing.** `pg_stat_statements` needs a few
  days of traffic before slow-query patterns emerge.
- **Storage Box retention correctness.** The 90-day cleanup logic won't
  trigger until day 91.
- **Any monthly/weekly aggregation.** Weekly digest emails (Sundays),
  monthly reports (day 1), Sunday anon-user prune — all fire on their
  own schedules.

### Realistic decommission timeline

**Compute downgrade is safe TODAY.** Verified:
- Postgres compute tier only affects the `public` schema — which nothing
  currently reads.
- Supabase Auth (GoTrue) is a separate service from Postgres compute;
  Nano/Free is fine for 52 users at current traffic (mostly cookie refresh).
- Supabase Storage is a separate S3-backed service, not compute-tier-dependent.
- All 5 active model bundles (v20260705 main, v_20260706_bets_xgb meta,
  fallbacks v9a_202425 + v14_recreate_2026_05_11) are cached locally on
  VPS. Storage downtime would only affect NEW model uploads after
  training, not inference.

**Recommended path (aggressive):**

1. **Today** — Downgrade Supabase Postgres compute to Nano or Free via
   the Supabase dashboard (Project Settings → Compute and Disk).
   - Keep the plan tier if it's tied to Auth Pro features you use
     (e.g. custom SMTP, higher MAU limits). Otherwise drop to Free.
   - Do NOT drop the `public` schema — leave as read-only safety net.

2. **Tomorrow (2026-07-10)** — Sanity check one full daily cycle
   completed. Same checks as the 2-day watch above.

3. **Weekend (2026-07-11/12)** — Peak load test.

4. **~+30 days (2026-08-13)** — Drop Supabase `public` schema. Only
   after confirming nothing in the codebase has quietly regressed to
   reading from Supabase.

**Conservative path (if you want to hedge):**

Same as above but delay step 1 until Monday 2026-07-13 to get one full
weekend of peak traffic under the belt before touching Supabase billing.

The delta between the two paths is ~4 days of Supabase Pro billing
(~$3-4 pro-rated). Choose based on how much you value the safety net.

---

## Rollback plan (if something breaks)

The Supabase project is untouched; Auth + `public` schema are still there.
To roll back the data plane:

1. **Frontend rollback** — SSH VPS, edit `/opt/odds-intel-web/.env.production.local`:
   ```
   NEXT_PUBLIC_POSTGREST_URL=<the original Supabase URL from .env.production.local.pre-flip-*>
   NEXT_PUBLIC_POSTGREST_ANON_KEY=<original NEXT_PUBLIC_SUPABASE_ANON_KEY>
   POSTGREST_SERVICE_KEY=<original SUPABASE_SERVICE_ROLE_KEY>
   ```
   Then `cd /opt/odds-intel-web && npm run build && pm2 restart odds-intel-web --update-env`.

2. **Engine rollback** — SSH VPS, edit `/opt/odds-intel-engine/.env`:
   ```
   DATABASE_URL=<the Supabase pooler URL from .env.pre-cutover-20260709-0858>
   ```
   Then `systemctl restart odds-scheduler.service`.

3. **Mac daemon rollback** — Restore `.env.pre-cutover-20260709-135541`. Then
   `launchctl kickstart -k gui/$(id -u)/com.oddsintel.coolbet-mac-daemon`.

4. Any writes to VPS during the rollback window would be lost — VPS oddsintel
   would need to be dumped and merged back to Supabase manually. But the
   Storage Box backup is a safety net.

Backup files kept on VPS + local Mac (all in gitignored `.env.*` files):
- VPS `/opt/odds-intel-engine/.env.pre-cutover-20260709-0858` (Supabase pooler URL)
- VPS `/opt/odds-intel-web/.env.production.local.pre-flip-*` (Supabase POSTGREST envs)
- Mac `.env.pre-cutover-20260709-135541` (Supabase pooler URL)

---

## Open items

- [ ] **User** — Add 2 Uptime Kuma probes via UI (SM-7.1/7.2 above).
- [ ] **User** — End-to-end regression: sign-in, Google OAuth, password reset,
      Stripe checkout, tier gating on `/matches/[id]`, admin CS2/LoL/Tennis
      pages. Migration didn't touch the auth paths but touched the profile
      fetch that reads user tier.
- [ ] **User** — Monday 2026-07-13: downgrade Supabase Postgres compute.
- [ ] **User** — ~2026-08-13: drop Supabase `public` schema.
- [ ] Netdata Postgres plugin (SM-8.6) — deferred; Uptime Kuma is primary
      alerting channel and covers the same visibility.

## Related files

- `dev/active/supabase-migration-plan.md` — original plan (2026-07-08 draft)
- `dev/active/supabase-migration-context.md` — living session notes
- `dev/active/supabase-migration-tasks.md` — SM-* task checklist
- `dev/active/supabase-migration-baseline.md` — pre-migration baseline snapshot
- `dev/active/supabase-migration-secrets.md` — JWTs + connection strings (**gitignored**)
- `PRIORITY_QUEUE.md` — SUPABASE-TO-VPS entry (marked ✅ Done)
- `CLAUDE.md` — architecture diagram updated
