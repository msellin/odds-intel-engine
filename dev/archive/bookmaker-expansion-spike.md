# GROWTH-BOOKMAKER-EXPANSION — Phase 0 Spike

> Status: Research deliverable. Updated 2026-06-05.
> Owner: spike agent. Phase 1 implementation tasks live separately.

## 1. Executive summary

We currently ship odds from **8 "accessible" books** (Bet365, Unibet, Betano, Marathonbet, 10Bet, 888Sport, Pinnacle, Coolbet) sourced via API-Football's bulk-odds endpoint + a Coolbet scraper. The 13-book AF list also returns SBO, Dafabet, 1xBet, William Hill, BetVictor, Betfair — but these are excluded from `ACCESSIBLE_BOOKMAKERS` (see `daily_pipeline_v2.py:992`). Real ceiling today is ~6 books per match for edge math.

**Recommended Phase 1 (3 books, ~1-2 weeks engineering):**
1. **Pinnacle direct** via tipsters RapidAPI feed — fixes the "we use Pinnacle as gospel but only get it via AF (paginated, sometimes missing)" problem and gives us delta polling at <30s.
2. **Bwin** (already in AF feed but currently treated as a noise book — promote, no new ingest cost).
3. **Betsson** via OpticOdds / SportsGameOdds aggregator — adds a sharp-ish Nordic dark horse with strong Estonia/EU coverage, and is a near-twin sanity check for our Coolbet line.

**Phase 2 (stretch, blocked by aggregator $$$):** 1xBet, SBOBET, William Hill (UK only) via an aggregator subscription if Phase 1 lifts edge-coverage measurably. **Skip US books (DraftKings/FanDuel/BetMGM) entirely** until we have a US legal entity — odds aren't useful to EU/Estonian users anyway.

The big honest finding: **we are not actually 13-book-limited** — AF already gives us 13. We are *coverage* limited by exclusions we made for accessibility/quality reasons. Phase 1 is more about un-excluding good books and adding Pinnacle as a first-class direct feed than building 5 new scrapers.

---

## 2. Bookmaker matrix

| Book | Data path | Sharpness | Markets | League depth | Effort | Geo | Recommendation |
|---|---|---|---|---|---|---|---|
| **Pinnacle** | AF (partial) + direct RapidAPI feed | Sharp (benchmark) | 1X2, OU, AH, BTTS, DC, props | Deep (all tiers) | 2-4d (RapidAPI client) | Curacao-licensed, no EU geo block on data | **PHASE 1** |
| **Bwin** | Already in AF | Mid-soft | 1X2, OU, AH | Top-5 + most EU | 0.5d (un-exclude + smoke) | EU-wide retail brand | **PHASE 1** |
| **Betsson** | OpticOdds / SportsGameOdds aggregator | Mid (sharp-ish for Nordic) | 1X2, OU, AH, BTTS | Nordic deep, EU mid | 3-5d (new aggregator client) | EE/SE/NO/DK native | **PHASE 1** |
| **Bet365** | AF only (no direct, no aggregator carries it cheaply) | Mid-soft, slow-mover | All | Very deep | already in feed — just verify | EU/UK | PHASE 1 (verify, no new work) |
| **Marathonbet** | Already in AF | Sharp-ish | 1X2, OU, AH | Deep (EE niche) | 0d — already accessible | EU + RU (license risk) | Already in — monitor |
| **William Hill** | AF (blacklisted for OU) | Mid | 1X2 OK, OU broken | UK-deep, EU mid | 1d (write fixed OU parser) | UK only practically | PHASE 2 |
| **Unibet** | Already in AF | Mid-soft | All | Deep | 0d | EU regional variants | Already in |
| **1xBet** | Aggregator only (OddsPapi $$ / SportsGameOdds) | Sharp on niche, soft on majors | All + exotic | Very deep (300+ leagues) | 1w + reputation risk | Curacao; regulatory issues in DE/UK/NL | PHASE 2 (with caveats) |
| **SBOBET** | Aggregator only (OpticOdds / 365oddsapi) | Sharp (Asian benchmark) | 1X2, OU, AH | Asian deep, EU thin | 1w | IoM-licensed, blocked in many EU | PHASE 2 |
| **DraftKings** | Aggregator only | Soft | All US-style | EPL only (no other soccer depth) | 1w | US-geo locked | **SKIP** |
| **FanDuel** | Aggregator only | Soft | All US-style | EPL only | 1w | US-geo locked | **SKIP** |
| **BetMGM** | Aggregator only | Soft | All US-style | EPL only | 1w | US-geo locked | **SKIP** |
| **Coolbet** | Direct scraper (already wired) | Mid | 1X2, OU, AH (half/full only) | EE+Nordic + top-5 | 0d | EE native | Already in — keep as placement venue |
| **Olimp.bet** | None — no aggregator carries it | Unknown | Unknown | Russia/Kazakhstan | High (custom scrape) | RU/KZ — geo & sanctions risk | **SKIP** |
| **ParionsSport (FDJ)** | Semi-public JSON at `pointdevente.parionssport.fdj.fr/api/` | Soft (monopoly book) | 1X2, OU | Ligue 1 deep, otherwise thin | 3-5d (custom scraper) | FR-only | PHASE 2 if FR vertical opened |
| **Betfair Exchange** | AF (excluded) + direct API | Sharpest (true market) | All exchange markets | Top-5 deep | 1w (different model — back/lay) | EU-wide; needs Betfair account | PHASE 2 dark-horse |

---

## 3. Per-book deep dive (top 10)

### Pinnacle — PHASE 1, top priority
Pinnacle is already our calibration benchmark (see `daily_pipeline_v2.py` — every league veto and CLV computation references "Pinnacle CLV ≥+5%"). But we ingest it only via AF's bulk-odds endpoint, which is ~2h refresh and occasionally drops Pinnacle entirely from a fixture (covered by code at `daily_pipeline_v2.py:~1010` — "When Pinnacle IS present, also drop any non-Pinnacle row priced more than 2× Pinnacle"). Direct ingest fixes this. The official Pinnacle API (`pinnacleapi.github.io`) requires a real Pinnacle account + bet placement history and rate-limits at 1 req per 2 min per endpoint per sport — workable but slow. The **tipsters/RapidAPI** mirror at `rapidapi.com/tipsters/api/pinnacle-odds` reports near-real-time odds with simpler auth; need to confirm pricing (page didn't render but community reports €20-50/mo for hobbyist tier). Sharpness uplift is huge: every dollar of CLV we measure today is Pinnacle-relative, so getting Pinnacle in <30s instead of 2h directly tightens the closing-line we benchmark against.

### Bwin — PHASE 1, near-zero cost
Already in AF's bulk feed. We exclude it from `ACCESSIBLE_BOOKMAKERS` (along with SBO, Dafabet, 1xBet, William Hill, BetVictor, Betfair) — but the stated reason ("Inaccessible books") doesn't fit Bwin: it's a massive EU retail brand owned by Entain, fully licensed across the EU including Estonia. Spot check `daily_pipeline_v2.py:986-1001` — Bwin is conspicuously absent without a comment. Likely an oversight when the constant was first written. Promoting it is a one-line change + smoke test. Bwin lines are mid-soft and slow-moving, which is *good* for us — they're the kind of book that lags Pinnacle by 10-30min, which is where most of our edge sits.

### Betsson — PHASE 1, real new integration
Not in AF's 13-book feed. Available via OpticOdds and SportsGameOdds aggregators. Betsson is Nordic-native (Sweden HQ, runs Coolbet's parent brand) — for our Estonia user base this is the second most important book after Coolbet. Sharpness sits between Pinnacle (sharp) and Bet365 (soft) for Nordic-market football. Aggregator cost: SportsGameOdds $99/mo gets 80+ books including Betsson, refresh ~30s. Integration is one new client (`workers/api_clients/sportsgameodds.py`) modeled on `odds_api.py`, plus mapping to our `bookmaker='Betsson'` rows in odds_snapshots. Estimated 3-5 dev days incl. smoke + monitoring.

### Bet365 — verify only
Already in our AF feed and already in `ACCESSIBLE_BOOKMAKERS`. The market-research panic ("RebelBetting has 100+ books, we have 13") slightly misreads our situation: we *have* Bet365, the world's largest retail book. What we're missing isn't Bet365 — it's depth (more sharps, more dark-horse softs for outlier finds). Phase 1 should just include a smoke check that Bet365 coverage stays above ~80% of fixtures via AF, since AF is known to drop Bet365 silently on some leagues.

### William Hill — PHASE 2, fix the parser
Already in AF feed but blacklisted for OU markets (`odds_quality.py:18` — "William Hill = 88% Under-favored on OU 1.5, line-shifted"). 1X2 from WH passes through fine. Phase 2 work: investigate whether WH's OU is genuinely broken or whether AF is mis-mapping their first-half/team-OU lines as full-time. If we can write a corrected parser (~1d), WH OU comes online. WH is also the only major UK book — meaningful for UK-bias league pricing (Championship, EFL).

### 1xBet — PHASE 2, with regulator caveats
Curacao-licensed, headquartered in Cyprus. Already in AF feed (excluded from ACCESSIBLE_BOOKMAKERS as "inaccessible"). Reputation issues: legitimately banned in Germany, Netherlands, France, UK. **Still legal in Estonia**, but writing "1xBet" into our UI may scare regulated-market visitors. Sharpness is uneven — sharp on niche Eastern European leagues, soft on majors. Coverage is the deepest of any book (300+ leagues). Phase 2 only — and even then, flag prominently in UI ("operator in some EU jurisdictions"). Don't promote to Phase 1 just because the data is there.

### SBOBET — PHASE 2, Asian sharp
Isle-of-Man licensed, but blocked or unregulated in most EU jurisdictions including Estonia. Sharpness on Asian Handicap is industry benchmark. Already in AF feed but excluded. For our Estonia/EU user base the *odds* are interesting (model calibration) but we can't promote the *book*. Approach: ingest as a calibration-only reference (like we use Pinnacle for CLV), don't surface in `value_bets.bookmaker` recommendations.

### DraftKings / FanDuel / BetMGM — SKIP
All US-only. None of our users are in legal US betting states, and our payment/affiliate plumbing has no US handling. Adding their odds to "best price" math would be actively misleading — we'd recommend a price the user cannot achieve. RebelBetting and OddsJam carry these because their primary market *is* US. We are not. If we ever open a US tier these become Phase 1 the same day; until then, skip.

### Coolbet — confirm (already integrated)
Confirmed: full direct scraper at `workers/automation/coolbet_*.py`, 30-min odds snapshots into odds_snapshots via `store_coolbet_odds_snapshot()`. Limited to full-line and half-line AH (no quarter lines — known limitation per `feedback_coolbet_limitations.md`). This is also our **placement venue** (paper-mode currently per SELF-USE-VALIDATION Phase 3), so its odds are not just edge fodder — they're the actual realizable price. Keep prioritized; do not break.

### Marathonbet — already in, sharp-leaning
In AF feed and already in `ACCESSIBLE_BOOKMAKERS`. Industry reputation: sharp on Russian/CIS leagues, mid on majors. We get them today; verify their coverage stays steady (AF sometimes drops Marathon on specific leagues). No action needed.

### ParionsSport (Pari-Sportifs.fr / FDJ) — PHASE 2 maybe
French state-monopoly book (FDJ). The endpoint `https://www.pointdevente.parionssport.fdj.fr/api/` is semi-public — there's a working open-source scraper at `github.com/bettor-league/parions-sport-batch`. Soft pricing (monopoly = no competitive pressure), Ligue 1 deep, low coverage on other leagues. Only worth doing if/when we explicitly target French users. Geo: works from any IP, but legal to consume from FR only.

---

## 4. Phase 1 recommendation — concrete spec

**Books, in priority order:**

1. **Pinnacle direct (RapidAPI tipsters feed)** — ~2-4 dev days
   - New client: `workers/api_clients/pinnacle_direct.py` modeled on `odds_api.py`
   - Endpoint: `pinnacle-odds.p.rapidapi.com/kit/v1/markets` (sport=soccer)
   - Delta polling at 5-min intervals; snapshot at 30-min
   - Write rows with `bookmaker='Pinnacle'` to `odds_snapshots`, dedup-safe with existing AF Pinnacle rows
   - Expected cost: €20-50/mo at hobbyist RapidAPI tier
   - **Edge-coverage uplift: high** — tightens our CLV benchmark from 2h-stale to <10min

2. **Bwin un-exclusion** — ~0.5 dev days
   - Edit `daily_pipeline_v2.py:992` ACCESSIBLE_BOOKMAKERS — add `"Bwin"`
   - Smoke test: verify Bwin rows present in last 7d odds_snapshots for ≥80% of top-5 fixtures
   - Audit Bwin OU quality (run same script that flagged William Hill in `odds_quality.py`); if Bwin OU passes the 1.02 implied-sum floor, ship
   - **Edge-coverage uplift: low-medium** — one more soft book in the mix, but Bwin lines are correlated with Bet365/Unibet so marginal lift

3. **Betsson via aggregator** — ~3-5 dev days
   - New client `workers/api_clients/sportsgameodds.py` (SportsGameOdds $99/mo Starter plan — gets Betsson + 80 others as bonus)
   - Or evaluate OpticOdds (no public pricing, likely $300+/mo enterprise) vs The Odds API 100K plan ($59/mo) — TOA reports Betsson under their EU region key
   - Recommend **The Odds API 100K plan at $59/mo** as cheapest path that confirms Betsson coverage; revisit SportsGameOdds only if TOA doesn't include Betsson on that tier (would need to sign up and verify)
   - Rate-limit aware scheduler entry every 30 min
   - **Edge-coverage uplift: medium** — Betsson is a structurally different book (Nordic-native) so its outliers correlate weakly with our current 8 books

**Total Phase 1 effort: ~1-2 weeks engineering, ~$60-100/mo new infra cost.**

**Out of Phase 1 scope (deliberately):** Bet365 (already in), Marathonbet (already in), DraftKings/FanDuel/BetMGM (skip), William Hill (Phase 2 — fix OU parser), 1xBet/SBOBET (Phase 2 — regulatory caveats), Olimp (skip), ParionsSport (Phase 2).

---

## 5. Cost analysis

| Option | Monthly cost | Books unlocked | Refresh | Verdict |
|---|---|---|---|---|
| Pinnacle RapidAPI (tipsters) | €20-50 | Pinnacle only | <30s | **YES — Phase 1** |
| The Odds API 100K plan | $59 | Pinnacle, Bet365 (limited), Unibet, Marathonbet, 1xBet, BetVictor, Betsson, Coolbet, Betclic, ~40 total | ~30s | **MAYBE — Phase 1** if Betsson confirmed |
| The Odds API 5M plan | $119 | same books, way higher quota | <30s | overkill for now |
| SportsGameOdds Starter | $99 | 80+ incl. Pinnacle, Betsson, Bwin, Bet365, DK/FD/MGM | 5s | overlap with AF; reconsider Phase 2 |
| OddsPapi (free tier) | $0 | 350+ incl. all targets, 250 reqs/mo | 1s | quota too small; paid pricing private |
| OddsPapi (paid) | "Custom" / per-request | 350+ | <1s | dark price = avoid for now |
| OpticOdds | $300-1000+ (enterprise) | 200+ incl. all | <1s | overkill, kill on price |
| **Recommended Phase 1 spend** | **~$80-110/mo combined** | Pinnacle direct + Betsson (via TOA) + Bwin (free, already AF) | n/a | **proceed** |

Sanity check vs `INFRASTRUCTURE.md`: current monthly spend is ~$50 (Railway $5 + AF Ultra $39 + Supabase free + Vercel free). Phase 1 doubles infra cost — defensible if it lifts Elite-tier value-bet count by ≥10%.

---

## 6. Risks / unknowns

- **Pinnacle RapidAPI feed legitimacy.** The tipsters mirror is a third party scraping Pinnacle's public site, not an official partnership. ToS-grey, occasional outages reported on RapidAPI discussions. Mitigate: keep AF Pinnacle ingest as fallback, alert on Pinnacle-row staleness in `health_alerts.py`.
- **Aggregator double-billing.** If we sign The Odds API for Betsson but TOA's Pinnacle is delayed vs RapidAPI, we end up paying for Pinnacle twice. Decision tree: subscribe to RapidAPI tipsters first ($25), confirm Pinnacle fresh, then evaluate TOA for Betsson-only.
- **1xBet brand risk.** If Phase 2 pulls in 1xBet, anyone in DE/UK/NL/FR seeing "1xBet best price" in our UI may distrust us. UI mitigation: badge books with `[Some EU restrictions]`. Or: ingest 1xBet odds for model only, don't surface as "best price" book.
- **AF feed silently dropping Pinnacle on niche leagues.** Existing code at `daily_pipeline_v2.py:~1010` already handles this defensively. Direct Pinnacle feed reduces this fragility — but make sure the new feed's league mapping is at least as wide as AF's, otherwise we just trade one gap for another.
- **Coolbet odds-snapshot collision.** When Pinnacle direct lands, dedup logic in `fetch_odds.py:store_odds` must not double-write Pinnacle rows from AF + RapidAPI for the same fixture+market+selection within the same minute. Confirm uniqueness constraint behavior before deploy.
- **Betsson EE-specific URL.** Betsson has regional sites (betsson.com / betsson.ee / betsson.se) — their odds can differ by region by 1-2%. The Odds API may serve only one regional variant; verify which one matches Estonian users.
- **TOA "all bookmakers" pricing claim is suspect.** Their page says "all bookmakers" on all paid tiers, but historically Pinnacle has been gated to higher plans on TOA. Verify with their support before committing.
- **Quota math at full daily polling.** TOA 100K plan = ~3,300 reqs/day. At 30-min refresh × top-5 + 41 leagues × 2 markets, naive call pattern blows the budget. Use their `/sports/{sport}/odds` bulk endpoint which costs `regions × markets` per call, not per fixture.
- **No protection against aggregator going down.** All Phase 1 books except Bwin route through external services. Add a `health_alerts.py` check: if Pinnacle row count over last 6h drops >50% vs trailing 7d median → telegram alert.

---

## Sources

- [The Odds API pricing & bookmaker list](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html)
- [The Odds API v4 docs](https://the-odds-api.com/liveapi/guides/v4/)
- [Odds API pricing 2026 comparison](https://oddspapi.io/blog/odds-api-pricing-2026-comparison/)
- [Pinnacle official API docs](https://github.com/pinnacleapi/pinnacleapi-documentation)
- [Pinnacle Odds on RapidAPI (tipsters)](https://rapidapi.com/tipsters/api/pinnacle-odds)
- [SportsGameOdds Pinnacle page](https://sportsgameodds.com/bookmakers/pinnacle-odds-api)
- [ParionsSport open-source scraper](https://github.com/bettor-league/parions-sport-batch)
- [OddsPapi sportsbook coverage](https://oddspapi.io/blog/sportsbook-api-350-bookmakers/)
- [Sharp vs soft sportsbook reference](https://www.rebelbetting.com/blog/difference-soft-sharp-bookmakers)
- Internal: `workers/jobs/daily_pipeline_v2.py:986-1001` (ACCESSIBLE_BOOKMAKERS)
- Internal: `workers/utils/odds_quality.py:13-21` (BLACKLISTED_OU_SOURCES)
- Internal: `workers/api_clients/api_football.py`, `workers/api_clients/odds_api.py`
- Internal: `DATA_SOURCES.md` (current 13-bookmaker AF coverage)
