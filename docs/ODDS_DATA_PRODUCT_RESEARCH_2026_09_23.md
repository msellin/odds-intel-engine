# Odds data product: research for goals 3 and 4 (2026-09-23)

> Parent task: `PRIORITY_QUEUE.md` #100 ODDS-DATA-PRODUCT-RESEARCH-2026-09-23. Desk research only.
> No code, DB writes or production changes. Companion: #099 EE-BOOK-SURVEY (`docs/EE_BOOK_SURVEY_2026_09_23.md`).
> **This is not legal advice.** Section (c) lists risks and questions for a lawyer.
> Vendor prices were read on 2026-09-23 unless marked otherwise. **[UNVERIFIED]** marks anything not confirmed from a primary source.

## Executive summary

1. **Replacing API-Football (goal 4) is mostly easy.** The model's only well-covered predictors are Elo, form, rest days and season context (`docs/MODELLING_DATA_AUDIT_2026_09_16.md:106-110`). All of them come from fixtures and results alone. Injuries, lineups, player stats and predictions cover less than 10% of rows, or are unused.
2. **The hard parts are the fixture spine and Pinnacle.** Every table, and all three of our own book sweepers, key on AF fixture IDs through fuzzy matching (`workers/automation/unibet_odds_feed.py:627`, `book_event_map`). Pinnacle is our sharp anchor. We get it through AF; Pinnacle closed its OFFICIAL API on 2025-07-23, but its public guest API (`guest.api.arcadia.pinnacle.com`) still answered our paired test on 2026-09-14 and is reachable from the VPS's Estonian exit (verified 2026-09-23), so a direct fallback exists.
3. **Fixtures and results are cheap to buy elsewhere.** Sportmonks costs €29–249/mo for 5–120 leagues, and wider coverage is Enterprise-only. football-data.org costs €49–199/mo. ESPN is free and already our backup. We touch about **609 leagues/30 days** and bet in **97**, so full breadth means Sportmonks Enterprise. That price is not published.
4. **Keep AF for now.** It costs about $39/mo (INFRASTRUCTURE.md:61, unverified) and nothing else matches its breadth at that price. Goal 4 is a hedge, not a saving.
5. **A pure Estonian odds feed (goal 3) is a thin market.** Coolbet is already sold by The Odds API (from $30/mo), OddsPapi, odds-api.io and OpticOdds. Unibet is widely carried, and Paf, Tonybet, Betsson and Betsafe are partly carried. **Olybet, Optibet and Epicbet appear in no API we checked**, and neither does an explicit Estonian-market Unibet or Coolbet. That gap is real but small.
6. **The likely buyers are few.** Estonian affiliates and tipsters (spordiguru, bettimine, betcompareestonia and similar) are a small, advertising-restricted market. Global aggregators would treat us as a supplier, not a competitor.
7. **Biggest legal risk: we cannot resell AF data.** AF's ToS prohibits resale to third parties. Any product must be built only on odds we collect ourselves.
8. **Second legal risk: bookmaker ToS and database rights.** Sui generis protection for bookmaker odds is weak because odds are *created* data (BHB v William Hill). But Ryanair v PR Aviation lets ToS bind where no database right exists. Innoweb and CV-Online (a Latvian case) make near-real-time mirroring the riskiest pattern.
9. **Third risk: account exposure.** *Corrected 2026-09-23:* since the VPS move, Coolbet (anonymous FlareSolverr session, `require_auth=False`) and Unibet-Site (logged-OUT tab) are collected WITHOUT the operator's accounts, which lowers this risk. It is not zero: the same operator stakes at those books, and a book that links a resold feed to its customer can still limit the account.
10. **Recommended next step:** do not build a product yet. (a) Finish #099 to see whether Olybet, Optibet and others can be collected at all. (b) Send 5 short "would you pay €X/mo" emails to Estonian affiliates and one to an aggregator (OddsPapi or odds-api.io) about supplying Olybet, Optibet and Epicbet. (c) Book one hour with an Estonian IP lawyer using the questions in §c.4. Treat goal 4 as a hedge: stub a secondary fixture source behind an interface, but don't migrate.

---

## (a) API-Football dependency map

Plan: **Mega**, 150,000 requests/day and 900/min, active until 2026-11-28. We use 9–23% of the daily allowance and call 18 of about 37 endpoints (`docs/AF_ENDPOINT_FREQUENCY.md:13-21`). The client is `workers/api_clients/api_football.py`.

**What the model actually needs.** Only nine predictors have 88% or better coverage: Elo, form PPG and momentum, rest days, plus season progress and league tier (`docs/MODELLING_DATA_AUDIT_2026_09_16.md:106-110`). Injuries (0–2.5%), xG (4%), player ratings (7–10%), referee (7%) and H2H (31%) are close to empty. Most of what AF supplies beyond fixtures, results and odds is therefore **not load-bearing** for picks.

| # | Data | AF endpoint → client fn (`api_football.py`) | Our consumers → tables | Criticality (what breaks without it) | Replacement options (cost, effort) | Commodity? |
|---|---|---|---|---|---|---|
| 1 | **Fixtures, leagues, teams (the ID spine)** | `/fixtures?date` `get_fixtures_by_date` :593, `/leagues` `get_leagues` :562, `/fixtures?ids` `get_fixtures_batch` :624 | `fetch_fixtures.py`, `daily_pipeline_v2.py` → `matches`, `leagues`, `teams`, `venues` | **Critical.** Every table joins on AF fixture and team IDs. All three direct-book sweepers fuzzy-match *to AF fixtures* (`unibet_odds_feed.py:627`, `coolbet_placer.fuzzy_match_event`, `book_event_map` in DATA_SOURCES.md). | Sportmonks €29/5 lg, €99/30, €249/120, Enterprise for all 2,300+ ([pricing](https://www.sportmonks.com/football-api/plans-pricing/)). football-data.org €49/30 comps to €199/100 ([pricing](https://www.football-data.org/pricing)). openfootball is free but only covers major leagues (not checked in detail). **The effort is the ID migration, not the data:** about 2–4 weeks to add a canonical `fixture` table with a per-source ID map. Our 609-league breadth pushes us to Enterprise pricing. | Data is a commodity. The **migration is hard.** |
| 2 | **Results / settlement** | `/fixtures?date&status=FT` `get_results_for_settlement` :652, `/fixtures?ids` for HT scores :2368 | `settlement.py:29,2670`, LivePoller FT → `matches` scores, bet grading | **Critical.** Without it, no bet grading, no ROI and no CLV. | **ESPN is already the fallback** (`settlement.py:2700-2766`, 70 league slugs in `workers/scrapers/espn_results.py`). Sportmonks or football-data.org also work. Direct books publish results too (Coolbet and Unibet settle our bets). Low effort for top leagues; the long tail needs a paid source. | Commodity |
| 3 | **Live scores / status** | `/fixtures?live` `get_live_fixtures` :611 | `live_poller.py`, `live_tracker.py` → `live_match_snapshots`, `matches.status` | Medium. Only drives instant settlement, because in-play betting was retired 2026-08-21. The nightly batch settle covers the gap. | Sportmonks livescores (all plans), football-data.org €12+ tier. | Commodity |
| 4 | **Bookmaker odds (12 books, bulk pre-match)** | `/odds?date` `get_odds_by_date` :866, `/odds?fixture` :857 (closing snaps) | `fetch_odds.py`, `run_closing_snap` → `odds_snapshots` | **Critical, for Pinnacle specifically.** Over the last 48h AF supplied Pinnacle, Bet365, 1xBet, Marathonbet, Betfair, William Hill, Betano, BetVictor and SBO. Our own sweepers supplied Coolbet, Epicbet and Unibet-Site (read-only DB query 2026-09-23). Pinnacle is the de-vig anchor for every edge and CLV figure. AF keeps odds for only 7 days (DATA_SOURCES.md). | **Pinnacle:** public API closed 2025-07-23 ([Arbusers](https://arbusers.com/access-to-pinnacle-api-closed-since-july-23rd-2025-t10682/)). Resellers: OddsPapi (free tier includes Pinnacle), The Odds API (Pinnacle flagged "from public website, may incur a delay" [bookmaker list](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html)), pinnapi and pinnodds (not evaluated). Soft books: The Odds API $30–249/mo, OddsPapi. | Soft books are a commodity. **The sharp anchor is hard.** |
| 5 | **Predictions (AF's own model)** | `/predictions` `get_prediction` :1227 | `fetch_predictions.py` → `predictions` | Low. It is one input among many, and the α=0 results say it adds nothing we can measure. | Drop it. Sportmonks sells predictions as a €15 add-on. | Commodity / droppable |
| 6 | **Standings** | `/standings` :1415 | `fetch_enrichment.py` → `league_standings` | Low to medium (`league position` sits in the 50–90% coverage band). | Can be computed from our own results table. Also in every paid API. | Commodity |
| 7 | **Team season stats** | `/teams/statistics` :1423 | `fetch_enrichment.py` → `team_season_stats` | Low. Form and Elo are already computed from our own results (`job_team_scoring_rates`, WORKFLOWS.md:92). | Can be computed from results. | Commodity |
| 8 | **H2H** | `/fixtures/headtohead` :1342 | `fetch_enrichment.py` → `match_signals` | Low (31% coverage, and H2H has a weak literature case). | Can be computed from our own results history. | Commodity |
| 9 | **Injuries / sidelined** | `/injuries` :1525, `/sidelined` :2104 | `fetch_enrichment.py` → `match_injuries`, `player_sidelined` | Low for the model today (0–2.5% coverage in the feature vector). | Sportmonks (all plans). Hard to get free for long-tail leagues. | Semi-hard, but not load-bearing |
| 10 | **Lineups** | `/fixtures/lineups` :1326 | `live_tracker.py` → `matches.lineup_confirmed`, `match_signals` | Low to medium. It is a timing signal, and lineup-driven features are sparse. | Sportmonks, football-data.org "Deep Data". | Commodity for top leagues |
| 11 | **Events (goals, cards) and match statistics (corners, shots, xG)** | `/fixtures/events` :1847, `/fixtures/statistics` :685 | `settlement.py` (post-match) → `match_events`, `match_stats` | **Medium to high for settlement of corners and cards markets** (the generic shadow settler reads `match_stats` corners via `settlement._corner_stats` — `job_corners_paper_settle` was removed 2026-09-26, #162 W1.3). Low for the model. | Sportmonks stats, football-data.org statistics add-on (€15), football-data.co.uk CSVs (free but slow, major leagues only). | Commodity for major leagues; the long tail is hard |
| 12 | **Player match stats / ratings** | `/fixtures/players` :2164 | `settlement.py` → `match_player_stats`; `job_team_avg_player_rating` | Low (7–10% coverage). | Sportmonks. Could also be dropped. | Droppable |
| 13 | **Coaches, venues, transfers** | `/coachs` :1372, `/venues` :1350, `/transfers` :2250 | `fetch_enrichment.py` → `team_coaches`, `venues`, `team_transfers` | Very low (weather uses venue coordinates, at 8.6% coverage). | Static data. Scrape once or drop. | Droppable |
| 14 | **Live odds** | `/odds/live` :1687 | Gated off since 2026-08-21 | None today | n/a | n/a |

**Existing non-AF sources** (DATA_SOURCES.md:13-19): ESPN (settlement backup), football-data.co.uk (historical odds and stats CSVs), The Odds API (free tier, tennis), OddsPapi (free tier, historical Pinnacle closes, quota exhausted), and our direct Coolbet, Unibet-Site and Epicbet sweepers. Those three already reach **500 leagues** in 14 days, against 427 for Pinnacle (read-only DB query 2026-09-23).

**Verdict on goal 4.** About 11 of the 14 rows are commodities, and most of them are not load-bearing for picks. Two things are genuinely hard:
- **Our own canonical fixture ID layer.** Every source then maps into it. This is 2–4 weeks of work, and it is a prerequisite for *any* odds product anyway, because customers need stable event IDs.
- **A Pinnacle source that is not AF.**

AF at about $39/mo is cheaper than any single replacement: Sportmonks Pro plus the odds add-on is about €264/mo for only 120 leagues. So "replace AF" should mean *removing the single point of failure*, not saving money.

---

## (b) Competitor scan: odds APIs

Checked 2026-09-23. Baltic-book presence is taken from each vendor's public book list. **A listed "Coolbet" or "Unibet" may be a different country's site** (Coolbet also operates outside Estonia, and Unibet lists are per-country). None of the lists we saw marked an Estonian (.ee) variant. **[UNVERIFIED which jurisdiction]**

| Vendor | Pricing (public) | Books | Baltic/Nordic books seen | Latency / delivery | Historical | Free tier |
|---|---|---|---|---|---|---|
| **The Odds API** ([home](https://the-odds-api.com/), [books](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html)) | Free 500 credits; $30/20K; $59/100K; $119/5M; $249/15M | ~40 overall per OddsPapi; 32 in the EU region | **Coolbet** (eu), Betsson, NordicBet, Unibet FR/IT/NL/SE/FI, Pinnacle (delayed) | REST polling, JSON; Sheets/Excel add-ons | Yes (costs credits) | Yes |
| **OddsPapi** ([blog, 2026-05](https://oddspapi.io/blog/best-odds-apis-2026-comparison/), [books](https://oddspapi.io/sportsbooks)) | Free 250 req/mo; paid custom (roughly $20 to $1,000+/mo, per [sportsapis.dev](https://sportsapis.dev/apis/oddspapi), unverified) | 370 (vendor claim, 2026-05-25) | **Coolbet, Paf, Betsson, Unibet, Pinnacle**; Optibet per a RapidAPI listing [UNVERIFIED] | REST; WebSocket on paid tiers | Yes, on the free tier | Yes |
| **odds-api.io** ([pricing](https://odds-api.io/pricing), [books](https://odds-api.io/sportsbooks)) | £49 (2 books), £99 (5), £179 (10), £229 (15); WebSocket add-on doubles the price | 365+ claimed | **Coolbet, Paf, Tonybet, Unibet**. No Pinnacle in the list (they blog about the Pinnacle shutdown). | REST 5K req/h; WebSocket under 100ms (add-on) | Not stated | Paused for new keys |
| **SportsGameOdds** ([pricing](https://sportsgameodds.com/pricing)) | Free (9 books, 10-min delay); $99 (77 books, 3 min); $299 (82 books, sub-minute); custom | 82 | None listed [UNVERIFIED, list not checked book by book] | REST; WebSocket on custom plans only | $299 tier and up | Yes |
| **OpticOdds** ([books](https://developer.opticodds.com/docs/sportsbooks)) | Sales-gated; reportedly about $5K/mo per sport [UNVERIFIED, from a search summary] | 200+ | **Coolbet, Betsafe, Betsson, Tonybet, Unibet (DK/SE/UK/AU)** | SSE streaming, sub-second | Enterprise | No |
| **OddsJam API** | Sales-gated; about $500–1,000+/mo estimated [UNVERIFIED] ([sportsapi.com](https://sportsapi.com/api-directory/oddsjam/)) | 100+, US-focused | Not checked | Streaming | Yes | No |
| **Sportmonks odds** ([pricing](https://www.sportmonks.com/football-api/plans-pricing/)) | €15/mo odds add-on; €129/mo Premium Odds (TXOdds, 140+ books) on top of a base plan | 140+ (premium) | Not checked book by book | REST | Yes | 14-day trial |
| **BetsAPI** ([pricing](https://betsapi.com/docs/pricing.html)) | From about $10/mo per package [search summary]; Bet365, Bwin, Betfair, SBO packages | Few, but deep markets | No Baltic books | REST, 3,600 req/h | Events API has history | $1 trial |
| **Enterprise feeds** (Sportradar, LSports, TXOdds, Betgenius) | $30K+/mo estimated for Sportradar [vendor-blog estimate, UNVERIFIED] | Deal-dependent | Unknown | Push | Yes | No |
| **Pinnacle resellers** (pinnapi, pinnodds) | Not checked | Pinnacle only | n/a | REST, SSE, WS | Unknown | Unknown |

### Where the gap is

| Book (Estonian-licensed) | Carried by an API we checked? | We collect it today? |
|---|---|---|
| Coolbet | Yes: The Odds API, OddsPapi, odds-api.io, OpticOdds (jurisdiction unclear) | Yes (VPS, anonymous FS session since 2026-09-23) |
| Unibet | Yes, but only per-country SE/DK/FR/etc. **Not seen as Unibet EE.** | Yes (Unibet-Site, VPS logged-out tab since 2026-09-23) |
| Paf | OddsPapi, odds-api.io | No |
| Betsson / Betsafe | OddsPapi, OpticOdds, The Odds API (Betsson) | No |
| Tonybet | odds-api.io, OpticOdds | No |
| **Olybet** | **None found** | No |
| **Optibet** | Only a RapidAPI mention [UNVERIFIED] | No |
| **Epicbet** | **None found** | Yes (VPS via FlareSolverr) |

**Assessment (sceptical).**
- **Supply.** The genuinely uncovered books are Olybet, Optibet and Epicbet, plus explicitly Estonian-market prices for Unibet and Coolbet. Estonian books often share prices across Nordic sister sites (Kindred, Betsson group), so an "EE-specific" price may differ little from the SE/FI feed that aggregators already sell. **That needs measuring before it is used as a selling point.**
- **Demand.** Estonia is small. Total gambling tax was €61M in 2025, and EMTA does not break out sports betting ([EMTA yearbook](https://www.emta.ee/eraklient/amet-uudised-ja-kontakt/maksu-ja-tolliamet/aastaraamat/hasartmang-ja-hasartmangumaks)). 45 licensed operators. Advertising is tightly regulated: mandatory warning text, and TTJA found 45% of adverts in breach in 2025 ([ERR](https://news.err.ee/1610088049/estonia-in-no-hurry-to-restrict-gambling-adverts)).

**Plausible buyers, in order of likelihood:**
1. **Global aggregators** (OddsPapi, odds-api.io, OpticOdds) buying Olybet, Optibet and Epicbet as a supplier feed. They would pay for books they lack, but they are experienced at collecting books themselves. B2B, low volume, possibly one-off.
2. **Estonian and Baltic affiliates and comparison sites** such as [spordiguru.com](https://www.spordiguru.com/parimad-koefitsiendid/), [bettimine.ee](https://www.bettimine.ee/coolbet-spordiennustus), [betcompareestonia.com](https://betcompareestonia.com/) and kasiino21. Today they compare "odds level" in prose. A widget or feed is a possible upsell, at maybe tens of euros a month each [UNVERIFIED, no price discovery done].
3. **Tipsters and Telegram channels**, which is effectively our own 👥 PICKS product.
4. **Media** (Delfi/Postimees sport). Unlikely without a bookmaker sponsorship deal.
5. **Bookmakers' own trading teams**, as competitor price monitoring. Real demand, but they usually buy from Sportradar/Kambi-tier vendors or scrape in-house.

**Bottom line.** Goal 3 as a standalone business looks **thin**: small buyer pool, low price points, strong free and cheap substitutes for the big books. The most realistic monetisation is (i) selling the uncovered books to one or two aggregators, or (ii) using the feed to make our own 👥 PICKS product better. It is not an "Estonian Odds API" SaaS.

---

## (c) Legal and licensing: risks and questions for a lawyer

### c.1 API-Football terms

- The AF/API-Sports ToS ([api-sports.io/terms](https://api-sports.io/terms)) states that its data "is obtained from partners, and it is prohibited to resell this data to third parties". **Caveat:** this is quoted from a search-engine extract. Direct fetches of the page returned HTTP 403, so the full clause, surrounding terms and last-updated date were not read. **Re-read it in a browser before relying on it.**
- **Implication.** Nothing AF-sourced can appear in a sold product: not Pinnacle, Bet365 or the other 7 AF books, not fixtures or IDs, not scores. That includes derived tables that just repackage AF values.
- **Grey zone for a lawyer:** whether AF *fixture IDs and team names* used as join keys, or model outputs trained on AF data, count as "reselling data".
- **Cleanest route:** own canonical IDs, from row 1 of §a.
- **Comparable vendor terms.** The Odds API also bans reselling its data "as a standalone data product" and says it aggregates "publicly accessible sources" without circumventing authentication ([T&C](https://the-odds-api.com/terms-and-conditions.html)). So buying from them and reselling is also out.

### c.2 EU database right (Directive 96/9/EC) and the key cases

| Case | Holding (short) | Relevance to reselling collected odds |
|---|---|---|
| **BHB v William Hill**, C-203/02, 2004 ([EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex%3A62002CJ0203)) | Investment in *creating* data (fixture lists) does not count toward sui generis protection. Only investment in obtaining, verifying or presenting existing data counts. | **Helps us.** A bookmaker *creates* its odds, so its odds table arguably lacks sui generis protection. Unsettled for odds specifically. **Ask the lawyer.** |
| **Ryanair v PR Aviation**, C-30/14, 2015 ([Kluwer blog](https://legalblogs.wolterskluwer.com/copyright-blog/ryanair-ltd-v-pr-aviation-bv-contracts-rights-and-users-in-a-low-cost-database-law/)) | Where a database is *not* protected, the Directive's user exceptions don't apply, so **ToS can ban scraping** contractually. | **Hurts us.** If BHB takes away the database right, the bookmaker's ToS becomes the main weapon, and we accept those ToS as account holders. |
| **Innoweb v Wegener**, C-202/12, 2013 ([SCL](https://www.scl.org/2984-database-right-innoweb-v-wegener-cjeu-judgment/)) | A dedicated meta-search engine that queries a protected database in real time re-utilises a substantial part of it. | Near-real-time odds mirroring is structurally close to this, *if* the odds DB is protected. |
| **CV-Online Latvia v Melons**, C-762/19, 2021 ([EUR-Lex](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex:62019CJ0762), [Bird & Bird](https://www.twobirds.com/en/insights/2021/uk/cv-online-latvia-cjeu-complicates-the-enforcement-of-database-rights)) | Baltic case. An aggregator infringes only if (1) the maker made a substantial investment in obtaining, verifying or presenting the data, **and** (2) the extraction risks the maker's ability to recoup that investment. The judgment weighs this against the benefit to users and competition. | **The most relevant test.** An odds comparison feed arguably *drives traffic to* the book, like KurDarbs redirecting to cv.lv, rather than substituting for it. A feed sold to *other bookmakers* (competitive price monitoring) is the opposite case and riskier. |

**Estonia specifically.** Database protection is implemented in the Estonian Copyright Act (Autoriõiguse seadus) [UNVERIFIED section numbers; not read]. No Estonian odds-scraping case law was found.

### c.3 How existing aggregators operate (as far as public material shows)

- **Enterprise vendors** (Sportradar, Kambi, TXOdds) supply odds *to* bookmakers or hold licensed data relationships. This is the only clearly "clean" model.
- **Developer APIs** (The Odds API, OddsPapi, odds-api.io) describe their data as collected from "publicly accessible sources" and push accuracy liability onto the user. None we read claims a licence from each bookmaker. [UNVERIFIED for OddsPapi and odds-api.io; their ToS were not read.] They appear to rely on:
  - public, logged-out pages;
  - bookmakers tolerating them, because comparison drives affiliate traffic;
  - operating from outside the EU. [UNVERIFIED]
- **Affiliates** often get odds through official affiliate or odds-widget feeds that bookmakers provide. [UNVERIFIED per book] **This is the obvious legitimate channel to ask Estonian books about first.**

### c.4 Our specific risk profile and questions for a lawyer

**Collection method matters.** Coolbet and Unibet-Site are collected through the **operator's logged-in account sessions** (DATA_SOURCES.md; `unibet_odds_feed.py`, the Coolbet Mac daemon). Epicbet is collected anonymously, but only after passing a Cloudflare JS challenge with FlareSolverr. Logged-in collection means we have *accepted* the book's ToS. The Cloudflare step could be read as circumventing a technical measure. Commercialising either raises the risk far above private analysis. *Corrected 2026-09-23: that describes the Mac era. On the VPS, Coolbet is read anonymously and Unibet-Site from a logged-out tab; no collection now uses the operator's accounts.*

**Existential risk for 🤖 OWN.** If a book links a resold feed to our accounts, it can close or limit the very accounts we stake from.

Questions for the lawyer:
1. Do Estonian bookmakers' odds tables attract sui generis protection, given BHB's "created data" rule? Does the answer differ for odds *history* we compiled ourselves? (Our own time series may be *our* protected database.)
2. Are the ToS of Coolbet, Unibet (Kindred), Epicbet, Olybet and Optibet enforceable against data we collected (a) while logged in, (b) anonymously, and (c) through a Cloudflare bypass?
3. Under CV-Online's two-part test, is a feed sold to affiliates (which sends traffic to the book) lower risk than one sold to competing bookmakers?
4. Does using AF fixture IDs or names as internal join keys taint a product built on our own odds?
5. Does selling odds data or comparison services in Estonia need anything under the Gambling Act (hasartmänguseadus) or the Advertising Act? Is a published odds feed "gambling advertising" that needs the mandatory warning?
6. Would a licensed or affiliate-feed agreement with each book (the clean route) be realistic, and on what terms?

---

## Sources

- Repo: `docs/AF_ENDPOINT_FREQUENCY.md`, `DATA_SOURCES.md`, `WORKFLOWS.md`, `INFRASTRUCTURE.md:61`, `docs/MODELLING_DATA_AUDIT_2026_09_16.md:100-120`, `workers/api_clients/api_football.py` (line numbers in the §a table), `workers/jobs/settlement.py:29,2700-2766`, `workers/scrapers/espn_results.py`, `workers/automation/unibet_odds_feed.py:487,627`.
- DB (read-only, 2026-09-23): 48h `odds_snapshots` by book; 30-day `matches` = 609 leagues and 16,451 matches; 30-day `simulated_bets` = 97 leagues; 14-day direct-book leagues = 500, Pinnacle = 427.
- External: the links inline above.
