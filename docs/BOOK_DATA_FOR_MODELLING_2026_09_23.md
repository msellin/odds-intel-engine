# What the books can give the model that API-Football can't

**2026-09-23 · task #102 (🤖👥 BOTH + 💼 DATA) · research only.** Owner's question:
what is missing from our modelling that API-Football (AF) does not supply, and can
the Estonian books' own feeds supply it — directly, or computed from what we
collect? And what does each book's in-play feed provide?

Owner's framing, adopted: match facts (a card in the 40th minute, a corner count)
need **one** source per match, not a consensus. So this is about finding the book
with the widest stats coverage, with the others as fallback — one or two
collectors, not one per book.

## 1. The gap is AF's coverage, not our fetching

Finished matches, last 30 days (query in the #102 session):

| | matches | AF league has fixture stats | we hold them | **AF has NO stats for the league** |
|---|---|---|---|---|
| **priced by our EE books** | 9,356 | 2,576 (28%) | 2,384 | **6,780 (72%)** |
| other | 6,545 | 358 | 262 | 6,187 |

- **We collect ~93% of what AF offers.** `settlement._enrich_one_match` fetches
  `/fixtures/statistics` only where `leagues.coverage_statistics_fixtures` is true,
  and that flag is false for most of our leagues.
- Absolute stats rows are steady (~1,400–2,200/month). The share fell from 40–60%
  (2025–26 winter) to 5–16% (May 2026 on) because the league set grew 4–5×, not
  because collection broke.
- **Where a stats row exists it is complete**: corners 98.1%, yellow cards 97.8%
  (last 60 days). ⚠️ `match_stats` has TWO card columns: `yellows_home/away`
  (legacy, unwritten since 2026-06) and `yellow_cards_home/away` (the one the
  writer fills). Any query on `yellows_*` reads 0% and is wrong.
- `match_events` (goals, cards, subs, VAR) covers 68% of all finished matches and
  90% of EE-priced ones — much wider than stats.

## 2. What we can compute from odds we already collect

For the **6,972** finished EE-priced matches with no stats row (30 days), our books
already posted, pre-match:

| line | matches | what it implies |
|---|---|---|
| team totals | 5,721 (82%) | market-expected goals per team (λ home, λ away) |
| corners O/U | 3,087 (44%) | market-expected total corners |
| cards O/U | 190 (3%) | market-expected cards — too thin |

These are **pre-match expectations, not outcomes**: they cannot settle a bet or
record what happened, but they are exactly the "sum-shaped" terms
`dev/active/per-market-feature-sets-design.md` says totals heads lack. Using them
is the open "market prices as features" decision in that doc — decide there
before building.

## 3. In-play feeds — what each book exposes (probed 2026-09-23)

Full probe with payload examples: appendix below.

| Book | live stats | timeline | clock/score | transport | auth |
|---|---|---|---|---|---|
| **Olybet** (BetConstruct) | **shots on target, attacks, dangerous attacks**, corners, fouls, offsides, penalties, throw-ins, subs, yellows | **yes** (goal/card/corner/pen/sub, minute + UTC) | yes, stoppage | **websocket push** (8 diffs / 25 s) | none; works from the Hetzner IP |
| **Optibet** (Enlabs) | shots, corners, fouls, free kicks, goal kicks, offsides, pens, cards, subs, throw-ins | no | minute (⚠️ seen frozen 3+ min) | REST, whole live board in one ~2 MB call | none |
| **Tonybet = 20bet** (Sportradar UOF) | corners, cards | no | to the second | REST (+ unidentified websocket) | none |
| **Paf** (Kambi) | corners, cards | score changes | to the second | REST (+ push ws) | none |
| Coolbet / Epicbet / Unibet (ours) | none read today | no | we take AF's | — | — |
| Betsafe | — | — | — | AWS WAF 403 | blocked |

**Three findings**

1. **Olybet is the richest stats source** and the natural single source: shots on
   target and dangerous attacks are not in AF's coverage for these leagues at all,
   plus a per-event timeline. ⚠️ One game's `external_provider_info` named
   `KievParser_Bet365` / `KievParser_FonBet` — BetConstruct appears to source some
   live data by scraping other books, so **accuracy must be validated** against
   AF where both exist.
2. **Tonybet sends the feed's own fair probability with every live price**
   (Sportradar UOF `probabilities`, pairs sum to 1.000). That is a margin-free
   provider price — an independent fair-value reference with no de-vig step.
   Seen live only; check whether pre-match carries it too. **20bet is the same
   book** (203/203 identical prices) — one sweeper, not two.
3. **Live stats appeared on minor leagues** (Thai FA Cup, Bhutan) — exactly the
   tier where AF has nothing. Promising for the 72% gap, but a probe is not a
   coverage measurement.

## 4. Honest limits

- **More stats is not a proven model win.** [[#077]] (2026-09-23) fed shots +
  corners into a goals rating: α = 0, and goals beat shots+corners. Per the
  research-before-train rule, any modelling use needs its literature question and
  expected result written down first.
- Concrete, model-independent uses: **settling corners/cards markets** (which need
  counts AF lacks on 72% of our fixtures), the referee/cards features (7% populated),
  a `match_events` fallback, and **product data** (💼 DATA: match stats for
  leagues AF does not cover is itself sellable).

## 5. Recommended next step

**Pilot one stats collector (Olybet primary, Optibet fallback), measure before
scaling.** Subscribe to live games, store the final stats snapshot + timeline per
match mapped via `book_event_map`, for ~1 week. Acceptance, in order:
1. **Accuracy:** on matches where AF has stats, Olybet/Optibet corners, cards and
   shots agree with `match_stats` (state the tolerance up front).
2. **Coverage:** share of the AF-no-stats EE-priced matches the pilot actually fills.
3. Only then: backfill schema, settlement fallback, features.

---

## Appendix — in-play feed probe (verbatim)

> Note: the appendix's Unibet rows describe the Mac-era *logged-in* tab; since 2026-09-23 the pre-match reader runs logged-OUT on the VPS (UNIBET-ON-VPS, #093).

### In-play feeds of Estonian-licensed books — #102 part 2

Probed 2026-09-23 ~12:30–12:45 UTC. Read-only, anonymous; no logins, no bets, no repo changes.
Live books were probed from the VPS through the Estonian SOCKS exit (`socks5h://127.0.0.1:1081`),
except Olybet Swarm. `python-socks` is not installed in the venv, so that one ran **direct from the VPS
(Hetzner IP)**, and it worked. Coolbet, Unibet and Epicbet were documented from our code and docs only, not probed.

Live football at probe time: mostly Thai FA Cup / Thai League, Bhutan, Argentina U20, Ukraine U19, plus
e-football (Kambi/Paf lists e-sims under football).

## Comparison

| Book | Live odds | Score / clock | Stats | Timeline | Suspension | Lineups/players | Transport | Auth |
|---|---|---|---|---|---|---|---|---|
| Coolbet (ours) | sidebets `matchStatus=LIVE` (limit non-binding, ~39 groups/48 mkts) + fo/fo-line odds | **not read by us**; state comes from AF | none read | none | per-outcome `status`, missing price | none | REST poll via FlareSolverr (Imperva) | anon, but FS session; ~6 s/fixture |
| Unibet-Site (ours) | Kindred `views/contest-page` via CDP on operator tab; 15 of 77 propositions reachable | yes: score, clock, phase, cards (cached, clock seen frozen for minutes) | cards only | no | per-option timestamps | no | CDP capture of SPA XHR (DataDome) | logged-in tab |
| Epicbet (ours) | `match.getSidebets` + `activeOdds.getLiveBetByMarketIds` (21 families incl. next goal, corners, cards) | **not read**; state from AF | none read | none | `status != open` → `suspended:true` | none | REST poll (tRPC JSON) | anon; CF challenge from DC IP → residential egress |
| **Optibet** | `GET /et/events/live` = all live events + all games/odds inline (~2 MB); `/et/events/{id}` same shape | yes: `player1/2.score`, `liveTime` (minute), `liveComment`, `currentPeriod`; per-half goals | **shots, corners, fouls, free kicks, goal kicks, offsides, penalties, red/yellow, subs, throw-ins** | no | `game.active`, `odds[].isActive` | no | REST poll (`cache-control: max-age=1`); Centrifuge lib in shell, not verified for SB | none |
| **Tonybet / 20bet** | `GET /api/event/list?period=0&status_in[]=2…&relations[]=odds…` (Sportradar UOF markets, `vendorMarketId`, `specifiers`) | yes: `result.clock.matchTime "80:31"`, `stopped`, stoppage fields, per-period scores | corners, yellow, red, yellow-red (extraStatistics/playerStatistics relations exist, empty on these games) | no | market `status` (1 open / -1 suspended), outcome `active` | `players` relation (id+name), `competitors[].players` squad ids | REST poll; websocket (Centrifuge, `ws-channel` header) for push | none |
| **Olybet** (BetConstruct) | Swarm `get` game+market+event; 37 mkts at 79' | yes: `info.score1/2`, `current_game_time`, `current_game_state`, stoppage minutes, `text_info` | **shots on target, attacks, dangerous attacks, corners, fouls, offsides, penalties, throw-ins, subs, yellow cards**, per-half score | **yes: `live_events[]` (goal/card/corner/penalty/sub, side, minute, UTC ts)** | `is_blocked` on game/market/event + market removal | no | **websocket push** (diffs) | anonymous session (`site_id` 56); recaptcha flag only for login |
| **Paf** (Kambi) | `listView/.../in-play.json`, `betoffer/event/{id}.json` (only 7 offers at 79' on a Thai game) | yes: `matchClock` min+sec, `running`, `period`, `minutesLeftInPeriod`; score with `who` | corners, yellow, red only | `liveFeedUpdates` (score changes, versioned ms); `occurrences` (empty on minor games) | offer `suspended`, outcome `status` | no | REST poll; push ws at `push.aws.kambicdn.com` (426 = ws-only, not opened) | none |
| **Betsafe** (Betsson OBG) | API host `sbobgapi-bsfee-wdns.bgplayground.net/api`; SignalR `rtf.bpsgameserver.com`, realtime `realtime6-bsfee.bgplayground.net` | — | — | — | — | — | — | **403 CloudFront/AWS WAF on every API call. Stopped there.** |

## Per-book detail

### Coolbet (from code: `workers/jobs/inplay_coolbet_collector.py`, `workers/automation/coolbet_explorer.py`)
- Markets: `GET /s/sbgate/sports/fo-market/sidebets` with `matchStatus=LIVE` (limit used to be pinned at 13, which returned
  about a quarter of the board; fixed 2026-09-18), plus `POST /s/sbgate/sports/fo-match`. Odds come from `POST /s/sb-odds/odds/current/fo`
  and `/fo-line/`, returned as `{outcome_id: {value, status, ...}}`.
- Stored in `inplay_book_quotes`, book='Coolbet'. Families: 1x2, ou, btts, dc, ah2, 1x2_1h. Selection labels are canonical.
  Suspension is recorded when `status=='SUSPENDED'` or when the price is missing.
- **Minute, seconds and score come from API-Football (`af_state`), not from Coolbet.** Our code does not read any Coolbet scoreboard, stats or timeline.
- Poll every 90 s with a max of 8 fixtures, serial, on a dedicated FS session `coolbet_inplay`. The collector respects the `daemons_paused` switch and never uses `coolbet_prod`.

### Unibet-Site (from `docs/INPLAY_BOOK_COMPARISON_2026_09_14.md`, `workers/automation/unibet_odds_feed.py`)
- The Kindred `contest-page` response returns `status: InPlay`, live prices with per-option timestamps, and a scoreboard (score, clock, phase, cards).
  The only way to get it is a CDP capture on the operator's logged-in tab. The scoreboard is cached and its clock was seen frozen for minutes.
- Only 15 of `propositionCount: 77` were reachable. Totals are half-goal lines only, and there is no 2-way AH in play.
  The production feed (`unibet_odds_feed.py`) is pre-match only and is not part of `inplay_book_quotes`.
- Deprioritised in-play (wider long-side margins).

### Epicbet (from `workers/jobs/inplay_epicbet_collector.py`, `inplay_collector.py`)
- Live list: `foCategory.getByCountry {isLiveBet:true}` → `match.getFoByLeague {period:"live"}`. Board: `match.getSidebets`
  + `activeOdds.getLiveBetByMarketIds` (the pre-match sibling returns stale pre-match prices on a live game with no error).
- Families: 1x2, ou, ah2, ah3, btts, dnb, dc, early_win, next_goal, corners_total/ah/1x2/home/away/1h, cards_total, ou_1h, 1x2_1h,
  team totals, correct_score. `product: live_bet` on each price.
- Stored in `inplay_book_quotes` (book='Epicbet'), one row per (fixture, instant) with markets nested as JSONB. Pulled markets are
  written as `odds:null, suspended:true`. It feeds the paper bots `bot_inplay_slowstate_v1` and its AF control arm.
  **Minute and score come from AF `/odds/live`.** Epicbet's own feed supplies no stats or timeline to us.
- The 1x2 pull is a goal detector: 19% vs a 1.18% baseline (16x lift, n=934), which is why the Coolbet second-clock collector exists.

### Optibet (Enlabs "ensb-trading")
- `GET https://ensb-trading.optibet.ee/et/events/live`: 200, JSON, ~2.0 MB, **110 live events all sports (8 football)**, every
  game and odd inline. `cache-control: max-age=1`. `/et/events/{id}` is the same object plus `marketGroupMap`. `/et/live-overview` returns 500 (it needs
  params the SPA sends). `/et/events/{id}/timeline|statistics|games` return 404. `Accept: application/json` is required; a bare UA gets an HTML page.
- Event fields: `liveTime` (minute), `liveComment` ("Esimene poolaeg"), `player1/2.score`, `scoreboard.results.currentPeriod`,
  `extraStats`, `stats.{firstTime,secondTime,total}.goals`, `matchingEventId` (pre-match twin), `betRadarMatchId` (null on these).
- Games: `type` (match/overOrUnder/...), `handicap`, `active`, `odds[].{value,isActive}`. Markets on a 31' Bhutan game: 39 games / 138 odds, including
  15-minute-window totals, next-goal, 2nd-half markets, and correct score.
- ⚠ Two reads 8 s apart showed no price changes. `liveTime` stayed at 31 across ~3+ min on the same event. It may be minute-granular
  and lag, or the clock may be frozen. Needs checking before we trust it as a clock.
- Richest stats object:
```json
"scoreboard":{"results":{"currentPeriod":"secondTime",
 "extraStats":{"player1":{"corners":"7","fouls":"16","freeKicks":"10","goalKicks":"3","offSides":"5","penalties":"2",
   "redCards":"1","shots":"15","substitutions":"0","throwIns":"13","yellowCards":"2"},
  "player2":{"corners":"4","fouls":"10","freeKicks":"19","goalKicks":"7","offSides":"0","penalties":"0","redCards":"0",
   "shots":"4","substitutions":"0","throwIns":"12","yellowCards":"1"}},
 "stats":{"firstTime":{"player1":{"goals":"2"},"player2":{"goals":"0"}},"total":{"player1":{"goals":"3"},"player2":{"goals":"0"}}}}}
```
  (There are no shots-on-target, possession, dangerous attacks or xG fields.)

### Tonybet / 20bet (same platform)
- The live menu is `GET platform.tonybet.com/api/v4/menu/live/et` (sports, leagues and event ids; 4 football at probe time).
- The event data endpoint came from the SPA bundle (`apiUrlList.liveData = "api/event/list"`):
  `GET /api/event/list?lang=et&period=0&sportId_eq=1&status_in[]=2&status_in[]=1&limit=20&relations[]=odds&relations[]=result&relations[]=statistics&relations[]=players&relations[]=competitors&relations[]=additionalInfo&relations[]=withMarketsCount`
  Status enum: 0 line, 1 stopped, 2 online, 3 dead, 4 ended. Other relations: extraStatistics, playerStatistics, broadcasts, tips, variants.
  `id_eq`/`id_in` filters are rejected (400). `/api/v2/event/{id}` returns event metadata without odds.
- Items carry `vendorEventId: "sr:match:…"`, so this is **Sportradar UOF**. `additionalInfo` exposes Sportradar coverage metadata
  (`coverage_source: venue`, `latency_category: Low`, `auto_traded`).
- `result`: `{clock:{matchTime:"80:31",stopped:false,stoppageTime,…}, matchStatusId, periods:[{number,team1Score,team2Score}], team1Score, team2Score}`.
- `statistics`: corners, yellowCards, redCards, yellowRedCards, greenCards (home/away). extraStatistics and playerStatistics came back empty on these low-tier games.
- Odds: ~29 markets per event in the list (withMarketsCount says 140–307 exist, so the full board comes from another call or the websocket).
  Markets use UOF ids plus specifiers (`vendorMarketId 18, "total=4"`). Status is 1 or -1. **Every live outcome carries `probabilities`**, which is the feed's own
  fair probability (0.755 + 0.245 = 1.000). Example: Over 4 @1.25, p=0.755. That gives the operator margin directly, with no de-vig step.
- **20bet = Tonybet**. Same event ids and identical prices and probabilities on all 203 common prices across 3 live games. One sweeper
  covers both; there is no second book here.
- Push: `api/v2/configurations` → `websockets.useSockets: true`. The bundle ships Centrifuge 5.3.5 and CORS allows `ws-channel` and `ws-session-uuid`
  headers. The endpoint URL was not identified.

### Olybet (BetConstruct Swarm)
- `nw.olybet.ee/conf.json` → `site_id 56`, `swarm.socketUrl wss://eu-swarm-newm.betconstruct.com/`. `request_session` (source 42)
  was accepted anonymously from the VPS DC IP. The session reply has `recaptcha_enabled: true` (it applies to login, not reads).
- `get` for `sport.alias=Soccer, game.type=1` returned **10 live soccer games**. One `subscribe:true` game query sent **8 pushed diffs in 25 s**
  (price-only deltas, plus info/text_info/stats updates).
- Game fields: `info` (score1/2, current_game_state `set2`, current_game_time, stoppage_firsthalf/secondhalf, add_minutes),
  `text_info` "2 : 0, (0:0), (2:0) 79`", `stats`, `live_events`, `last_event`, `is_blocked`, `markets_count`, `external_provider_info`.
- Markets had 37 at 79' (corners markets heavy: totals, handicap, team corners, 81-90 corner window, corner correct score). Main 1x2 was
  absent by then; `RestOfTheMatchWinner` was present. Each event has `price`, `base` (line), `type`.
- Timeline `live_events[]`: `{type_id, side, current_minute, period_sequence, time (unix), time_utc}`. The type ids decoded against the
  stats totals are: 1 goal, 3 yellow card, 4 corner, 5 penalty, 6 substitution.
- `external_provider_info` on the richest game listed `"10":"KievParser_Bet365","6":"KievParser_FonBet"`. BetConstruct's live
  data source for this game appears to be scraped from Bet365 and FonBet.
- Richest stats object (Khon Kaen Utd 2-0 Navy, 79'):
```json
"stats":{"shot_on_target":{"team1_value":6,"team2_value":1},"attack":{"team1_value":129,"team2_value":64},
 "dangerous_attack":{"team1_value":43,"team2_value":18},"corner":{"team1_value":7,"team2_value":3},
 "foul":{"team1_value":12,"team2_value":8},"offside":{"team1_value":3,"team2_value":0},"penalty":{"team1_value":1,"team2_value":0},
 "yellow_card":{"team1_value":1,"team2_value":1},"substitution":{"team1_value":3,"team2_value":3},"throw_in":{"team1_value":22,"team2_value":8},
 "passes":{"team1_value":null,"team2_value":null},"score_set1":{"team1_value":0,"team2_value":0},"score_set2":{"team1_value":2,"team2_value":0}},
"live_events":[{"type_id":"1","side":"1","current_minute":"46'","period_sequence":3,"time":1790165165}, ...]
```
- No lineups or player data in the game object. `is_stat_available` points at a separate stats product that was not probed.

### Paf (Kambi offering `paf`)
- `listView/football/all/all/all/in-play.json?lang=en_GB&market=EE` → 10 events (2 real, 8 e-football sims), each with `liveData` + main betOffers.
  `event/live/open.json` → 78 live events all sports. `betoffer/event/{id}.json` → full live board. `event/livedata/{id}.json` and
  `event/{id}/livedata.json` both work.
- `liveData`: `matchClock {minute, second, running, period, periodId, minutesLeftInPeriod, secondsLeftInMinute, version}`,
  `score {home, away, who, version}`, `statistics.football {yellowCards, redCards, corners}`, `liveStatistics[]` (occurrenceTypeId counts),
  `liveFeedUpdates[]` (score changes with ms `version`), `occurrences[]` (empty on these games), `tickers[]`.
- Board is thin in play: 7 betOffers / 17 outcomes on a 79' Thai FA Cup game (totals, team totals, 3-way hcp, next goal).
  Outcome prices are milli-odds with `changedDate` and `status`, and offers carry `suspended`.
- Push: `push.aws.kambicdn.com/socket.io` answers 426 (websocket-only). It was not opened.
- Example:
```json
{"matchClock":{"minute":79,"second":42,"period":"2nd half","running":true,"periodId":"SECOND_HALF"},
 "score":{"home":"2","away":"0","who":"HOME"},
 "statistics":{"football":{"home":{"yellowCards":1,"redCards":0,"corners":7},"away":{"yellowCards":1,"redCards":0,"corners":3}}},
 "liveFeedUpdates":[{"type":"SCORE","score":{"home":"1","away":"0","version":1790165166322}},{"type":"SCORE","score":{"home":"2","away":"0","version":1790167122445}}]}
```

### Betsafe (Betsson OBG)
- The SPA HTML (served even on a 404 route) exposes `sportsbookCore.cloudFrontHttpClient.baseUri = https://sbobgapi-bsfee-wdns.bgplayground.net/api`,
  SignalR hubs `rtf.bpsgameserver.com/hub/{customer,group}-transmit`, realtime host `realtime6-bsfee.bgplayground.net`, and AWS WAF
  threat-detection script `…edge.sdk.awswaf.com/…/challenge.compact.js`.
- Three API calls (`/api/sb/v1/widgets/events-table/v2`, the same path on `www.betsafe.ee/api`, and `/api/sb/v2/categories`) all returned **403 CloudFront**.
  Stopped per the no-WAF-bypass rule. Reaching it needs a real browser session (like Unibet); not viable as plain REST.

## Rate / anti-bot notes
- Optibet, Tonybet/20bet and Kambi all answered the EE exit with no challenge. Optibet sits behind Cloudflare (`__cf_bm` cookie set) but
  nothing was challenged.
- **CHANGED 2026-09-26:** the EE exit (and the Hetzner IP) now get Optibet's 403 block page on `ensb-trading.optibet.ee` — see DATA_SOURCES.md → Optibet. An Optibet live-stats fallback for #105 would hit the same block.
- Swarm accepted the Hetzner DC IP directly.
- Betsafe: AWS WAF blocks at the edge.
- Total volume: Optibet ~15 API calls, Tonybet ~20 API + ~90 static CDN chunks (bundle reading), Olybet 2 ws sessions, Paf 6, Betsafe 4.
