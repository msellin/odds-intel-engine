# OddsIntel — Progress Tracker

> Shared between frontend (odds-intel-web) and engine (odds-intel-engine) teams.
> Last updated: 2026-04-27

---

## Current Status

### Engine (odds-intel-engine)

**DONE:**
- [x] Historical data: 133K soccer matches, 18 leagues, 20 seasons
- [x] Soccer model: 10 iterations (v0-v10), all documented in SOCCER_FINDINGS.md
- [x] Tennis model: 11 iterations (v0-v10), all documented in TENNIS_FINDINGS.md
- [x] Feature engineering: ELO, xG proxy, form, H2H, rest days
- [x] Kambi odds scraper (Unibet/Paf) — free, real odds, working
- [x] Sofascore fixture scraper — free, 467+ matches/day, working
- [x] Team name mapping (Kambi → football-data format)
- [x] Supabase client for all DB operations
- [x] Daily pipeline v2 — stores matches/odds/bets in Supabase
- [x] 5 bot users created with different strategies
- [x] First live run: 19 matches, 12 predicted, 10 bets placed in Supabase
- [x] GitHub Actions workflow for automated daily runs
- [x] All pushed to github.com/msellin/odds-intel-engine

**IN PROGRESS / NEXT:**
- [ ] Fix odds_snapshots column names (created_at issue)
- [ ] Predictions storage (currently 0 stored — need to debug)
- [ ] Settlement pipeline (match results → settle pending bets)
- [ ] Add Supabase secrets to GitHub repo for Actions to work
- [ ] Improve team name coverage (Swedish/Estonian/Danish teams missing)
- [ ] Add Coolbet odds scraping (blocked by Incapsula, may need alternative approach)

### Frontend (odds-intel-web)

**DONE:**
- [x] All pages built with mock data
- [x] Supabase schema deployed
- [x] Tier gating system
- [x] Now reading from Supabase (switching from JSON to DB queries)

**IN PROGRESS / NEXT:**
- [ ] Supabase Auth (real login/signup)
- [ ] Display real match data from Supabase
- [ ] Display real bot performance
- [ ] Stripe integration
- [ ] Deploy to Vercel

### Tennis Module
- [x] Research documented (TENNIS_RESEARCH.md)
- [x] 317K matches downloaded (ATP + WTA + Challenger)
- [x] 100K matches with odds
- [x] Surface-specific ELO system
- [x] 11 model versions tested
- [x] Data leakage bug found and fixed (fatigue features)
- [x] Results: 1-3% short of profitability (same as soccer)
- [x] ATP 250 = softest market (not WTA as expected)

---

## Key Findings (Both Sports)

Both soccer and tennis models show the SAME pattern:
- Pure stats models are 2-5% ROI short of profitability
- Bookmakers already price in ELO, form, serve stats, etc.
- The gap can ONLY be closed by real-time information (injuries, lineups, news)
- Lower-tier events (soccer tier 3-4, tennis ATP 250) are softer markets
- Selectivity (fewer, higher-edge bets) consistently improves ROI

**The real edge = SPEED, not better stats.** Getting injury/lineup news 1-2 hours before odds adjust is where professional bettors make their 3-8% ROI.

---

## Data in Supabase (live)

| Table | Rows | Notes |
|-------|------|-------|
| bots | 5 | Different strategies |
| matches | 19 | Today's matches |
| simulated_bets | 10 | Pending bets across 4 bots |
| teams | ~30 | Auto-created from match data |
| leagues | ~15 | Auto-created from match data |
| odds_snapshots | 0 | Column name bug being fixed |
| predictions | 0 | Storage bug being fixed |

---

## Architecture

```
Sofascore API (free) → Fixtures/Results
Kambi API (free)     → Odds (Unibet/Paf)
                          ↓
              Python Daily Pipeline
                          ↓
                  Supabase Database
                          ↓
              Next.js Frontend (Vercel)
```

---

## Supabase Credentials

Both repos have .env files with credentials (gitignored).
Engine: /odds-intel-engine/.env
Frontend: /odds-intel-web/.env.local
