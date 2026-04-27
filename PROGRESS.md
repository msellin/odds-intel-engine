# OddsIntel — Progress Tracker

> Shared between frontend (odds-intel-web) and engine (odds-intel-engine) teams.
> Update this file when completing tasks. Check it before starting new work.

---

## Current Status

### Engine (odds-intel-engine) — Claude Agent 1
- [x] Project setup, dependencies, venv
- [x] Historical data import script (133K matches, 18 leagues, 20 seasons downloaded)
- [x] Feature engineering module (form, H2H, league position, rest days)
- [x] Prediction model (XGBoost for 1X2, O/U 2.5, BTTS with calibration)
- [x] Backtest engine (value detection, P&L simulation, calibration check)
- [x] Database schema SQL (001_initial_schema.sql)
- [x] Pushed to GitHub: github.com/msellin/odds-intel-engine
- [ ] **IN PROGRESS**: Running backtest on historical data (optimizing for speed)
- [ ] Supabase schema deployment (SQL is written, needs to be run)
- [ ] Data import to Supabase (once schema is deployed)
- [ ] Scrapers (Transfermarkt injuries, FBref xG, weather)
- [ ] API-Football integration
- [ ] The Odds API integration
- [ ] Twitter monitoring
- [ ] NLP news pipeline (Claude/OpenAI)
- [ ] Paper trading bots
- [ ] Self-evaluation engine

### Frontend (odds-intel-web) — Claude Agent 2
- [x] Next.js project initialized with TypeScript + Tailwind
- [x] shadcn/ui configured (dark theme)
- [x] All pages built with mock data:
  - [x] Landing page (/)
  - [x] Today's matches (/matches)
  - [x] Match detail with tabs (/matches/[id])
  - [x] Value bets (/value-bets)
  - [x] Track record (/track-record)
- [x] Tier gating system (Scout/Analyst/Sharp/Syndicate)
- [x] Supabase client config + GitHub configured
- [ ] Supabase Auth (real login/signup)
- [ ] Replace mock data with Supabase queries
- [ ] User profile page with tier management
- [ ] Stripe integration
- [ ] PWA manifest
- [ ] Deploy to Vercel

### Database (Supabase)
- [x] Schema SQL written (odds-intel-engine/supabase/migrations/001_initial_schema.sql)
- [x] Schema deployed to Supabase (by frontend agent)
- [ ] Historical data imported
- [ ] RLS policies tested

---

## Who Does What

| Task | Owner | Status |
|------|-------|--------|
| Database schema | Engine (written) → Frontend (deployed) | Done |
| Historical data import to Supabase | Engine | Not started |
| Supabase Auth | Frontend | Not started |
| Replace mock data with DB queries | Frontend | Not started |
| Backtest validation | Engine | In progress |
| Scrapers | Engine | Not started |
| API integrations | Engine | Not started |
| Stripe billing | Frontend | Not started |
| Vercel deployment | Frontend | Not started |

---

## Supabase Credentials

Both repos have .env files with credentials (gitignored).
- Engine: /odds-intel-engine/.env
- Frontend: /odds-intel-web/.env.local

---

## Key Decisions Made

1. **Two separate repos** (not monorepo) — frontend deploys to Vercel, engine to Railway
2. **Supabase as the hub** — both repos read/write to the same database
3. **Python for engine** — better ML/scraping ecosystem
4. **Next.js for frontend** — SSR, PWA-capable
5. **Football-first** — start with soccer, expand to tennis/esports later
6. **4 pricing tiers** — Scout (free), Analyst (€4.99), Sharp (€14.99), Syndicate (€49.99)
