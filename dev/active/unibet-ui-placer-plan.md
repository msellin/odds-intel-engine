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
