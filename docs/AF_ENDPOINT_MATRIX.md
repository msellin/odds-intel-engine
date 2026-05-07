# API-Football v3.9.3 — Endpoint Matrix

Full audit of every endpoint in the API, mapped against our current usage. Based on the official v3.9.3 documentation (130 pages, dated 28/04/2026) and a codebase scan of `workers/` and `scripts/`.

---

## Section 1 — Endpoints We Currently Use

| Endpoint | Params We Use | Pipeline Worker | Notes |
|---|---|---|---|
| `/status` | — | `daily_pipeline_v2.py` | Quota check before pipeline runs |
| `/leagues` | `id`, `season`, `current=true` | `fetch_fixtures.py` | Discovers active leagues and current seasons |
| `/fixtures` | `league+season`, `date`, `live=all`, `id`, `team+last+status` | `fetch_fixtures.py`, `live_tracker.py`, `settlement.py` | Core data source for all match data |
| `/fixtures/statistics` | `fixture`, `half=true/false` | `fetch_enrichment.py` | Half-time stats since v3.9.3; full-match stats always available |
| `/fixtures/events` | `fixture` | `fetch_enrichment.py`, `live_tracker.py` | Goals, cards, substitutions |
| `/fixtures/lineups` | `fixture` | `fetch_enrichment.py`, `live_tracker.py` | Starting XI, coach, formation |
| `/fixtures/players` | `fixture` | `fetch_enrichment.py` | Player-level match stats (shots, passes, rating) |
| `/fixtures/headtohead` | `h2h`, `last` | `fetch_enrichment.py` | H2H history between two teams |
| `/standings` | `league+season` | `fetch_enrichment.py` | League table and form |
| `/odds` | `fixture`, `date+page` | `fetch_odds.py` | Pre-match odds from 13 bookmakers; 10 results/page |
| `/odds/live` | bare (all) | `live_tracker.py` | In-play odds; updated every 5–60s; no history stored |
| `/predictions` | `fixture` | `fetch_predictions.py` | AF's own prediction scores and win % |
| `/injuries` | `fixture`, `ids` (batch up to 20) | `fetch_enrichment.py` | Current injury/suspension list per match |
| `/teams` | `search` | `api_football.py` | Team lookup by name |
| `/teams/statistics` | `team+league+season` | `fetch_enrichment.py` | Season form, goals, cards, formations, penalty stats |
| `/venues` | `id` | `fetch_enrichment.py` | Venue surface, capacity — used in AF-VENUES signal |
| `/coachs` | `team` | `fetch_enrichment.py` | Manager details — used in MGR-CHANGE signal |

---

## Section 2 — New Params on Endpoints We Already Use (Quick Wins)

These are additions to endpoints we already call. No new endpoint needed — just update the call params.

| Endpoint | New Param | Available Since | What It Unlocks | Effort | Impact |
|---|---|---|---|---|---|
| `/fixtures` | `ids=id-id-id` (up to 20) | 3.x (batch) | Fetch events+lineups+stats+players for up to 20 fixtures in **one call** instead of 4 separate calls per fixture. Massive reduction in daily API quota usage during enrichment. | Low — update `fetch_enrichment.py` loop to batch | **High** — saves ~75% of API calls for multi-fixture days |
| `/fixtures/statistics` | `half=true` / `half=false` | 3.9.3 | First-half and second-half stats separately. Opens half-time performance signals (xG by half, shots by half, defensive shape shifts). Data available from 2024 season onward. | Low — add two calls instead of one | **Medium** — new signals: H1 vs H2 dominance, late-game collapse detection |
| `/fixtures/rounds` | `dates=true` | 3.9.3 | Returns date ranges per round, not just round names. Useful for scheduling enrichment fetches at the right time. | Low | Low |
| `/teams/statistics` | — (response expanded) | 3.x | Response now includes: goals over/under counts, scoring by minute bucket (0-15, 15-30…), cards by minute, most-played formation, penalty stats (scored/missed %). Already in the response — just parse additional fields. | Low — update parsing code | **Medium** — penalty efficiency, late-game scoring patterns are useful model signals |

---

## Section 3 — Endpoints We Should Add

Grouped by priority.

### Priority 1 — High Impact, Low Effort

| Endpoint | Added in | What It Returns | Use Case | Effort | Impact |
|---|---|---|---|---|---|
| `/players/squads` | 3.8.1 | Full squad list for a team (player IDs, names, ages, positions) | Pre-match: know the 25-man squad. Combined with `/injuries`, identify how many first-team players are unavailable. Also resolves player IDs for future calls to `/players` stats. | Low — one call per team per week | **High** — enables injury severity scoring (1 striker out vs 5 defenders out is very different) |
| `/sidelined` | old endpoint, batch params new in 3.9.3 | Player's full history of injuries/suspensions (type, reason, dates) | "Is this player injury-prone?" signal. Currently we know if a player is out *now* — this shows chronic absence patterns. Code exists (`api_football.py`) but isn't called anywhere in the pipeline. Batch up to 20 players/coachs per call. | Low — wire up existing function | **High** — injury recurrence rate is a strong availability signal |
| `/odds/mapping` | old | List of all fixture IDs that have pre-match odds available | Use before bulk odds fetch to know exactly which fixtures have odds — avoids wasting calls on fixtures with no odds data. 100 results/page, updated daily. | Low — one call per day | **Medium** — reduces wasted `/odds` calls on fixtures with no coverage |

### Priority 2 — Medium Impact, Medium Effort

| Endpoint | Added in | What It Returns | Use Case | Effort | Impact |
|---|---|---|---|---|---|
| `/players` (statistics) | old | Season stats per player: goals, assists, shots, passes, dribbles, rating, cards, minutes played | Player form signal. "Striker hasn't scored in 8 games" or "midfielder averaging 9.1 rating last 5." Expensive to fetch (20 per page) — only pull for key players, not full league. | Medium — need to identify which players to track, handle pagination | **Medium** — adds individual player form to match context |
| `/transfers` | old | All transfers in/out for a player or team | Detect recent squad changes. A team with 5 new signings in January may be unsettled. Code exists in `api_football.py` but isn't called. | Low — wire up existing function | **Medium** — squad stability signal, especially useful around transfer windows |
| `/players/profiles` | 3.9.3 (NEW) | Full player profile: DOB, nationality, height, weight, photo. Searchable by lastname. 250 per page. | Resolve player metadata when we have a name but no ID. Useful for sidelined/injury data enrichment. | Low | Low-Medium |
| `/players/teams` | 3.9.3 (NEW) | A player's full career club history (all teams and seasons) | "New signing hasn't played in this league before" signal. Contextualizes transfer impact. | Medium — need to identify players first | **Low-Medium** |

### Priority 3 — Useful for Content / Display (Frontend Value)

| Endpoint | Added in | What It Returns | Use Case | Effort | Impact |
|---|---|---|---|---|---|
| `/players/topscorers` | old | Top 20 scorers per league+season | League stats display on frontend. Can also flag "league top scorer is playing today" as a signal boost. | Low — one call per league per week | **Medium** — display value + "star player" signal |
| `/players/topassists` | 3.8.1 | Top 20 assisters per league+season | Same as topscorers — display and signal value | Low | Low-Medium |
| `/players/topyellowcards` | 3.8.1 | Top 20 yellow card earners per league+season | Suspension risk signal. "This player is one yellow from a ban" context. | Low | Low-Medium |
| `/players/topredcards` | 3.8.1 | Top 20 red card earners per league+season | Disciplinary context | Low | Low |
| `/odds/live/bets` | 3.9.2 (NEW) | All 137 available bet types for in-play odds | We currently fetch all live odds without knowing what bet types exist. This lets us filter `/odds/live` calls to specific bet IDs (match winner, next goal, over/under) rather than getting everything. | Low — one call to cache, then use IDs in live fetcher | **Medium** — reduces live odds payload size, enables targeted bet type monitoring |
| `/odds/bets` | old | All available bet type IDs for pre-match odds (separate catalog from live) | Reference: know all bet type IDs so we can fetch specific markets (e.g. Asian handicap only). | Low | Low |
| `/odds/bookmakers` | old | All available bookmakers with IDs | We use `bookmaker` IDs in odds calls but never fetch the catalog. Lets us verify IDs and add new bookmakers dynamically. | Low | Low |

---

## Section 4 — Reference Endpoints (Skip)

These exist but return lookup/reference data we don't need in the pipeline. Not worth adding.

| Endpoint | What It Is |
|---|---|
| `/timezone` | List of valid timezone strings for the `timezone` param |
| `/countries` | List of all countries covered |
| `/leagues/seasons` | All seasons available per league |
| `/teams/seasons` | All seasons a team has data for |
| `/teams/countries` | Countries with team data |
| `/players/seasons` | All seasons with player data |
| `/trophies` | Player/coach trophy list — no meaningful prediction value |

---

## Summary: Recommended Implementation Order

| # | Task | Endpoint(s) | Why Do It First | Prio / Impact |
|---|---|---|---|---|
| 1 | **Batch fixture enrichment** | `/fixtures?ids=...` | Cuts enrichment API calls by ~75%. Free win — same data, fewer calls. | P1 / 🔴 Critical |
| 2 | **Half-time stats** | `/fixtures/statistics?half=true` | New signals for first/second half dominance detection. 2024+ data available now. | P1 / 🟠 High |
| 3 | **Wire up /sidelined** | `/sidelined` | Code already exists, just not called. Player injury history → injury recurrence signal. | P1 / 🟠 High |
| 4 | **Wire up /transfers** | `/transfers` | Code already exists, just not called. Squad disruption signal around transfer windows. | P1 / 🟡 Medium |
| 5 | **Squad loader** | `/players/squads` | Get full squad per team weekly → enables injury severity scoring (who's missing, what position). | P2 / 🟠 High |
| 6 | **Odds mapping prefetch** | `/odds/mapping` | Know which fixtures have odds before bulk-fetching — eliminates wasted calls. | P2 / 🟡 Medium |
| 7 | **Live bet type filter** | `/odds/live/bets` | Cache 137 in-play bet type IDs → filter live odds calls to just the markets we care about. | P2 / 🟡 Medium |
| 8 | **Top player stats** | `/players/topscorers`, `/players/topassists`, `/players/topyellowcards` | Display value on frontend + "key player is in today's fixture" signal boost. | P3 / 🟡 Medium |
| 9 | **Parse expanded team stats** | `/teams/statistics` response fields already returned | Penalty efficiency %, scoring by minute, formation dominance — parse fields already in the response. | P2 / 🟡 Medium |

---

*Generated 2026-05-07 from API-Football v3.9.3 documentation (130 pages) + codebase scan.*
