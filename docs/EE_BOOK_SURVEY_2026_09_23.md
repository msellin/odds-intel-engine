# Estonian-licensed sportsbooks — survey for new odds sweepers

**2026-09-23 · task #099 (🤖👥 BOTH + 💼 DATA).** Which Estonian-licensed books can
we sweep, and in what order? Serves the owner's four goals: better Telegram odds
and more picks, better OWN prices (owner will open an account anywhere), an
Estonian odds product, and our own odds API.

**Why breadth, not sharpness.** No book in the API-Football feed is sharper than
Pinnacle at any Pinnacle margin band (`ANCHOR_IS_NOT_SHARP_2026_09_14.md`,
2026-09-23 addendum). The lever is more *placeable* prices:
`BOOK_SET_COUNTERFACTUAL_2026_09_22.md` measured +2.1% on price from a wider set.

## Method

1. **Licence list:** EMTA's register of legal gambling operators
   (<https://www.emta.ee/en/business-client/registration-business/gambling-operators/list-legal-gambling-operators>),
   filtered to holders of a betting ("toto") licence. 31 operators, most of them
   casino-first.
2. **Odds engine:** each sportsbook loaded in a real browser from an Estonian
   residential IP, reading the iframes, API hosts and config it loads. The engine
   decides price diversity: two brands on one provider's trading are one price.
3. **Reachability:** the odds endpoint requested from the VPS through the zone.ee
   Estonian exit (`socks5h://127.0.0.1:1081`), plain `curl`, no login.

## Results

| Book | Licensee | Odds engine | Endpoint (anonymous) | From VPS exit | Own prices? |
|---|---|---|---|---|---|
| **Coolbet** | StayCool OÜ | own | — | ✅ swept since 2026-09-23 | yes |
| **Unibet** | Lexbyte Digital | Kindred own | SPA XHR | ✅ swept (logged-out Chrome) | yes |
| **Epicbet** | Ducks In A Row OÜ | own | tRPC JSON | ✅ swept | yes |
| **Optibet** | Optiwin OÜ | Enlabs in-house (`ensb-trading.optibet.ee`, "betex") | REST `/et/groups`, `/et/events/…` | ✅ 200 JSON | **yes — own trading** |
| **Olybet** | OB Holding 1 OÜ | **BetConstruct** (Swarm, `site_id` 56; sportsbook frame `nw.olybet.ee`, `AuthToken=anonymous`) | websocket `swarm-newm.betconstruct.com` | ✅ conf 200 (websocket not yet opened) | provider prices + operator margin |
| **Tonybet** | Osaühing Tonybet | Tonybet platform (`platform.tonybet.com/api/v4/…`) | REST | ✅ 200, 287 KB menu | yes (to confirm) |
| **20bet** | Moon Technologies OÜ | **same platform as Tonybet** (`platform.20bet.win/api/v4/…`) | REST | ✅ 200, 1,273 football events | ⚠️ may share Tonybet's trading — diff before building |
| **Paf** | AS PAFER | **Kambi** (`#/sports-hub/football`) | Kambi offering API, offering `paf` | ✅ 200, 410 KB | Kambi prices + Paf margin. ⚠️ validate vs site — Unibet-Kambi diverged (38% higher than site) because unibet.ee LEFT Kambi; Paf has not, but prove it |
| **Betsafe** | Triogames OÜ | Betsson in-house (OBG widget `btsplayground.net`) | not captured — websocket/widget; AWS WAF + Group-IB fraud | ⚠️ untested | **yes — own trading** |
| **Betmaster** | BM Baltics Ltd | unknown (odds not visible as XHR) | — | untested | ? |
| **Ninja (ninjasports.ee)** | Ninja Global OÜ | **Altenar** | — | untested | provider prices |
| **Bet365** | Hillside (New Media Malta) | own | Cloudflare challenge (403 from exit) | ❌ | yes — but **already in the AF feed** |
| Luckybet | SIA Luckybet.ee | ? | Cloudflare challenge (403) | ❌ | ? |
| Nubet, Vivatbet, Chanz | various | not probed | | | |
| Fenixbet | OÜ Novoloto | xtreme.bet platform; no sports link found | | | looks casino-only |
| Campeonbet | Campeon Interactive EE | — | domain → `/terminated/` | | defunct |
| Totalisaator (toto.ee) | AS Totalisaator | pool betting, not fixed odds | | | out of scope |

**⚠️ Betano is NOT on the EMTA list**, yet several scripts tag it "we bet here"
(e.g. `OUR_BOOKS` in `scripts/anchor_book_sharpness_research.py`). If that tag
feeds any OWN placement or "placeable" logic, it is wrong for an Estonian
operator. To check.

## Recommended build order

Ranked by *new, placeable price* per unit of effort:

1. **Optibet.** Own trading, plain REST, reachable. The cleanest win.
2. **Tonybet** (+ 20bet if its prices differ). Plain REST; one sweeper can cover
   both platforms by base URL. Diff the two first.
3. **Olybet.** BetConstruct Swarm is a well-known websocket protocol; anonymous.
4. **Paf.** Cheapest of all (reuse `workers/automation/unibet_kambi.py` with
   offering `paf`), but only after a site-vs-API price check, the test Unibet-Kambi
   failed.
5. **Betsafe.** Own trading, so valuable, but behind AWS WAF with no captured
   endpoint. Probably a logged-out browser like Unibet. Spike before committing.
6. Later: Betmaster, Ninja (Altenar), Nubet, Vivatbet, Chanz.

**Acceptance for every sweeper** (the pattern that qualified Coolbet, Unibet and
Epicbet): its prices match the live site for the same fixtures, it writes
`book_event_map` pairings (for the near-kickoff close), it is registered on the
VPS scheduler with `_run_job`, and a freshness watchdog covers it.

## Not covered here

- Market depth per book (O/U lines, BTTS, corners) and league coverage. Measure
  per sweeper once built.
- Legal/ToS for **reselling** these prices (goal 3/4) — see
  `docs/ODDS_DATA_PRODUCT_RESEARCH_2026_09_23.md` (#100). Collecting for our own
  use is what we already do for three books.
