# Pending Manual Steps

Things that require Margus to do manually (can't be done by an agent).
Delete each item once done.

---

## odds-intel-engine

### 0. Run migration 006 in Supabase
Run `supabase/migrations/006_model_improvements.sql` in Supabase SQL editor.
Adds 11 new columns to `simulated_bets` for calibration, alignment, Kelly, odds movement.
**Must be done before next daily pipeline run** — pipeline will try to write these columns.

### 1. Fix Gemini API key before production
Current key belongs to a different GCP project (AI Training Analyst — being dropped).
Before going live with paying users: create a new GCP project for OddsIntel,
generate a fresh API key, update GitHub secret + local .env.

---

## odds-intel-web

### 2. Deploy to Vercel
- Link project to Vercel via CLI or dashboard
- Set environment variables: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- Push to trigger deployment

### 3. Buy domain
oddsintel.ai or similar. Connect to Vercel project.

### 4. Stripe integration (before Pro tier launch)
- Create Stripe account + products (Pro €19/mo, Elite €49/mo)
- Add Stripe keys to Vercel env vars
- Implement checkout + webhook handler in odds-intel-web
