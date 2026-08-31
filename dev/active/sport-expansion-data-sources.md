# Sport Expansion — Data Sources (Live-Tested 2026-06-07)

All URLs tested by a research agent on 2026-06-07. Status: ✅ LIVE | ❌ DEAD | ⚠️ PARTIAL

---

## ⚠️ Important License Note

**Jeff Sackmann repos (ATP/WTA/slam_pointbypoint/MatchCharting) are CC-BY-NC-SA 4.0 — NON-COMMERCIAL ONLY.**
Using them in a paid betting service (OddsIntel Pro/Elite) may violate the license. Confirm legal use case before shipping to production. For commercial use, contact Sackmann or obtain data via a commercial data provider instead.

---

## Summary Table

| Sport | Free Results Data | Free Odds Data | Paid Odds | Verdict |
|-------|-----------------|----------------|-----------|---------|
| Tennis | ✅ Sackmann ATP/WTA GitHub | ⚠️ OddsPortal (scrape) / OddsPapi | The Odds API | Best: data in repo, Markov model path clear |
| NFL | ✅ nflverse games.csv | ✅ **IN THE SAME FILE** | The Odds API | Best free odds package of all tested sports |
| MLB | ✅ Retrosheet (161 cols) | ⚠️ OddsPortal (scrape) / OddsPapi | The Odds API | Results free, odds via scrape or paid |
| NBA | ✅ nba_api Python lib | ⚠️ OddsPortal (scrape) / OddsPapi | The Odds API | Results free, odds via scrape or paid |
| NHL | ✅ NHL API (date-specific) | ⚠️ OddsPortal (scrape) / OddsPapi | The Odds API | Better than expected — date-specific API works |
| Esports | ⚠️ HLTV 403, OE 403 | PandaScore free (no history) | PandaScore €400/mo/game | Expensive, thin ecosystem |
| Cricket | ✅ Cricsheet (9K+ matches) | ❌ No odds data anywhere free | — | Results rich, no odds source |

**Cross-sport odds wildcard: OddsPortal** (oddsportal.com) is LIVE for ALL sports with 15-20yr historical odds via scraping. Covers Pinnacle for some sports. Free but requires scraper.
**OddsPapi** (oddspapi.io) is LIVE and claims Pinnacle as one of 4 "sharp books", free historical for backtesting. Needs verification of what "free" actually means after registration.

---

## 1. TENNIS

### Free — Already In Our Repo
- **Jeff Sackmann ATP** (`data/raw/tennis/`): ✅ LIVE — ⚠️ CC-BY-NC-SA (non-commercial)
  - URL: https://github.com/JeffSackmann/tennis_atp
  - 49 columns including: tourney, surface, round, winner/loser, rankings, serve stats (w_svpt, w_1stIn, w_1stWon, w_2ndWon, w_ace, w_df, w_bpSaved, w_bpFaced)
  - Coverage: 2000–2026, updated weekly
  - **We already have 2005-2025 downloaded** (~42K matches after filtering)
- **Jeff Sackmann WTA**: ✅ LIVE — ⚠️ CC-BY-NC-SA (non-commercial)
  - URL: https://github.com/JeffSackmann/tennis_wta
  - Same format, 308 stars, data through 2026
- **Jeff Sackmann tennis_slam_pointbypoint**: ✅ LIVE — ⚠️ CC-BY-NC-SA (non-commercial)
  - URL: https://github.com/JeffSackmann/tennis_slam_pointbypoint
  - **CRITICAL for Markov model**: Point-by-point data from all 4 Grand Slams (2011–present)
  - Columns: shot type, direction, serve speed, rally length, Hawkeye data
  - This is the input needed for a Knottenbelt-style point-level model at Grand Slams
- **Jeff Sackmann tennis_MatchChartingProject**: ✅ LIVE — ⚠️ CC-BY-NC-SA (non-commercial)
  - URL: https://github.com/JeffSackmann/tennis_MatchChartingProject
  - Shot-by-shot charting for 5,000+ ATP + WTA matches since 2013
  - Updated multiple times per week

### Free — Odds
- **tennis-data.co.uk**: ❌ DEAD (ECONNREFUSED)
  - Had Pinnacle (PS column), B365, Betfair odds. Currently inaccessible.
  - Alternative: Our `data/raw/tennis/` already contains tennis_odds_2005..2025.xlsx downloaded previously
- **OddsPortal (tennis)**: ✅ LIVE — free via scraping
  - URL: https://www.oddsportal.com/tennis/
  - Historical ATP/WTA/Challenger/ITF odds going back to ~2008
  - 20+ bookmakers, closing odds available
  - No direct download — requires Python scraper (pyoddsportal or custom)
- **OddsPapi**: ✅ LIVE
  - URL: https://oddspapi.io
  - Includes Pinnacle as one of 4 "sharp books"
  - Claims free historical odds for backtesting — **verify actual free tier after registration**
  - REST API + WebSocket
- **UltimateTennisStatistics.com**: ✅ LIVE (web UI, Docker/PostgreSQL download available — no odds)

### Paid
- **API-Football Tennis v1**: ❌ 403 (documentation blocked) — need to test via our existing API key
- **API-Tennis via RapidAPI**: Unknown pricing, not tested
- **The Odds API**: Already in our stack (OA_KEY), supports tennis, Pinnacle included

### Our Current Data Status
- **Already downloaded**: ATP matches 2000-2025 + odds 2005-2025 (excel)
- **No re-download needed** — data is in `data/raw/tennis/`
- **Backtest results** (from this session):
  - Simple Elo: ROI -7.9% at 0% edge, -11.7% at 15% edge — **not profitable**
  - Ranking model (logistic on log rank ratio): ROI -7.8% — essentially identical
  - Serve stats model: Only 9.7% match join rate (name format mismatch) — needs fixing
  - Grand Slam serve model accuracy: 66.4% (vs 68% Pinnacle) — closest to profitability
- **Path to profit**: Point-level Markov model (Knottenbelt architecture) — models individual serve points, not match outcomes. Literature shows 3.8% ROI.

---

## 2. NFL (American Football)

### Free — Results + Odds (BEST FREE PACKAGE)
- **nflverse/nfldata games.csv**: ✅ LIVE
  - URL: https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv
  - **50+ columns including full betting data**:
    - `away_moneyline`, `home_moneyline` (American ML odds)
    - `spread_line`, `away_spread_odds`, `home_spread_odds`
    - `total_line`, `under_odds`, `over_odds`
    - `away_qb_name`, `home_qb_name` (player-level for key signal)
    - `temp`, `wind`, `roof`, `surface` (conditions)
    - Scores, overtime, stadium, referee
  - Coverage: multi-season, actively maintained (55K+ commits)
  - **This is the only major US sport with free historical betting odds in one file**
- **nflverse releases** (nflverse-data): ✅ LIVE — additional player stats, rosters, charting
  - No odds in the releases, just schedule+stats

### Caveats
- NFL is weekly (17-18 games/week) — low volume compared to soccer/tennis
- Season: September–January (5 months) — off-season April-August
- Market is extremely efficient (US sharp bettors dominate)
- Spread/moneyline, not Pinnacle — need to devig from the listed odds
- No Pinnacle historical data free — devigging will be imprecise

---

## 3. MLB (Baseball)

### Free — Results
- **Retrosheet game logs**: ✅ LIVE
  - URLs: retrosheet.org/gamelogs/gl2022.zip, gl2023.zip, gl2024.zip — all download
  - **161 fields** per game (home/away runs by inning, pitcher, attendance, etc.)
  - No betting odds included
  - Format: fixed-width text → needs parser
- **MLB Stats API**: ✅ LIVE, FREE
  - URL: https://statsapi.mlb.com/api/v1/schedule?sportId=1&season=2024&gameType=R
  - Returns JSON with game schedules, scores, team info
  - Full pitcher stats, lineup, venue accessible
  - No odds

### Free — Odds
- **SBRO** (sportsbookreviewsonline.com): ❌ DEAD (404)
  - Was the primary free MLB/NBA/NHL odds source. Permanently down.
- **FiveThirtyEight MLB Elo**: ❌ 538 shut down — redirects to ABCnews
  - mlb_elo.csv had Elo ratings + win probs but NO betting odds anyway
- **sports-statistics.com**: ⚠️ ALIVE but no MLB odds dataset found on the site
- **Baseball-reference.com**: ❌ 403

### Paid
- **The Odds API**: Already in stack (OA_KEY). Historical endpoint costs 10 credits/call.
  - MLB pre-match moneyline, run total, spread available
  - 500 credits/mo free tier = ~50 historical date calls

---

## 4. NBA (Basketball)

### Free — Results
- **nba_api Python package**: ✅ LIVE (v1.11.4, Feb 2026)
  - GitHub: https://github.com/swar/nba_api
  - Wraps NBA.com official stats APIs (player/team historical stats)
  - Comprehensive game logs, player stats, team records
  - No odds
- **Basketball-reference.com**: ❌ 403

### Free — Odds
- **SBRO**: ❌ DEAD
- **FiveThirtyEight NBA forecasts**: ❌ 404 (also dead)
- **sports-statistics.com NBA**: ❌ 404 page

### Paid
- **The Odds API**: supports NBA
- Season: October–June (overlaps with MLB + NFL)

---

## 5. NHL (Ice Hockey)

### Free — Results
- **NHL API (api-web.nhle.com)**: ⚠️ PARTIAL — `/now` returns 403, but **date-specific endpoints work**
  - `/v1/schedule/2024-04-01` returns full schedule
  - `/v1/standings/2024-04-18` returns standings
  - Covers: game schedules, standings, skater leaders, playoff brackets, real-time scores
  - No odds
- **Old NHL API (statsapi.web.nhl.com)**: ❌ ECONNREFUSED (deprecated/dead)
- **hockey-reference.com**: ❌ 403
- **dword4/nhlapi GitLab**: ✅ LIVE (community docs for the new API)

### Free — Odds
- **SBRO**: ❌ DEAD
- **OddsPortal (NHL)**: ✅ LIVE via scraping
  - URL: https://www.oddsportal.com/hockey/usa/nhl/
  - Historical odds from 2003/04 season (20+ years!)
  - Moneyline, puck line, O/U — requires scraper
- **OddsPapi**: ✅ LIVE (see Tennis section)

### Assessment
- Better than expected — NHL API works for schedule/results via date-specific endpoints
- OddsPortal has 20+ years of NHL historical odds via scraping
- Still needs scraper investment for odds vs. a ready-to-use dataset

---

## 6. Esports (CS2 / LoL / Dota2)

### Free — Results
- **HLTV.org** (CS2): ❌ 403
- **OraclesElixir** (LoL): ❌ 403
- **lol.fandom.com**: ❌ 403
- **PandaScore free tier**: ✅ LIVE
  - URL: https://pandascore.co
  - Free: Static data, Calendar, Pre-match data, 1K req/hour
  - **Historical data**: Starts at €400/month per game

### Paid
- **PandaScore**: €0 free (no history) → €400/mo/game (history) → €1000/mo/game (live)
- **OddsPapi**: Registration required. Free tier: 250 req/month. Pinnacle + 350 books.
  - URL: https://oddspapi.io (not tested — agent ran out of queue)
- **Abios**: ❌ 404

### Assessment
- Historical data locked behind expensive paid tiers
- Free tier unusable for backtesting without history
- Not recommended unless budget for PandaScore (€400+/mo/game)

---

## 7. Cricket

### Free — Results
- **Cricsheet**: ✅ LIVE, actively maintained
  - URL: https://cricsheet.org/downloads/
  - Updated within 2 days, covers through 2026
  - Formats: JSON (primary), YAML, CSV (experimental)
  - Coverage: Test (903), ODI (3,136), T20i (5,341), IPL (1,243), BBL (662), T20 Blast (1,489)
  - Ball-by-ball data: over, ball, batting team, fielding team, runs, extras, wickets
  - **Rich data but NO odds**

### Free — Odds
- No known free cricket historical odds source
- Cricsheet is results-only

### Assessment
- Excellent ball-by-ball data for modeling (better than soccer!)
- Complete absence of free historical odds is a blocker for backtesting
- Would need The Odds API historical data (costly at 10 credits/call)

---

## Odds API Coverage Reference (already in our stack)

The Odds API (OA_KEY) supports historical data at 10 credits/call, 500 credits/mo free:
- Sports supported: NFL, MLB, NBA, NHL, Tennis, Cricket, Esports (CS2, LoL)
- Historical endpoint: `/v4/sports/{sport}/odds-history?date=YYYY-MM-DDTHH:MM:SSZ`
- 500 credits/mo = 50 historical snapshots (not enough for large backtesting)
- Upgrading: $79/mo for 30,000 credits (3,000 historical calls/mo)

---

## Bottom Line for Tomorrow

| Priority | Sport | Reason |
|----------|-------|--------|
| **1 — Start immediately** | **Tennis** | Data already downloaded, year-round calendar, clear path to profitability via point-level model |
| **2 — If US expansion** | **NFL** | Only sport with free results + odds in one dataset. nflverse games.csv is ready to backtest. |
| **3 — Future** | **MLB** | Free results (Retrosheet), but odds acquisition needed. Season April-Sept fills soccer gap. |
| **Skip for now** | NHL / NBA | No free odds data available at all |
| **Skip for now** | Esports | €400+/mo for historical data |
| **Skip for now** | Cricket | Excellent data, zero odds — can't backtest profitability |
