# OddsIntel — Infrastructure & Costs

> Last updated: 2026-04-27

---

## Service Stack

| Service | Role | Plan | Status |
|---------|------|------|--------|
| **Supabase** | PostgreSQL DB, Auth, RLS, REST API | Free | Active |
| **GitHub Actions** | 4 scheduled workflows (engine automation) | Free (public repos) | Active |
| **GitHub** | Source control (2 repos, both public) | Free | Active |
| **Vercel** | Frontend hosting (Next.js 16) | Hobby (free) | Not yet deployed |
| **Gemini API** | AI news checker (2.5 Flash) | Free | Active |
| **Sofascore API** | xG post-match only (unofficial/public) | Free (no key) | Active (demoted) |
| **Kambi API** | Odds for 41 leagues (public) | Free (no key) | Active |
| **ESPN API** | Settlement results backup (public) | Free (no key) | Active |
| **API-Football** | PRIMARY: fixtures, results, odds, lineups, injuries, live stats | Ultra ($29/mo) | Pending setup |

### Not yet active (needed for launch)

| Service | Role | When needed | Plan | Est. Cost |
|---------|------|-------------|------|-----------|
| **Domain** | oddsintel.ai or similar | Milestone 1 (free tier launch) | One-time purchase | ~€10-15/yr |
| **Vercel Analytics** | Privacy-friendly page analytics (no GDPR banner) | Milestone 1 | Included in Hobby | Free (up to 2.5K events/mo) |
| **Sentry** | Error monitoring & alerting | Milestone 1 | Free (5K errors/mo) | €0 |
| **Stripe** | Payment processing (Pro/Elite tiers) | Milestone 2 (Pro launch) | No monthly fee | 2.9% + €0.25/txn |
| **Plausible** | Alternative to Vercel Analytics if more depth needed | Optional | Cloud | €9/mo (10K pageviews) |
| **Resend / Postmark** | Transactional email (welcome, receipts, alerts) | Milestone 2 | Free tier | €0 up to 3K emails/mo |

---

## GitHub Actions Usage

Both repos are **public** — GitHub Actions minutes are unlimited for public repos.

| Workflow | Schedule | Runs/day | ~Min/run | ~Min/day |
|----------|----------|----------|----------|----------|
| Daily Pipeline | 08:00 + 21:00 UTC | 2 | 3-5 | ~8 |
| News Checker | 4x/day (09:00, 12:30, 16:30, 19:30 UTC) | 4 | 2-3 | ~10 |
| Odds Snapshots | 11x/day (every 2h + 2 pre-match) | 11 | 2-3 | ~28 |
| Live Tracker | Every 5min, 12-22 UTC | 132 | 2-3 | ~330 |
| **Total** | | **~149** | | **~376 min/day** |

Monthly estimate: **~11,280 min/month**

> **If repos ever go private:** GitHub Free gives 2,000 min/month. Overage is $0.008/min = **~$74/month**. Keep repos public to avoid this.

---

## Supabase Usage

| Resource | Free Tier Limit | Current Usage | Headroom |
|----------|----------------|---------------|----------|
| Database | 500 MB | Low (<50 MB) | Plenty |
| Auth MAU | 50,000 | 0 (no users yet) | Plenty |
| Storage | 1 GB | Not used | N/A |
| Bandwidth | 2 GB | Low | Plenty |
| Edge Functions | 500K invocations | Not used | N/A |
| Realtime connections | 200 concurrent | Not used | N/A |

**When to upgrade:** Supabase Pro ($25/mo) will be needed when:
- DB exceeds 500 MB (est. 3-6 months at current data rate)
- You want daily backups / point-in-time recovery (recommended before accepting payments)
- You need more than 200 concurrent realtime connections

---

## Current Monthly Cost: ~€27 ($29)

API-Football Ultra is the only paid service. Everything else runs on free tiers. Both repos are public.

See `DATA_SOURCES.md` for full data architecture, migration plan, and alternatives evaluation.

---

## Cost Projections by Phase

### Phase 1: Free Tier Launch (Milestone 1)

| Service | Plan | Monthly Cost |
|---------|------|-------------|
| Supabase | Free | €0 |
| Vercel | Hobby | €0 |
| Vercel Analytics | Included | €0 |
| Sentry | Free | €0 |
| GitHub Actions | Free (public) | €0 |
| Gemini API | Free | €0 |
| **API-Football** | **Ultra** | **~€27 ($29)** |
| Domain | .ai domain (yearly) | ~€1/mo amortized |
| **Total** | | **~€28/mo** |

### Phase 2: Pro Launch — First Paying Users (Milestone 2)

| Service | Plan | Monthly Cost |
|---------|------|-------------|
| Supabase | **Pro** (backups before payments) | $25/mo (~€23) |
| Vercel | Hobby | €0 |
| Stripe | Per-transaction | ~€1-3/mo (few customers) |
| Sentry | Free | €0 |
| Domain | | ~€1/mo |
| **Total** | | **~€27/mo** |

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
| 0 | — | €0 | €0 | €0 |
| 5 | 5 Pro | €95 | ~€27 | **+€68** |
| 10+2 | 10 Pro, 2 Elite | €288 | ~€55 | **+€233** |
| 50+10 | 50 Pro, 10 Elite | €1,440 | ~€100 | **+€1,340** |
| 200+50 | 200 Pro, 50 Elite | €6,300 | ~€200 | **+€6,100** |

> Stripe takes 2.9% + €0.25 per transaction — factored into cost estimates above.

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

**Later (if needed):** Move to Option B — migrate just the live tracker to **Railway** (free tier: 500 hrs/mo), **Fly.io** (free tier), or a **Hetzner VPS** (~€5/mo). The remaining workflows (daily pipeline, news checker, odds snapshots) use ~1,380 min/month — under the 2K private-repo free limit. Total cost: €0 or ~€5/mo for VPS.

---

## Key Decisions & Notes

- **Repos are public** — keeps GitHub Actions free (saves ~$74/mo). No secrets in code; all credentials in `.env` (gitignored) and GitHub Secrets.
- **Supabase Pro is the first real cost** — upgrade before accepting payments (need backups).
- **No paid odds APIs yet** — Sofascore + Kambi are free/public. OddAlerts or BSD Sports Data API are candidates if we need broader bookmaker coverage later.
- **Gemini 2.5 Flash is near-free** — even at 4x/day, costs ~$1.20/month. Won't be a cost concern.
- **Live tracker is the heaviest workflow** — 132 runs/day. If GitHub ever throttles, move to Railway/Fly.io free tier or a €5/mo VPS.
