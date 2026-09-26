# Estonian books beyond Optibet — alternatives survey (2026-09-26)

**#101 follow-up, research only (🤖👥 BOTH).** Optibet is parked: both VPS egresses get its 403
block page (DATA_SOURCES.md → Optibet). This extends `docs/EE_BOOK_SURVEY_2026_09_23.md`. It does
not repeat that survey: the licensee list, Tonybet (built), 20bet (= Tonybet, 203/203 prices
identical) and the 48 h coverage table all stand. What is new is below: the books the 09-23
survey left as "untested / not probed", Paf's open caveat, and a margin read for each book.

## Method, and how gently it was done

- **Licence list:** the EMTA register page says "Last updated: 18.09.2026", which is before the
  09-23 pull, so the 31 betting licensees in the 09-23 survey are still the list.
- **Probes:** every probe was a normal page load in the desktop app's browser pane from the
  operator's Mac, on an Estonian residential IP. The engine was read from the page's own network
  requests. **No VPS egress was used.** No login, no account, no terms accepted, and cookie
  banners were declined. Where a book's JSON was read, it was one or two calls from inside the
  page, the same call the page itself makes. Per site: 1–3 page loads plus 0–3 API calls. Optibet
  was not contacted.
- **Margin:** 1X2 overround (Σ1/odds − 1) on the same fixtures where possible (UEFA Nations
  League, 26–28 Sep), next to our stored Pinnacle and Tonybet quotes. These are small samples and
  point-in-time reads: take them as an order of magnitude, not a measurement.

## Findings per book

| Book (licensee) | Engine / provider | Skin of a book we have? | Public JSON odds? (how found) | Breadth | In-play | 1X2 margin read | Anti-bot seen | Req per 48 h sweep (est.) |
|---|---|---|---|---|---|---|---|---|
| **Paf** (AS PAFER) | **Kambi**, offering **`pafee`** (not `paf` as the 09-23 survey wrote) | No. Unibet.ee LEFT Kambi (KAMBI-FEED-DIVERGENCE), so Paf is now our only Kambi price | ✅ `eu.offering-api.kambicdn.com/offering/v2018/pafee/listView/football/all/all/all/matches.json?lang=en_GB&market=EE`: **528 football events (406 pre-match) with main odds in ONE call**. Found in the network tab of paf.ee/en/spordiennustus | Kambi standard: 1X2, O/U, AH, BTTS, DC, corners, cards per event (`betoffer/event/{id}`) | yes (same API, `live` views) | **Nations League 5.6–7.0%**; median across the whole board **12.3%** (p10 8.2%, p90 14.6%): tight on top leagues, very wide below | none on the Kambi CDN (Cloudflare only, no challenge) | **1** for the main board; +1 per fixture for a deep board |
| **Ninja** (Ninja Global OÜ) — `ninjacasino.com/ee/spordiennustused` (`ninjasports.ee` redirects there) | **Altenar** (widget SDK `sb2wsdk-altenar2.biahosted.com`, `integration=ninjacasino`) | No. Altenar's own trading. Prices sit on a fractional ladder (2.2858, 5.3334…), a UK-style feed | ✅ `sb2frontend-altenar2.biahosted.com/api/widget/GetEvents?…&integration=ninjacasino&countryCode=EE&champIds=…`: anonymous GET that returns events + markets + odds (normalised arrays); also `GetLiveEvents`, `GetSportInfo`. Found in the network tab after opening a league | 1X2, DC, DNB, Total, BTTS in the list call; **`mc` ≈ 312 markets per event** on the full board | yes (`GetLiveEvents`) | **7.8–8.7%, flat** (25 NL fixtures, e.g. Bulgaria–Luxembourg 2.29/2.80/3.50) | none seen | ~5–15 (per championship batches, paged via `pageCount`) |
| **Vivatbet** (licensee per EMTA list) | **1xBet platform** (`v3.traincdn.com` host app, `bff-api`, `fatman-api`) | Likely a **1xBet skin**. 1xBet itself arrives via API-Football but is EMTA-**blocked**, so Vivatbet may be a *placeable* 1xBet-derived price | ⚠️ not captured: odds arrive neither as window XHR nor as fetch (worker, websocket or server-rendered). 1xBet platforms usually expose a `LineFeed` JSON; **not confirmed here** | 1xBet-style depth (hundreds of markets) | yes | **one sample: Iceland–Estonia 1.24/5.65/13 = 6.0%** (AF 1xBet on the same match 2.9%, not time-matched) | none seen at page level | unknown until the feed call is found |
| **Olybet** (OB Holding 1 OÜ) | **BetConstruct** Swarm websocket (09-23: `site_id` 56). Today the sport page loads its book in an iframe; content API `content-api.orakulas.lt` | No (BetConstruct is not in our set) | websocket (anonymous `AuthToken`), not REST; 09-23 survey reached it | **deepest, ~73 markets per event**; richest live stats (→ #105) | yes, with stats + timeline | not measured (needs the socket) | reCAPTCHA on the page (login forms); the socket was anonymous on 09-23 | 2 socket sessions |
| **Betmaster** (BM Baltics Ltd) | **Sportradar UOF feed**, own REST: `betmaster.ee/api/feed/sr/…` (`matches/main/upcoming`, `catalog/tree`, `tournaments/blurb`); market ids = UOF, like Tonybet | Partly. Same Sportradar source as Tonybet, **different prices and margin** (USA–Peru 1.45/3.95/6.2 vs Tonybet 1.44/4.30/7.00) | ✅ anonymous JSON; each outcome carries Sportradar's **fair probability `p`** (as Tonybet does). Found in the network tab of betmaster.ee/et/sportsbook | UOF main + extended sets (`markets_set=main_extended`) | yes (`matches/main/live`) | **~10%** (USA–Peru 10.4%, Waterford–Newbridge 10.0%) vs Tonybet ~7% | none seen | per tournament; not sized (main page lists only 8 upcoming) |
| **Betsafe** (Triogames OÜ) | **Betsson in-house** B2B sportsbook widget (`d-cf.btsplayground.net`, `betting-b2b` module) | No. Own trading (Betsson) | ⚠️ odds come over **SignalR** (websocket); no REST odds call in the window; the widget renders inside a closed component (no DOM text). 09-23: plain curl got 403 from CloudFront/AWS WAF | Betsson standard, deep | yes | not measured | **AWS WAF + Group-IB fingerprinting** (`fraud.bpsgameserver.com/api/fl/idgib-…`) | browser-only collection (like Unibet) |
| **Luckybet** (SIA Luckybet.ee) | third-party sportsbook at `sport.luckybet.ee/{partner-guid}/…` with a `Tools/RequestHelper` bridge iframe, likely **Digitain** (pattern only, not confirmed) | unknown | not captured (iframe; one visit only) | — | — | — | loaded normally in a real browser (09-23: curl got Cloudflare 403) | — |
| Nubet | — | — | **site closed** ("Nubet.com – Closed") | | | | | drop |
| Chanz | — | — | **casino-only** in Estonia (no sports links) | | | | | drop |
| Optibet siblings (optibet.lv / .lt, Enlabs) | same Enlabs trading | yes (= Optibet) | — | | | | | no diversity, and not EE-licensed |
| Exchanges | — | — | **none holds an EMTA licence**; betfair.com is EMTA-blocked. Betfair Exchange is read (London exit) for sharpness only, never placeable | | | | | — |

Reference on the same Iceland–Estonia match (our DB, AF + own sweeps): Pinnacle 4.2%, Tonybet
3.7%, Epicbet 4.1%, Coolbet 2.6%; against these, Optibet 7.7%, Ninja 7.8%, Paf 5.6%, Vivatbet 6.0%.

## What each would add

A soft (wide-margin) book rarely sets the best price on its own. It is worth sweeping when its
trading is **independent**, so it is wrong in different places from the books we have, or when it
**lags**. Engine diversity matters more than brand count.

| Rank | Book | 🤖 OWN | 👥 PICKS | Cost / risk |
|---|---|---|---|---|
| **1** | **Paf** | Placeable once the owner opens an account (EE-licensed). Kambi re-prices from its own models, and its lower-league margin (~12%) makes it a mispricing venue in those leagues rather than a best-price venue | **Only Kambi price we would hold**: a distinct desk, so it adds anchor diversity. Tight on top leagues (5.6–7%), so it will sometimes set the best price | **Cheapest of any book: 1 request for the whole main board.** Today's check removes the 09-23 caveat: paf.ee's own page renders straight from `offering/v2018/pafee`, so the API **is** the site (the Unibet-Kambi failure was unibet.ee leaving Kambi; Paf has not). Re-use `workers/automation/unibet_kambi.py` with offering `pafee` |
| **2** | **Ninja (Altenar)** | Placeable after an account at Ninja Casino. Altenar's fractional-ladder feed is a distinct line, useful where it disagrees with Kambi / Sportradar books | A new engine (Altenar) in the best-price set; ~312 markets per event, so broad market coverage | Anonymous REST GET, normalised JSON, ~5–15 requests per sweep. Flat ~8% margin, so it will set the best price less often than Paf |
| 3 | **Vivatbet** | **Potentially the most interesting for OWN:** if it is 1xBet's line at a wider margin, it is a placeable venue for a book we already see (1xBet, via AF) but cannot bet (EMTA-blocked) | Little new for PICKS: 1xBet is already in our AF feed | Feed call not found (worker / socket). **Before any build: one time-matched diff of Vivatbet vs AF 1xBet on ~10 fixtures** to prove or disprove "skin" |
| 4 | **Olybet** | Placeable after an account; BetConstruct trading | Depth (~73 markets per event) and the best in-play stats (#105) | Websocket client (more code); the 09-23 plan already queues it under #105 |
| 5 | **Betmaster** | Placeable after an account; ~10% margin = soft | Low diversity: Sportradar source like Tonybet (different margin). Its Sportradar fair `p` duplicates Tonybet's `book_fair_probs` | Anonymous REST; size not measured |
| 6 | **Betsafe** | Betsson's own trading, the most valuable independent desk on this list | Distinct desk | **Browser-only** (SignalR + AWS WAF + Group-IB). A logged-out browser like Unibet's is the only honest route, and the fingerprinting makes it a live block risk. Defer |
| — | Luckybet | unknown | unknown | engine unconfirmed; one more gentle look (iframe network tab) if Paf/Ninja are done |

## Recommendation

1. **Build Paf next.** One request per sweep, site = API (verified today), and the only Kambi
   desk available to us now that Unibet has left Kambi. The acceptance step is the usual
   site-price check (a handful of fixtures), which is cheap because the page and the sweep read
   the same URL.
2. **Then Ninja (Altenar).** Anonymous REST, cheap, and a new engine family. Flat ~8% margin, so
   judge it on how often it sets the best price after two weeks, not on coverage alone.
3. **Before anything else: one 10-fixture Vivatbet-vs-1xBet diff** (browser pane, no build). If
   it is 1xBet's line, it answers an OWN question worth more than a new PICKS book.

Lessons carried over from Optibet (not optional for these builds):
- Probe through the browser pane, never with a curl loop.
- Size the sweep before the first scheduled run: Paf is 1 request and Ninja ~5–15. Keep both well
  under the budget the book's own page would generate.
- Read Estonian books only from the exit the scheduler will use, and only once the design is
  settled. The Optibet block most likely followed ~20 exploratory curl requests on 09-24.

## Not covered

- League coverage vs our fixtures per book: this was the 09-23 survey's §"Coverage vs Epicbet"
  method, and it needs a full-board pull, which is deliberately not done here. Paf's 09-23 row
  (+10 new fixtures) still stands.
- Whether each book limits winning accounts: an OWN question for the owner.
- Resale / ToS for a data product (#100).
