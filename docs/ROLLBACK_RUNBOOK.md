# OddsIntel — Deploy & Rollback Runbook

One-page reference for reverting a bad deploy. Everything runs on the **Hetzner
VPS (204.168.199.8)** — Railway was eliminated 2026-06-29 and the frontend
migrated off Vercel to VPS pm2 2026-07-07. There is no Railway or Vercel
dashboard to click; rollback is git + systemd/pm2.

> **How deploys reach the box:** push to `main` triggers GitHub Actions.
> `deploy.yml` pulls the engine on the VPS (and `systemctl restart
> oddsintel-scheduler` when `workers/**` or `requirements.txt` changed);
> `odds-intel-web/.github/workflows/deploy.yml` pulls + clean-builds + `pm2
> restart odds-intel-web`; `migrate.yml` applies `supabase/migrations/**` to
> the VPS Postgres. So the fastest rollback is almost always **revert the
> commit and push** — let the same automation redeploy the good state.

---

## When to roll back

| Symptom | Component | Section |
|---------|-----------|---------|
| Frontend broken (blank page, 500s, broken API routes) | odds-intel-web (VPS pm2) | → [Frontend rollback](#frontend-rollback) |
| Pipeline jobs failing / scheduler not running | oddsintel-scheduler (VPS systemd) | → [Engine rollback](#engine-rollback) |
| DB migration broke something | VPS Postgres 17 | → [DB migration rollback](#db-migration-rollback) |

---

## Engine rollback

The scheduler (`workers/scheduler.py`) runs as systemd unit
**`oddsintel-scheduler.service`** on the VPS and auto-deploys from any push to
`main` via `deploy.yml`.

### Option A — Revert the commit + push (preferred)

```bash
# 1. Find the SHA that broke things
git log --oneline -10

# 2. Revert it and push — Actions redeploys the good state
git revert <bad-sha> --no-edit
git push
```

`deploy.yml` will pull on the VPS and (if `workers/**` changed) restart the
scheduler automatically.

### Option B — Roll back on the box directly (Actions broken / urgent)

```bash
ssh root@204.168.199.8 'cd /opt/odds-intel-engine \
  && git fetch origin \
  && git reset --hard <good-sha> \
  && systemctl restart oddsintel-scheduler \
  && systemctl is-active oddsintel-scheduler'
```

> A manual `reset --hard` on the box leaves it ahead of what `main` expects;
> the daily `deploy_drift_check.yml` (06:00 UTC) will alert that the repo is
> not on `main`. Get `main` back to the good SHA (Option A) as soon as the
> incident is over so the box and the branch reconcile.

### Verify engine rollback

```bash
ssh root@204.168.199.8 'systemctl is-active oddsintel-scheduler \
  && journalctl -u oddsintel-scheduler -n 40 --no-pager'
```

Check the Uptime Kuma dashboard (https://status.oddsintel.app) — the Scheduler
Heartbeat monitor should be green.

---

## Frontend rollback

The Next.js frontend (`odds-intel-web`) runs under **pm2 (:3000)** behind nginx
on the same VPS and auto-deploys on push to `main` via its own `deploy.yml`
(pull + clean build + `pm2 restart odds-intel-web`).

### Option A — Revert the commit + push (preferred)

```bash
cd ../odds-intel-web
git revert <bad-sha> --no-edit
git push
```

Triggers a fresh clean build + pm2 restart on the VPS. Remember
`NEXT_PUBLIC_*` are baked at build time, so a rollback that touches env needs
the rebuild that this path performs.

### Option B — Roll back on the box directly

```bash
ssh root@204.168.199.8 'cd /opt/odds-intel-web \
  && git fetch origin \
  && git reset --hard <good-sha> \
  && npm ci && npm run build \
  && pm2 restart odds-intel-web \
  && pm2 status odds-intel-web'
```

### Verify frontend rollback

```bash
curl -s https://oddsintel.app 2>/dev/null | head -5
# or load the site in a browser — check the /picks list renders
ssh root@204.168.199.8 'pm2 status odds-intel-web'
```

---

## DB migration rollback

Migrations in `supabase/migrations/` are applied automatically to the **VPS
Postgres 17** on push to `main` via `migrate.yml` (recorded in
`_schema_migrations`). Supabase now holds Auth + Storage only — the public
schema was dropped 2026-07-13 — so there is no Supabase Table Editor to fix
data in.

**Migrations are additive — there is no automated down-migration.** If a
migration broke something, the fix is another migration.

### Procedure

1. **Identify the bad migration** — check `supabase/migrations/` for the last file.
2. **Write a reversal migration** in a new file:
   ```bash
   # Next migration number is always current highest + 1
   ls supabase/migrations/ | tail -5
   # e.g. if last is 073_foo.sql, create 074_revert_foo.sql
   ```
3. **Write the reversal SQL** — e.g. `DROP TABLE`, `ALTER TABLE ... DROP COLUMN`, `DROP POLICY`.
4. **Commit and push** — `migrate.yml` applies it automatically.
5. **Verify** with `psql` against the VPS Postgres (see below).

### Emergency: apply migration manually

```bash
# Run against the VPS Postgres directly (DATABASE_URL points at the VPS box)
ssh root@204.168.199.8 'psql "$DATABASE_URL" -f /opt/odds-intel-engine/supabase/migrations/074_revert_foo.sql'
```

---

## Checklist after any rollback

- [ ] `systemctl is-active oddsintel-scheduler` returns `active`
- [ ] Uptime Kuma (status.oddsintel.app) — Scheduler Heartbeat + Coolbet Health Ping green
- [ ] oddsintel.app loads and the `/picks` list shows data
- [ ] `pm2 status odds-intel-web` shows `online`
- [ ] Check `pipeline_runs` in VPS Postgres — no stuck `running` rows
- [ ] If a migration was involved: verify table structure via `psql`

---

## Contacts / links

| Resource | Where |
|----------|-------|
| VPS (SSH) | `ssh root@204.168.199.8` |
| Uptime Kuma | https://status.oddsintel.app |
| Engine deploy workflow | `.github/workflows/deploy.yml` |
| Web deploy workflow | `odds-intel-web/.github/workflows/deploy.yml` |
| Migration workflow | `.github/workflows/migrate.yml` |
| Drift check | `.github/workflows/deploy_drift_check.yml` (daily 06:00 UTC) |
| Supabase (Auth + Storage only) | https://supabase.com/dashboard |
| Engine repo | github.com/margus/odds-intel-engine |
| Frontend repo | github.com/margus/odds-intel-web |
