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
