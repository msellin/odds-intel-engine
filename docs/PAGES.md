# OddsIntel Web — All Pages Reference

Last updated: 2026-05-05

---

## Public pages (no login required)

| Route | Page | What it shows |
|-------|------|---------------|
| `/` | Landing | Marketing homepage — hero, feature comparison by tier, pricing cards, FAQ, CTA buttons to sign up |
| `/login` | Login | Email + OTP login form. Also supports Google and Discord OAuth |
| `/signup` | Sign up | Account creation with email + OTP flow |
| `/terms` | Terms of Service | Legal terms of service copy |
| `/privacy` | Privacy Policy | Privacy policy copy |
| `/changelog` | Changelog | Chronological list of model improvements, feature releases, and bug fixes |

---

## App pages (login required)

All routes below are protected. Unauthenticated users are redirected to login.

### Matches & Betting

#### `/matches` — Match List
Shows every fixture we track across 280+ leagues. Sorted by league priority (starred leagues first, then by tier ranking).

- **Free:** Live scores, basic team form, community vote, worst odds
- **Pro:** Full bookmaker odds comparison (13 bookmakers), odds movement, confirmed lineups preview, injury flags
- **Elite:** Same as Pro — no extra data here specifically

Key features:
- Live score polling every 30s during active matches
- Filter by date, league, or starred leagues only
- Each row links to the match detail page

#### `/matches/[id]` — Match Detail
Full profile for a single fixture. The most data-dense page on the site.

- **Free:** Basic team info, community voting, personal pick logger, pre-match odds (worst available)
- **Pro:** Odds movement chart, all bookmaker odds, injury/suspension lists, confirmed lineups + formations, post-match stats (xG, shots, possession, cards)
- **Elite:** AI probability output, edge %, CLV tracking per pick, natural language AI explanation of the bet ("Why does the model like this?")

#### `/value-bets` — Value Bets
Daily list of all matches where the model's edge over the market exceeds the threshold.

- **Free:** 1 teaser pick per day (blurred/locked), prompt to upgrade
- **Elite:** Full list — match, selection, model probability %, market odds, edge %, CLV after settlement

---

### Personal Tracking

#### `/my-picks` — My Picks
Personal prediction tracker — separate from the AI model. You log your own picks and track your hit rate.

- **All tiers:** Log picks, see W/L record, current streak, overall accuracy %
- After 5+ picks, shows comparison hint (your accuracy vs the AI model's)

#### `/profile` — Profile & Settings
Account management page.

- Email/account info
- Current subscription tier with upgrade buttons
- Starred league management — add/remove leagues to prioritize in the match list
- Email notification toggles: daily digest, weekly report, watchlist alerts

#### `/bankroll` — Bankroll Analytics (Elite only)
Personal betting performance dashboard. Non-Elite users see an upgrade CTA.

- Summary stats: net units, ROI, hit rate, avg CLV, max drawdown, pending picks
- Cumulative units over time chart (recharts AreaChart, green/red by final position)
- Model benchmark: your hit rate and ROI vs the AI model's on the same picks
- Per-league breakdown: W/L, net units, ROI per competition
- Last 20 picks with date, match, odds, CLV, and result

#### `/predictions` — Predictions Index
SEO-optimised prediction hub. Lists 8 featured leagues with links to per-league prediction pages.

#### `/predictions/[league]` — League Predictions
Per-league prediction page (e.g. `/predictions/premier-league`). Shows upcoming fixtures with model probability bars, confidence badges (High/Medium/Low), and AI preview teasers. Includes FAQ schema markup for search indexing.

#### `/welcome` — Welcome
Post-signup onboarding screen. Shown once after account creation.

- Lists what's available on free tier
- CTA to browse matches or upgrade

---

### Analytics & History

#### `/track-record` — Track Record
Shows the AI model's historical prediction and betting performance. This is the main transparency/credibility page.

See detailed section-by-section breakdown below.

---

### Educational

#### `/how-it-works` — How It Works
Feature explainer organized by tier. Explains what Free, Pro, and Elite each include. Lists the four signal groups the model uses and gives a plain-language methodology overview.

#### `/methodology` — Methodology
Technical deep-dive. Explains the model architecture (Poisson + XGBoost blend), data sources (API-Football, Kambi, ESPN), signal types, and how predictions are generated and calibrated.

---

### Admin (superadmin only)

#### `/admin/bots` — Bot Dashboard
Aggregated statistics across 16 paper-trading AI bot strategies.

- Summary stats per bot: P&L, hit rate, ROI, CLV
- Click any bot → drill into full bet history with bankroll progression chart
- Filter by bot, market, result

---

## Track Record page — section-by-section breakdown

The track record page (`/track-record`) is public-readable but has tiered data depth. Sections in render order:

### Section 1: Hero stats (everyone)
Three cards at the top:
- **Avg Closing Line Value** — the model's average CLV across all settled bets, shown as a percentage (e.g. +28.3%). Green if positive. Also shows "X% of bets beat the closing line."
- **Value Opportunities Identified** — total count of times the model has flagged a market as mispriced since launch
- **Coverage** — number of leagues and bookmakers tracked, update frequency

### Section 2: CLV Education (everyone)
Explains what Closing Line Value means with a concrete visual example (model probability → bookmaker odds → closing line). Includes links to value-bets and how-it-works pages.

### Section 3: Live System Status (everyone)
8-cell grid showing real-time pipeline health:
- Last odds scan (time ago)
- Last prediction run (time ago)
- Matches tracked today
- Live matches right now
- Value opportunities found today
- Odds updates processed today
- Leagues tracked
- Active paper trading bots

This section exists to prove the system is actually running — not a demo.

### Section 4: Statistical Significance progress bar (everyone)
Progress toward 500 settled bets (the threshold for statistical significance). Shows current count vs target with explanatory text. Proactively tells users the sample is too small to draw strong conclusions from win rate alone — CLV is the more reliable early indicator.

### Section 5: Early Results (everyone, collapsible)
Collapsed by default. Shows:
- Predictions settled count + 1x2 accuracy (vs 33% random baseline)
- Avg CLV on bets placed
- Value bets placed count + avg edge %
- Home / Draw / Away accuracy breakdown (correct/total per outcome)
- Disclaimer about sample size volatility

### Section 6: Prediction History (collapsible, tiered)
Full log of every settled prediction, collapsed by default.

| Column | Free | Pro | Elite |
|--------|------|-----|-------|
| Date | ✅ | ✅ | ✅ |
| Match | ✅ | ✅ | ✅ |
| Pick (Home/Draw/Away) | ✅ | ✅ | ✅ |
| Conf (model confidence %) | ✅ | ✅ | ✅ |
| Worst odds | ✅ | ✅ | ✅ |
| Best odds | 🔒 PRO | ✅ | ✅ |
| CLV | 🔒 PRO | ✅ | ✅ |
| Edge % | 🔒 ELITE | 🔒 ELITE | ✅ |
| Result (✓/✗) | ✅ | ✅ | ✅ |

- **Free:** 20 rows visible, then upgrade banner
- **Pro/Elite:** All rows, load-more in batches of 50

### Section 7: Footer CTA (everyone except Elite)
Conversion close — different copy for Free vs Pro users, hidden entirely from Elite.

### Section 8: Bot P&L Dashboard (superadmin only)
Full paper-trading performance table. Only visible to accounts with `is_superadmin = true`.
- 8 summary stat cards (total bets, pending, won, lost, hit rate, ROI, total staked, total P&L)
- Filterable bet table (by bot strategy, league, result)
- Columns: date, match, league, bot, market, selection, odds, edge %, result, stake, P&L

---

## API routes (non-UI)

| Route | Purpose | Auth |
|-------|---------|------|
| `/api/bet-explain` | Gemini AI explanation for a value pick | Elite only |
| `/api/live-odds` | Real-time odds stream for match detail | Pro+ |
| `/api/stripe/checkout` | Create Stripe checkout session | Authenticated |
| `/api/stripe/portal` | Stripe customer portal link | Authenticated |
| `/api/stripe/upgrade` | Subscription upgrade | Authenticated |
| `/api/stripe/webhook` | Stripe event handler (payment updates) | Stripe signature |
| `/auth/callback` | OAuth callback (Google/Discord) | — |

---

## Tier summary across all pages

| Page | Free | Pro | Elite |
|------|------|-----|-------|
| Landing `/` | Full | Full | Full |
| Matches list | Basic odds | Full odds + injuries | Same as Pro |
| Match detail | Basic | Odds chart + lineups + stats | + AI probability + CLV + explanation |
| Value bets | 1 teaser/day | — | Full list |
| My Picks | Full | Full | Full |
| Track Record | 20 history rows | Full history + CLV | + Edge % |
| How it works | Full | Full | Full |
| Methodology | Full | Full | Full |
| Profile | Full | Full | Full |
| Admin / Bots | ❌ | ❌ | Superadmin only |
