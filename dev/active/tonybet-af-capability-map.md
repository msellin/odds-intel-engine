Parent: PRIORITY_QUEUE.md #101 EE-SWEEPERS-2026-09-23

# Tonybet (and Epicbet) vs API-Football: capability map

> Step 0 of #101. Research and probing only: no repo code, no DB writes.
> Probed 2026-09-23, 13:00–14:30 UTC, from the VPS through the Estonian SOCKS exit (`socks5h://127.0.0.1:1081`).
> **Request budget used:**
> - **Tonybet platform API:** 37 requests, at least 0.8 s apart.
> - **Epicbet core-proxy:** about 20 requests.
> - **Sportradar:** 2 CDN fetches plus 3 feed probes.
> - **Tonybet's CloudFront JS bundle:** about 1,110 static-file fetches, needed to find the event-detail query. These are cached CDN assets, not the platform API. They are listed here because they go over the spirit of the 150-request cap. A sweeper never needs to fetch them.
>
> Temp files on the VPS were deleted. **[UNVERIFIED]** marks anything inferred rather than observed.

## TL;DR

- **Tonybet covers the fixture spine, results with half-time scores, live score and clock, and live corners and cards.** Its odds are deep: 114–138 market types per match on the full board, with a margin-free Sportradar probability on almost every outcome.
- **Tonybet covers none of AF's enrichment:** no standings, H2H, injuries, lineups, events timeline, shots, possession, xG or player stats.
- **Tonybet's results window is short.** The `result` relation drops out about 1–2 days after kickoff. Post-match `statistics` are mostly gone at full time. The sweeper must capture them while the match is live or within 24 h.
- **Epicbet fills part of the enrichment gap for top leagues:**
  - standings
  - each team's last 15 matches with goals, corners, cards and shots on target
  - top scorers
  - an AI-written preview
  - lineups over socket.io
- **Neither book** gives an events timeline, shots, possession, xG or player ratings. **Neither is a Pinnacle replacement.**
- **Every Tonybet event carries a Sportradar match ID** (`sr:match:N`, 500/500 in the 48 h sample). Optibet carries the same ID family as `betRadarMatchId`. That gives a cross-book fixture key that needs no fuzzy matching.

## Tonybet API facts established in this probe

| Fact | Evidence |
|---|---|
| **One endpoint serves both the list and the detail view:** `GET /api/event/list` | SPA chunks call only `/api/event/list?…`. The event page calls it with `eventId_eq=<id>&main=0`. |
| **Valid `relations[]`** (from the API's own validation error) | competitors, league, result, odds, players, sportCategories, variants, withMarketsCount, broadcasts, rounds, statistics, extraStatistics, playerStatistics, additionalInfo, sport, tips, cashoutMarkets, pinnedEvents, recentEvents. **`team` is invalid.** |
| **Valid filters seen in the bundle** | `sportId_eq`, `status_in[]`, `leagueId_in[]`, `leagueId_eq`, `eventId_in[]`, `eventId_eq`, `competitorId_in[]`, `competitor1Id_neq`, `competitor2Id_neq`, `isTop_eq`, `isTopLive_eq`, `oddsExists_eq`, `time_gte`, `time_lte`, `main` (1 = main markets only, 0 = full board), `period`, `limit`, `page`. `id_eq` returns 400 "Invalid filter". |
| **Status enum** | 0 line (pre-match), 1 stopped, 2 live, 4 ended, 5 settled/closed (seen, meaning inferred), 6 seen in unfiltered lists. **Ended matches need a `time_gte`/`time_lte` window.** Without one, `status_in[]=4` timed out after 30 s. |
| **Main board vs full board** | The main board (`main` omitted or `1`) returns 13 market types / about 52 lines per event. The full board (`eventId_eq` + `main=0`) returned: UCL 3 weeks out, 114 types / 223 lines / 854 outcomes; MLS next day, 138 types / 495 lines / 4,027 outcomes; live U20 match, 115 types / 228 lines. |
| **Volume (football)** | 585 events with status 0 kicking off in the next 48 h (130 leagues). About 300 in the next 24 h. The line menu shows 1,276 football events plus 108 outrights. Ended: about 280 per 24 h (status 4 + 5). |
| **Times are UTC** | Tonybet `2026-09-23 16:45:00` matches Epicbet `16:45:00+00` for Leuven W v Roma W. |
| **Status catalogue** | `/api/match-statuses/get-all/en`: 254 statuses, including 60 Postponed, 70 Cancelled, 80 Interrupted, 90 Abandoned, 100 Ended, 110 AET, 120 AP. |
| **Market catalogue** | `/api/market-descriptions/get-all-markets/en`: 1.3 MB, all sports, maps market id to name and specifier template. Fetch it once a day. |

## The capability table

Legend: ✅ = observed, 🟡 = partial or conditional, ❌ = not found, **[U]** = unverified.
"When" means **P** pre-match, **L** live, **F** post-match.

| # | AF data type / field group we store | AF (what, coverage) | Tonybet (endpoint + field; coverage observed; when) | Epicbet (endpoint + field; coverage; when) | Gap / notes |
|---|---|---|---|---|---|
| 1 | **Fixture spine**: `matches` (date, teams, league, season, round, AF ids), `leagues`, `teams` | `/fixtures?date`, `/leagues`; about 609 leagues/30 d; our ID spine | ✅ `event/list`: `id`, `time` (UTC), `competitor1Id/2Id`, `leagueId`, `roundId`, `roundType`/`cupLevel`, `sportCategoryId`. `competitors` relation: name, abbreviation, `countryId`, **`gender`, `ageGroup`** (U20…), logo. `league` relation: name, country code, `hasOutrights`. **`vendorEventId = sr:match:N` on 500/500.** 130 leagues/48 h. P/L/F | ✅ `match.getFoByLeague` / `getSidebets`: id, `startDate`, `homeTeamId/awayTeamId`, names, `leagueId`, `leagueName`, `regionName`, `type`. `fixtureId` (a Genius Sports id, [U]) appears only on some matches. P | Both books are narrower than AF (Tonybet about 300 football events/day). **Gender and age-group flags** make Tonybet easier to match than Coolbet. **The sr id is a better join key than names.** AF has no season field equivalent; `rounds` gives a round name only. |
| 2 | **Status and schedule changes**: `matches.status`, postponements, `date_disputed_*` | Fixture status codes | ✅ `result.matchStatusId` (254-status catalogue incl. postponed/cancelled/abandoned/AET/AP) and event `status`. P/L/F | 🟡 match drops off the board; live phase via socket `match-summary-{id}` [U] | Tonybet is good enough to flag postponements for fixtures it carries. |
| 3 | **Results / settlement**: `score_home/away`, `result` | `/fixtures?status=FT`; ESPN as backup | ✅ `result.team1Score/team2Score`, `matchStatusId=100` (Ended). **99/100** events that kicked off 1 day earlier had it. **Window is short:** 0/142 for status 4 two days back, 1/54 for status 5. Query: `status_in[]=4&status_in[]=5&time_gte&time_lte`. F | ❌ No REST results endpoint found. `getTeamStats` returns a team's past results with a lag (see #13). | Tonybet works as a **third settlement source** if it is polled within 24 h of full time. It cannot backfill. |
| 4 | **HT / period scores**: `ht_score_*`, `h2_score_*` | `/fixtures?ids` score.halftime | ✅ `result.periods[]`: per-period `team1Score/team2Score`, `type:"regular_period"`, `matchStatusCode` 6/7. 95/99 had 2 periods, plus extra-time periods where played. F | ❌ | Tonybet periods give **HT and 2H directly**. Better than AF, which needs a second call. |
| 5 | **Live score / status / clock**: `live_match_snapshots` (minute, score) | `/fixtures?live` | ✅ `status_in[]=2&status_in[]=1` + `result`: `clock.matchTime` ("32:27"), `stoppageTime`, `remainingTime`, score, current period. 11 live at 13:30 UTC on a Wednesday. L | 🟡 socket.io `/s/core-proxy/public/sport-base/socket.io` channels `match-{id}`, `match-summary-{id}` (from bundle, [U] not connected) | Tonybet via REST polling, which is simple. |
| 6 | **Pre-match odds, core markets**: `odds_snapshots` 1X2, O/U, AH, BTTS, DC, DNB | `/odds?date`, 13 books | ✅ Main board: 1X2, totals (incl. quarter lines e.g. `total=3.75`), AH (`hcp=-0.75`), BTTS, DC, DNB, correct score, winning margin, odd/even, 1H market. Full board adds much more. P/L | ✅ Already swept (`epicbet_explorer.py`) | Tonybet is **one book**. It adds breadth for 🤖/👥, but nothing sharp. |
| 7 | **Odds, stats markets** (corners, cards, 1H, team totals) | AF books, patchy | 🟡 Full board only (`main=0`). MLS: **18 corner market types** (incl. 1H corners, corner handicap, range, last corner), goalscorer ×7. **No card/booking markets** in the 3 events examined. UCL 3 weeks out had no corners yet; they appear later [U timing]. P/L | ✅ `getSidebets` (corners 13/30, cards 5/30 per module notes) | Cards are thin on Tonybet [U: only 3 events sampled]. |
| 8 | **Pinnacle (sharp anchor)** | Via AF | ❌ | ❌ | Unchanged. Pinnacle guest API remains the fallback. |
| 9 | **Closing line** (`run_closing_snap`) | `/odds?fixture` near KO | ✅ Poll the full board at T-10 min. Each outcome also carries `probabilities`. | ✅ | We build it ourselves. |
| 10 | **Live odds** (gated off since 2026-08-21) | `/odds/live` | ✅ Same endpoint with status 2. 228 lines on a live U20 match. `additionalInfo.extended_live_markets_offered`. L | ✅ `activeOdds.getLiveBetByMarketIds` | Available if in-play ever returns. |
| 11 | **Predictions** (`predictions`, AF model) | `/predictions` | 🟡 Not a model, but `odds[].outcomes[].probabilities` is Sportradar's margin-free probability: **854/854** (UCL), **3,831/4,027** (MLS), 152/152 per main board. `tips` relation: 0/500 events. P/L | 🟡 `match.getAiInfoById` → text sections "Recent Form", "Head-to-Head History", "Key Players", "Conclusion" + `lastUpdated`. `hasAiInformation` on 34/36 top-league matches. P | Tonybet probabilities are a **better "prediction" than AF's** for benchmarking. Epicbet AI text is 👥 PICKS copy material (licensing [U]). |
| 12 | **Standings** (`league_standings`) | `/standings` | ❌ No endpoint in the bundle or the relations list | ✅ `match.getLeagueStandings {leagueId,language,country}` → rank, points, W/D/L, GF/GA, played, `lastSix` form. `hasStandings` 29/36 in top leagues, and it is also a flag on categories. P | Epicbet only, top leagues. We can also compute standings from results. |
| 13 | **Team season stats** (`team_season_stats`) | `/teams/statistics` | ❌ | 🟡 `match.getTeamStats {matchId,language}` → per team, the **last 15 matches**: result, goals scored/conceded, **cards** (team/rival/total), **corners** (taken/conceded/total), **shots on target** (team/rival), home/away, rival id+name, provider `matchId`. `hasStats` 29/36 in top leagues (about 17% of all matches per #101 note). P | This is **historical per-match corners, cards and SOT**, the one real stats find. It can back-fill `match_stats` corners/cards/SOT for top leagues via rival+date matching [U mapping]. |
| 14 | **H2H** (`h2h_raw`, `match_signals`) | `/fixtures/headtohead` | ❌ (`recentEvents` relation was empty on all 4 full-board events) | 🟡 AI text only; `getTeamStats` rivals are not H2H | Compute from our own results. |
| 15 | **Injuries / sidelined** | `/injuries`, `/sidelined` | ❌ | ❌ (AI "Key Players" text mentions form, not absences) | No replacement. Low value (0–2.5% coverage in the model). |
| 16 | **Lineups / formations / coaches** (`lineups_*`, `formation_*`, `coach_*`) | `/fixtures/lineups`, `/coachs` | 🟡 **Squads only:** `competitors[].players` (player id list) + `players` relation (id→"Surname, First"). 305 names for 5 fixtures. Not a starting XI, no formation, no coach. P | 🟡 socket.io channel `lineup-{matchId}-{teamId}` when `isLineupEnabled` (10/36 top-league matches, likely only near KO) [U: payload not seen] | Lineups remain an AF-only (or Sportradar-widget) item. |
| 17 | **Events timeline** (`match_events`: goal/card/sub/VAR/penalty with minute, player, assist) | `/fixtures/events` | ❌ No timeline. Only counters (#18). | ❌ via REST. socket `match-summary` carries a `phase` [U: detail] | **Biggest gap.** No minute, scorer, assist, subs or VAR from either book. |
| 18 | **Fixture statistics, full match** (`match_stats`: corners, yellows/reds, shots, SOT, possession, fouls, offsides, saves, passes, xG…) | `/fixtures/statistics` | 🟡 `statistics` relation: **corners, yellowCards, redCards, yellowRedCards** (+`greenCards` null), home/away. **Live: 8/8** live events had it. **Post-match: 1/100** ended the day before and 5/142 two days before, so it is purged at full time. `extraStatistics`/`playerStatistics` were empty on every football event checked (≈10 incl. live), and `statistic_exists=0` on 500/500 [U: probably other sports]. L | 🟡 Historical only via `getTeamStats` (corners, cards, SOT). Live socket `match-team-stats-{match}-{team}`, `smart-stats-{id}` [U] | Tonybet covers **corners and cards only**, and only if we snapshot the live board at or just before FT. No shots, possession, fouls, offsides, saves, passes or xG. |
| 19 | **HT stat splits** (`*_ht` columns) | `/fixtures/statistics?half=true` | 🟡 Derivable: snapshot `statistics` when `matchStatusId=31` (Halftime) | ❌ | Corners/cards HT only. |
| 20 | **Player match stats** (`match_player_stats`) | `/fixtures/players` | ❌ (`playerStatistics` empty) | ❌ (`getTopGoalScorers`: season goals per player for the two teams' league, 5 per side) | No replacement. Low value (7–10% coverage). |
| 21 | **Venue / referee** | `/venues`, fixture.referee | 🟡 `additionalInfo.neutral_ground` only. No venue, no referee. | ❌ | Referee is AF-only. |
| 22 | **Transfers / coaches** | `/transfers`, `/coachs` | ❌ | ❌ | Droppable per §(a). |

## AND MORE: what Tonybet and Epicbet expose that AF does not

1. **Margin-free probabilities on every outcome** (Tonybet `probabilities`). This is Sportradar's fair probability, so it is effectively a second "fair price" alongside de-vigged Pinnacle. Coverage was 95–100% of outcomes. It lets us:
   - benchmark our model against a professional trading feed per market, including corners and goalscorers, which Pinnacle doesn't price;
   - estimate Tonybet's own margin per market exactly (odds × probability).

   **Caveat:** it is the *supplier's* line before Tonybet's own adjustments, not a sharp market. Test it like any anchor (`docs/ANCHOR_IS_NOT_SHARP_2026_09_14.md`) before trusting it.
2. **Sportradar match IDs** (`sr:match:N`) on 100% of events. Optibet also carries `betRadarMatchId`. This gives a **provider-level cross-book fixture key**, the missing piece for the canonical fixture table in §(a) of the research doc. Epicbet's `fixtureId` looks like a Genius Sports id [U], which is a second provider key family.
3. **Market breadth:** 114–138 market types per match on the full board, versus about 13 on the main board. Examples: 10-minute 1X2, goal-time intervals, winning margin, exact goals, correct score ladders, corner ranges, last corner, goalscorer combos (anytime + 1X2, anytime + correct score). Both books price **quarter lines**, which Coolbet doesn't.
4. **Player markets:** first, last and anytime goalscorer plus combos. Outcomes carry `player`/`playerVendorId`. Epicbet has `marketType=players` (about 2,000 extra groups per top match, per module notes).
5. **Bet-builder availability** (`hasBetBuilder` 19/500 on Tonybet; Epicbet `isBetBuilderAvailable`, provider `p4`), plus `cashoutMarkets` and `oddsBooster` flags.
6. **Data-quality metadata** in `additionalInfo`: `coverage_source` (venue 212 / tv 65 / none 60), `latency_category` (Low/Moderate/NotSet), `auto_traded`, `period_length`, `neutral_ground`, `extended_live_markets_offered`. This tells us whether live stats come from a scout at the venue or from TV. Nothing in AF says this.
7. **Competitor metadata:** `gender` and `ageGroup` on every team, plus country id and logo. This cuts the women's/youth mis-match class that fuzzy name matching keeps producing.
8. **Squad lists** per team, as Sportradar player ids with names.
9. **Epicbet extras:**
   - AI match previews (form, H2H, key players, conclusion);
   - last-15 match logs with corners, cards and SOT;
   - top scorers per team;
   - `match.getLeagueBracket` (cup trees);
   - `league.getStats`;
   - `hotStatsByFoCategory` (popularity);
   - `market-ticket-count-{id}` socket channel, which counts bets per market and is a **public-money signal** [U: payload].

## The Sportradar LMT widget

Every Tonybet event has
`liveMatchTracker = https://ws-cdn001.akamaized.net/sportradar/en/standalone/match.lmtPlus#matchId=<N>`.

- **Standalone page:** a 4 KB HTML shell. It loads `https://ws-cdn001.akamaized.net/sportradar/widgetloader` (global `BETSIR`) and calls `BETSIR('addWidget', '.sr-widget', 'match.lmtPlus', {matchId…})`. In the Tonybet SPA itself, chunk `34824.js` calls `window.SIR("addWidget", el, "match.lmtPlus", {layout:"single", detailedScoreboard:"all", matchId})` using the `sr:match` number. Tonybet's own widgetloader URL (with its client alias) comes from runtime config and is not in the static bundle.
- **Data feed:** the widget reads Sportradar "gismo" JSON feeds, with the pattern `…fn.sportradar.com/<client>/<lang>/Etc:UTC/gismo/<feed>/<matchId>`. Two probes for `match_info` and `match_timeline` on `lmt.fn.sportradar.com/common/…`, plus one on `widgets.fn.sportradar.com/sportradar/…`, all returned **HTTP 200 with `{"code":403,"message":"Unauthorized feed"}`**. The feeds are gated per licensed client. **I stopped there and did not look for a client alias.**
- **What lmtPlus shows** (Sportradar product behaviour, [U] not observed here): pitch tracker, timeline of goals/cards/subs, match stats (shots, possession, attacks), lineups, H2H and standings. That is most of rows 14, 16, 17 and 18.
- **⚠️ Legal flag.** This is **Sportradar-licensed data served to Tonybet's widget under Tonybet's licence.** Reading it directly would mean using a third party's licensed feed outside the licence, possibly by borrowing an operator's client alias. That is a ToS and database-right question and belongs on the lawyer list in `docs/ODDS_DATA_PRODUCT_RESEARCH_2026_09_23.md` §c.4 as a new question: *"May we read Sportradar LMT/gismo feeds exposed through a bookmaker's public widget?"* **Do not build around it.** The same applies to Epicbet's Genius Sports `bgTracker` widget.

## Recommendation: Tonybet sweeper call plan

Our demand is about 250 AF fixtures per 48 h, of which Tonybet matches about 156 (survey). Tonybet's own volume is about 300 football events per day, about 585 per 48 h.

| Phase | Call | Relations / params | Cadence | Requests/day | Covers rows |
|---|---|---|---|---|---|
| **Daily catalogue** | `/api/market-descriptions/get-all-markets/en`, `/api/match-statuses/get-all/en` | — | 1×/day | 2 | decoding |
| **Pre-match board** | `event/list?sportId_eq=1&status_in[]=0&period=0&time_gte=now&time_lte=now+48h&limit=100&page=N` | `odds, competitors, league, rounds, additionalInfo, withMarketsCount, players` | every 30 min (6 pages) | about 290 | 1, 2, 6, 9, 11, 16 (squads) + sr id for matching |
| **Deep board** (matched fixtures only) | `event/list?eventId_eq=<id>&main=0` | `odds, result, additionalInfo, variants, withMarketsCount` | at T-24 h, T-3 h, T-30 min, T-5 min (closing) | 4 × about 78/day ≈ 310 | 7, 9, 11, player markets |
| **Live** | `event/list?sportId_eq=1&status_in[]=2&status_in[]=1&limit=100` | `result, statistics, additionalInfo` (add `odds` only if in-play returns) | every 60 s (usually 1 page) | about 1,440 (720 at 120 s) | 5, 18 (corners/cards), 19 (snapshot at `matchStatusId=31`), last snapshot before `status` → 4 = FT corners/cards |
| **Post-match** | `event/list?sportId_eq=1&status_in[]=4&status_in[]=5&time_gte=now-36h&time_lte=now&limit=100&page=N` | `result, statistics` | every 2 h (about 3 pages) | about 36 | 3, 4 (must run within 24 h: results purge after about 1–2 days) |
| **Total** | | | | **about 2,100/day** (about 1,400 at 120 s live polling) | |

Notes for the build:
- **Payload size:** full-board responses are 0.2–0.8 MB each, so the deep board is about 250 MB/day. Keep `players`/`variants` off the deep board unless we decode player markets.
- **Key on `vendorEventId`.** Store it in `book_event_map`, add a nullable `sr_match_id` for future Optibet joins, and use `gender`/`ageGroup` as hard match filters.
- **Enrichment:** Tonybet does not replace AF for standings, H2H, injuries, lineups, timeline, shots, xG or player stats. If we want any of these without AF, Epicbet covers `getLeagueStandings`, `getTeamStats` (1 call per matched top-league fixture, about 30–60/day) and `getAiInfoById`. **Rows 15, 17 and 20 have no book-side source at all.**
- **Open checks [U]:**
  - Do card markets appear on Tonybet closer to kickoff?
  - What is in the Epicbet socket payloads (lineups, match-summary, team-stats, market-ticket-count)?
  - Does status 5 mean "settled"?
  - Are `extraStatistics`/`playerStatistics` ever populated for football?
