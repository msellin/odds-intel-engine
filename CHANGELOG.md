# OddsIntel Changelog

User-facing changelog covering both engine (pipeline/model) and frontend (UI/UX) changes.
Newest entries at the top. Internal refactors and infrastructure changes are noted briefly.

---

## 2026-05-05

### Bankroll Analytics (Elite)
- New `/bankroll` page for Elite subscribers: cumulative units chart, ROI, hit rate, avg CLV, max drawdown, model benchmark comparison, per-league breakdown, and last 20 picks with CLV
- Accessible via "Bankroll Analytics" link in the profile dropdown (Elite/superadmin only)

### Watchlist Alerts
- Saved matches now trigger email alerts: kickoff reminder ≤2h before KO (all tiers), odds movement ≥5% in last 6h (Pro/Elite)
- Notification settings now manageable from the Profile page — toggle switches for daily digest, weekly report, and watchlist alerts

### Weekly Performance Digest
- New Monday morning email (08:00 UTC): model W/L/units for prior week + top upcoming fixtures
- Pro/Elite version includes your personal pick stats (hit rate, net units, avg CLV)
- Opt out via the notification settings toggle on your Profile page

### League Priority Overhaul
- Matches are now sorted by a 6-tier league system: CL/WC/Euros at top, then Europa League group, then Big 5 domestic leagues, then strong secondary, then all others
- Previously the model treated Champions League and Premier League as the same tier — now corrected

### Model
- Dynamic Dixon-Coles ρ per league tier: each tier now has its own fitted correlation coefficient (from historical scoreline frequencies) instead of a global constant. Improves low-scoring draw accuracy in lower-tier leagues.
- New paper trading bot: `bot_proven_leagues` — focuses on the 5 leagues with the strongest cross-era backtest signals (Singapore, Scotland, Austria, Ireland, South Korea)

### Predictions Pages
- New `/predictions` index and `/predictions/[league]` pages — SEO-optimised match prediction pages for 8 featured leagues, with probability bars, model confidence badges, and FAQ schema
- Linked from the main nav

### Pick Cards
- Share any of your picks as a branded image — hit "Share" on the My Picks page to get a pre-rendered OG card with match, selection, odds, and result

### Match Intelligence
- Model vs Market vs Users widget on every match detail page: three colored bars showing where the model, the implied odds, and community votes each sit — highlights tension when they disagree by >5pp

---

## 2026-05-04

### Docs Restructure
- Merged `docs/reddit_warmup_comments.md` + `docs/reddit_launch_posts.md` → `docs/REDDIT_LAUNCH.md` (single file: progress tracker + all 6 post drafts + subreddit rules)
- Moved `LAUNCH_PLAN.md` → `docs/LAUNCH_PLAN.md`
- Established convention: root `/*.md` = agent protocol docs; `docs/` = strategy, playbooks, reference

### Stripe — Live Mode
- **Payments now live**: real checkout for Pro (€4.99/mo) and Elite (€14.99/mo) — annual and founding rates also active
- Production webhook active at `https://www.oddsintel.app/api/stripe/webhook` — tier upgrades apply instantly on payment

### Bot Dashboard (Superadmin)
- **Bot detail modal**: click any bot row to see its full bet history — date, match, market, odds, stake, result, P&L, closing line value (CLV), and a bankroll progression chart
- Inactive bots (no settled bets yet) are shown greyed out but still clickable

### Alignment Signals — Expanded
- **Sharp bookmaker consensus** signal added: tracks whether sharp books (Pinnacle, Betfair, etc.) agree with the model pick direction
- **Pinnacle anchor** signal added: compares model probability vs Pinnacle-implied probability — flags picks where the sharpest market in the world agrees (+) or strongly disagrees (–)
- Fixed alignment bug: bets with no active dimensions now correctly show `NONE` instead of `LOW`

### Odds Data
- Pinnacle odds now captured during every odds-collection run and stored as a reference signal

### Matches Page
- Fixed: page now shows **today's matches only** (from 00:00) plus **yesterday's matches still in progress** — previously showed a rolling 2-day window including finished yesterday matches

### Performance
- Track record page now loads from pre-computed nightly stats instead of running heavy queries on every page load

---

## 2026-05-03

### Model Calibration
- Platt scaling fitted on 400 real match outcomes — probability calibration error (ECE) reduced by 86–97% across all markets (1x2, O/U)
- Calibration now runs automatically after settlement when enough data is available

### Infrastructure
- Dashboard cache written nightly at 21:00 UTC — track record, bot stats, and system status all read from cache

---

## 2026-05-01

### Bot Dashboard
- New superadmin-only page at `/admin/bots` showing per-bot P&L, hit rate, stakes, ROI, and market breakdown
- 16 paper trading bots running since 2026-04-27 with €1,000 starting bankroll each
- Bots bet on 1x2, O/U 1.5/2.5/3.5, and BTTS markets with Kelly-sized stakes
- Explains why some bots have 0 bets: strict edge thresholds, league filters, or market conditions not yet triggered

### Pipeline Monitoring
- Daily morning update script: bar charts, threshold progress, per-bot P&L, calibration ECE, pipeline health

---

## 2026-04-29

### Betting Bots — Expanded
- 6 new bots added (total: 16): BTTS specialist, O/U 1.5 defensive, O/U 3.5 attacking, draw specialist, optimised home/away variants
- A/B test: bots split into pre-match (2h before KO) and last-minute (30min) timing cohorts to measure information timing value
- Exposure control: stake automatically halved for 3rd+ bet in same league per bot per day

### Settlement
- Instant settlement triggered on full-time detection from live tracker (previously waited for nightly batch)
- Closing Line Value (CLV) recorded per bet for model benchmarking

---

## 2026-04-28

### Infrastructure — Railway Migration
- Pipeline moved from GitHub Actions to Railway ($5/mo) — always-on scheduler, no 12-minute job limits
- Smart live polling: 30s intervals during live matches, 60s/5min when quiet, fully automatic
- All 9 pipeline jobs (fixtures, enrichment, odds, predictions, betting, live tracker, news, settlement, pre-KO refresh) now run on Railway

### Frontend
- AI match previews published daily at 09:00 UTC (Gemini-powered)
- Email digest: subscribers receive daily match picks summary

---

## 2026-04-27

### Match List UX
- Team crests displayed on match list and detail pages
- Countdown timer to kick-off for upcoming matches
- Form strip (last 5 results) shown per team

### Track Record
- New track record page design: leads with Closing Line Value and intelligence alignment
- Bot bets shown separately from model predictions — clearer distinction
- Statistical significance progress bars: tracks milestones (30 alignment bets → 100 → 200 → 500)

---

## 2026-04-26

### Model
- XGBoost ensemble blended with Poisson model (50/50) — improved accuracy on high-variance matches
- Sharp bookmaker classification: 13 books scored by historical accuracy, feeds into signal weighting
- Dixon-Coles correction applied to home/away Poisson rates

### Signal System
- 11 signals tracked per match: odds movement, line movement, injury alerts, lineup news, form delta, ELO gap, H2H record, referee stats, situational (rest days, travel), sharp consensus, Pinnacle anchor
- Signals feed into alignment score (NONE / LOW / MED / HIGH) shown on match detail

---

## 2026-04-25

### Frontend
- Signal accordion on match detail: groups signals by category with expand/collapse
- Signal delta: shows what changed since your last visit (Pro)
- Intelligence summary card on match detail (SUX-4)
- Live in-play odds chart for Pro users during live matches
- Natural language bet explanations via Gemini (Elite tier, BET-EXPLAIN)

### Tier System
- Tier structure finalised: Free / Pro (€4.99/mo) / Elite (€14.99/mo)
- Stripe checkout, webhook, and billing portal live (test mode)
- Server-side tier gating on all Pro/Elite data — client never receives data it shouldn't have

---

## 2026-04-24

### Data Sources
- API-Football Ultra: fixtures, odds (13 bookmakers), lineups, injuries, H2H, events, player stats
- Kambi scraper: supplementary odds for 41 additional leagues
- ESPN used as settlement results backup
- Historical match data backfill: 354,000 matches across 275 leagues for model training

### Model Foundation
- Global ELO ratings: 8,385 teams
- Poisson model with 3-tier fallback (A: own history / B: league averages / C: AF predictions)
- Prediction pipeline: runs at 05:30 UTC daily, betting evaluation at 06:00 UTC
- Paper trading began — all bets logged to DB, zero real money

---

## 2026-04-20

### Launch
- OddsIntel engine repo initialised
- Public matches page live (no auth required)
- Auth: magic link OTP + Google OAuth
- Free tier: match list, signal grades, today's picks teaser
- Pro tier: full signal detail, odds movement, lineups, injuries, value bets
