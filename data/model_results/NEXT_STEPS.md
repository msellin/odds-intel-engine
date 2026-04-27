# OddsIntel — Next Steps (Updated 2026-04-27)

## Current State

### Pipeline (fully operational once GitHub secrets set)
- **08:00 UTC** — Morning: fetch Kambi+Sofascore odds, run Poisson predictions, place paper bets
- **09:00 UTC** — AI news checker: Gemini 2.5 Flash flags injuries/suspensions per bet
- **21:00 UTC** — Settlement: match results → settle bets, compute CLV, update bankrolls
- **Every 2h** — Odds snapshots: pre-match CLV timeline (minutes_to_kickoff)
- **Every 5min** — Live tracker: in-play scores/stats/events (12-22 UTC)

### Prediction coverage
- **Tier A** (512 teams, targets_v9.csv): 18 European leagues, full odds calibration
- **Tier B** (22 leagues, targets_global.csv): Norway/Sweden/Poland/Romania/Serbia/Ukraine/Turkey/Greece/Croatia/Denmark/Iceland/Hungary/Bulgaria/Cyprus/Georgia/Latvia/Portugal + **Scotland/Austria/Ireland/South Korea/Singapore** (just added from mega backtest)
- **Tier C**: On-demand Sofascore team history

### Bots running
| Bot | Strategy | Signal source |
|-----|----------|---------------|
| bot_v10_all | All tiers, 1X2+O/U | 18-league v9 backtest |
| bot_lower_1x2 | Tier 2-4, 1X2 only | English lower league finding |
| bot_conservative | 10%+ edge only | All |
| bot_aggressive | 3% edge, high volume | All |
| bot_greek_turkish | Greece + Turkey | 2022-25 backtest ⚠️ era-sensitive |
| **bot_high_roi_global** | Scotland/Austria/Ireland/Korea | **Mega backtest (new)** |

---

## Priority Queue

### 1. Odds coverage for Singapore & South Korea (HIGH VALUE)
**Singapore S.League: +27.5% ROI across 5 consecutive seasons** — strongest signal found.
Problem: Kambi doesn't cover Singapore. Need a different odds source.

Options:
- **bet365 scraping** (most coverage, technically risky)
- **Pinnacle API** — covers Asian markets, good odds, has an unofficial API
- **OddsPortal scraping** — Singapore S.League appears there occasionally
- **Asian bookmakers**: SBOBET, 188BET have Singapore/S.Korea but no clean API

Task for next agent: research whether Pinnacle API or a free scraper can provide Singapore Premier League odds. Until then, bot_high_roi_global tracks the signal but can't bet.

### 2. Tier B Backtest — validate new leagues before trusting live bets
bot_high_roi_global is NOW placing paper bets on Scotland/Austria/Ireland/South Korea (Tier B).
We don't yet know if these ARE profitable with our current Poisson model (vs mega backtest's simpler model).

Script to build: `scripts/backtest_tier_b.py`
- Load targets_global.csv (52K matches, 2015-present)
- Run Poisson model with same edge thresholds as Tier B bots
- Output: per-league hit rate, ROI, avg odds — validate which of the 22 leagues are actually worth betting now

### 3. news_checker.py — run multiple times per day (QUICK WIN)
Currently runs once at 09:00 UTC. Lineups only confirmed ~1h before kickoff.
Update `news_checker.yml` to also run at 12:30, 16:30, 19:30 UTC to catch late lineup news.
Cost: ~$0.04/day total (Gemini 2.5 Flash).

### 4. Gemini API key — must fix before production
Current key (AIzaSy...) belongs to a different Google Cloud project (AI Training Analyst).
If that project is deleted, news checker silently breaks.
Action: create dedicated GCP project for OddsIntel, generate new key, update GitHub secret + .env.

### 5. Scotland League Two — confirm odds coverage
We know Kambi covers Scottish football (Unibet/Paf). But does it cover League Two specifically?
Check tomorrow's Kambi snapshot output — if Scottish lower-tier teams appear, we're good.

### 6. bot_greek_turkish era discrepancy
Prior backtest (2022-25): Greece/Turkey positive ROI
Mega backtest (2005-15): Greece -14.4%, Turkey -10.4%
Both used different models and different eras. The 2022-25 signal is more recent and used a better model.
Action: **do not disable** bot_greek_turkish, but wait for 30+ live settled bets before drawing conclusions.
CLV tracking will show whether it's finding value at time of pick.

---

## Data Gaps

| Gap | Status | Impact |
|-----|--------|--------|
| Singapore odds source | ❌ no solution yet | Can't bet on +27.5% ROI signal |
| South Korea odds (Kambi?) | ⚠️ unclear | K League Challenge +3.2% ROI |
| Scotland League Two (Kambi?) | ⚠️ need to confirm | League Two +12.3% ROI |
| O/U 0.5/1.5/3.5 historical odds | ❌ not in BTB data | Can't backtest O/U lines |
| OddsPortal scraper | ❌ not built | Would push coverage 43% → 80% |

---

## Key Research Findings (Reference)

### Mega backtest (354K matches, 275 leagues, 2005-15)
Top consistently profitable leagues:
1. Singapore S.League: **+27.5% ROI**, 5/5 seasons
2. Scotland League Two: **+12.3% ROI**, 2/2 seasons (also +21% in 2022-25 backtest — cross-era confirmed)
3. Ukraine Division 2: **+9.4% ROI**, 3/4 seasons
4. Estonia Esi Liiga: **+6.3% ROI**, 2/4 seasons
5. Austria Erste Liga: **+5.5% ROI**, 5/7 seasons
6. Sweden Division 1 Norra: **+4.7% ROI**, 5/7 seasons

Pattern: obscure/lower-tier leagues = less bookmaker pricing effort = more edge.
Top leagues (England, Germany, Spain) = worst ROI (-9% to -15%).

Full findings: `data/model_results/MEGA_BACKTEST_FINDINGS.md`

### Prior backtest (18 leagues, 2022-25)
- Tier 3-4: +4.8% to +21% ROI
- Greece Super League: +45% (v10 model) — era-sensitive
- Turkey Super Lig: positive (era-sensitive)
- Scotland lower tiers: +21%

Full findings: `data/model_results/SOCCER_FINDINGS.md`
