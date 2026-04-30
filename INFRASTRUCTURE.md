# OddsIntel — Infrastructure & Costs

> Last updated: 2026-04-30 — Supabase upgraded to Pro. Added model_calibration table (Platt scaling).

---

## Service Stack

| Service | Role | Plan | Status |
|---------|------|------|--------|
| **Supabase** | PostgreSQL DB, Auth, RLS, REST API | **Pro ($25/mo)** | Active — upgraded 2026-04-29 |
| **GitHub Actions** | 4 scheduled workflows (engine automation) | Free (public repos) | Active |
| **GitHub** | Source control (2 repos, both public) | Free | Active |
| **Vercel** | Frontend hosting (Next.js 16) | Hobby (free) | Active (oddsintel.app) |
| **Gemini API** | AI news checker (2.5 Flash) | Free | Active |
| **Kambi API** | Odds for 41 leagues (public) | Free (no key) | Active |
| **ESPN API** | Settlement results backup (public) | Free (no key) | Active |
| **API-Football** | PRIMARY: fixtures, results, odds, lineups, injuries, live stats | Ultra ($29/mo) | Active |
| **Sentry** | Error monitoring & alerting (frontend) | Free (5K errors/mo) | Active |
| **Stripe** | Payment processing (Pro/Elite tiers) | No monthly fee | **Test mode** — products + webhook live, awaiting production keys |
| **Domain** | oddsintel.app | Registered + connected to Vercel | Active |

### Not yet active

| Service | Role | When needed | Plan | Est. Cost |
|---------|------|-------------|------|-----------|
| **Plausible** | Alternative to Vercel Analytics if more depth needed | Optional | Cloud | €9/mo (10K pageviews) |
| **Resend / Postmark** | Transactional email (welcome, receipts, alerts) | Milestone 2 | Free tier | €0 up to 3K emails/mo |

### Stripe — going to production checklist

When ready to accept real payments (switch from test → live mode):

1. In Stripe dashboard: switch to **Live mode**
2. Re-run `source venv/bin/activate && python scripts/setup_stripe.py` with live secret key → get live price IDs
3. Update Vercel env vars: `STRIPE_SECRET_KEY`, `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`, all `STRIPE_*_PRICE_ID` and `STRIPE_*_PRODUCT_ID` vars → live values
4. Create new webhook endpoint in Stripe **live mode** → `https://www.oddsintel.app/api/stripe/webhook` → same 3 events → copy new `whsec_` secret → update `STRIPE_WEBHOOK_SECRET` in Vercel
   > **Note:** Use `www.oddsintel.app` not `oddsintel.app` — Vercel redirects the bare domain to www with a 301, and Stripe does not follow redirects.
5. ~~Upgrade Supabase to Pro ($25/mo)~~ — ✅ Done 2026-04-29

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

## Supabase Usage (Pro plan — upgraded 2026-04-29)

| Resource | Pro Limit | Current Usage (2026-04-29) | Headroom |
|----------|-----------|---------------------------|----------|
| Database size | **8 GB** | ~150-200 MB (845K odds_snapshots rows, 2 days data) | Massive — years |
| Database (rows) | — | odds_snapshots 845K, matches 885, predictions 590, match_signals 2,701 | Fine |
| Auth MAU | 100,000 | 2 users | Plenty |
| Storage | 100 GB | Not used | N/A |
| Bandwidth | 5 GB/mo | Low | Plenty |
| Backups | ✅ Daily automated + PITR (7 days) | Active | — |
| Project pausing | Never pauses | Active | — |
| Custom SMTP | ✅ Available | Not yet configured | Needed for STRIPE-EMAIL task |

**Odds snapshot growth pattern** (observed): Pipeline started April 27 — 845K rows in 2 days.
- Steady-state: ~600 scheduled matches × ~1,400 rows each = ~840K rows constant for upcoming matches
- After finishing + pruning: scheduled matches shrink from ~1,400 rows to ~22 rows (opening + closing only)
- Daily pruning runs after settlement (21:00 UTC) via `scripts/prune_odds_snapshots.py --apply`
- At 120 bytes/row steady state: ~100-200 MB for odds_snapshots. Well within 8 GB Pro limit.

**When to watch next:** If expanding to new sports (tennis, basketball) — each adds a comparable snapshot volume.

---

## Current Monthly Cost: ~€52 ($54)

| Service | Plan | Monthly Cost |
|---------|------|-------------|
| API-Football | Ultra | ~€27 ($29) |
| Supabase | Pro | ~€23 ($25) |
| Domain | oddsintel.app | ~€1 amortized |
| **Total** | | **~€51/mo** |

All other services (Vercel, GitHub Actions, Gemini, Sentry, Kambi, ESPN) on free tiers.

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
| Gemini API | Free | €0 |
| **API-Football** | **Ultra** | **~€27 ($29)** |
| Domain | oddsintel.app | ~€1/mo amortized |
| **Total** | | **~€52/mo** |

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
| 0 | — | €0 | ~€52 | **-€52** |
| 5 | 5 Pro | €25 | ~€52 | **-€27** |
| 11+1 | 11 Pro, 1 Elite | €70 | ~€52 | **~break-even** |
| 20+3 | 20 Pro, 3 Elite | €145 | ~€55 | **+€90** |
| 50+10 | 50 Pro, 10 Elite | €400 | ~€75 | **+€325** |
| 200+50 | 200 Pro, 50 Elite | €1,748 | ~€200 | **+€1,548** |

> Break-even is 11 Pro + 1 Elite subscribers, or ~14 Pro-equivalent subscriptions. Costs based on current stack: API-Football Ultra ($29) + Supabase Pro ($25) + domain (€1).

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

**Later (if needed):** Move to Option B — migrate just the live tracker to **Railway** (free tier: 500 hrs/mo), **Fly.io** (free tier), or a **Hetzner VPS** (~€5/mo). The remaining workflows (daily pipeline, news checker, odds snapshots) use ~1,380 min/month — under the 2K private-repo free limit. Total cost: €0 or ~€5/mo for VPS.

---

## Key Decisions & Notes

- **Repos are public** — keeps GitHub Actions free (saves ~$74/mo). No secrets in code; all credentials in `.env` (gitignored) and GitHub Secrets.
- **Supabase Pro** — upgraded 2026-04-29. Daily backups + PITR active. 8 GB DB limit vs 500 MB free.
- **No paid odds APIs yet** — Kambi is free/public. OddAlerts or BSD Sports Data API are candidates if we need broader bookmaker coverage later.
- **Gemini 2.5 Flash is near-free** — even at 4x/day, costs ~$1.20/month. Won't be a cost concern.
- **Live tracker is the heaviest workflow** — 132 runs/day. If GitHub ever throttles, move to Railway/Fly.io free tier or a €5/mo VPS.
