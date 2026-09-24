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
- **HLTV `/stats/teams/ftu`** — user suggested 2026-06-09. Likely Flash/Trade/Utility
  team stats (first-3-utility usage, trade-kill rates). Verify with full cookies.
  Same scraper pattern as pistols — high probability of +0.005-0.010 AUC.

---

# Deep Research — CS2 Game Dynamics & Signals We're Missing (2026-06-09)

Research-agent findings from the "what else could we predict on" pass.
Ranked TOP 5 signals to integrate next, by (predictive lift × accessibility / effort):

## #1 — Pistol Round Win Rate (rolling, per-team, per-map)

**Mechanism:** Winning both pistols → ~70-80% match-win in pro Bo1 play.
Pistol → $3,250 anti-eco → bonus round → 3-0 start → ~$15k economy lead.
Round 1 outcome alone changes match-win prob by 12-18pp.

**Source:** `https://www.hltv.org/stats/teams/pistols?startDate=&endDate=&teamName=...`
**Effort:** 1 day. Nightly scraper → `cs2_team_pistol_stats`.
**Estimated lift:** **+0.010 to +0.015 AUC** (partly orthogonal to rating).

## #2 — Starting-Side Signal + Map-Specific Side Bias

**Mechanism:** Map side bias is real (Nuke 57% CT, Anubis 57% T, Overpass +12.8pp
CT edge). Which side a team **starts on** (knife round result) shifts win prob
4-8% on lopsided maps. We capture CT/T halftime rounds but not "which side
the team chose at knife."

**Source:** PandaScore `/csgo/games/{id}` returns side-start; HLTV match
scoreboard JSON also exposes it.
**Effort:** 1 day. Add `starting_side` column to `cs2_hltv_match_maps`;
parser extension. Interaction term with map_side_bias in model.
**Estimated lift:** **+0.008 to +0.012 AUC** (concentrated on Nuke/Anubis/Overpass).

## #3 — Veto-Derived Predicted Decider + "Forced Off Permaban"

**Mechanism:** Veto is documented as ~50% of pre-match edge. Permabans are
extremely stable (top teams ban same map 70%+ of the time). The decider map
(left over after bans) is highly predictable from teams' veto histories.
"Forced off permaban" — when a team's preferred map gets banned out and they
must play their 4th-favorite — historically -8 to -12pp win prob hit.

**Source:** Already in our `cs2_hltv_match_veto` table (937+ rows).
**Effort:** 1 day. Pure SQL/Python on existing data. Add:
- `rolling_permaban_freq` per team (last 20 vetoes)
- `predicted_decider_winrate_diff` (team-specific decider map win rate)
- `forced_off_permaban_flag`
**Estimated lift:** **+0.006 to +0.010 AUC**.

## #4 — Demo-Derived Stats (Leetify-style: trade-kill %, utility, multi-kills)

**Mechanism:** Demo-parsed stats are mostly **orthogonal** to scoreboard. Top
predictors:
- Trade-kill % (high-trade teams have lower variance)
- Utility damage per round (correlates with map control)
- Multi-kill rate (3K+, 4K, ACE)
- Time alive (lurker proxy)
- Opening duel split by side

**Source:** HLTV demo downloads (free .dem.gz from match pages) + `demoinfocs-golang`
parser. ~30s per demo on commodity hardware. Or Leetify Pro API (paid).
**Effort:** 3-5 days. Build Go/Python demo parser worker. Store per-team
per-match aggregates.
**Estimated lift:** **+0.010 to +0.018 AUC** — highest single feature class,
because it's mostly new info.

## #5 — Roster-Change × Role Interaction

**Mechanism:** IGL absence is documented as -10 to -20pp win prob (catastrophic
for team coordination). Star fragger absence is -5 to -10pp. AWPer absence
varies by map (more impact on long-sightline maps like Mirage, Dust2).
"Roster change in last 30 days" alone is too coarse — needs to know **which role** changed.

**Source:** HLTV team page roster history + Liquipedia role tags. ~50 active
rosters × manual role mapping = 2-hour spreadsheet.
**Effort:** 2 days. Extend `cs2_hltv_team_rosters` with `role` column; add
interaction term in stacking model.
**Estimated lift:** **+0.007 to +0.012 AUC** (conditional — high on the ~5%
of matches with recent roster changes; lower overall).

## Other interesting findings (not in top 5)

| Signal | Why interesting | Why not top 5 |
|--------|-----------------|---------------|
| Anti-eco efficiency (% of rounds won when opponent on eco) | Differentiates teams 65-85% | Needs demo or paid PandaScore |
| Force-buy conversion rate | Some teams are notorious force-buyers | Demo parsing needed |
| LAN vs Online performance gap | Real for some teams (-10-15% online for NA orgs) | Captured indirectly by recent form |
| Time-zone fatigue | EU teams playing NA-time finals drop measurably | Hard to derive — needs venue + schedule |
| Player form decay (10-map rolling vs 3-month) | Catches slumps + hot streaks | Lower priority; partly captured by our `days_since_match` |
| CS2 vs CSGO era flag | Post-major-patch effects | Already implicit in `--since 2025-06-01` window |
| Smoke meta (volumetric) impact | Teams strong on utility gained edge | Captured by recent form post-2024 |
| Pistol → bonus round chain | Documented 70%+ conversion | Captured by #1 pistol stat |

## Map side bias reference (2024-2025 pro data)

| Map | CT% | T% | Edge |
|-----|-----|-----|------|
| Nuke | 57% | 43% | +14 CT |
| Overpass | 56.4% | 43.6% | +12.8 CT |
| Mirage | 54% | 46% | +8 CT |
| Train (re-added 2025) | ~54% | ~46% | +8 CT (trending) |
| Inferno (post-rework) | 51% | 49% | +2 CT |
| Ancient | 50.8% | 49.2% | +1.6 CT (most balanced) |
| Dust2 | ~50% | ~50% | balanced |
| Vertigo | 47.8% | 52.2% | +4.4 T |
| Anubis | 43% | 57% | +14 T (most T-sided) |

## Combined lift estimate

If all top-5 land cleanly: baseline 0.673 → ~0.715-0.730 AUC.
Net realistic after stacking redundancy: **+0.025-0.035 over current best 0.688**.
Target landing zone: **AUC 0.71-0.73** with all 5 features integrated.

## Sources cited

- Abios — CS2 map win rates
- Bitskins — CS2 map sides 2025
- Dust2.us — FACEIT side bias data
- HLTV — `/stats/teams/pistols`, `/stats/players?csVersion=CS2`, Rating 3.0
- ProSettings, Boosteria — CS2 economy guides
- Rush B Media — Anatomy of map vetoes
- Leetify — Stats glossary + Leetify Rating
- SCOPE.GG blog — First duel predictive analysis
- PandaScore — CS2 docs
- CSspot — LAN vs Online performance gap research
- Pinnacle — CS2 tournament tactics analysis

