# OddsIntel — Deploy & Rollback Runbook

One-page reference for reverting a bad deploy. Each section has the exact command — no guessing.

---

## When to roll back

| Symptom | Platform | Section |
|---------|----------|---------|
| Frontend broken (blank page, 500s, broken API routes) | Vercel | → [Vercel rollback](#vercel-rollback) |
| Pipeline jobs failing / scheduler not running | Railway | → [Railway rollback](#railway-rollback) |
| DB migration broke something | Supabase | → [DB migration rollback](#db-migration-rollback) |

---

## Railway rollback

The Railway scheduler (`workers/scheduler.py`) auto-deploys from any push to `main`.

### Option A — Redeploy a previous git SHA

```bash
# 1. Find the SHA to roll back to
git log --oneline -10

# 2. Force Railway to deploy that SHA via the CLI
railway up --detach --environment production
# (opens Railway CLI pointing at your project)

# OR push a revert commit to trigger auto-deploy:
git revert HEAD --no-edit
git push
```

### Option B — Revert via Railway dashboard

1. Open [railway.app](https://railway.app) → your project → **Deployments**
2. Find the last good deployment in the list
3. Click **Redeploy** (the three-dot menu on that row)
4. Railway will re-run that exact image — no git push needed

### Verify rollback succeeded

```bash
# Health endpoint (replace with your Railway service URL)
curl https://<your-railway-service>.railway.app/health
# Should return {"status": "ok", ...}
```

---

## Vercel rollback

The Next.js frontend (`odds-intel-web`) deploys to Vercel automatically on push to `main`.

### Option A — Vercel CLI instant rollback

```bash
cd ../odds-intel-web

# Install CLI if needed
npm i -g vercel

# List recent deployments
vercel list

# Roll back to a specific deployment URL
vercel rollback <deployment-url>
# e.g. vercel rollback https://odds-intel-abc123.vercel.app
```

### Option B — Vercel dashboard

1. Open [vercel.com](https://vercel.com) → **odds-intel-web** project
2. Click **Deployments** tab
3. Find the last known-good deployment
4. Click the three-dot menu → **Promote to Production**

This is instant — no redeploy, just an alias swap.

### Option C — Revert commit + push

```bash
cd ../odds-intel-web
git revert HEAD --no-edit
git push
```

Triggers a fresh Vercel build from the reverted code.

### Verify Vercel rollback

```bash
curl -s https://oddsintel.app/api/health 2>/dev/null | head -5
# or just load the site in a browser — check the match list renders
```

---

## DB migration rollback

Migrations in `supabase/migrations/` are applied automatically on push to `main`.

**Migrations are additive — there is no automated down-migration.** If a migration broke something, the fix is another migration.

### Procedure

1. **Identify the bad migration** — check `supabase/migrations/` for the last file.
2. **Write a reversal migration** in a new file:
   ```bash
   # Next migration number is always current highest + 1
   ls supabase/migrations/ | tail -5
   # e.g. if last is 073_foo.sql, create 074_revert_foo.sql
   ```
3. **Write the reversal SQL** — e.g. `DROP TABLE`, `ALTER TABLE ... DROP COLUMN`, `DROP POLICY`.
4. **Commit and push** — GitHub Actions runs the migration automatically.
5. **Verify** in Supabase dashboard → Table Editor or SQL Editor.

### Emergency: apply migration manually

```bash
# Connect to Supabase DB directly (get DATABASE_URL from Railway or .env)
psql "$DATABASE_URL" -f supabase/migrations/074_revert_foo.sql
```

---

## Checklist after any rollback

- [ ] Verify `/health` on Railway returns `status: ok`
- [ ] Verify oddsintel.app loads and match list shows data
- [ ] Check Supabase → `pipeline_runs` table — no stuck `running` rows
- [ ] Check healthchecks.io dashboard — scheduler still pinging green
- [ ] If a migration was involved: verify table structure in Supabase SQL Editor

---

## Contacts / links

| Resource | URL |
|----------|-----|
| Railway dashboard | [railway.app](https://railway.app) |
| Vercel dashboard | [vercel.com](https://vercel.com) |
| Supabase dashboard | [supabase.com/dashboard](https://supabase.com/dashboard) |
| Healthchecks.io | [healthchecks.io](https://healthchecks.io) |
| Engine repo | github.com/margus/odds-intel-engine |
| Frontend repo | github.com/margus/odds-intel-web |
