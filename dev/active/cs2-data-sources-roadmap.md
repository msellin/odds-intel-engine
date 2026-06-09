# CS2 Data Sources — Inventory + Integration Roadmap (2026-06-09)

Living document tracking what we get from where. Built after the Virtus.pro
vs Oxuji incident exposed gaps in our recent-form data + the PandaScore
free-tier probe revealed unused signals.

## What we already use

| Source | Free? | What we pull | Where it lives | Cron |
|--------|-------|--------------|---------------|------|
| **HLTV** `/team/{id}`, `/player/{id}` | scrape, no auth | Current rosters, days_in_team, individual Rating 2.0 | `cs2_hltv_team_rosters`, `cs2_hltv_player_ratings` | daily 02:00 UTC |
| **HLTV** `/stats/teams/maps/{id}/{slug}` | auth (Cloudflare cookies) | Per-team-per-map career win % + clutch/comeback | `cs2_hltv_team_map_stats` | manual (auth expires) |
| **HLTV** `/stats/players/{id}/{slug}` | auth | Per-player career K/D, ADR, KAST, headshot %, kills/round | `cs2_hltv_player_stats` (1,270 done) | manual |
| **HLTV** `/matches/{id}/{slug}` | scrape, no auth | Per-(player, map) Rating/K-D/ADR/KAST/Rating, per-side CT/T rounds, veto sequence | `cs2_hltv_matches/match_maps/match_veto/player_match_stats` | Railway every 30 min |
| **HLTV** `/ranking/teams` | scrape | Weekly top-248 rank + points | `cs2_hltv_rankings` | daily 05:00 UTC |
| **HLTV** `/results` | scrape | Match ID queue feed | `cs2_hltv_match_queue` | Railway 3×/day |
| **PandaScore** `/csgo/matches/past?filter[status]=finished` | free 1000 req/hr | Match results, scores, winner, tournament tier (a/b/c/d), prizepool | `cs2_pandascore_matches` | every 6h |
| **PandaScore** `/csgo/teams` | free | Current roster with PandaScore player IDs | `data/esports/cs2/pandascore_rosters.json` | daily |
| **bo3.gg** `/matches` | free | Live + finished CS2 matches with single-bookie reference odds | `cs2_upcoming_matches`, `cs2_results` | every 4h |
| **bo3.gg** `/player_transfers` | free | Roster change detection (45-day window) | scanner inline | every 4h |
| **Coolbet** anon-read | free | Multi-market CS2 odds (one of the books we place at) | `cs2_upcoming_matches.coolbet_odds*` | every 30 min |
| **Pinnacle** guest API | free (geo-blocked from EU) | Moneyline closing line (gold standard truth) | `cs2_upcoming_matches.pinnacle_odds*` | every 30 min, Railway-only |
| **GRID Open Access** | free | Fixture cross-check (CS2 = titleId 28) | scanner | every 4h |
| **EgamersWorld** | scrape | Alternate ranking signal | `cs2_egamersworld_rankings` | weekly |
| **GGSCORE** | scrape | Alternate ranking signal | `cs2_ggscore_rankings` | weekly |

## What we should add next (ranked by ROI)

### 1. PandaScore `/tournaments/past` extras
Already wiring tier + prizepool (2026-06-09). Other inline fields worth capturing:
- `region` (EU/NA/SA/APAC) — regional strength patterns
- `has_bracket` (true=playoffs, false=group/league) — bracket matches have lower upset rate
- `country` — LAN venue tracking

### 2. PandaScore `/players` (free)
Sort by `modified_at` to detect roster movement events. Currently we only know "team X has player Y" from /teams response. The events feed would let us derive "Y joined X on date Z" — exact roster-age timestamps without scraping HLTV /stats/teams/lineups.

Endpoint: `GET /csgo/players?sort=-modified_at&per_page=100`

### 3. Faceit Data API (free tier, requires key signup)
Endpoint: `https://open.faceit.com/data/v4/`
Bearer auth via `developers.faceit.com`.

Best for:
- Tier-3/4 players outside HLTV's tracking
- ELO + match history for unknown players
- Recent form for the long tail

URL patterns:
- `/players?nickname={nick}` → player_id
- `/players/{player_id}/stats/cs2` → lifetime + last-N
- `/players/{player_id}/history?game=cs2&limit=20` → recent form

Rate limit: undocumented, keep ≤ 1 req/s.

### 4. Leetify public API (no auth for basics)
Endpoint: `https://api.leetify.com/api/profile/{steamId64}`

Best for:
- Trade %, utility usage, T/CT split per player
- Recent skill estimates that HLTV rank doesn't capture

Gotcha: Requires Steam64 → player nickname mapping (one-time join table).
Pro players need a public Steam-linked Leetify account; coverage ~80% of tier 1-2.

### 5. Esports Earnings API (free, key)
Endpoint: `http://api.esportsearnings.com/v0/`

Best for: tournament tier weighting via prize money.
- `LookupHighestEarningTeamsByGame?gameid=839` (CS2 = 839)
- `LookupTournamentsByGameId?gameid=839`

PandaScore tier is fine for now; Esports Earnings adds historical prize
money totals which proxy "is this team used to high-stakes pressure."

### 6. EsportsCharts (scrape)
URL pattern: `https://escharts.com/tournaments/csgo/{slug}`

Best for: peak viewers per tournament as a tier-quality proxy. Some big
prizepool events have low viewers (suspicious) → de-tier them.

## Sources we explored but skipped

| Site | Why skipped |
|------|-------------|
| csstats.gg, csgostats.gg | Cloudflare-walled, no API, MM/Premier ranks only (not pro signal) |
| Tracker.gg | Same as above |
| gosugamers | Site moribund since 2023, data stale |
| ESEA | Login-walled; FACEIT overlaps |
| VLR.gg | Valorant only, not CS |
| GameTracker | Historical results overlap with PandaScore + HLTV |

## Probe results (2026-06-09)

### PandaScore free tier surface

| Endpoint | Status | Notes |
|----------|--------|-------|
| `/matches/past` (list) | ✅ 200 | Filter status=finished is critical (99% canceled without it) |
| `/matches/{id}` (single) | ❌ 403 | `detailed_stats` paywalled |
| `/players` (list/search) | ✅ 200 | Name, nationality, current_team, modified_at |
| `/players/{id}/stats` | ❌ 403 | Career stats paywalled |
| `/tournaments/past` | ✅ 200 | Has tier (a/b/c/d), prizepool, region |
| `/leagues` | ✅ 200 | Basic metadata |
| `/series/past` | ✅ 200 | Series → tournament hierarchy |
| `/teams` | ✅ 200 | Current roster with player IDs |
| `/games` | ❌ 404 | Doesn't exist on this tier |
| `/streams` | ❌ 404 | Doesn't exist |

### GGSCORE + eScoreNews
Both 403 from Estonian IP. Test from Railway US IP next session.

### EgamersWorld
200 from EU IP. Has roster + form + h2h mentions. Worth deeper parse if we
want a third ranking signal beyond HLTV/PandaScore.

## Empirical findings (2026-06-09)

### Older data hurts the model

Sweep at v4 baseline (just hltv_v1 saved_prob, no extras):

| --since | N | AUC |
|---------|---|-----|
| 2025-06-01 | 3,106 | 0.673 |
| **2025-01-01** | **4,576** | **0.675** ← sweet spot |
| 2024-06-01 | 6,920 | 0.660 |
| 2024-01-01 | 8,639 | 0.646 |
| 2023-01-01 | 9,238 | 0.644 |

CSGO-era data (pre-Oct 2023) is actively harmful. Don't extend --since past
2025-01-01 for production retraining without a model_version split.

### Sneak peek best so far

v5 v4-ALL + bo_format: **AUC 0.688** (n=3,106) — +1.5pp over hltv_v1 baseline.

XGBoost LOSES to logistic at this N: tree models overfit ≤5k matches.
Promote to XGBoost only after match-details + PandaScore backfill push N
to ≥10k with rich features.

## Open questions for next session

- Wire tournament_tier into v7 sneak peek — measurable lift?
- Can we get FACEIT ELO via API to enrich tier-3/4 player ratings?
- After PandaScore backfill completes, UNION with cs2_results → v7 base
- Pinnacle Railway test — did the US IP work? Read scraper_state notes.
