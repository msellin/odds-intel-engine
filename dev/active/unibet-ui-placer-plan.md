# Unibet automated betting tool (UI placer) — plan

**Goal:** place our model's real-money bets on Unibet through the UI, mirroring the
Coolbet own-betting stack, so we have a SECOND executable book (Unibet covers more
upcoming fixtures than Coolbet — 115 vs 93).

## The architecture to mirror (Coolbet stack)
| Coolbet | Unibet equivalent to build |
|---|---|
| `coolbet_session.py` / `coolbet_browser_sync.py` (CDP + Imperva session, JWT) | Unibet session/auth on unibet.ee (CDP-Chrome; unknown bot-protection) |
| `scripts/place_coolbet_ui.py` (`place_for_bot`, `fetch_account_holds`, `reconcile_account_to_real_bets`, dedup, fail-closed) | Unibet UI placer — SAME shape, reuse the generic parts |
| `coolbet_placer.py` gates (`_min_edge_for`/`_min_odds_for`, exposure, blast-radius) | REUSE — the gate stack is book-agnostic |
| pick→coolbet-event fuzzy matcher (`match_coolbet_to_simulated`) | pick→unibet-event matcher (needed because the odds feed ids ≠ site ids) |
| `PLACEABLE_BOTS` ∩ per-bot toggle ∩ pause flags | same safety model, Unibet toggles |

## The two unknowns that gate everything (resolve FIRST)
1. **Can we drive unibet.ee's authenticated bet slip at all?** The odds feed (Unibet-Kambi,
   public offering API) is NOT the site. unibet.ee diverged (KAMBI-FEED-DIVERGENCE) and
   placing is flagged "FlareSolverr-class". Bot-protection on the logged-in site is unknown.
2. **ID mapping**: the offering-API event ids appear nowhere in unibet.ee's HTML, so every
   pick must be fuzzy-matched to the SITE's event/selection (teams/market/selection), like
   the Coolbet matcher. Need to confirm unibet.ee exposes enough to match + place.

## Phased build (paper-first, real money owner-gated — same discipline as Coolbet)
- **Phase 0 — FEASIBILITY SPIKE (do before committing):** log into unibet.ee in CDP-Chrome,
  navigate to a football event, open the bet slip, read the DOM. Answer: is there
  bot-protection? can CDP drive the slip? what selectors/structure? Is it Kambi-widget based
  (reusable criterion parsing) or bespoke? 30-60 min. GO/NO-GO gate.
- **Phase 1 — session/auth**: CDP login + session-alive check (mirror coolbet_browser_sync).
- **Phase 2 — read-only account + matcher**: fetch account holds, pick→unibet-event matcher.
- **Phase 3 — PAPER placer**: `place_for_bot` shape, gates reused, execute=False, writes to a
  shadow/real_bets vocabulary. Reconcile + dedup + fail-closed. NOT placeable.
- **Phase 4 — real money (owner-gated)**: add Unibet bots to a PLACEABLE set + toggles, after
  a dry-run, exactly like Coolbet.

## Risks
- unibet.ee may have heavier bot-protection than the offering API (which has none). If CDP
  can't drive it, this becomes a FlareSolverr/behavioral problem — Phase 0 tells us.
- Real money on a new transport — paper-first, fail-closed, blast-radius caps from day one.
- Do NOT reuse Coolbet's PLACEABLE_BOTS; Unibet needs its own explicit set so a Coolbet bot
  can never accidentally place on Unibet and vice versa.

## Phase-0 spike findings (2026-09-09, live CDP :9222 session)
GO — feasible, but a genuine from-scratch build (comparable to the Coolbet placer, NOT a copy).
1. **Drivable via the real CDP session** — login works (balance €100,00 visible), the operator's
   session passes protection.
2. **Bot protection = DataDome** (cookie present) — same class as Coolbet's Imperva: must drive
   the REAL logged-in browser, rate-limited, behavioral-block risk. No headless/fresh automation.
3. **Bespoke sportsbook, NOT Kambi** — no kambi global/script/iframe. So Coolbet's Kambi criterion
   parsing is NOT reusable; the bet-slip driver is fresh Unibet-specific work. (This is exactly the
   KAMBI-FEED-DIVERGENCE the odds module warned about.)
4. **Estonian locale** — odds are `2,50` (comma), balances `100,00 €`. Every parser/matcher must
   handle comma decimals.
5. **A "kulutamiste limiit" (spending-limit) modal** interstitials the sportsbook — the driver must
   dismiss it ("Ei, aitäh") on load, like Coolbet's cookie/limit interstitials.
6. **DOM introspection is non-trivial** — odds did not surface in a PROGRAMMATICALLY-opened tab even
   after dismissing the modal (0 elements), while the operator's own navigated tab renders them.
   Likely cause: a fresh CDP-opened tab is degraded by DataDome / the SPA doesn't fully init, OR the
   odds live in shadow DOM / web components. IMPLICATION: the driver should operate on the operator's
   LIVE-navigated tab (not spawn fresh tabs), use Playwright locators (which pierce open shadow DOM),
   and map selectors interactively against a real rendered event — this is Phase 1-2 work.

## Revised effort estimate
- Auto-login (reads UNIBET_* from .env, drives the form, dismisses the limit modal): ~0.5 day.
- Bet-slip driver (select outcome → set stake → place; Unibet-bespoke selectors, live tab, shadow
  DOM handling): 1.5-2 days — the bulk.
- pick→unibet-event matcher (comma locale, site ids ≠ feed ids): ~1 day.
- Gate reuse + reconcile/dedup/fail-closed + paper placer: ~1 day (mostly reuse).
- Real-money enablement (own PLACEABLE set + toggles + dry-run): ~0.5 day, owner-gated.
Total ~4-5 days, phased, paper-first.

## END GOAL (owner, 2026-09-09) — unified book-agnostic best-price placement
NOT per-book real-money bots. ONE book-agnostic bot per MARKET (1x2, O/U) that:
1. Computes the model trigger window per fixture (min-odds to clear the gate) — this is
   Stage A `pick_triggers`, which ALREADY EXISTS.
2. Checks the CURRENT REAL odds at ALL executable books (Coolbet + Unibet site odds).
3. Writes the row (the bet lands) if AT LEAST ONE book clears the gate.
4. Places ONCE, at the book with the BEST clearing odds. If only one passes → place there;
   if both pass → place at the better price. Marks it placed so no other placer double-bets.
   => NO DUPLICATES, best executable price, by design.

This is the BOOK_AGNOSTIC_EDGE_ENGINE (Stage A windows + Stage B matcher) promoted from
PAPER to REAL MONEY with multi-book best-price routing. The current 2 per-book Coolbet
real-money bots (bot_coolbet_1x2_model_v1 / _ou_model_v1) are the interim; they get
REPLACED by unified bots (e.g. bot_model_1x2_v1 / bot_model_ou_v1) that route to best-of
{Coolbet, Unibet}.

### Build order to reach it
1. **Unibet real SITE odds feed** — sweep the Kindred `sportsbff` API
   (`sports-api/api/v2/views/contest-page?_typ=GetContestWithPricesReq&contestKey=<id>`,
   listings via `quickbrowse`/`az-menu`) with the session; write TRUE site odds as a
   bookmaker (e.g. `Unibet-Site`). NOT the divergent public Kambi feed (proven: feed 3.20
   vs site 3.50 on Derby, both fresh). This is the prerequisite for everything else.
2. **Unibet UI placer** (session/login + slip driver) — mostly done this session; needs
   auto-login creds + hardening. Selectors: clear=`bet-list-header-trash-icon`,
   search=`input[name=search]`→show-all→fixture, outcome=`propositionOptionBtn` (odds-disambig),
   stake=`input[placeholder="0.00"]`, place="Tee panus". Session logs out under automation →
   auto-login is essential.
3. **Unified best-price router** (Stage B real-money): per trigger, choose the best clearing
   book, route to that book's UI placer, mark placed (shared `match_exposure` across books
   already reads `real_bets`).
4. **Unibet TRIGGER bots (paper)** — mirror the Coolbet trigger family (model + sharp anchors)
   for Unibet, once the site odds feed exists — for watching, like the Coolbet triggers.

## Odds feed — findings (2026-09-09) — it's an anti-bot task, do it right
The Unibet SITE odds live in the Kindred API `sportsbff-ams.kindredext.net/sports-api/api/v2/
views/contest-page?_typ=GetContestWithPricesReq&contestKey=<eventKey>` (listings via
`quickbrowse` / `az-menu`). Response shape: `contest` → propositions → options with
`optionDisplayName` ("1"/"X"/"2", "Üle"/"Alla") + `"price"` (decimal) + `"line"`. Confirmed
the REAL prices are here (Derby home price 3.5 — matches the site, not the 3.20 Kambi feed).

Getting the JSON is the hard part (all three tried this session):
- **Direct fetch** (page.evaluate / ctx.request) → CORS + DataDome block ("Failed to fetch" /
  503). The SPA's own requests carry a DataDome token; injected fetches don't.
- **Playwright response-body capture** (connect_over_cdp) → flaky, bodies evicted before read.
- **DOM scrape** → works for targeted placement but SPA lazy-renders markets → bulk returns {}.

**DECISION — the reliable approach:** capture the SPA's OWN `contest-page` responses via a RAW
CDP Network-domain session (Network.enable + Network.responseReceived + Network.getResponseBody
called promptly), like coolbet_session.py's raw-CDP websocket usage — raw CDP retains bodies.
Feed loop: navigate the football listing (SPA fetches listings → contestKeys), then navigate/
prefetch each event (SPA fetches contest-page → capture), parse propositions → write
odds_snapshots as bookmaker `Unibet-Site` (NOT the divergent `Unibet`/`Unibet-Kambi` Kambi feed).
Rate-limit + reuse the operator's session (DataDome). ~half-day focused build. THIS unblocks:
Unibet bots → best-price router → Unibet trigger bots.

## Odds feed — BUILT (2026-09-09) — `workers/automation/unibet_odds_feed.py`
Build-step 1 landed. `fetch_event_odds(event_url, *, match_id, write, minutes_to_kickoff)`
captures the SPA's OWN `contest-page` response via a RAW CDP Network session on the
operator's established tab, parses 1x2 + O/U-2.5 (keyed on the language-stable
`propositionType`, contamination-proof), and writes bookmaker `Unibet-Site` via the
shared `store_book_odds_snapshots`. Offline smoke test `UNIBET-SITE-ODDS-PARSE` on a
committed fixture (`tests/fixtures/unibet_contest_derby.json`). Read-only; never places.

### DEFINITIVE transport matrix (all tested live 2026-09-09, supersedes the guesses above)
| Method | Result | Why |
|---|---|---|
| RAW-CDP capture of the SPA's own contest-page (established tab) | ✅ 200, true prices | rides the SPA's real XHR (DataDome token + headers + params) |
| Fresh / background CDP tab | ❌ 500/204 | DataDome degrades non-established tabs |
| Injected `fetch()` from the established page | ❌ CORS "Failed to fetch" | Kindred sends no ACAO for injected XHR |
| **FlareSolverr → the Kindred API** (Coolbet's odds pattern) | ❌ **HTTP 400 "Bad request"** | FS passes DataDome, but a bare browser GET lacks the SPA's required XHR headers/params |
**Conclusion:** the Coolbet ODDS path (FlareSolverr → a plain JSON API) does NOT transfer.
Coolbet exposes a plain JSON API FS can hit; Unibet's Kindred API rejects everything but
the SPA's own fully-formed XHR. Only reading the SPA's own responses works. Coolbet
BETTING is CDP+JWT; Coolbet ODDS is FS+API. Unibet BETTING is CDP (unibet_placer); Unibet
ODDS is CDP-capture (this module) — there is no FS+API shortcut.

### KAMBI-FEED-DIVERGENCE — now measured with 3 books in the DB (Derby, 2026-09-09)
1x2 home: **Unibet-Kambi 3.20 · Coolbet 3.30 · Unibet-Site 3.50**. The site reads
HIGHER than both the public Kambi feed AND Coolbet — the site is the best price and
where best-price routing would place. The Kambi feed UNDERSTATES the site by 0.30, so a
Kambi-only screen would MISS this soft edge. This is the evidence for the divergence task.

### DESIGN CONSEQUENCE — two-stage, low-volume (parity with the placer)
True site odds cost ONE tab navigation per event (no API, no derivable slug, no navigable
contestKey). And aggressive repeated navigation trips DataDome behavioral throttling
(observed: after ~8 probe navigations the contest-page started 500-ing). So:
1. **Broad soft-book screen (cheap)** = the existing public Kambi feed (`unibet_kambi.py`,
   `Unibet-Kambi`, ~700 events) — the substrate for Unibet TRIGGER bots, exactly as
   Coolbet's broad API sweep feeds Coolbet trigger bots.
2. **Targeted site confirm (raw-CDP, low-volume)** = this module, called per CANDIDATE
   fixture the router/placer is acting on — confirms the true placeable price before
   writing/placing (the placer already re-checks live odds).
A book-wide `Unibet-Site` sweep is deliberately NOT built: it would need ~700 navigations
and risks a behavioral block. Whether it is ever needed hinges on the divergence measure.

### Remaining for the router (next)
- **Fixture → event_url resolver** (shared with the placer; ~the "matcher" in the phased
  plan) so the router can call `fetch_event_odds(url)` for an arbitrary DB fixture. The
  placer today takes an event_url directly; resolution (site search → event page) is the
  missing shared piece.
- **Unified best-price router** (Stage B real money): per trigger/pick, capture Unibet-Site
  + read Coolbet, place ONCE at the better clearing book, mark placed (no duplicates).
- **Kambi-vs-site divergence measurement** → decides if the Kambi screen is trustworthy or
  a broad site sweep is actually required for the trigger bots.


## 3c — fixture→URL resolver: the SLUG is the wall (2026-09-09)
`find_event(home,away,date)` is SOLVED: injected-fetch the search API `sports-api/api/v2/search?_typ=GetSearchResults&query=<team>` (same transport as the odds feed) → `SearchContest{contestKey, category, name, startDateTimeUtc}`, fuzzy-match on home+away. Proven live.

**BUT the placer needs the navigable SLUG URL** (e.g. `/betting/odds/football/england/championship/derby-county-vs-west-brom`), and the slug is NOT obtainable: not in the search/lobby/contest-page JSON (no url/slug/path/seoUrl field), and not in the rendered category-page DOM (no event anchors/hrefs — the SPA renders events via virtualized/onClick components; slug lives only in client state). Slug construction from name is unreliable ('West Bromwich' → 'west-brom'). So contestKey→URL is a live-R&D problem, same class as the odds feed. Options (for when Unibet placement is actually wired, Stage 5): (a) drive a real click on the event element + read the landed `location.href` (hard to target the element reliably); (b) read the SPA's internal router/store state via Runtime.evaluate (fragile); (c) accept operator-supplied URLs for the small candidate set (what we did for the Derby €10 bet). NOT blocking 3b (paper triggers) or the odds feed — only the placement executor arm. Recommendation: defer the slug resolver to Stage 5 (placement wiring, owner-gated) and proceed with 3b now.

### 3c — SOLVED 2026-09-09: the URL is constructible (contestKey routes)
The owner's example `/betting/odds/football/england/championship/derby-county-vs-west-bromwich/
17863292b0e17b03a260b456e755bfd8` revealed the format: `/betting/odds/<category-path>/<slug>/<contestKey>`.
PROVEN the SPA routes on the trailing **contestKey** — a garbage slug (`xxx-vs-yyy/<key>`) still loads
the right event (contest-page 200). So no slug scraping is needed. `unibet_odds_feed.resolve_event_url(
home, away, date)`: injected-fetch the search API (`search?_typ=GetSearchResults&query=<team>`) → nested
`SearchContest{contestKey, category, name}` → `fuzzy_match_event` → `build_event_url(category, name, key)`.
Validated live: resolve("Derby","West Brom") → exact URL (fuzzy 100), and navigating it → contest-page 200.
Smoke `UNIBET-EVENT-URL-RESOLVER`. The placer's executor arm can now be handed a resolved URL for any DB
fixture. (Placement itself — driving the slip with real money — is Stage 5, owner-gated.)
