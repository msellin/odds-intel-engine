# OddsIntel — Infrastructure & Costs

> Last updated: 2026-07-13 — SUPABASE-CLEANUP-DROP **complete**. Both Supabase projects (OddsIntel `jjdmmfpulofyykzwiuqr` + CrossRank/BoxRank shared `wvcnhlzzawvwoitkllid`) migrated to **Free tier**. OddsIntel `public` schema dropped via psql; CrossRank `public` + `box` dropped via SQL Editor. Combined saving: **~$50/mo (~€600/yr)**. Only Auth + Storage remain on Supabase for both projects.
> 2026-07-09 SUPABASE-TO-VPS — 18 GB `public` moved to Hetzner VPS Postgres 17. Supabase kept for Auth (52 users) + Storage (`models` bucket, 222 MB / 233 objects).
> 2026-07-07 VERCEL-TO-VPS — odds-intel-web migrated off Vercel to VPS pm2 + nginx.
> 2026-06-29 RAILWAY-ELIMINATION — scheduler moved to Hetzner VPS (€5.49/mo). Railway cancelled.

---

## Service Stack

| Service | Role | Plan | Status |
|---------|------|------|--------|
| **Supabase** | **Auth only** (52 users in `auth.users`) + **Storage** (`models` bucket, 222 MB / 233 objects). Data plane migrated to Hetzner VPS Postgres 17 on 2026-07-09 (SUPABASE-TO-VPS). `public` schema dropped 2026-07-13 (SUPABASE-CLEANUP-DROP). | **Free ($0)** since 2026-07-13 | Downgraded from Pro. DB 18 MB / 500 MB cap; models bucket 222 MB / 1 GB cap. |
| **Hetzner VPS** | Pipeline scheduler + LivePoller + InplayBot (long-running process) + FlareSolverr (HLTV/CS2 scraping) + **odds-intel-web Next.js frontend (pm2 + nginx)** since 2026-07-07 | **€5.49/mo** | Active since 2026-06-29 (RAILWAY-ELIMINATION). 2 vCPU / 4 GB RAM / 40 GB disk. systemd unit `oddsintel-scheduler.service` — `Restart=always`, venv Python, TZ=UTC. FlareSolverr in Docker (no persistent profile — HLTV sessions are ephemeral). Frontend at `/opt/odds-intel-web`, pm2 process `odds-intel-web` on port 3000, nginx reverse proxy on 80. GitHub Actions auto-deploy on push to main. See `docs/VPS_NEXTJS_MIGRATION_RUNBOOK.md` for the playbook to move more sites. After code push: `git pull && venv/bin/pip install -r requirements.txt && systemctl restart oddsintel-scheduler`. |
| **GitHub Actions** | Manual workflow_dispatch + DB migrations only | Free (public repos) | Active — crons disabled, ~100 min/month |
| **GitHub** | Source control (2 repos, both public) | Free | Active |
| **Vercel** | ~~Frontend hosting~~ | Free (paused 2026-07-07 at 301% of Fluid CPU quota) | **oddsintel.app migrated off Vercel to VPS 2026-07-07.** Vercel account still holds `box-ranking` and `procurement-intel` projects but service is paused. Kept as fallback until VPS-hosted frontend has 48h of clean operation, then delete. |
| **Gemini API** | news_checker (2.5 Flash), match_previews, settlement loss classifier, bet-explain (2.5 Flash Lite) | **Pay-as-you-go** (~$0.20-0.30/mo) | Active — billing enabled 2026-05-05. Free tier RPD=20 was too low (news_checker alone needs 64+/day). |
| **Kambi API** | Odds for 41 leagues (public) | Free (no key) | Active |
| **ESPN API** | Settlement results backup (public) | Free (no key) | Active |
| **API-Football** | PRIMARY: fixtures, results, odds, lineups, injuries, live stats | **150K tier ($39/mo)** | Active — ⚠️ **Do NOT downgrade to Pro** — 15s live polling needs 18K-45K calls/day (Pro limit: 7.5K) |
| **Sentry** | Error monitoring & alerting (frontend only) | Free (5K errors/mo) | Active — removed from engine/Railway (cron monitors were exceeding free budget) |
| **healthchecks.io** | External scheduler heartbeat — pings every 5min, emails if Railway goes silent | Free | Active 2026-05-08. `HEALTHCHECKS_IO_PING_URL` env var on Railway. Would have caught the 2026-05-08 pool outage in 5 min vs 11h. |
| **Stripe** | Payment processing (Pro/Elite tiers) | No monthly fee | **Live mode** ✅ — production keys active 2026-05-04. Pro €4.99/mo, Elite €14.99/mo + annual + founding rates. Promo code `REDDIT` (100% off first month). Webhook idempotency added 2026-05-08 (processed_events table). |
| **Domain** | oddsintel.app | Registered + connected to Vercel | Active |

### Active (free tier)

| Service | Role | Plan | Notes |
|---------|------|------|-------|
| **Resend** | Email digest + value bet alerts + pipeline health alerts + weekly retrain verdict + weekly threshold check (2026-06-06) + weekly bot maturity review (2026-06-15) | Free (3K emails/mo) | Active since 2026-05-01. `RESEND_API_KEY` + `ADMIN_ALERT_EMAIL` on Railway. Sunday 06:00 + 06:30 emails added 2026-06-15. |
| **The Odds API** | WC 2026 odds (AF coverage_odds=false for the WC league) | Free (500 credits/mo) | Active since 2026-06-06 — gated to 2026-06-11 → 2026-07-19 WC window. 3 credits/sweep × ~38 sweeps = ~114 credits / 500 quota. `OA_KEY` env on Railway. |
| **Cloudflare Turnstile** | Invisible captcha on anonymous Supabase signup (ANON-AUTH-PHASE-4) | Free | Active since 2026-06-10. `NEXT_PUBLIC_TURNSTILE_SITE_KEY` in Vercel envs. Supabase Auth → Attack Protection captcha toggled on. |
| **CDP-Chrome (operator's Mac)** | Coolbet JWT auto-renew via `--remote-debugging-port=9222` | Free (runs on operator's existing Mac) | Active since 2026-06-12. Separate Chrome profile `Chrome-CDP-OddsIntel`. JWT auto-renews every ~20min via Coolbet's `/s/auth/renew-token`. `workers/automation/coolbet_mac_daemon.py` reads via raw websockets. launchd-managed. |
| **Mac launchd (Coolbet HTTP jobs)** | All jobs that hit Coolbet HTTP — cannot run on VPS (Imperva 403's the Linux Chrome fingerprint + Hetzner IP) | Free (operator's Mac) | Three LaunchAgents in `local/launchd/`: `com.oddsintel.coolbet-mac-daemon` (continuous placement), `com.oddsintel.coolbet-odds-snapshot` (:03/:33 bulk odds → `odds_snapshots`), `com.oddsintel.cs2-coolbet-scanner` (:17/:47 CS2 markets → `cs2_upcoming_matches`). Both scanners moved off VPS 2026-07-03 after silent 7-day 403 outage. `oi_local_flaresolverr` Docker on `localhost:8191` handles the CF/Imperva challenge with real-Mac fingerprint. Guardrail: `test_coolbet_scrapers_moved_to_mac` smoke test. |

### Not yet active

| Service | Role | When needed | Plan | Est. Cost |
|---------|------|-------------|------|-----------|
| **Plausible** | Alternative to Vercel Analytics if more depth needed | Optional | Cloud | €9/mo (10K pageviews) |

### Stripe — production setup ✅ Done 2026-05-04

All steps complete:

1. ~~Switch to Live mode~~ ✅
2. ~~Re-run `setup_stripe.py` with live key~~ ✅ — Products: `prod_USD0AoBcAGStdg` (Pro), `prod_USD0cniBCa2i4m` (Elite)
3. ~~Update Vercel env vars~~ ✅ — All `STRIPE_*` vars updated to live values
4. ~~Create live webhook~~ ✅ — `https://www.oddsintel.app/api/stripe/webhook`, `whsec_` updated in Vercel
5. ~~Upgrade Supabase to Pro~~ ✅ — Done 2026-04-29

---

## GitHub Actions Usage

All scheduled jobs run on Hetzner VPS (systemd). GitHub Actions used only for manual triggers + DB migrations.

| Usage | Runs/month | ~Min/month |
|-------|-----------|-----------|
| Manual pipeline runs | ~5-10 | ~50 |
| DB migrations | ~5-10 | ~20 |
| Backfill (while active) | ~240 | ~600 |
| **Total** | — | **~100-200** (without backfill) |

> **Going private is now safe:** ~100-200 min/month is well under the 2,000 free private-repo limit.

---

## Supabase Usage (2026-07-13 — Free tier active)

| Resource | Free Limit | Current Usage (2026-07-13) | Headroom |
|----------|-----------|---------------------------|----------|
| Database size | **500 MB** | **18 MB** (auth 2.9 MB + storage 880 kB + supabase_migrations 496 kB + realtime 384 kB + vault 40 kB) | ✅ 27× headroom |
| Storage | 1 GB | **222 MB / 233 objects** in `models` bucket | ✅ 4.5× headroom |
| Auth MAU | 50,000 | 52 users | ✅ plenty |
| Bandwidth | 5 GB/mo | Low (auth cookie refresh only) | ✅ plenty |
| Project pausing | Auto-pauses after 1 week inactivity | Auth traffic keeps it warm | ✅ fine |
| Projects per org | 2 | 2 (OddsIntel + CrossRank/BoxRank shared) | ✅ at cap — no room to add more |

**gp3 overage retired.** Pro tier billed on disk size (24 GB gp3 auto-grown for the original 18 GB import, ~$2/mo overage above the 8 GB Pro allowance). Path A (Free tier) moves both projects to shared storage — gp3 concept goes away, dashboard may show the old 24 GB number cosmetically but it's no longer billed.

---

## Current Monthly Cost: ~€40/mo (Free tier active since 2026-07-13)

| Service | Plan | Monthly Cost |
|---------|------|-------------|
| API-Football | 150K tier | ~€36 ($39) |
| Supabase | **Free** ✅ since 2026-07-13 | €0 |
| Hetzner VPS | CX22 | ~€5.49/mo |
| Gemini API | Pay-as-you-go | ~€0.20 ($0.20) |
| Domain | oddsintel.app | ~€1 amortized |
| **Total** | | **~€40/mo** ↓ from ~€65/mo |

> Saved ~€25/mo (~€300/yr) on OddsIntel alone by dropping Supabase Pro. CrossRank/BoxRank shared project also downgraded same day for another ~€25/mo saved. Data plane on Hetzner VPS (own DB), Supabase Auth + Storage fit under Free tier caps.

> Gemini cost: ~75 calls/day × ~500 tokens/call = ~1.1M tokens/month. Flash at $0.15/1M input + $0.60/1M output ≈ $0.20-0.30/mo. Essentially free but billing must be enabled — free tier RPD cap is only 20/day.

All other services (Vercel, GitHub Actions, Sentry, Kambi, ESPN) on free tiers.

### Verification stack (added 2026-06-24, zero monthly cost)
| Service | Purpose | Cost |
|---------|---------|------|
| GitHub (public repo) | Daily ledger commits (`ledger/YYYY-MM-DD.json`) signed by `github-actions[bot]` | €0 (open-source GitHub Actions minutes well under free tier — 1 cron at ~30s/day) |
| OpenTimestamps calendars | Bitcoin blockchain anchor on every daily snapshot — free public service via `pip install opentimestamps-client` | €0 |
| Telegram (public channel `@oddsintelpicks`) | Auto-posts every calibrated-maturity pick at signal time. Free for everyone, no signup. | €0 |
| Bot rename via @BotFather | "Coolbet Bot" → "OddsIntel" | €0 |

T-24h coverage adds ~40 AF calls/day (3 new `job_odds_tomorrow` cron runs at 04/10/16 UTC) on the 7500/day Ultra budget — fits comfortably within the existing $39/mo API-Football tier.

See `DATA_SOURCES.md` for full data architecture, migration plan, and alternatives evaluation.

---

## Cost Projections by Phase

### Phase 1 + Phase 2: Current State (Milestone 1 live, Milestone 2 ready)

> Supabase Pro was added proactively before Stripe production keys — no longer a separate phase cost event.

| Service | Plan | Monthly Cost |
|---------|------|-------------|
| **Supabase** | **Pro** ✅ upgraded 2026-04-29 | ~€23 ($25) |
| Vercel | Hobby | €0 |
| Stripe | Per-transaction (when live) | ~€1-3/mo (few customers) |
| Sentry | Free | €0 |
| GitHub Actions | Free (public) | €0 |
| Gemini API | Pay-as-you-go | ~€0.20 |
| **API-Football** | **150K tier** | **~€36 ($39)** |
| Domain | oddsintel.app | ~€1/mo amortized |
| **Total** | | **~€61/mo** |

### Phase 3: Growing (50-200 users)

| Service | Plan | Monthly Cost |
|---------|------|-------------|
| Supabase | Pro | €23 |
| Vercel | **Pro** (team, previews, more bandwidth) | $20/mo (~€19) |
| Stripe | 2.9% + €0.25/txn | ~€10-25/mo |
| Sentry | Free or Team ($26/mo) | €0-24 |
| Plausible or Vercel Analytics Pro | If needed | €0-9 |
| Transactional email | Free tier likely sufficient | €0 |
| **Total** | | **~€55-100/mo** |

### Phase 4: Scale (500+ users)

| Service | Plan | Monthly Cost |
|---------|------|-------------|
| Supabase | Pro (possibly compute add-ons) | €23-50 |
| Vercel | Pro | €19 |
| Stripe | 2.9% + €0.25/txn | ~€50-100 |
| Sentry | Team | €24 |
| Dedicated odds API (OddAlerts/BSD) | If needed | €0-50 |
| Monitoring (Betterstack/Grafana) | If needed | €0-20 |
| **Total** | | **~€120-260/mo** |

---

## Revenue vs Cost Break-Even

| Subscribers | Plan Mix | Monthly Revenue | Monthly Costs | Net |
|-------------|----------|----------------|---------------|-----|
| 0 | — | €0 | ~€65 | **-€65** |
| 5 | 5 Pro | €25 | ~€65 | **-€40** |
| 13+1 | 13 Pro, 1 Elite | €80 | ~€65 | **~break-even** |
| 20+3 | 20 Pro, 3 Elite | €145 | ~€65 | **+€80** |
| 50+10 | 50 Pro, 10 Elite | €400 | ~€75 | **+€325** |
| 200+50 | 200 Pro, 50 Elite | €1,748 | ~€200 | **+€1,548** |

> Break-even is ~13 Pro + 1 Elite subscribers. Costs based on current stack: API-Football 150K ($39) + Supabase Pro ($25) + Hetzner VPS (€5.49) + domain (€1).

> Stripe takes 1.5% + €0.25/txn for EU cards, 2.9% + €0.25 for non-EU. Revenue based on Pro €4.99/mo, Elite €14.99/mo.

---

## If Repos Go Private — Cost Options

The live tracker (132 runs/day, ~9,900 min/month) is the expensive workflow. GitHub Free gives 2,000 min/month for private repos; overage is $0.008/min.

| Option | Actions Min/mo | Monthly Cost | Notes |
|--------|---------------|-------------|-------|
| Keep public (current) | 11,280 | **€0** | Code visible, but no secrets in repo — safe |
| Private, keep everything | 11,280 | **~$74** | Full overage cost |
| Private, live tracker → 15min | ~4,200 | **~$18** | Less granular live data, still fine pre-launch |
| Private, live tracker → 30min | ~2,700 | **~$6** | Good enough for pre-match odds tracking |
| Private, move live tracker off Actions | ~1,380 | **€0** | Under 2K free limit; live tracker on separate host |
| GitHub Pro ($4/mo) | 3,000 included | Saves ~$8 vs overage | Marginal improvement |

### Decided Strategy

**Now:** Stay public (Option C). The competitive moat is in the data (Supabase) and execution speed, not the code. A Poisson+XGBoost pipeline with scrapers isn't worth protecting with $74/mo.

**After LIVE-INFRA migration:** All pipeline jobs move to Railway ($5/mo). GH Actions is used only for manual triggers + backfill. Going private becomes nearly free — remaining GH Actions usage drops to <100 min/month (manual triggers only). The $74/mo concern is eliminated.

---

## Key Decisions & Notes

- **Repos are public** — keeps GitHub Actions free (saves ~$74/mo). No secrets in code; all credentials in `.env` (gitignored) and GitHub Secrets.
- **Supabase Pro** — upgraded 2026-04-29. Daily backups + PITR active. 8 GB DB limit vs 500 MB free.
- **No paid odds APIs yet** — Kambi is free/public. OddAlerts or BSD Sports Data API are candidates if we need broader bookmaker coverage later.
- **Gemini billing enabled 2026-05-05** — free tier RPD cap is only 20/day; news_checker alone needs 64+/day. Pay-as-you-go cost is ~$0.20-0.30/mo (negligible). Engine jobs use `gemini-2.5-flash`; bet-explain uses `gemini-2.5-flash-lite` (separate quota bucket).
- **Live tracker is the heaviest workflow** — 132 runs/day. If GitHub ever throttles, move to Railway/Fly.io free tier or a €5/mo VPS.
