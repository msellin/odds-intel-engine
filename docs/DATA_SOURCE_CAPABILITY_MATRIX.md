# Data-source capability matrix — API-Football vs Epicbet (2026-09-10)

Built to answer: *what can we get from AF vs from Epicbet, prematch and in-play?* The
short version: **AF is a DATA provider (odds + everything around a match); Epicbet is a
BOOKMAKER (odds only, but rich, and both prematch + live).** They are not substitutes —
Epicbet can't replace the non-odds half of AF that the model actually needs.

Legend: ✅ available/used · ⚠️ available but limited/unused · ❌ not offered

| Capability | AF prematch | AF in-play | Epicbet prematch | Epicbet in-play |
|---|---|---|---|---|
| **Fixtures / schedule** | ✅ | ✅ (live list) | ⚠️ (implied by odds board) | ⚠️ |
| **1X2 odds** | ✅ (13 books) | ✅ | ✅ | ✅ (`isLiveBet`) |
| **Over/Under (2.5/3.5…)** | ✅ | ✅ | ✅ | ✅ |
| **Asian handicap** | ✅ | ⚠️ | ✅ | ✅ |
| **Double chance** | ✅ | ❌ | ✅ | ✅ |
| **BTTS** | ✅ | ⚠️ | ✅ | ✅ |
| **Team totals** | ✅ | ⚠️ | ✅ (now modelled) | ✅ |
| **First-half markets (1x2 / O/U)** | ⚠️ | ⚠️ | ✅ | ✅ |
| **Corners (total / team / handicap)** | ❌ (odds) | ❌ | ✅ | ✅ |
| **Cards (total / team)** | ❌ (odds) | ❌ | ✅ | ✅ |
| **Player props** | ⚠️ | ❌ | ✅ (~2,000 groups) | ⚠️ |
| **Pinnacle (sharp anchor)** | ✅ | ⚠️ | ❌ (not a book Epicbet carries) | ❌ |
| **Live score** | — | ✅ | ❌ (only implied via settlement) | ⚠️ |
| **Match stats (shots, possession, corners count, cards count)** | ✅ (~17-32% leagues) | ✅ (same leagues) | ❌ | ❌ |
| **Events timeline (goals, cards, subs)** | ✅ | ✅ | ❌ | ❌ |
| **Lineups / formations** | ✅ | ✅ | ❌ | ❌ |
| **Player match stats** | ✅ | ✅ | ❌ | ❌ |
| **Standings / league tables** | ✅ | — | ❌ | ❌ |
| **Head-to-head** | ✅ | — | ❌ | ❌ |
| **Injuries / suspensions** | ✅ | — | ❌ | ❌ |
| **AF's own predictions** | ✅ | — | ❌ | ❌ |
| **Settlement results (final/HT score)** | ✅ | ✅ | ⚠️ (bettor could infer) | ⚠️ |

## What this means
- **Epicbet is our richest ODDS source** (prematch 119 markets, and a live `isLiveBet` feed we don't yet use) — it beats AF on *market breadth* for corners/cards/1H/team-totals and gives a **direct, less-stale** price than AF's aggregated feed.
- **But it cannot replace AF.** The model needs the non-odds half — **match stats, events, lineups, standings, H2H, injuries, settlement** — which a bookmaker does not publish. So the "drop the AF subscription" idea (ODDS-API-PRODUCT) only ever covers the odds half; the data half needs AF or another sports-data provider.
- **Pinnacle (our sharp anchor) is not on Epicbet** — the anchor keeps coming through AF's Pinnacle feed. Another reason AF stays load-bearing.

## How do odds APIs / AF actually get their data? (the owner's question)
Two different pipelines, bundled into one product:
- **Odds** — aggregators (The Odds API, OddsJam, and AF's odds half) collect prices from many bookmakers the **same way we do**: scrape/API each book and normalise into one schema. There is no magic feed — it's exactly Coolbet + Unibet + Epicbet + N more books, bundled. Our 3 direct scrapers are a small version of that engine.
- **Match data** (scores, stats, events, lineups, standings) — comes from **sports-data providers** (Sportradar / Opta / Stats Perform-style feeds, or the league/federation official feeds), collected by people at the venue + computer-vision/manual tagging. AF resells/repackages that. This is the half a bookmaker scraper can NOT reproduce — which is the real moat of a data API and the reason "replace AF with book scrapers" only goes halfway.

So: to ever be a full AF replacement we'd need an odds-aggregation engine (we have the seed) **plus** a match-data source (the hard, expensive half). Realistic near-term value is the odds half + using it better — which is what USE-COLLECTED-MARKETS and EPICBET-INPLAY-COLLECTION are.
