# OddsIntel — Sports Betting Intelligence Platform

## Master Research & Architecture Document

> Compiled from 6 independent research sources (3 AI agents + 3 external AI tools)
> Date: 2026-04-26

---

## TABLE OF CONTENTS

1. [Which Sports Are Most Beatable](#part-1-which-sports-are-most-beatable)
2. [Which Leagues Have the Softest Markets](#part-2-which-leagues-have-the-softest-markets)
3. [Real-Time News Sources & Data Edges](#part-3-real-time-news-sources--data-edges)
4. [Data APIs — What to Build With](#part-4-data-apis--what-to-build-with)
5. [What Makes a Betting Model Actually Work](#part-5-what-makes-a-betting-model-actually-work)
6. [Regulatory & Practical Considerations](#part-6-regulatory--practical-considerations)
7. [Strategic Conclusions & App Architecture Direction](#part-7-strategic-conclusions--app-architecture-direction)
8. [Validation System — Paper Trading & Self-Improvement](#part-8-validation-system--paper-trading--self-improvement)
9. [Technical Architecture & Costs](#part-9-technical-architecture--costs)
10. [Product Tiers & Monetization Strategy](#part-10-product-tiers--monetization-strategy)

---

## PART 1: Which Sports Are Most Beatable

### The Core Principle

**Beatability is inversely proportional to bookmaker attention.** The more money and analytical firepower pointed at a market, the more efficient it becomes. The edge lives in the gaps — where data exists but bookmakers have not fully priced it in.

Important distinction: **Predictability ≠ Profitability.** The NBA is highly predictable but extremely efficiently priced. You make money from MISPRICING, not from being right.

### Consensus Ranking Across All Sources

| Rank | Sport | Beatability | Consensus Confidence | Key Reason |
|------|-------|-------------|---------------------|------------|
| 1 | **Football/Soccer (lower leagues)** | HIGHEST | All 6 sources agree | Thousands of matches, massive info asymmetry in lower divisions, bookmakers can't cover everything |
| 2 | **Tennis (WTA + Challenger)** | HIGH | 5/6 sources rank top 3 | Pure 1v1 binary sport, surface specialization, WTA more volatile than ATP, Challenger/ITF lines are automated |
| 3 | **Baseball (MLB)** | HIGH | 4/6 sources rank top 3 | 162-game season, pitcher matchups dominate, deepest statistical dataset (sabermetrics), volume overcomes variance |
| 4 | **College Basketball (NCAA)** | HIGH | 4/6 agree | 350+ teams, bookmakers can't price mid-majors, huge public bias on favorites |
| 5 | **Esports (CS2, LoL tier 2-3)** | MEDIUM-HIGH | All sources agree on potential | Immature market, roster changes bookmakers miss, patch meta shifts, but low liquidity |
| 6 | **College Football** | MEDIUM-HIGH | 2/6 sources (US-focused) | Biggest info asymmetry in US sports, 130+ FBS teams, early-season mispricing |
| 7 | **NBA Basketball** | MEDIUM | All sources agree | Load management creates edges, back-to-backs, but market is getting sharper every year |
| 8 | **MMA/UFC** | MEDIUM | 3/6 sources | Styles make fights, weight cuts hidden factor, prop markets (method of victory) are soft |
| 9 | **Ice Hockey (NHL/KHL)** | LOW-MEDIUM | All sources agree it's tough | High variance (puck luck), but KHL has info asymmetry, NHL totals market has documented inefficiency |
| 10 | **Cricket (T20)** | MEDIUM | 2/6 sources | 35-40% upset rate, pitch/dew mispriced, but IPL is getting modeled heavily |
| 11 | **Top 5 Football Leagues** | LOW | All 6 sources agree | Most efficient market in sports. Pinnacle margins 2-3%. Don't try to outsmart it on 1X2. |

### Sport-by-Sport Deep Analysis

#### Football/Soccer
- **Predictability:** Medium (low scoring = high variance per match)
- **Variance:** High (draws, red cards, penalties add randomness)
- **Market efficiency:** Varies MASSIVELY by league (this is the opportunity)
- **Data availability:** Excellent for top leagues (xG, lineups, tracking), limited for lower leagues
- **What pros focus on:** Asian Handicap markets (most liquid, lowest margin), lower-league value, early-season pricing, newly promoted/relegated teams, in-play betting
- **Edge sources:** Lineups (HUGE), motivation (relegation vs dead rubber), scheduling (Europa Thursday → Sunday league), weather

#### Tennis
- **Predictability:** High for ATP (Elo models work well), medium for WTA (more upsets)
- **Variance:** Moderate in BO3, lower in BO5
- **Market efficiency:** ATP Grand Slams sharp, WTA and Challenger very soft
- **Data availability:** Excellent (point-level data exists)
- **What pros focus on:** WTA (inconsistent players), Challenger/ITF (automated lines), surface transitions
- **Edge sources:** Injury withdrawals, travel fatigue (back-to-back tournaments), indoor vs outdoor, surface-specific Elo
- **Research finding:** Gao & Kowalczyk achieved 80%+ prediction accuracy using serve data

#### Baseball (MLB)
- **Predictability:** Very high (sabermetrics — WAR, xERA, BABIP, FIP explain 60-70% of outcomes)
- **Variance:** Medium (162-game season smooths it out)
- **Market efficiency:** Getting sharper but still exploitable via bullpen usage, weather, umpires
- **Data availability:** The most data-rich traditional sport
- **What pros focus on:** Totals (over/under), first-5-innings lines, player props, bullpen fatigue patterns
- **Edge sources:** Starting pitcher changes (biggest line-mover), platoon splits, park factors, weather (wind direction critical)

#### Esports (CS2, LoL, Dota 2)
- **Predictability:** Medium-high (meta + team form)
- **Market efficiency:** Low for tier 2-3, getting efficient for tier 1 Majors
- **CS2:** 57% of total esports betting handle (2025). Best for betting — round-based, deep stats
- **LoL:** 26.1% of turnover. Minor regions (PCS, CBLOL, LFL) much softer
- **Dota 2:** Declining market share (7.7%, down from 14% in 2023)
- **Edge sources:** Roster changes, patch/meta shifts, map-specific performance, scrim leaks

#### MMA/UFC
- **Predictability:** Low-medium (fight outcomes unpredictable)
- **Variance:** Extremely high (one punch ends it)
- **What pros focus on:** Fight style matchups (grappler vs striker), weight cuts (huge hidden factor), method-of-victory props
- **Edge sources:** Late replacements, training camp info, prop markets on prelim fights

### What Professional Syndicates Actually Do

Based on research across all sources:

1. **They measure CLV, not win rate.** Closing Line Value (difference between odds you got vs Pinnacle close) is the only reliable long-term profitability predictor
2. **They bet volume.** 500-1,000+ bets needed to overcome variance
3. **They exploit breadth, not depth.** Bet across WNBA, golf, lower-league soccer, not just EPL
4. **They target soft books.** Edge is often getting better prices before lines move, not predicting better
5. **They specialize.** 1-2 sports, 2-5 leagues max. Information edge > model complexity
6. **Realistic ROI:** 2-5% long-term. Elite syndicates maybe 5-10% with massive volume. Anyone claiming 20%+ is unsustainable.

---

## PART 2: Which Leagues Have the Softest Markets

### The Principle

Less bookmaker attention = softer odds = more exploitable inefficiencies. The tradeoff: lower-attention markets also have higher bookmaker margins (7%+ vs 3-5% in top leagues), less data, and lower betting limits.

### Football/Soccer — League-by-League

| League | Beatability | Bookmaker Margin | Why It's Beatable |
|--------|-------------|------------------|-------------------|
| **Finnish Veikkausliiga** | HIGHEST | ~5.7% | Summer schedule, Finnish-language news = info wall, minimal bookmaker attention |
| **Scandinavian leagues (Allsvenskan, Eliteserien)** | HIGH | ~5-6% | Summer calendar, extreme weather, artificial pitches, large quality gaps |
| **J2 League (Japan)** | HIGH | ~6% | "Incredible parity," multi-team promotion battles, timezone advantage |
| **Brasileirao** | HIGH | ~5-6% | Insane travel across continent-sized country, extreme squad rotation, local-media-only news |
| **Estonian Second Division** | VERY HIGH | ~7%+ | Study found abnormal profit on <10% of events, systematic mispricing of longshots |
| **3. Liga (Germany)** | HIGH | ~6-7% | Minimal global attention, bookmaker models weakest here |
| **Serie B (Italy)** | MEDIUM-HIGH | ~5-6% | Goal-scoring patterns create profitable models, less bookmaker resource |
| **2. Bundesliga** | MEDIUM-HIGH | ~5% | Good data exists but fewer bookmaker resources than Bundesliga |
| **Segunda Division (Spain)** | MEDIUM-HIGH | ~5-6% | Similar to Serie B, less attention than La Liga |
| **Ligue 2 (France)** | MEDIUM-HIGH | ~5-6% | Less coverage, local knowledge advantage |
| **English Championship** | MEDIUM | ~4-5% | Most unpredictable league in Europe, but getting more attention |
| **Eerste Divisie (Netherlands)** | MEDIUM-HIGH | ~6% | Less monitored, good data from Dutch football infrastructure |
| **Polish Ekstraklasa / Romanian Liga I** | MEDIUM-HIGH | ~6-7% | Eastern European leagues with local info edge |
| **MLS** | MEDIUM | ~5% | Salary cap parity, Designated Player mismatches, extreme travel |
| **Canadian Premier League** | SOFT | ~6%+ | 135% payout historically found in studies |
| **Cup Competitions (FA Cup, DFB-Pokal, Copa del Rey)** | MEDIUM-HIGH | Varies | Single-leg knockouts, rotation, lower-league unknowns — hard for bookmakers to price |
| **World Cup Qualifiers** | MEDIUM-HIGH | ~5% | Home advantage overestimated, lower liquidity, favorite-longshot bias |
| **Dutch Eredivisie** | MEDIUM | ~4-5% | High-scoring, attacking philosophy benefits Over/Under models |
| **Champions League** | LOW | ~3% | Extremely efficient, massive betting volume |
| **Premier League** | VERY LOW | ~3% | Maximum efficiency, syndicates, advanced models everywhere |
| **La Liga** | VERY LOW | ~3% | Same as above |
| **Bundesliga** | LOW | ~3-4% | Slightly softer than EPL but still very efficient |
| **Serie A** | LOW-MEDIUM | ~3-4% | Lower-table matches can have some value, early movers find edge |

### Tennis — Tour-by-Tour

| Tour | Beatability | Why |
|------|-------------|-----|
| **ITF Futures** | HIGHEST | Automated lines, huge info gaps, match-fixing risk though |
| **ATP Challenger** | HIGH | Minimal coverage, bookmakers copy lines with wide margins |
| **WTA (all levels)** | MEDIUM-HIGH | Higher upset frequency, wider overrounds, information asymmetry vs ATP |
| **ATP 250/500** | MEDIUM | Some pricing errors, less attention than Masters |
| **WTA 250** | MEDIUM-HIGH | Lower volumes, more volatile |
| **Grand Slams (main draw)** | LOW | Efficiently priced, massive volume |

**Surface-specific insight:**
- **Clay:** Most specialist-dependent. Clay specialists upset higher-ranked hard-court players. Models weighting surface-specific stats outperform generic ones
- **Grass:** Short season, small sample sizes, high serve dominance. Upsets more common
- **Hard court:** Most balanced, largest samples, most efficiently priced

### Basketball

| League | Beatability | Notes |
|--------|-------------|-------|
| **NCAAB mid-major conferences** | HIGH | 350+ teams, bookmakers use automated lines, Saturday volume creates soft spots |
| **EuroLeague / European leagues** | MEDIUM | Stronger home court advantage than NBA (exploitable), less global attention |
| **NBA low-profile weeknight** | MEDIUM | Load management, back-to-backs, player props exploitable |
| **NBA marquee matchups** | LOW | Maximum efficiency |

### Ice Hockey

| League | Beatability | Notes |
|--------|-------------|-------|
| **KHL (Russia)** | MEDIUM-HIGH | Massive info asymmetry — Russian sources have better analysis. CSKA/SKA dominate with budget gaps |
| **SHL (Sweden) / Liiga (Finland)** | MEDIUM | Local knowledge not accessible to international bookmakers |
| **NHL** | LOW-MEDIUM | Well-covered, but situational spots (back-to-back road, 500+ mile travel) give 4.2% ROI |

### Esports

| League/Tier | Beatability |
|-------------|-------------|
| **CS2 tier 2-3 regional** | HIGH |
| **LoL minor regions (PCS, CBLOL, LFL, ERLs)** | HIGH |
| **Dota 2 (declining attention)** | MEDIUM-HIGH |
| **VALORANT** | MEDIUM-HIGH (new, immature) |
| **CS2 tier 1 Majors/BLAST** | MEDIUM (getting efficient) |

### Key Strategic Finding

> A study of Estonia's second division found abnormal profit opportunities on less than 10% of events. Even in inefficient markets, MOST matches are correctly priced. The edge is narrow — you need to identify the specific 5-10% of matches where mispricing exists.

---

## PART 3: Real-Time News Sources & Data Edges

### The Edge Hierarchy — Where Information Breaks First

This is the most important section. Historical stats are commoditized — every tool uses them. The REAL edge is getting NEWS before odds move.

```
LAYER 1: Insiders / beat reporters on X/Twitter  → 1-4 HOURS before official
LAYER 2: Official injury reports / entry lists    → The confirmed baseline  
LAYER 3: Press conferences                        → Manager quotes reveal intent
LAYER 4: Fantasy / aggregator sites               → Fast interpretation + context
LAYER 5: Data APIs (confirmed lineups)            → 30-60 min pre-match
LAYER 6: Mainstream media (ESPN, BBC)              → By now, odds already moved
```

**Your app needs to live at layers 1-3. By layer 5-6, the value is gone.**

### Football/Soccer — Complete Source Map

#### Lineup Leaks (The Money Accounts on X/Twitter)

| Account | What They Leak | Speed | Reliability |
|---------|---------------|-------|-------------|
| **@FPL_Rockstar** | PL lineup info, 60-90 min before kickoff | Fastest | Very high — "The OG of team leaks" |
| **@Leaked_FPL** | Predicted/leaked XIs before official | Fast | High |
| **@Teamnewsandtix** | Club-specific injury updates | Very fast | High |
| **@FFScout** | Injury updates, predicted lineups, presser summaries | Fast | High |

#### League-Specific Reliable Journalists

| League | Journalist | Handle | Specialty |
|--------|-----------|--------|-----------|
| **Premier League** | Ben Dinnery | @BenDinnery | THE football injury authority |
| **Premier League** | David Ornstein | @David_Ornstein | Breaking team news, The Athletic |
| **La Liga** | Guillem Balague | @GuillemBalague | Spanish football insider (Sky Sports) |
| **La Liga** | Sid Lowe | @sidlowe | Deep La Liga analysis (The Guardian) |
| **Serie A** | Gianluca Di Marzio | @DiMarzio | Italian transfers & team news (Sky Italia) |
| **Serie A / Global** | Fabrizio Romano | @FabrizioRomano | Transfer gold standard, 23.5M followers |
| **Bundesliga** | Christian Falk | @cfbayern | Bayern & German football (BILD) |
| **Bundesliga** | Raphael Honigstein | @honigstein | German football specialist (The Athletic) |
| **Ligue 1** | Mohamed Bouhafsi | @moaborsen | French football insider |

#### Injury Tracking Sites — Ranked

| Site | Coverage | Update Speed | Best For | Cost |
|------|----------|-------------|----------|------|
| **PremierInjuries.com** (Ben Dinnery) | Premier League | Daily + breaking | PL club-by-club with expected return dates | Free/Premium |
| **Knocks and Bans (knocksandbans.com)** | Premier League | Real-time | PL injuries, suspensions, absences | Free |
| **PhysioRoom (physioroom.com)** | Premier League | Daily | Most comprehensive PL injury statistics | Free |
| **Transfermarkt.com** | Global (all leagues) | Daily | Comprehensive injury histories globally | Free |
| **RotoWire Soccer** | Multi-league | Daily | Injuries + lineup tracking combined | Free/Premium |
| **Fantasy Football Hub** | Premier League | Gameweek | Predicted lineups for every PL gameweek | Subscription |
| **Premier League Official** | Premier League | Official | Club-by-club updates direct from source | Free |
| **NBC Sports PL Injury Table** | Premier League | Weekly | 2025-26 season updates | Free |

#### Press Conference Summaries — Where to Find Fast

| Source | Coverage | Speed | Cost |
|--------|----------|-------|------|
| **FootballTeamNews.com** | 50+ tournaments | Fast | Paid — built specifically for bettors |
| **OneFootball app** | 100+ leagues | Fast | Free |
| **Sky Sports Football** | Major leagues | Fast | Free |
| **Club official YouTube channels** | Per club | Fastest | Free |
| **Club Twitter accounts** | Per club | Fastest | Free |

#### Lineup Confirmation Sources

| Source | Speed | Coverage |
|--------|-------|----------|
| **Flashscore / FotMob** | Slightly faster (direct API to organizers) | Global |
| **Official club Twitter** | Source of truth | Per club |
| **WhoScored.com** | Predicted + confirmed lineups | Major leagues |
| **RotoWire Soccer Lineups** | Pre-match confirmed | PL, Bundesliga, La Liga, Serie A |

#### Other Football Intelligence

| Data Type | Best Source |
|-----------|------------|
| **Referee assignments + tendencies** | FootyStats.org, WhoScored.com, Transfermarkt referee stats, Soccerway |
| **Formations & tactics** | WhoScored.com, Forebet.com |
| **Transfer news (squad strength)** | @FabrizioRomano, @DiMarzio, Transfermarkt |
| **Suspensions (cards)** | Transfermarkt, league official sites |
| **Fixture congestion** | Manual tracking — Europa/CL Thursday + Sunday league |
| **Travel/fatigue** | Calculate distance between cities + days between matches |
| **Motivation factors** | League table position + games remaining, relegation math |

### Tennis — News Sources

| Source | What It Covers | Speed |
|--------|---------------|-------|
| **atptour.com / wtatennis.com** | Official withdrawal announcements — appears here FIRST | Fastest |
| **@EntryLists** (X/Twitter) | Tournament entry lists and changes | Very fast |
| **@BenRothenberg** (X/Twitter) | Tennis insider news | Fast |
| **@TennisAbstract** (X/Twitter) | Statistical analysis + news | Fast |
| **Tennis365.com** | Fast aggregation of withdrawal news | Fast |

**Key tennis edge:** Tournament entry lists and withdrawal deadlines are public on ATP/WTA sites BEFORE odds adjust. Monitoring official entry/withdrawal feeds gives you a window where a player is confirmed out but bookmakers haven't yet pulled the market.

### NBA — News Sources

| Source | What It Covers | Speed |
|--------|---------------|-------|
| **@ShamsCharania** (X/Twitter) | THE fastest NBA insider (replaced Woj at ESPN) | Fastest for trades/injuries |
| **@Underdog__NBA** (X/Twitter) | Late scratches and starting fives | Fastest for lineups (beats major reporters by seconds) |
| **official.nba.com/nba-injury-report** | Mandatory report — teams submit by 5PM day before | Baseline source |
| **@NBAInjReports** (X/Twitter) | Automated fast posting of official report | Fast |
| **RotoWire NBA** | Official report + return timelines + context | Fast |
| **Team beat reporters** | Know about rest days hours before official report | 2-4 hour edge |

**Key NBA edge:** The official injury report deadline (5PM day before) creates a known window. Beat reporters tweet "Player X expected to rest" hours before. Following beat reporters = 2-4 hour edge before lines move.

### MLB — News Sources

| Source | What It Covers | Speed |
|--------|---------------|-------|
| **RotoWire Lineup Card** | THE go-to for confirmed lineups/pitchers | Fastest non-official |
| **RotoWire Daily Lineups** | Tomorrow's probable pitchers | 2-3 days out |
| **RotoGrinders MLB Lineups** | Confirmed batting orders | Fast |
| **MLB.com Starting Lineups** | Official source | Baseline |
| **Team beat writers (per club)** | Late scratches, bullpen day decisions | Fastest for changes |

**Key MLB edge:** Starting pitcher changes are the single biggest line-mover. Late scratches break on Twitter from beat reporters before any API updates.

### MMA — News Sources

| Source | What It Covers |
|--------|---------------|
| **@araborsen (Ariel Helwani)** | Elite MMA insider — fights, injuries, replacements |
| **@MMAFighting** | Comprehensive MMA news |

### Weather Data — Critical for Outdoor Sports

| Source | Type | Cost | Best For |
|--------|------|------|----------|
| **OpenWeatherMap API** | General weather | Free tier | Basic weather data |
| **Meteostat API** | Historical + forecast | Free | Matching weather to stadium locations |
| **RotoGrinders WeatherEdge** | Purpose-built for sports betting | Free | MLB/NFL specific (wind, temp at stadiums) |

**Key thresholds:**
- Wind >15 mph significantly impacts football totals
- Rain reduces passing production ~12%
- Temperature extremes (>85F or <25F) reduce scoring ~8%

---

## PART 4: Data APIs — What to Build With

### Complete API Comparison

#### Tier 1: Enterprise (Best Data, Highest Cost)

| Provider | Coverage | Lineup Data | Latency | Cost | Notes |
|----------|----------|-------------|---------|------|-------|
| **Sportradar** | 40-50+ sports, officially licensed | Yes | Sub-second | $500-5,000+/mo (custom B2B) | Industry standard. What bookmakers use |
| **Opta (Stats Perform)** | Football-focused, expanding | Yes, detailed formations | Sub-second | Enterprise only ($10K+/year) | Powers most major media outlets. Best football data quality |
| **Genius Sports** | Official NFL/NCAA partner | Yes | Real-time | Enterprise only | Official betting data for American sports |

#### Tier 2: Professional (Best Bang for Buck)

| Provider | Coverage | Lineup Data | Latency | Cost | Notes |
|----------|----------|-------------|---------|------|-------|
| **Sportmonks** | Football + Cricket + F1 | Yes (lineups, formations, jersey numbers, subs) | ~15s | EUR 69-129/mo | Best football-specific mid-tier |
| **API-Football** | 1,200+ football competitions | Yes | ~15s | $19-39/mo per sport | Best value. Free tier: 100 req/day |
| **SportsDataIO** | 10-13+ leagues (NFL, NBA, MLB, NHL, soccer, tennis) | Yes | Near real-time | $25-200/mo per sport | Good US sports coverage |

#### Tier 3: Budget / Free

| Provider | Coverage | Limitations | Cost | Notes |
|----------|----------|-------------|------|-------|
| **SharpAPI** | 8 US sports + soccer | SSE streaming, 12 req/min on free | $0/mo (free tier) | Good for prototypes |
| **football-data.org** | 12 competitions (PL, CL, etc.) | Rate limited, no live lineups | Free | Good for fixtures/standings |
| **StatsBomb Open Data** | Event-level match data | Post-match only, no live | Free | Excellent for model training (xG data) |
| **ESPN (unofficial)** | 30+ sports | 30-60s delay, no API key | Free | Scores + schedules only |
| **TheSportsDB** | Community-driven, multi-sport | Limited depth | Free / $9/mo Patreon | Good for logos/images |
| **OpenFootball** | Historical data | Static datasets, no real-time | Free | Research/training only |

#### Odds Feed Providers

| Provider | Books Covered | Type | Cost | Notes |
|----------|-------------|------|------|-------|
| **The Odds API** | 40+ global bookmakers | Polling | $30-59/mo (500 credits free) | Industry standard for odds aggregation |
| **SharpAPI** | 8 US sports + soccer | SSE streaming | Free tier available | Good for prototypes |
| **Betfair Exchange API** | Betfair only | Real-time | Free with Betfair account | CRUCIAL — sharpest odds, true market prices |
| **Pinnacle API** | Pinnacle only | Near real-time | Requires Pinnacle account | Gold standard benchmark for sharp pricing |

### Recommended Stack for MVP

| Layer | Provider | Cost/mo | What It Gives You |
|-------|----------|---------|-------------------|
| Core football data | **API-Football** | €19-39 | 1,200+ competitions, lineups, live scores |
| Deep football data | **Sportmonks** | €69 | Formations, jersey numbers, subs, better structure |
| Odds feeds | **The Odds API** | €30-59 | Real-time odds from 40+ bookmakers |
| Historical stats | **football-data.co.uk** | Free | Historical match data for model training |
| Advanced stats | **StatsBomb Open Data** | Free | Event-level xG data for model training |
| Weather | **Meteostat API** | Free | Stadium weather conditions |
| News/social monitoring | **X/Twitter API** | ~€100 | Monitor insider accounts for breaking news |
| Exchange odds | **Betfair API** | Free | True market prices, sharp benchmark |

**Total MVP cost: ~€250-350/month for data**

### Scaling Path

When revenue justifies it:
- Upgrade to **Sportradar** ($500+/mo) for sub-second latency
- Add **Opta** for the deepest football event data
- Add per-sport APIs (tennis: Sportradar Tennis, NBA: SportsDataIO)

---

## PART 5: What Makes a Betting Model Actually Work

### The Fundamental Distinction: Prediction vs Value

| Prediction Model | Value Betting Model |
|-----------------|---------------------|
| Goal: Predict who will win | Goal: Find where odds are MISPRICED |
| Output: Win probability (e.g., 58%) | Output: Edge % (book says 50%, model says 58% = 8% edge) |
| "Arsenal will beat Fulham" | "Arsenal will beat Fulham 78% of the time. At -200 odds (66.6% implied), there's an 11.4% edge" |
| Can be accurate AND lose money | Only bets when edge > threshold (3-5%) |

**Critical point:** You can accurately predict outcomes but still LOSE money if you bet at wrong odds. The app must show value, not just predictions.

### Most Important Variables/Features for Models

| Feature Category | Specific Variables | Impact |
|-----------------|-------------------|--------|
| **Lineups & Injuries** | Key players out, absence duration, formation changes | Can shift win probability 10-20%. HIGHEST impact single variable |
| **Team Form** | Last 5-10 matches, xG, shots on target, possession in final third | Current form > season-long stats |
| **Home/Away Splits** | Team performance home vs away, specific ground stats | Home advantage = 3-4 points in soccer on average |
| **Motivation** | Relegation battle, European qualification, dead rubber | Underdogs fight harder in relegation battles |
| **Rest/Travel** | Days between matches, travel distance, fixture congestion | NHL: 4.2% ROI boost in specific travel situations |
| **Head-to-Head** | Historical matchups, tactical mismatches | Some teams have "bogey teams" — limited but real |
| **Weather** | Wind, rain, temperature | Weather integration = 18.9% ROI improvement in one study |
| **Referee Tendencies** | Cards per game, penalties awarded, foul threshold | Mandatory for Asian Handicap card lines |
| **Regression to Mean** | xG vs actual goals, over/under performance | Identifies teams currently over/underperforming |
| **Scheduling** | Thursday Europa → Sunday league, midweek CL | Teams playing European midweek underperform on weekends |

### The Kelly Criterion — How Pros Size Bets

Formula:
```
f* = (bp - q) / b

Where:
f* = fraction of bankroll to wager
b  = net odds received (decimal odds - 1)
p  = your estimated probability of winning  
q  = probability of losing (1 - p)
```

**Most professionals use Fractional Kelly:**
- Starting out: 0.25 Kelly (quarter Kelly)
- Experienced: 0.5 Kelly (half Kelly)
- Full Kelly is mathematically optimal but variance is brutal

Example: If your model says 60% win probability at 2.10 odds:
```
f* = (1.10 × 0.60 - 0.40) / 1.10 = 0.236 (23.6% of bankroll)
Quarter Kelly = 5.9% of bankroll
```

### Realistic ROI Expectations

| Level | ROI | Volume Needed | Notes |
|-------|-----|---------------|-------|
| **Sharp bettors** | 2-5% long-term | 500-1,000+ bets/year | This is genuinely good |
| **Elite syndicates** | 5-10% | Massive volume | With specialization + speed |
| **Scam/unsustainable** | 20%+ claimed | Any | Small sample or fraud |

### Common Mistakes in Betting Models

1. **Overfitting** — model perfectly explains past data but fails live
2. **Ignoring lineups/news** — pure stats models miss the biggest variable
3. **Using closing odds as prediction** — closing line is a benchmark, not a predictor
4. **Not tracking CLV** — Closing Line Value is the ONLY reliable long-term metric
5. **Betting too many markets** — dilutes edge, increases variance
6. **Ignoring market liquidity** — 15% edge on a $10 max bet is worthless
7. **Not specializing** — you cannot outmodel Pinnacle on NFL spreads. Focus on 1-2 niches.

### Closing Line Value (CLV) — The Key Metric

CLV = the difference between the odds you got and the closing odds at Pinnacle.

If you consistently get better odds than Pinnacle's close, you are a profitable bettor long-term. Period. This is what separates real edge from luck.

### Odds Movement & Sharp Money Detection

**Steam move:** Rapid, market-wide odds shift caused by sharp money across multiple sportsbooks.

**Reverse Line Movement (RLM):** Odds move AGAINST public betting percentage.
- Example: 80% of bets on Team A, but odds shorten on Team B
- This means sharp money is behind Team B and bookmakers respect it
- **This is the strongest value signal in sports betting**

Best tools for tracking:
| Tool | Features | Cost |
|------|----------|------|
| **MomentumOdds.com** | Live arbitrage, sharp money, steam moves, real-time | Free |
| **OddsPortal** | Odds comparison, historical line movement | Free/Paid |
| **BetExplorer** | Line movement history | Free |
| **SportBot AI** | AI-powered steam detection + alerts | Paid |
| **OddsJam** | Odds comparison + value detection | Paid |
| **RebelBetting** | Arbitrage + value bet detection | Paid |

---

## PART 6: Regulatory & Practical Considerations

### Legal Landscape

**Generally allowed (data/analytics apps):**
- UK, EU (Estonia included), Australia, most of Europe
- USA: state-by-state, but analytics apps (not accepting wagers) are fine

**Key distinction:** If your app provides data and suggestions but does NOT accept wagers, you avoid gambling licenses entirely. You are a "data analytics/research tool."

### Required Disclaimers

- "For entertainment/informational purposes only"
- "No guarantee of profit"
- "Past performance does not indicate future results"
- 18+ gambling warning
- Local gambling addiction hotlines (per jurisdiction)
- "Not financial advice"

### App Store / Play Store Policies

- **Allowed if:** No direct betting, no guaranteed profit claims
- **Apple Guideline 5.3:** Governs gambling apps strictly. Odds-displaying apps face scrutiny
- **Must comply** with gambling laws per region
- May need to **geo-fence** in some jurisdictions
- Position as "sports analytics" not "betting tips"

### The "Gubbing" Problem

Soft bookmakers (Bet365, DraftKings, FanDuel) will **limit or ban** winning accounts.

How pros handle this:
- Use **betting exchanges** (Betfair, Matchbook) — can't be limited
- Use **sharp/no-limit books** (Pinnacle, Circa) — welcome winners
- Multiple accounts (legally grey, against TOS)
- Bet early before limits tighten
- **Your app should educate users about this**

### Positioning Strategy

**Don't position as a tipster app.** Position as:
> "We don't sell tips. We show you what the data says and let you decide."

This:
- Avoids the scam stigma of tipster culture
- Is regulatory safer
- Attracts smarter bettors (bigger spenders)
- Creates trust through transparency

---

## PART 7: Strategic Conclusions & App Architecture Direction

### Where All 6 Sources Agree

1. **Lower-league football is the most accessible edge** for a solo builder — massive match volume, clear info asymmetry, doesn't require enterprise data
2. **Lineups and injuries are the #1 variable** — more important than any statistical model
3. **The real moat is speed** — getting news before odds move, not better predictions
4. **Specialization beats breadth** — focus on 1-2 sports, 2-5 leagues max
5. **Transparency is the product differentiator** — show your work, track record honestly
6. **Value betting > outcome prediction** — the app must compare model probability vs bookmaker odds

### Recommended Sport Focus for MVP

**Primary: Football/Soccer**
- Largest global audience
- Most match volume
- Clear league tiering from soft to sharp
- Best data API ecosystem
- Your personal knowledge (you already do this manually)

**Secondary (future expansion):**
- Tennis (WTA + Challenger) — individual sport, simpler to model
- Esports (CS2) — growing, soft markets, tech-savvy audience

### Recommended League Tiering

**Tier 1 — Full AI analysis + news monitoring + value detection:**
- Premier League, La Liga, Bundesliga, Serie A, Ligue 1 (user expectation)
- Championship, Serie B, 2. Bundesliga, Segunda Division (where edge exists)
- Eredivisie (high-scoring, good for O/U models)

**Tier 2 — Stats analysis, lighter news:**
- Scandinavian leagues (summer content when top leagues off)
- Cup competitions
- Champions League / Europa League

**Tier 3 — Future expansion:**
- Brasileirao, J-League, MLS
- World Cup qualifiers
- Finnish Veikkausliiga (if you can crack Finnish-language news)

### The Killer Feature: News Impact Alerts

No existing tool does this well:

```
ALERT — Arsenal vs Brighton (Saturday 17:30)

30 minutes ago: @BenDinnery reports Saka missed 
training with hamstring tightness.

IMPACT: Arsenal's xG drops ~0.4 without Saka.
Current odds: Arsenal Win @ 1.55
Our adjusted model: Arsenal Win should be @ 1.72

→ Value has SHIFTED to Brighton +0.5 Asian Handicap
→ Bookmaker odds haven't moved yet.
```

**That alert, delivered 1-2 hours before odds adjust, is worth more than any prediction model.**

### Competitive Moat

Not the model (anyone can build one). The moat is:

1. **Accumulated trust** — months of transparent, verified track record that can't be faked
2. **Personal data lock-in** — user's betting history, preferences, bankroll, league interests
3. **Explanation quality** — AI-generated reasoning that teaches users WHY, not just WHAT
4. **Speed of news integration** — the pipeline from insider tweet to adjusted model probability
5. **Community (eventually)** — aggregated "wisdom of the crowd" alongside AI picks

### Technical Architecture Direction (High Level)

```
DATA PIPELINE:
X/Twitter API → News scraper (insider accounts)
                → NLP sentiment + entity extraction
                → Match impact assessment
                → Model probability adjustment
                → Value detection vs live odds
                → Push notification to user

API-Football → Fixtures, lineups, live scores
Sportmonks  → Deep stats, formations
The Odds API → Live odds from 40+ bookmakers  
StatsBomb    → Historical xG for model training
Meteostat    → Weather layer
Betfair API  → Sharp market benchmark (CLV tracking)

PREDICTION ENGINE:
Historical data → Train base model (xG, form, H2H, home/away)
Live data       → Adjust for lineups, injuries, weather, motivation
Odds comparison → Calculate edge % vs bookmaker implied probability
Kelly sizing    → Suggest stake based on user bankroll + edge

USER-FACING APP:
Today's matches → AI analysis per match (show reasoning)
Value finder    → Highlight mispriced odds
News alerts     → Push when something changes pre-match
Track record    → Transparent hit rate + P&L (brutal honesty)
Personal profile → Preferred leagues, markets, bankroll tracking
```

---

## PART 9: Technical Architecture & Costs

### What Needs AI vs What Doesn't

| Task | AI Needed? | What Does It Instead |
|------|-----------|---------------------|
| Historical match results | No | API or CSV import, store in DB |
| Team form (last 10 games) | No | Simple database query |
| Home/away splits | No | Database query + basic math |
| Head-to-head records | No | Database query |
| xG data | No | StatsBomb/Understat import |
| Injury history per player | No | Scrape Transfermarkt, store dates |
| Referee tendencies | No | Scrape WhoScored/Transfermarkt, calculate averages |
| Weather for past matches | No | Meteostat historical API backfill |
| Historical odds | No | football-data.co.uk (free CSVs going back 20+ years) |
| Historical lineups | No | API-Football stores past lineups |
| League tables at any point | No | Calculate from match results |
| Fixture congestion | No | Count days between matches |
| Travel distance | No | Google Maps API once, cache permanently |
| Prediction model | No | Logistic regression / Poisson / XGBoost |
| Value detection | No | Compare two numbers (model prob vs implied prob) |
| Kelly calculation | No | Simple formula |
| Track record / P&L | No | Database queries |
| Calibration analysis | No | Statistics (predicted % vs actual %) |
| **Interpreting injury tweets** | **YES** | **Claude/OpenAI API** |
| **Generating match previews** | **YES** | **Claude/OpenAI API** |
| **Summarizing press conferences** | **YES** | **Claude/OpenAI API** |

**90% of the system is regular data engineering. AI is only the news interpretation layer.**

### Complete Data Source Map (20+ Independent Feeds)

#### Layer 1: Match & Statistical Data

| Source | What It Provides | Cost | Update Speed |
|--------|-----------------|------|-------------|
| **API-Football** | Fixtures, results, lineups, standings for 1,200+ leagues | $19/mo (7,500 req/day) | 15s |
| **Sportmonks** | Deep stats, formations, player ratings, corners, cards | EUR 29-99/mo (5-30 leagues) | 15s |
| **football-data.org** | Historical data for 12 major competitions | Free (10 req/min) | Daily |
| **StatsBomb Open Data** | Event-level xG data (historical, GitHub) | Free | Static |
| **FBref** | Advanced team stats (xG, xA, possession, shots) — scrape | Free | Post-match |
| **Understat** | xG per match, per team, per player — scrape | Free | Post-match |
| **Transfermarkt** | Player values, injury history, transfers, managers — scrape | Free | Daily |
| **WhoScored** | Formations, lineups, player ratings, referee stats — scrape | Free | Post-match |

#### Layer 2: Odds & Market Data

| Source | What It Provides | Cost | Update Speed |
|--------|-----------------|------|-------------|
| **The Odds API** | Live odds from 70+ bookmakers across 40+ sports | $20-49/mo (20K-90K calls) | Polling |
| **Betfair Exchange API** | True market prices (sharpest available) | GBP 499 one-time (personal) | Real-time |
| **football-data.co.uk** | Historical closing odds for major leagues (CSV) | Free | Static |
| **OddsPortal** | Historical odds movement, line history — scrape | Free | Hourly |

#### Layer 3: News & Intelligence (THE EDGE)

| Source | What It Provides | Cost | Speed |
|--------|-----------------|------|-------|
| **Twitter/X API** | Monitor 50+ insider accounts for lineup/injury leaks | ~$5-10/mo (pay-per-read @ $0.001/read) | Real-time |
| **PremierInjuries.com** | PL injury tracking — scrape/RSS | Free | Daily + breaking |
| **Knocks and Bans** | PL absences — scrape | Free | Daily |
| **Transfermarkt injuries** | Global injury data — scrape | Free | Daily |
| **RotoWire Soccer** | Multi-league injuries + lineups — scrape/RSS | Free | Daily |
| **FootballTeamNews.com** | Press conference summaries, probable XIs | Paid (small) | Pre-match |
| **Club official Twitter** | Confirmed lineups (60 min before KO) | Via X API | Pre-match |

#### Layer 4: Environmental & Contextual Data

| Source | What It Provides | Cost |
|--------|-----------------|------|
| **Meteostat API** | Historical + forecast weather by stadium location | Free |
| **OpenWeatherMap** | Real-time weather | Free tier |
| **Transfermarkt referees** | Referee assignment + tendencies — scrape | Free |
| **Google Maps API** | Travel distance between stadiums (fatigue calc) | Free tier (cache results) |

#### Layer 5: AI Processing

| Provider | Model | Input Cost (per 1M tokens) | Output Cost (per 1M tokens) | Best For |
|----------|-------|--------------------------|----------------------------|----------|
| **OpenAI** | GPT-4o-mini | $0.15 | $0.60 | Cheapest NLP option |
| **OpenAI** | GPT-4o | $2.50 | $10.00 | Higher quality analysis |
| **Anthropic** | Claude Haiku 4.5 | $1.00 | $5.00 | Good balance quality/cost |
| **Anthropic** | Claude Opus 4.6 | $5.00 | $25.00 | Best quality match previews |

For news NLP (short tweets, entity extraction): GPT-4o-mini at ~$5-15/month
For match preview generation (user-facing text): Claude Haiku or GPT-4o at ~$20-50/month

### Exact API Pricing (Researched April 2026)

#### API-Football

| Tier | Monthly Cost | Requests/Day | Notes |
|------|-------------|--------------|-------|
| Free | $0 | 100/day | All endpoints, all 1,236 leagues, 10 req/min |
| Paid entry | $19/mo | 7,500/day | All endpoints, all leagues |
| Higher tiers | Up to $450+/mo | Up to 1,500,000/day | Same data, more volume |

#### Sportmonks

| Tier | Monthly Cost | Leagues |
|------|-------------|---------|
| Free | $0 | 2 leagues (Danish, Scottish) |
| Starter | EUR 29/mo | 5 leagues of choice |
| Growth | EUR 99/mo | 30 leagues of choice |
| Pro | EUR 249/mo | 120 leagues of choice |

#### The Odds API

| Tier | Monthly Cost | API Calls/Month |
|------|-------------|----------------|
| Free | $0 | 500 |
| Rookie | $20/mo | 20,000 |
| Champion | $49/mo | 90,000 |
| Superstar | $99/mo | 4,500,000 |

#### Betfair Exchange API

| Item | Cost |
|------|------|
| Personal app key | GBP 499 one-time |
| Commercial vendor license | GBP 999 certification |
| Commercial app key | GBP 5,000 one-time |
| Ongoing API access | Free (no monthly fee) |

Requires a funded Betfair account.

#### X (Twitter) API

| Tier | Monthly Cost | Notes |
|------|-------------|-------|
| Free | $0 | 100 reads/mo (useless) |
| Pay-per-use reads | $0.001/read | 1,000 reads = $1 — best for monitoring |
| Basic | $200/mo | 10,000 posts/mo |
| Pro | $5,000/mo | Overkill |

For monitoring ~50 accounts every 5 minutes: ~5,000-10,000 reads/month = $5-10/mo via pay-per-use.

#### football-data.org

| Tier | Monthly Cost | Details |
|------|-------------|---------|
| Free | $0 | 12 major competitions, 10 req/min |
| Paid | EUR 12-29/mo | More competitions, 30 req/min |

"Access to top competitions is and will be free forever."

### Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| **Frontend** | Next.js + React | SSR, fast, can be PWA (installable on phone) |
| **Backend** | Next.js API routes | Same codebase, serverless |
| **Database** | PostgreSQL via Supabase ($0-25/mo) | Structured data, free tier, good tooling |
| **Background workers** | Railway ($5-20/mo) | Persistent workers for scrapers, cron jobs |
| **Cache** | Upstash Redis ($0-10/mo) | Cache odds, reduce API calls |
| **AI/NLP** | OpenAI GPT-4o-mini or Claude Haiku | News interpretation, match previews |
| **Push notifications** | Firebase Cloud Messaging | Free, works on web + mobile |
| **Payments** | Stripe | Standard, supports EUR |
| **Frontend hosting** | Vercel ($0-20/mo) | Fast, easy deployment |

### Database Schema

```sql
-- Core match data
matches          (id, date, home_team_id, away_team_id, league_id, season,
                  score_home, score_away, result, status)

match_stats      (match_id, xg_home, xg_away, shots_home, shots_away,
                  possession_home, corners_home, corners_away,
                  yellows_home, yellows_away, reds_home, reds_away)

-- Odds tracking
odds_snapshots   (match_id, bookmaker, market, selection, odds, 
                  timestamp, is_closing)

-- Teams & players
teams            (id, name, league_id, stadium_lat, stadium_lng, country)
players          (id, name, team_id, position, market_value)

-- Injuries & availability
injuries         (player_id, injury_type, date_from, date_to, matches_missed)

-- Lineups
lineups          (match_id, team_id, player_id, position, is_starter,
                  minute_subbed_in, minute_subbed_out)

-- Referees
referees         (id, name)
referee_matches  (referee_id, match_id, yellows, reds, penalties_given, fouls)

-- Managers
managers         (id, name)
manager_tenures  (manager_id, team_id, date_from, date_to)

-- Weather
match_weather    (match_id, temp_c, wind_kmh, wind_direction, rain_mm, humidity)

-- Leagues & seasons
leagues          (id, name, country, tier, is_active)
seasons          (id, league_id, year, start_date, end_date)

-- Predictions & value detection
predictions      (id, match_id, market, model_probability, implied_probability,
                  edge_percent, confidence, reasoning, created_at)

-- News events
news_events      (id, match_id, source, source_url, raw_text,
                  extracted_entity, impact_type, impact_magnitude,
                  detected_at, processed_at)

-- Paper trading / bot bets
simulated_bets   (id, bot_id, match_id, market, selection, odds_at_pick,
                  pick_time, stake, model_probability, edge_percent,
                  closing_odds, clv, result, pnl, bankroll_after,
                  news_triggered, reasoning)

-- Bot profiles
bots             (id, name, strategy, description, starting_bankroll,
                  current_bankroll, is_active)

-- User bets (when product launches)
user_bets        (id, user_id, match_id, market, selection, odds,
                  stake, result, pnl, created_at)

-- Self-evaluation
model_evaluations (id, date, league_id, market, total_bets, hits,
                   hit_rate, roi, avg_clv, calibration_score, notes)
```

### Background Workers

| Worker | Schedule | What It Does | API Used |
|--------|----------|-------------|----------|
| **fixture_sync** | Every 6h | Pull upcoming fixtures | API-Football |
| **odds_sync** | Every 15min | Pull live odds for upcoming matches | The Odds API |
| **stats_sync** | Every 2h | Pull team/player stats updates | API-Football + Sportmonks |
| **injury_scraper** | Every 30min | Scrape PremierInjuries, Transfermarkt, etc. | Web scraping |
| **twitter_monitor** | Every 5min | Check insider accounts for breaking news | X API |
| **weather_sync** | Every 3h | Pull weather for upcoming match locations | Meteostat/OpenWeather |
| **lineup_monitor** | Every 10min (matchday only) | Check for confirmed lineups | API-Football + Twitter |
| **model_run** | Hourly + on news trigger | Run predictions, detect value | Internal |
| **result_settler** | Every 30min | Check completed matches, settle bets, update P&L | API-Football |
| **self_eval** | Daily midnight | Calibration analysis, generate performance report | Internal |
| **news_nlp** | On trigger | AI analyzes impact of detected news item | Claude/OpenAI API |
| **odds_movement_detector** | Every 15min | Detect steam moves, reverse line movement | The Odds API |

### Monthly Cost Breakdown

#### Phase 0: Data Hoarding (your laptop, NOW)

| Item | Cost |
|------|------|
| Scraping historical data | $0 |
| football-data.co.uk CSVs | $0 |
| StatsBomb Open Data | $0 |
| Transfermarkt scraping | $0 |
| Python + PostgreSQL local | $0 |
| **Total** | **$0/mo** |

#### Phase 1: Live Pipeline (Validation)

| Item | Cost |
|------|------|
| API-Football (entry) | $19/mo |
| The Odds API (Rookie) | $20/mo |
| Supabase (free tier) | $0 |
| Railway (hobby, workers) | $5/mo |
| **Total** | **~$44/mo** |

#### Phase 2: Add News Intelligence

| Item | Cost |
|------|------|
| Phase 1 costs | $44/mo |
| X/Twitter API (pay-per-read) | $5-10/mo |
| OpenAI GPT-4o-mini (news NLP) | $5-15/mo |
| **Total** | **~$55-70/mo** |

#### Phase 3: User-Facing Product

| Item | Cost |
|------|------|
| Phase 2 costs | $70/mo |
| Supabase (Pro, for production) | $25/mo |
| Vercel (Pro, hosting) | $20/mo |
| Railway (Pro, more workers) | $20/mo |
| Claude/OpenAI (match previews) | $20-50/mo |
| Upstash Redis (caching) | $10/mo |
| Stripe fees | 2.9% + $0.30 per transaction |
| **Total** | **~$165-195/mo** |

#### Phase 4: Growth (2,000+ users)

| Item | Cost |
|------|------|
| Phase 3 costs | $195/mo |
| The Odds API (Champion) | $49/mo (upgrade from $20) |
| Sportmonks (Growth, 30 leagues) | EUR 99/mo |
| More AI usage (previews at scale) | $50-100/mo |
| Betfair API (one-time) | GBP 499 |
| **Total** | **~$350-450/mo** |

#### Revenue vs Cost at Scale

```
2,000 users × EUR 4.99/mo  = EUR 9,980/mo revenue
Costs:                        EUR 400/mo
Profit:                       EUR 9,580/mo (~96% margin)

10,000 users × EUR 4.99/mo = EUR 49,900/mo revenue
Costs (scaled):               EUR 800/mo
Profit:                       EUR 49,100/mo (~98% margin)
```

The business has near-zero marginal cost per user — you pull the same data whether you have 10 users or 50,000.

### Key Features (Priority Order)

#### MVP — Validation Phase (Month 1-3)

1. Data pipeline running daily (fixtures, stats, odds)
2. Historical data warehouse (years of match data)
3. Base prediction model (xG, form, H2H, home/away)
4. Value detection (model prob vs bookmaker odds)
5. Paper trading bots (8 strategies running in parallel)
6. Simple validation dashboard

#### V1 — Launch (Month 4-6)

7. User-facing match pages with AI reasoning ("X-Ray mode")
8. Value bets highlighted per match
9. Transparent track record page (all bets, including losses)
10. News impact alerts (push notifications)
11. User accounts + Stripe subscription (free / EUR 4.99 Pro)

#### V2 — Growth (Month 7-12)

12. Personal betting profile (preferred leagues, bankroll tracking)
13. Odds movement tracker (visual history, steam detection)
14. More leagues (lower divisions, tennis, esports)
15. Public self-evaluation dashboard (builds trust)
16. Bookmaker affiliate integration

#### V3 — Expansion (Year 2)

17. Mobile app (React Native or Flutter)
18. API access for developers
19. Betfair one-click betting integration
20. Community features (user picks alongside AI)
21. Multi-language support

### Build Timeline

```
PHASE 0 — Data Hoarding (Week 1-2, $0/mo)
  ✓ Scrape historical data (results, odds, injuries, lineups)
  ✓ Build database schema, import everything
  ✓ Calculate derived stats (form, H2H, etc.)
  ✓ Train base prediction model
  ✓ Backtest against historical odds → first signal of viability

PHASE 1 — Live Pipeline (Week 3-6, $44/mo)
  → Connect API-Football + The Odds API
  → Live fixtures, odds, lineups flowing in
  → Paper trading bots placing simulated bets
  → Basic dashboard to monitor

PHASE 2 — News Intelligence (Week 7-8, $70/mo)
  → Twitter/X monitoring for insider accounts
  → NLP pipeline (entity extraction, impact scoring)
  → bot_news_only starts tracking
  → Compare news bots vs stats-only bots

PHASE 3 — Run & Validate (Week 9-12, $70/mo)
  → Don't touch the model (avoid overfitting)
  → Accumulate 200+ bets
  → Weekly self-evaluation reports
  → GO/NO-GO decision at week 12

PHASE 4 — Build Product (Month 4-6, $195/mo)
  → Only if validation passes
  → Frontend, user accounts, payments
  → Launch with 3 months of verified track record
```

---

## PART 10: Product Tiers & Monetization Strategy

### The Core Insight: Data Is the Product

The AI predictions are just one way to monetize. The real product is aggregating 6-8 separate data sources into one screen. Currently a serious bettor opens:

```
Tab 1: SoccerStats.com    → form, H2H
Tab 2: Transfermarkt       → injuries, suspensions
Tab 3: WhoScored           → lineups, formations
Tab 4: OddsPortal          → odds comparison
Tab 5: PremierInjuries     → injury details
Tab 6: Twitter             → latest team news
Tab 7: Weather site        → match conditions
Tab 8: FBref               → xG, advanced stats

Time: 20-30 minutes per match
```

Your app: ONE screen, 2 seconds. That alone is worth paying for.

### Tier Structure

#### Tier 1: "Scout" — EUR 0 (Free)

The hook. Get them in the door.

- Today's matches across top 5 leagues
- Basic stats (form, last 5 results, league position)
- Head-to-head record
- Match results after played

Does NOT include: odds, injuries, lineups, value detection, lower leagues.

#### Tier 2: "Analyst" — EUR 4.99/mo

The data layer. Everything in one place. No AI predictions.

- ALL leagues (30+ competitions)
- Full injury/suspension tracker per match
- Confirmed lineups as soon as they drop
- Odds comparison across 40+ bookmakers
- Odds movement history (opening → current)
- Weather conditions at stadium
- Referee assignment + historical tendencies
- Fixture congestion indicator
- xG and advanced stats
- News feed per match (aggregated from all sources)

This tier alone beats every free stats site because nothing else aggregates injuries + odds + lineups + weather + referee + xG in one view. **Lowest churn tier** — people pay for convenience regardless of prediction accuracy.

#### Tier 3: "Sharp" — EUR 14.99/mo

The intelligence layer. AI does the thinking.

- Everything in Analyst, plus:
- AI match analysis with full reasoning ("X-Ray mode")
- Value bet detection (model probability vs bookmaker odds)
- Edge % per market (1X2, O/U 2.5, BTTS)
- News impact alerts (push notifications)
- "Something changed" alerts when lineup/injury news breaks
- Confidence score per prediction
- Transparent track record (all picks, all results)
- Self-evaluation dashboard (where AI is right/wrong)
- Personal bankroll tracker
- Kelly stake suggestions

#### Tier 4: "Syndicate" — EUR 49.99/mo

For power users, developers, and small betting groups.

- Everything in Sharp, plus:
- API access to all data
- Webhook alerts (integrate with own tools)
- CSV/Excel export of all historical data
- Custom league/market focus
- Steam move detection + alerts
- Closing Line Value tracking
- Multiple bot strategy backtests
- Early access to new features

### Why Data-First Beats Tips-First

```
TIPSTER APP:
  User thinks: "Are the tips good?"
  Bad week → "This app sucks" → unsubscribe
  Good week → "Lucky" → still skeptical
  Churn: HIGH

DATA APP:
  User thinks: "This saves me 30 minutes per match"
  Bad week → still uses it for research
  Good week → still uses it for research
  Churn: LOW (utility doesn't depend on predictions)
```

### Prediction Delay Strategy

If AI predictions are genuinely profitable:

- Option A: Share them live (Tier 3) → more subscribers
- Option B: Keep them private → use yourself, sell only data
- Option C: Delay for free users → free users see picks 24h late, paying users see them live before odds move

Option C is smart — value of a pick decreases over time as odds adjust. Live = EUR 14.99 value. After the match = free marketing.

### Revenue Projections

Conservative scenario (Year 1):

```
Free (Scout):      2,000 users × EUR 0      = EUR 0/mo
Analyst:             500 users × EUR 4.99   = EUR 2,495/mo
Sharp:               200 users × EUR 14.99  = EUR 2,998/mo
Syndicate:            30 users × EUR 49.99  = EUR 1,500/mo
                                              ─────────────
Monthly revenue:                               EUR 6,993
Monthly costs:                                 EUR 200
Monthly profit:                               ~EUR 6,800
Annual profit:                                ~EUR 81,600
```

Growth scenario (Year 2):

```
Free (Scout):      6,000 users × EUR 0      = EUR 0/mo
Analyst:           2,500 users × EUR 4.99   = EUR 12,475/mo
Sharp:             1,200 users × EUR 14.99  = EUR 17,988/mo
Syndicate:           300 users × EUR 49.99  = EUR 14,997/mo
                                              ─────────────
Monthly revenue:                               EUR 45,460
Monthly costs:                                 EUR 400
Monthly profit:                               ~EUR 45,000
Annual profit:                                ~EUR 540,000
```

### Additional Revenue Streams

| Stream | How | When |
|--------|-----|------|
| **Subscriptions** | Tiered plans as above | From launch |
| **Bookmaker affiliates** | "Bet365 has best odds for this pick" → affiliate link (EUR 50-200/signup) | From launch |
| **Data API** | Developers build on your aggregated data | Month 6+ |
| **Content/newsletter** | Weekly "value report" (free marketing funnel) | From launch |
| **White-label** | Sell dashboard to betting communities/forums | Year 2 |
| **B2B licensing** | Small bookmakers buy your data feed | Year 2+ |

---

## Sources & References

### Academic / Research
- Sports prediction and betting models: The case of tennis (JSA, 2020)
- Tennis betting: Can statistics beat bookmakers? (ResearchGate)
- Statistical enhanced learning for tennis prediction at Grand Slams (arXiv, 2025)
- Are Betting Markets Inefficient? Evidence From Simulations (SAGE Journals, 2023)
- A Statistical Theory of Optimal Decision-Making in Sports Betting (PMC, 2023)
- Market efficiency in NCAA College Basketball (Springer)
- Intransitive Player Dominance in Tennis Forecasting (arXiv, 2025)
- WTA vs ATP market efficiency study (Universidad de Navarra)

### Data & Tools
- Sportradar: sportradar.com
- API-Football: api-football.com
- Sportmonks: sportmonks.com
- The Odds API: the-odds-api.com
- SharpAPI: sharpapi.io
- SportsDataIO: sportsdata.io
- StatsBomb Open Data: statsbomb.com
- football-data.org
- Betfair Exchange API
- MomentumOdds.com (free steam/sharp money tracker)

### News & Intelligence
- PremierInjuries.com (Ben Dinnery)
- Knocks and Bans: knocksandbans.com
- PhysioRoom: physioroom.com
- FootballTeamNews.com
- RotoWire: rotowire.com
- Transfermarkt: transfermarkt.com
- atptour.com / wtatennis.com

### Industry Analysis
- SportsBettingDime: Best sports to bet on guide
- Punter2Pro: Market efficiency analysis
- Pinnacle: Closing Line Value resources
- Top100Bookmakers: Betting margin analysis
- soccer-rating.com: League profitability analysis
- betting-forum.com: Market efficiency discussions

### Key Twitter/X Accounts to Monitor
- @FPL_Rockstar (PL lineups)
- @BenDinnery (PL injuries)
- @David_Ornstein (PL team news)
- @FabrizioRomano (transfers globally)
- @DiMarzio (Serie A)
- @cfbayern (Bundesliga)
- @GuillemBalague (La Liga)
- @honigstein (Bundesliga)
- @moaborsen (Ligue 1)
- @ShamsCharania (NBA)
- @Underdog__NBA (NBA lineups)
- @EntryLists (tennis)
- @araborsen (MMA)

---

## PART 8: Validation System — Paper Trading & Self-Improvement

### Why Validate Before Building the Product

Before investing months in UI, user features, and marketing — prove the model works with fake money on real matches. This serves three purposes:

1. **Validates the core thesis** — does the model actually find value?
2. **Builds training data** — every bet becomes feedback that improves the model
3. **Creates launch proof** — "here's what our system would have made over 3 months" is the best marketing

### Phase 1: Backtesting (Historical Data)

Run the model against past seasons where results are known:

```
For every match in 2024-25 season:
  → Feed model ONLY data available BEFORE that match
  → Model outputs: probability, value bet Y/N, suggested market
  → Compare against actual closing odds
  → Record: did it hit? what was the CLV? what was the P&L?
```

**Limitation:** Can't simulate news impact (injuries breaking mid-day) or real-time odds movement. Backtesting validates the stats model only, not the full product.

### Phase 2: Paper Trading (Live Simulation)

Run it live with fake money for 8-12 weeks:

```
DAILY AUTOMATED LOOP:

1. Morning: Fetch today's matches + live odds
2. Model analyzes each match
3. News pipeline checks for injuries/lineup changes
4. System identifies value bets (edge > 3%)
5. "Bot user" places simulated bet:
   - Records: match, market, odds AT TIME OF PICK, stake (Kelly-sized)
   - Timestamps everything
6. After match: result recorded, P&L calculated
7. Dashboard updates running performance
```

### What to Record Per Simulated Bet

```json
{
  "match": "Arsenal vs Brighton",
  "date": "2026-05-03",
  "league": "Premier League",
  "league_tier": "top5",
  
  "market": "Over 2.5",
  "pick_odds": 1.75,
  "pick_time": "2026-05-03T10:30:00Z",
  "model_probability": 0.68,
  "implied_probability": 0.57,
  "edge_percent": 11.0,
  "confidence": "high",
  "reasoning": "Arsenal 8/10 home Over, Brighton conceding 1.6/game away...",
  
  "news_impact": true,
  "news_detail": "Saka confirmed fit after training doubt",
  "odds_before_news": 1.82,
  "odds_after_news": 1.75,
  
  "closing_odds_pinnacle": 1.65,
  "clv": 0.10,
  
  "result": "won",
  "actual_goals": 3,
  "pnl": 7.50,
  
  "stake": 10.00,
  "stake_kelly_fraction": 0.25,
  "bankroll_before": 1000.00,
  "bankroll_after": 1007.50
}
```

### Bot User Profiles — Run Multiple Strategies in Parallel

| Bot | Strategy | Purpose |
|-----|----------|---------|
| **bot_value_all** | Bets every value pick (edge > 3%) across all leagues | Baseline — does the model find value? |
| **bot_value_strict** | Only bets edge > 5%, high confidence | Is higher selectivity more profitable? |
| **bot_news_only** | Only bets when a news event shifts the model | Does the news pipeline actually create edge? |
| **bot_lower_leagues** | Only bets Championship, Serie B, 2.Bundesliga | Are lower leagues really softer? |
| **bot_top_leagues** | Only bets top 5 leagues | Confirms these are hard to beat (negative control) |
| **bot_over_under** | Only Over/Under market | Is O/U more beatable than 1X2? |
| **bot_1x2** | Only 1X2 market | Compare against O/U |
| **bot_flat_stake** | Flat 10 EUR per bet (no Kelly) | Compares Kelly vs flat staking |
| **bot_random** | Random bets at same volume | CONTROL GROUP — proves edge isn't luck |

### Go / No-Go Decision Criteria

After 8-12 weeks (minimum 200+ bets for statistical significance):

| Metric | GO (build the product) | NO-GO (rethink) |
|--------|----------------------|-----------------|
| **CLV** | Consistently positive (>1%) | Negative or flat |
| **ROI** | > +2% across 200+ bets | < +1% or negative |
| **bot_random** | Clearly negative (proves skill) | Similar to main bots (edge is luck) |
| **bot_news_only** | Positive ROI (news adds value) | Negative (pipeline doesn't help) |
| **Lower vs Top leagues** | Lower leagues outperform | No difference |
| **Calibration** | Predicted probabilities match actual hit rates | Consistently overconfident or underconfident |

### Phase 3: Self-Evaluation & Auto-Improvement

The system continuously analyzes its own performance and adjusts:

#### What It Self-Evaluates

| Dimension | What It Learns | How |
|-----------|---------------|-----|
| **League-specific biases** | "I overestimate goals in Serie A" | Predicted O/U % vs actual per league over 100+ matches |
| **Market-specific accuracy** | "I'm great at O/U but bad at BTTS" | Hit rate + ROI per market type |
| **Confidence calibration** | "When I say 70%, it actually hits 62%" | Predicted probability vs actual outcome frequency |
| **News impact accuracy** | "Injury news shifts model by X, actual impact is Y" | Pre-news vs post-news predictions against results |
| **Timing analysis** | "Picks 4h before kickoff have better CLV than 24h before" | CLV by time-of-pick |
| **Feature importance** | "Form matters more than H2H in lower leagues" | Feature importance analysis per league |
| **Staking optimization** | "Quarter Kelly outperforms half Kelly at my edge size" | Compare bot P&L curves over time |

#### Calibration Chart — The Most Important Self-Check

```
When model says 50% → actually happens 49.2%  (well calibrated)
When model says 60% → actually happens 57.8%  (slightly overconfident)  
When model says 70% → actually happens 63.1%  (overconfident — needs correction)
When model says 80% → actually happens 71.4%  (significantly overconfident)

→ AUTO-FIX: Apply Platt scaling to compress high-confidence predictions
```

A perfectly calibrated model: when it says 70%, it hits 70% of the time. Most models are overconfident at the extremes — the system detects and corrects this automatically.

#### Self-Evaluation Report (Generated Weekly/Monthly)

```
SELF-EVALUATION — April 2026

WHERE I'M ACCURATE:
  Over 2.5 in Bundesliga:     67% hit rate, +8.1% ROI
  Home wins in Championship:  61% hit rate, +5.2% ROI

WHERE I'M FAILING:
  BTTS in Serie A:            44% hit rate, -11% ROI
  → Finding: overweighting attacking stats, 
    underweighting Italian defensive organization
  
  Away wins in Ligue 1:       39% hit rate, -8% ROI
  → Finding: home advantage in Ligue 1 stronger 
    than model assumes (+0.4 xG, not +0.2)

NEWS PIPELINE VALUE:
  Picks with news trigger:    63% hit, +6.1% ROI
  Picks without news:         55% hit, +1.8% ROI
  → News pipeline adds +4.3% ROI

AUTO-ADJUSTMENTS APPLIED:
  - Reduced BTTS confidence in Serie A by 12%
  - Increased home advantage weight for Ligue 1
  - Deprioritized away win picks in French football
```

### The Flywheel Effect

```
More bets recorded
    → More self-evaluation data
        → Model adjusts its weaknesses
            → Better predictions
                → Better ROI
                    → More confidence in system
                        → More users (if launched)
                            → Even more data
                                → Cycle continues
```

### Development Timeline for Validation System

```
WEEK 1-2: Data pipeline
  → API-Football: fixtures, lineups, stats
  → The Odds API: live odds from bookmakers
  → Historical data import (football-data.co.uk, StatsBomb)

WEEK 3-4: Base prediction model
  → Features: home/away form, goals scored/conceded, H2H, xG
  → Output: probability per market (1X2, O/U 2.5, BTTS)
  → Compare vs bookmaker implied probability → edge %

WEEK 5-6: Paper trading engine
  → Bot users placing simulated bets
  → Recording everything (picks, odds, timestamps, results)
  → Auto-settling after match completion
  → Basic dashboard (database + simple web page)

WEEK 7-8: News pipeline
  → Twitter/X monitoring for key insider accounts
  → NLP: extract injury/lineup info from tweets
  → Auto-adjust model probabilities when news breaks
  → bot_news_only starts tracking

WEEK 9-12: Run and observe
  → Don't touch the model (avoid overfitting to recent results)
  → Let it accumulate 200+ bets
  → Weekly self-evaluation reports
  → Analyze which bots perform, which fail

WEEK 12: GO / NO-GO decision
  → If GO: build user-facing app with real track record data
  → If NO-GO: analyze which component failed, iterate model
```
