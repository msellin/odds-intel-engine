# OddsIntel — Product Roadmap

> Living document. Update this as tasks are completed, decisions change, or priorities shift.
> Last updated: 2026-04-28

---

## Product Vision

Two parallel lanes, one product:

**Lane 1 — Match Intelligence** (data & analytics)
A genuinely useful place to research football matches — odds comparison, form, stats, AI injury alerts. Most of the value is visible free; depth unlocks with subscription.

**Lane 2 — Betting Tips** (model-driven picks)
Our best bot's picks surfaced as tips for paying users. Only launched once ROI is validated with real settled bets. Not sold as "bots" — sold as picks with model confidence.

Both lanes feed the same frontend. Tips are the Elite tier differentiator when ready.

---

## Tier Structure

| Tier | Price | Target user |
|------|-------|-------------|
| **Free (Scout)** | €0 | Casual football fan, curious about the product |
| **Pro (Analyst)** | €19/mo | Does own research, wants better data |
| **Elite (Sharp)** | €49/mo | Serious bettor, wants model-backed picks |

### What each tier includes

**Free (Scout)**
- All 467 daily fixtures (teams, kickoff, league)
- Live scores during matches
- Odds from 2-3 bookmakers (not full comparison)
- Match interest indicator (🔥 / ⚡ / —) based on data coverage and activity
- Today only — no historical view

**Pro (Analyst)**
- Everything in Free
- Full odds comparison across all sources
- Pre-match odds movement timeline
- Team form, H2H, goals stats
- AI injury/suspension alerts per match
- Directional model signal (Home lean / Away lean / Even — no raw %)
- Full match history, not just today
- All matches we have model data for

**Elite (Sharp)** ← launch when 60+ settled bets validated
- Everything in Pro
- Exact model probability % and edge %
- Value bet list (what the model would pick today)
- CLV tracking (beat the closing line analysis)
- Tips from top-performing bot once ROI validated
- Early access to new league signals

### Key UX principle
**Everyone sees all matches. Depth of information varies by tier.**

A free user sees all 467 fixtures but with basic info. A Pro user sees the same list but with odds/form/signals on the ones we have data for. No one loses matches from their dashboard by upgrading — they gain depth.

Filter toggle: "Show all matches" (default) / "Show matches with [my tier] data" — additive, not restrictive.

---

## Current System State (2026-04-28)

### Backend (odds-intel-engine)
| Component | Status | Notes |
|-----------|--------|-------|
| API-Football Ultra | ✅ PRIMARY | 75K req/day, all endpoints T1–T13 integrated |
| Fixtures (AF primary) | ✅ Running | 143+ fixtures/day with venue + referee |
| Odds (13 bookmakers) | ✅ Running | Per-bookmaker stored in odds_snapshots |
| Poisson + XGBoost model | ✅ Running | 50/50 blend, 3-tier fallback, 92% match coverage |
| Daily pipeline (GitHub Actions) | ✅ 08:00 UTC | T2/T3/T9/T10 enrichment + predictions + bets |
| AI news checker | ✅ 09:00 UTC | Gemini 2.5 Flash, flags non-injury news |
| Settlement pipeline | ✅ 21:00 UTC | T4/T8/T12 enrichment + settle + CLV + ELO |
| Live tracker | ✅ Every 5min | T5/T6/T7/T8 live data, 12-22 UTC |
| Odds snapshots | ✅ Every 2h | CLV timeline + live odds (is_live flag) |
| 6 paper trading bots | ✅ Since 2026-04-27 | Accumulating ROI data |
| Match enrichment (T2–T13) | ✅ Done | team stats, injuries, standings, H2H, lineups, events, players |

### Frontend (odds-intel-web)
| Page | Status | Notes |
|------|--------|-------|
| Landing page | ✅ Built | With pricing tiers |
| Auth (login/signup) | ✅ Built | Supabase Auth |
| /matches | ✅ Public | Smart sort (odds first), dual layout, view toggle, no login required |
| /matches/[id] | ✅ Public | Free: best odds + pro teaser. Auth: full odds comparison |
| /value-bets | ✅ Built | Shows bot picks — needs tier gating |
| /track-record | ✅ Built | Bot performance — verify real data connected |
| /profile | ✅ Built | User preferences |
| Stripe payments | ❌ Not built | Needed for Pro/Elite |
| Public free-tier view | ✅ Built | /matches loads without login, interest indicators |

### Data coverage reality
- Fixtures with any model data: ~200/467 (43%) — limited by odds coverage
- Matches with Tier A predictions (calibrated): ~50-100/day
- Matches with Tier B predictions (new leagues): expands to ~180/day
- Singapore S.League (+27.5% ROI): no odds feed yet — biggest gap

---

## Milestones

### Milestone 1 — Free Tier Launch
**Goal:** Public-facing product. Someone can find the site, see today's matches, understand what the product is, and sign up.

**Status:** 🟡 In progress

Tasks:
- [x] **B1** — Public fixtures endpoint (`getPublicMatches()` via Supabase anon key, no auth required)
- [x] **B2** — Match interest score (`interestScore()` → 🔥/⚡/— based on odds availability)
- [x] **F1** — Public matches page (smart sort, dual layout, "with odds" toggle, sign-up banner)
- [x] **F1b** — Public match detail page (`getPublicMatchById()` — best odds, data coverage, pro teaser for free users)
- [ ] **F7** — Stitch redesign: landing page (3-tier pricing, honest copy) + matches page (collapsible leagues, dense rows with inline odds, search filter). Prompted 2026-04-27 with competitive research (Flashscore/OddsPortal/SofaScore/FotMob/Bet365). Awaiting designs.

**Ready to launch when:** Public page shows today's fixtures with interest indicators, signup flow works, free tier is clearly differentiated from what you get paid.

---

### Milestone 2 — Pro Tier Launch
**Goal:** First paying customers. Enough depth to justify €19/mo.

**Status:** 🔲 Not started

Tasks:
- [ ] **B3** — Tier-aware data API (Next.js layer strips fields by tier — odds detail, model signals, AI alerts)
- [ ] **B4** — news_checker.py runs 4x/day (add 12:30, 16:30, 19:30 UTC crons for lineup-confirmed checks)
- [x] **F2** — Tier-gated match depth (free: best odds + blurred pro teaser, auth: full odds table via TierGate)
- [x] **F3** — "All matches / With odds only" view toggle on matches list
- [ ] **F4** — Live score display (matches in progress show current score + last updated)
- [x] **F6** — Track record page connected to real Supabase data (`getAllBets()` from `simulated_bets`)
- [ ] **F8** — Stripe integration (Pro + Elite products, webhook handler, tier column update)
- [ ] **F9** — Onboarding flow (post-signup: show what free vs pro vs elite unlocks, with real examples)

**Ready to launch when:** Stripe works, Pro users see odds/form/directional signals, free users see the gap and understand the upgrade.

---

### Milestone 3 — Elite Tier + Tips Launch
**Goal:** Tips product live. Requires validated ROI.

**Status:** 🔲 Blocked on data collection

**Blocking condition:** Top-performing bot needs 60+ settled bets with positive ROI. At current pace (~5-10 bets/day), earliest: ~2 weeks.

Tasks:
- [ ] **B5** — Tier B backtest (`scripts/backtest_tier_b.py`) — validate Scotland/Austria/Ireland ROI before trusting live Tier B bets
- [ ] **B6** — Singapore/South Korea odds source (Pinnacle API or OddsPortal for Asian leagues — +27.5% ROI signal)
- [ ] **B7** — Tip validation milestone tracker (alert when bot crosses 60 bets + positive ROI threshold)
- [ ] **F5** — Value bets page redesign (free=teaser/locked, Pro=directional, Elite=full picks with edge %)
- [ ] **F10** — My bets / tip tracking (user follows a tip → `user_bets` table, personal P&L vs model P&L)

**Ready to launch when:** B7 fires (ROI validated), Stripe Elite tier active, F5 shows picks cleanly.

---

### Ongoing / Infrastructure
- [x] **B-OPS1** — Gemini API key: get dedicated key from AI Studio (Pro license), replace borrowed key before production
- [x] **B-OPS2** — Add `GEMINI_API_KEY` to GitHub secrets (manual step, needed for news checker in Actions)
- [x] **B-OPS3** — Migration 003: run `ALTER TABLE simulated_bets ADD CONSTRAINT uq_bet_...` in Supabase SQL editor (prevents duplicate bets)
- [x] **B-OPS4** — BetExplorer odds scraper (covers Singapore, S. Korea, Scotland lower divs, Ireland, Austria — fills gap leagues via Ajax API, no Playwright needed)
- [x] **B-OPS5** — RLS public read policies added to Supabase (matches, teams, leagues, odds_snapshots, bots, simulated_bets, predictions, live_match_snapshots, match_events, news_events)
- [x] **B-OPS6** — GitHub Actions `contents: write` permission for daily pipeline git push
- [x] **B-OPS7** — Data quality pipeline: match_stats population, ELO storage, model_evaluations, team form cache (migration 005)
- [x] **B-OPS8** — ~~BSD Sports Data API~~ Superseded by API-Football Ultra (13 bookmakers already stored per match)
- [ ] **B-DATA1** — European Soccer DB (Kaggle): download + parse 13-bookmaker odds → train sharp/soft bookmaker model
- [ ] **B-DATA2** — Footiqo: check gap league coverage (Singapore, S. Korea, Scotland) → validate +27.5% ROI signal with independent odds
- [ ] **B-DATA3** — OddAlerts API evaluation: 20+ bookmakers real-time odds for live sharp money detection + user-facing odds comparison

---

## Launch Checklist (pre-Milestone 1 go-live)

### Manual steps (only Margus can do these)
- [x] Gemini API key — created in AI Studio for OddsIntel project
- [x] Migration 009 applied in Supabase SQL editor (2026-04-28)
- [x] `shots_on_target_home/away` columns added to match_stats + schema reloaded
- [x] Deploy to Vercel: project linked, env vars set (`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SECRET_KEY`)
- [x] Domain: oddsintel.app bought and connected to Vercel
- [ ] Stripe: create account + products (Pro €19/mo, Elite €49/mo), add keys to Vercel

### Code/infra tasks before go-live
- [ ] Set up analytics (Vercel Analytics or Plausible)
- [ ] Set up error monitoring (Sentry free tier)
- [ ] Add OG image / meta tags for social sharing
- [ ] Google Search Console — submit sitemap
- [ ] Privacy policy page

---

## Frontend Data Display Backlog

> Data is in DB and ready. These are frontend-only tasks once the relevant page exists.
> See migration 009 tables. All items require migration 009 applied (done 2026-04-28).

### Available Now (data already in DB — items 1–5 done in odds-intel-web)

| # | Feature | DB source | Tier | Notes |
|---|---------|-----------|------|-------|
| 1 | Scores on match detail | `matches.score_home/away` | Free | ✅ Done |
| 2 | Venue + referee | `matches.venue_name/referee` | Free | ✅ Done |
| 3 | Post-match stats | `match_stats` shots/possession/corners | Analyst | ✅ Done |
| 4 | Multi-bookmaker odds table | `odds_snapshots` 13 bookmakers | Analyst | ✅ Done |
| 5 | Odds movement chart | `odds_snapshots` multi-timestamp | Analyst | ✅ Done |

### Pending Frontend Work (data in DB, frontend not built)

| # | Feature | DB source | Tier | Notes |
|---|---------|-----------|------|-------|
| 6 | Team season stats card | `team_season_stats` | Pro | Form "WWDLW", goals avg, clean sheet% home/away |
| 7 | Injury list per team | `match_injuries` | Pro | Red/orange dot, player name + status |
| 8 | HT vs FT stats comparison | `match_stats.*_ht` columns | Pro | Post-match only |
| 9 | Live odds in-play | `odds_snapshots` with `is_live=true` | Pro | Live chart during match |
| 10 | Live score badge | `live_match_snapshots` | Free | Score + minute on match list |
| 11 | Lineup card | `matches.lineups_home/away`, `formation_*` | Pro | Formation + starting XI |
| 12 | Match event timeline | `match_events` | Free (goals) / Pro (full) | Goals/cards as icons at each minute |
| 13 | H2H history table | `matches.h2h_raw`, `h2h_*_wins` | Free | Last 10 meetings |
| 14 | League mini-standings | `league_standings` | Free | Both teams' current position |
| 15 | Player ratings table | `match_player_stats` | Pro | Post-match only, rating + goals + assists |

---

## Completed Work Log

| Task | Date | Summary |
|------|------|---------|
| TASK-01 Free Tier Foundation | 2026-04 | Public matches page, auth, track record, tier gating, interest score |
| TASK-06 Model Improvements P1-P4 | 2026-04-28 | Calibration, odds movement, Kelly stake sizing, alignment filter (log-only) |
| TASK-07 API-Football T1–T13 | 2026-04-28 | All 13 enrichment endpoints integrated, 6 new DB tables, live tracker rewrite, backfill script |

---

## Open Decisions

These need an answer before the relevant tasks can start:

| Decision | Options | Status |
|----------|---------|--------|
| Tier names final? | Scout/Analyst/Sharp vs Free/Pro/Elite vs other | ⏳ Pending |
| Tips: sell as picks or sell as "bot signals"? | "Today's picks" vs "What bot_X would bet" | ⏳ Pending |
| Design: Stitch review or partial redesign? | Polish after free tier vs Stitch redesign | ⏳ Pending — skip until after M1 |
| user_bets feature needed for launch? | Option A (follow a tip) vs skip for now | ⏳ Pending — skip until Milestone 3 |
| Pricing final? | €19/€49 vs other | ⏳ Pending |
| Single domain vs app subdomain? | oddsintel.ai for everything vs app.oddsintel.ai | ✅ Decided — single domain for now |

---

## Notes & Context

**Why bots are internal tools, not the product:**
The 6 bots (bot_aggressive, bot_conservative, etc.) are validation instruments — they help us find which markets and leagues have real edge before selling tips. The product is the picks that come out of the best-performing bot, not the bots themselves.

**Scotland League Two cross-era signal:**
+12.3% ROI in mega backtest (2005-15) AND +21% in recent 2022-25 backtest. Two different models, two different eras, same direction. Most consistent signal we have.

**Greece/Turkey era sensitivity:**
bot_greek_turkish showed positive ROI in 2022-25 data but negative in 2005-15 data. Keep running for data collection but don't promote to tips until 30+ settled bets confirm current-era edge.

**CLV as the short-term proof metric:**
Results take weeks to accumulate (variance). CLV (did we beat the closing line?) is measurable within days of picking. A bot beating the closing line consistently = finding real value even before wins/losses confirm it.
