# Coolbet Own-Betting — Flow & Architecture (single source of truth)

**Purpose.** One place that defines exactly how WE bet our own money on Coolbet:
what generates the picks, every gate a pick must clear, the thresholds and why,
and what is paper vs real today. Written 2026-09-08 because the flow was being
half-remembered in conflicting ways (odds floor "was inplay", placer reads
"shadow bets", etc.). If you change any gate, update this file in the same commit.

This is the **🤖 OWN** path only. It is NOT the customer `/picks` product and NOT
the `/admin/shadow-bots` page (see "Two surfaces" below). For the transport chain
(Mac → FlareSolverr → Imperva → Coolbet API) and its failure modes, see
`docs/COOLBET_RUNBOOK.md` — this doc is the betting LOGIC, not the plumbing.

## One-line summary

A continuous Mac daemon takes the **calibrated bots' pre-match picks** from
`simulated_bets`, and for each one **re-prices it live at Coolbet** and places a
flat stake **only if** it still clears a stack of gates (maturity, per-market
edge floor, a per-market odds floor, a live-edge floor, blast-radius limits, and not
already placed). It is **pre-match only** and **paper-only today**
(`execute=False`), pending the real-money flip.

## The flow

```
04:00  Fixtures      AF → matches
07-22  Odds (30min)  AF bulk odds (13 books) + Coolbet sweep (Mac) → odds_snapshots
       Pick-gen      run_betting (morning cohort) + betting_refresh (KO windows):
                     model/line-shop vs de-vigged Pinnacle at best-accessible price
                     → writes a pick to simulated_bets (ON CONFLICT dedup)  [ALL bots]
       PLACER        coolbet_mac_daemon._tick() (continuous KeepAlive) →
                     load_qualified_bets() + place_all_bets()  [coolbet_placer.py]
       Settlement    settle bets, CLV, ELO (settlement.py)
```

The placer runs on its OWN cadence, independent of pick-gen; it re-evaluates the
pending set each tick and only acts on picks that still qualify at the LIVE
Coolbet price (so lateness is safe — it never bets a stale stored price).

## The gate stack — a pick is placed only if it clears ALL of these, in order

| # | Gate | Where | Current value | Why |
|---|------|-------|---------------|-----|
| 1 | **Source** | `load_qualified_bets` | `simulated_bets`, `result='pending'`, `m.date > NOW()` | The bots' picks; **pre-match only** |
| 2 | **Maturity** (CHERRY-PICK) | `_allowed_maturity_labels` | `COOLBET_RECORD_ALLOWED_MATURITY` = **calibrated** | Real money only on proven bots; every bot still fires into simulated_bets |
| 3 | **Edge floor** (per-market) | SQL prefilter `_MIN_EDGE` (3%) → `_min_edge_for(market)` | **1x2 13% · O/U 8% · AH 5% · DNB 5%** (BTTS/DC retired = None) | Edge = `best_price × devig(Pinnacle) − 1`; floors validated by `scripts/edge_floor_backtest.py` (walk-forward) |
| 4 | **Not already placed** | `NOT EXISTS real_bets today` | — | Dedup: one bet per (match,market,selection)/day |
| 5 | **Live re-price @ Coolbet** | `get_live_odds_and_id` + `_MIN_REMAINING_EDGE` | ≥ **3% live edge** at the Coolbet price | The pick's edge is vs the BEST book; Coolbet may be worse (COOLBET-VALUE: 57/58 were −EV at Coolbet despite +7% best-book). Re-checks at the price we can actually take |
| 6 | **Odds floor** (per-market, 2D-GATE-PER-MARKET-ODDS-FLOOR) | `_min_odds_for(market)` (default `COOLBET_MIN_ODDS`) | **1x2 ≥ 2.80 · O/U ≥ 1.80 · AH/DNB ungated · unknown ≥ 2.80** | Was a single global 2.80 (a 1x2 CLV number). Joint edge×odds sweep: 1x2 profit peaks at odds≥2.8, O/U at odds≥1.8 (the 2.80 floor rejected 84% of O/U bets, −€1.1k profit). O/U beats the close at 1.8+ / turns negative below. **PRE-MATCH gate — not inplay** |
| 7 | **Blast-radius** (SafetyGuardrails) | `place_all_bets` kwargs | flat **€10** stake (or Kelly), + optional max_stake_per_bet / max_bets_per_hour / max_total_stake; self-pause on error streak | Limits real-money exposure |

## Pre-match only — no inplay

The main path (`load_qualified_bets`) filters `m.date > NOW()`. There is a
separate `load_qualified_inplay_bets` but it is an **admin override** (bypasses
window/edge/dedup) and is NOT part of the automated daemon flow. So our Coolbet
own-betting **never bets in-play** — consistent with INPLAY-SHELVED-REVIVE-GATE
(inplay is retired/parked). Any "2.80 for inplay" recollection is a conflation:
2.80 is the pre-match real-money odds floor (gate 6).

## Paper vs real

**Paper-only today.** The daemon hardcodes `execute=False`; picks flow through
every gate and are recorded/simulated but no real bet is posted. The real-money
flip is gated on model proof + a fresh-JWT path + explicit owner authorization
(feedback_coolbet_execute_safety). When it flips, gate 6 (odds floor) is the one
that "first matters" — it was added to THIS path precisely for that moment.

## Two surfaces — do not confuse them

- **The automated placer** (this doc): reads `simulated_bets`, gates to
  calibrated, places on Coolbet. The 🤖 OWN real-money path.
- **The `/admin/shadow-bots` page**: a DISPLAY/reference that reads `shadow_bets`
  (including experimental line-shop / Coolbet-value shadow bots) and shows an
  "upcoming picks — single place to look when placing real money" panel. This is
  where the operator eyeballs picks for MANUAL placement and sees which upcoming
  matches have picks. It is NOT the automated placer's source.

They overlap (same underlying model) but are different tables and bot sets. When
"the placer" and "the shadow-bots page" seem to disagree, this is why.

## Config (env, `.env`)

| Env | Meaning | Default |
|---|---|---|
| `COOLBET_RECORD_ALLOWED_MATURITY` | maturity allowlist for real placement | calibrated |
| `COOLBET_MIN_EDGE` | global edge prefilter | 0.03 |
| `COOLBET_MIN_REMAINING_EDGE` | live-edge floor at Coolbet price | = MIN_EDGE (0.03) |
| `COOLBET_MIN_ODDS` | pre-match odds floor (1x2/default only; O/U=1.80, AH/DNB ungated via `_MIN_ODDS_BY_MARKET`) | 2.80 |
| `COOLBET_STAKE` | flat stake € | 10.0 |
| per-market edge floors | `_MIN_EDGE_BY_MARKET` in code | 1x2 0.13 · o/u 0.08 · ah 0.05 · dnb 0.05 |

## Known-open / not-yet-validated

- **Edge × odds is now a per-market 2D gate (validated 2026-09-08, 2D-GATE-PER-MARKET-ODDS-FLOOR).**
  The joint edge×odds sweep was run on executable (`simulated_bets`, ~4.2k
  settled), the idealized fixture-level set (1x2 104k / O/U 182k), and
  cross-checked against CLV bands. Each market has its own profit ridge, so the
  odds floor is now per-market (`_MIN_ODDS_BY_MARKET`): **1x2 edge≥13% & odds≥2.8**
  (executable +€1063, the robust peak), **O/U edge≥8% & odds≥1.8** (+€1663 vs €544
  at the old global 2.8 floor; O/U beats the close at 1.8+). On executable data
  the 1x2 2D gate beats BOTH 1D gates (edge-only +€563, odds-only −€18). The
  idealized set confirms the edge floors are robust at 104k/182k but is blind to
  the odds effect (best-of-books rescues low-odds picks we can't get) — which is
  exactly why the odds floor is validated on executable + CLV, not idealized.
- **Asian Handicap has no fold-robust floor** (recent losses at every level) —
  AH-VIABILITY-REVIEW may retire it like BTTS.
- Real-money flip still pending (paper-only today).
