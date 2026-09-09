# Coolbet Own-Betting — Flow & Architecture (single source of truth)

## CURRENT STATE (2026-09-08) — RESOLVED: model-edge on both markets

The old open question ("line-shop 3% vs model per-market floor governs real
money") is **decided by evidence** (BOT-2D-AUDIT, held-out OOS): line-shop loses
out-of-sample (1x2 −24%, O/U −17%, sweep/pin all negative — a best-of-books
selection artifact); model-edge holds (v10 +28% 1x2 / +34% O/U OOS). So:

| Bot | Signal | Market | Real money |
|---|---|---|---|
| `bot_coolbet_ou_model_v1` | model-edge | O/U | **ON** |
| `bot_coolbet_1x2_model_v1` | model-edge | 1x2 (**home-underdogs @10%, odds≥2.80**) | **ON** |
| `bot_coolbet_ou_model_v1` | model-edge | O/U 2.5 (8%, odds≥1.80) | **ON** |
| `bot_coolbet_value_v1` | line-shop | 1x2 | **RETIRED** |

> **FAVLONG-CUTS-2026-09-09:** the real-money 1x2 bot now bets **home-underdogs only, edge ≥10%,
> odds ≥2.80** (per-bot `BOT_THRESHOLDS`=0.10 + a home+odds≥2.80 filter in the mirror job). The
> by-selection backtest found home-underdogs are the one fold-robust 1x2 engine; home-favs lose,
> aways aren't robust, draws are a sharp-trigger edge the model can't see (ANALYSIS_GOTCHAS §57,
> BETTING_GATE_DECISIONS "1x2 by type"). The **pooled/paper `_MIN_EDGE_BY_MARKET['1x2']` stays 13%**
> (the coolbet_placer/daemon Path B + trigger windows are all-selection) — the tables below that
> say "1x2 13%" describe that paper path, not the real-money placeable bot.

- Placement = `place_coolbet_ui.py --all-enabled --execute` (launchd), placing
  every bot in `PLACEABLE_BOTS ∩ coolbet_placer_bots(ui_place_enabled=true)`.
- Every run first VERIFIES the real Coolbet account (panuste ajalugu), reconciles
  it into `real_bets`, and FAILS CLOSED if it can't — so no manual/auto bet is
  ever double-placed (COOLBET-ACCOUNT-VERIFY-GATE).
- Per-bot on/off toggles live on `/admin/shadow-bots` (COOLBET-PLACER-CONTROL).
- The gate-stack sections below still describe the line-shop path for history;
  the ACTIVE real-money bots are the two model-edge ones above.


**Purpose.** One place that defines exactly how WE bet our own money on Coolbet:
which bot, which picks, which edges, which floors, and what actually places the
money. Rewritten 2026-09-08 after a long-running confusion was finally pinned:
**there are TWO placers, and the one that stakes real money is NOT the one whose
edge floors we spend most time tuning.** If you change any gate, update this file
in the same commit. For the transport chain (Mac → FlareSolverr → Imperva →
Coolbet) and its failure modes see `docs/COOLBET_RUNBOOK.md` — this doc is the
betting LOGIC, not the plumbing.

This is the **🤖 OWN** path only. It is NOT the customer `/picks` product.

---

## DATA FLOW — the definitive table map (audited & verified 2026-09-09)

The recurring "which table do picks/bets come from?" confusion, settled with a full
code+DB trace (COOLBET-PICK-TABLE-AUDIT). **Four tables, and they are NOT interchangeable:**

| Table | Written by | Read by | What it is |
|---|---|---|---|
| `simulated_bets` | `daily_pipeline_v2.py` (the model pipeline) for internal/anchor bots incl. **`bot_v10_all`** | **`/picks` + `/performance`** (customer-facing), and the **paper** mac-daemon (`coolbet_placer.load_qualified_bets`) | Primary paper ledger. Bankroll/EV picks. Market spelled `o/u` / selection `over 2.5`. |
| `shadow_bets` | per-bot mirror jobs: `coolbet_model_ou_shadow.py`, `coolbet_model_1x2_shadow.py`, trigger matcher, etc. | via the view below | Append-only shadow ledger. Holds the **placeable model-edge bot rows**. Market re-spelled `over_under_25` / selection `over`. |
| `shadow_bets_unique` (VIEW) | — (DISTINCT ON bot×match×market×selection over `shadow_bets`; def in migration 298) | **`place_coolbet_ui.py` `load_picks()` — the LIVE REAL-MONEY placer**, and `/admin/shadow-bots` | The canonical real-money read path. |
| `real_bets` | `place_coolbet_ui.py`/`coolbet_ui_placer.py` (real, balance-confirmed) **AND** the paper daemon (`record=True, execute=False` → phantom rows) **AND** manual-bet reconciliation | `/performance` overlay | Placement ledger — **dual-purpose; a row alone does NOT prove money moved.** Only a `coolbet_placement_attempts` row with `outcome='placed'` proves a real stake. |

**The one sentence that removes the confusion:**
> Real money is placed by **`place_coolbet_ui.py`** (launchd, hourly 06:00–21:00 UTC), reading **`shadow_bets_unique`**, for **`PLACEABLE_BOTS = {bot_coolbet_ou_model_v1, bot_coolbet_1x2_model_v1}` ∩ the `coolbet_placer_bots` toggle**. Those placeable rows are a filtered, re-labelled **copy of `bot_v10_all`'s `simulated_bets` picks** (mirror jobs select `maturity_label='calibrated'`, per-market edge floor, `result='pending'`). Everything else is paper or customer-facing.

**Placement schedules (both books):**
| Placer | Schedule | Reads | Real money? |
|---|---|---|---|
| Coolbet UI placer (`place_coolbet_ui.py --all-enabled --execute`) | launchd hourly **06:00–21:00 UTC** | `shadow_bets_unique` | **YES** (execute=True, balance-confirmed) |
| Coolbet mac daemon (`coolbet_mac_daemon` → `coolbet_placer`) | every 30 min (was stale since 2026-08-23 — verify) | `simulated_bets` | **NO** — paper (`execute=False`), but `record=True` writes phantom `real_bets` rows |
| **Unibet placer (`unibet_placer.place_bet`)** | **NONE — no launchd job; manual only** | **no pick table — takes `event_url`+outcome as args** | manual only; real-money Unibet automation is **not live** |

**Known inconsistencies this audit surfaced (see COOLBET-PICK-TABLE-AUDIT in PRIORITY_QUEUE):**
1. Two placers read two different tables (paper→`simulated_bets`, real→`shadow_bets_unique`); the API/`coolbet_placer` path is now effectively paper-only dead-weight for real money.
2. **Customer `/picks` + `/performance` show `simulated_bets` (`bot_v10_all`), NOT the rows we actually stake** (the shadow model-edge bots) — except where `real_bets` is overlaid.
3. **One logical bet carries three market spellings** (`o/u`→`over_under_25`, selection `over 2.5`→`over`, floor dict keyed `o/u`) depending on the table.
4. `real_bets` mixes real + manual + phantom-paper rows (10 of the last 15 rows had no `outcome='placed'` attempt); use `coolbet_placement_attempts` to prove a real stake.

---

## ⚠ Read this first — there are TWO placers, and they are different

Both write to the `real_bets` table (that table deliberately holds two market
vocabularies, one per placer — see `place_coolbet_ui.py` header). They do NOT
share a source, a bot, or an edge gate. They share only the per-market **odds**
floor helper.

| | **UI placer — REAL MONEY** | API placer — paper |
|---|---|---|
| File | `scripts/place_coolbet_ui.py` (+ `coolbet_ui_placer.py` driver) | `workers/automation/coolbet_placer.py` |
| launchd job | **`com.oddsintel.coolbet-ui-placer`** — hourly 06:00–21:00, runs with `--execute` | `com.oddsintel.coolbet-mac-daemon` — continuous |
| Source table | **`shadow_bets_unique`** (view over `shadow_bets`) | `simulated_bets` |
| Which bot(s) | code whitelist `PLACEABLE_BOTS` ∩ DB toggle `coolbet_placer_bots` — seeded **`bot_coolbet_value_v1` ON, `bot_coolbet_ou_model_v1` OFF** | all model bots, gated to `calibrated` maturity |
| Edge basis | **Coolbet's OWN price vs de-vigged Pinnacle** (line-shop) | model ensemble vs de-vigged Pinnacle at best-accessible book |
| Edge floor | **flat 3%** (`BOT_THRESHOLDS`, the bot's `_LINESHOP_TRUE_EDGE_MIN`). Does **NOT** call `_min_edge_for`. | per-market `_min_edge_for` — **1x2 13% · O/U 8%** |
| Odds floor | `_min_odds_for` ✓ (shared) | `_min_odds_for` ✓ (shared) |
| Places real money? | **YES** — `--execute` in the plist; writer behind every `real_bets` row since 2026-08-27, incl. the 2026-08-31 −€92.80 incident. Per-bot on/off is the runtime `coolbet_placer_bots` toggle (superadmin, `/admin/shadow-bots`) | **No** — `execute=False` hardcoded in the daemon |
| Uses our model? | **No** — pure line-shop against Pinnacle | Yes — the calibrated ensemble |

**The consequence you must internalise:** the per-market EDGE-floor work
(`BOT-CONFIG-GOLDEN-MIDDLE` 1x2=13%, `EDGE-FLOORS-OTHER-MARKETS` O/U=8%,
`2D-GATE-PER-MARKET-ODDS-FLOOR`) lives in the **API/paper** placer. It does
**not** gate the money we actually stake. Real money rides on
`bot_coolbet_value_v1`'s flat **3%** line-shop edge. The only one of our recent
tuning changes that reaches real money is the per-market **odds** floor, because
both placers call the same `_min_odds_for`. This divergence is tracked as
`COOLBET-REALMONEY-EDGE-GATE-RECONCILE` (see Known-open).

---

## Path A — the REAL-MONEY placer (the one that matters)

### A1. Where the picks come from — `bot_coolbet_value_v1` (a line-shop bot)

Generated by `_run_coolbet_value_pass` in `workers/jobs/daily_pipeline_v2.py`
(runs in the morning cohort + each betting-refresh KO window). **It does not use
our prediction model at all.** For every Coolbet-priced selection it asks a
single question: *is Coolbet's own price better than the sharp (Pinnacle) fair
price?*

```
edge = coolbet_price × devig_shin(pinnacle_prob) − 1
keep the pick if:
  edge ≥ 0.03                 (_LINESHOP_TRUE_EDGE_MIN)
  tier ∈ {1, 2}               (_LINESHOP_TIERS — T3/T4 negative both ways)
  1.30 ≤ odds ≤ 5.00
  Pinnacle vig ≤ 10%          (skip garbage anchors)
  pair_gap ≤ 4h               (Coolbet & Pinnacle quotes not stale vs each other)
  passes the soft-book outlier filter
→ write to shadow_bets as bot_coolbet_value_v1
```

Markets it covers today: **1x2, over_under_25, over_under_35** (no AH / BTTS /
DC). ~3,000 picks since 2026-08-26. `shadow_bets_unique` is a dedup VIEW over
`shadow_bets`.

### A2. What the UI job does, per pass (hourly 06:00–21:00)

`scripts/place_coolbet_ui.py --execute`, driven by `coolbet_ui_placer.py`:

1. **Attach** to the operator's already-running Chrome via CDP (port 9222).
   Never spawns a browser; never types credentials (`coolbet_browser_sync`
   owns login, reading `COOLBET_USER/PASS`). Single-run flock so two passes
   never drive the same tab.
2. **Load picks:** `load_picks(bot)` → `shadow_bets_unique` for
   `bot_coolbet_value_v1`, `m.date > NOW()`, `result` pending. (No maturity or
   edge gate in the query — gates are applied in the loop below.)
3. **Per pick, in order — place only if ALL pass:**

| # | Gate | Where | Value / rule |
|---|------|-------|--------------|
| 0 | **Account verification** (COOLBET-ACCOUNT-VERIFY-GATE, the FIRST real-money gate) | `fetch_account_holds(page)` in `main()`, once per run | Reads the operator's ACTUAL Coolbet account (pending single tickets) via CDP: navigate the tab to `HISTORY_PAGE`, confirm the tab URL contains `panuste-ajalugu` **AND** `is_logged_in`, then `fetch_pending_bets_via_cdp` + `normalize_for_dedup`. **FAILS CLOSED**: if the account can't be read+verified (tab not on history, not logged in, CDP error, any exception) the whole run is forced to **dry-run** (`bot_execute` all `False`) — cannot-verify never stakes. On success, `reconcile_account_to_real_bets` inserts a `real_bets` row (`bookmaker='Coolbet'`, stake/odds from the ticket, `result='pending'`) for any account ticket not already represented — so `real_bets` reflects the real account each run, feeding both the exposure dedup and the picks "placed" column. Combos are logged and skipped (single-bet dedup only) |
| 1 | **Already placed** (dedup) | `already_placed` (`coolbet_placement_attempts`) + per-pick **account-hold check** (`already_on_coolbet_account`, matches the pick against the verified `account_holds`) + `exposure_conflict` vs `real_bets` | one bet per pick. NB the UI placer does **not** read `simulated_bets.user_placed_at/skipped_at` (those are Path B only); it writes `user_pick_marks` but does not read it back. UUID dedup alone misses ~45% of `shadow_bets_unique` dupes — `exposure_conflict` against `real_bets` (in-memory within a pass) is the real guard, and gate 0's reconcile keeps `real_bets` aligned with the actual account so a **manually-placed** bet is deduped too. The per-pick account-hold check is belt-and-suspenders: it catches a bet that lands on the account between the run-start reconcile and this pick |
| 2 | **Kickoff cutoff** | `KICKOFF_CUTOFF_MIN` | never place inside N min of KO (Coolbet suspends markets pre-KO) |
| 3 | **Odds-band (CLV)** | REALMONEY-ODDS-BAND-MISMATCH, gated on `odds_at_pick` | reject bands whose de-vigged CLV is decisively negative |
| 4 | **Odds floor** (per-market) | `_min_odds_for(market)` — **shared with the API placer** | **1x2 ≥ 2.80 · O/U ≥ 1.80 · unknown ≥ 2.80** |
| 5 | **Per-match exposure** | COOLBET-MATCH-EXPOSURE-GUARD | caps concurrent stake on one fixture |
| 6 | **Edge threshold** | `BOT_THRESHOLDS[bot]` = **0.03** | the bot's flat 3% line-shop edge — **NOT** `_min_edge_for` |
| 7 | **Real-money allowlist** (COOLBET-PLACER-CONTROL) | `PLACEABLE_BOTS ∩ ui_place_enabled_bots()` | code-level hard whitelist `PLACEABLE_BOTS = {value_v1, ou_model_v1}` intersected with the runtime DB toggle `coolbet_placer_bots` (superadmin flips it at `/admin/shadow-bots`). Seed: value_v1 ON, ou_model_v1 OFF. **Fails CLOSED** (places nothing) on any DB read error. A bot outside `PLACEABLE_BOTS` can never place even if a row enables it. Any disallowed bot → forced dry-run |
| 8 | **Kill switch** | `coolbet_state.is_placement_paused()` | DB flag halts the whole placement loop |

**Line-shop O/U stop (`lineshop_ou_stop`) — scoped to `bot_coolbet_value_v1`
only.** The line-shop bot loses on O/U (realized −17% ROI, negative every month),
so its O/U picks are skipped at placement (`REALMONEY_SKIP_MARKET_PREFIXES`,
override `COOLBET_UI_PLACE_OU=1`). As of COOLBET-MODEL-OU-SHADOW-BOT this skip is
gated on `args.bot == "bot_coolbet_value_v1"` and does **not** apply to the
model-edge O/U bot below. (The scope check reads `bot_name` inside the extracted per-bot flow `place_for_bot`.)

**`bot_coolbet_ou_model_v1` — the model-edge O/U real-money vehicle (off by
default).** `workers/jobs/coolbet_model_ou_shadow.py` (scheduled :10/:40) mirrors
the calibrated model's O/U picks (`simulated_bets` market=`o/u`, edge≥0.08 on
`calibrated_prob`, lines 2.5/3.5 only) into `shadow_bets` in the line-shop
vocabulary (`over_under_25`/`over_under_35` + `over`/`under`), so they load and
place through this same Path-A UI placer with the validated per-market gates
(edge≥8% via `BOT_THRESHOLDS[bot]=0.08` feeding `min_odds_for`, odds≥1.80 via
`_min_odds_for('o/u')`). It settles via the generic goals O/U resolver — no
custom settler. Real money stays OFF until its `coolbet_placer_bots` row is
toggled `ui_place_enabled=true` (superadmin, `/admin/shadow-bots`) with explicit
owner authorization — it is seeded OFF; this is the built vehicle for the
`COOLBET-REALMONEY-EDGE-GATE-RECONCILE` decision below.

4. **Drive the browser:** search the match (`input[name="sportSearch"]`) → click
   the match → find the market's odds button (`button-odds-<marketId>`) → **read
   the live Coolbet odds** → identify the outcome by its label text → re-check
   the odds floor at the live price → fill stake (`yourStake<marketId>`) →
   **`--execute` clicks place-bet (real money); `--stage` leaves it in the slip;
   default dry-run does neither.**
5. **Record:** every attempt → `coolbet_placement_attempts`; a successful place →
   a `real_bets` row **with the actual placed odds**. Self-verification is by
   **balance delta** (reads Coolbet balance before/after `place()`, refuses to
   record `placed` unless it moved by the stake) — there is **no ticket-id
   readback** (Coolbet returns no ticket id in this UI flow), so overlapping
   placements can fool the delta check. Manual placements are now captured a
   different way: gate 0's `reconcile_account_to_real_bets` reads them off the
   actual account each verified run and writes a `real_bets` row with the
   ticket's odds (`captured_odds = actual_odds = first_bet_odds`, notes
   `coolbet-account-sync ticket #<id> (self-verified <date>)`), so a hand-placed
   bet both appears in the track record and dedups future placement.

---

## Path B — the API/paper placer (model-edge; the one we keep tuning)

`coolbet_mac_daemon._tick()` (continuous) → `load_qualified_bets()` +
`place_all_bets()` in `coolbet_placer.py`. **`execute=False` is hardcoded**, so
it never stakes real money; in `record=True` mode it can still write paper
`real_bets` rows. Its gate stack:

| # | Gate | Value |
|---|------|-------|
| 1 | Source | `simulated_bets`, pending, `m.date > NOW()` (pre-match only) |
| 2 | Maturity (CHERRY-PICK) | `COOLBET_RECORD_ALLOWED_MATURITY` = **calibrated** |
| 3 | **Edge floor (per-market)** | `_min_edge_for` — **1x2 13% · O/U 8% · AH 5% · DNB 5%** (BTTS/DC retired = ∞) |
| 4 | Dedup | `NOT EXISTS real_bets` for (match,market,selection) |
| 5 | Live re-price @ Coolbet | `_MIN_REMAINING_EDGE` ≥ 3% at the live Coolbet price |
| 6 | **Odds floor (per-market)** | `_min_odds_for` — **1x2 2.80 · O/U 1.80** (shared) |
| 7 | Blast-radius | flat €10 (or Kelly), max-stake / max-bets-per-hour / self-pause |

This is the path `edge_floor_backtest.py` validates. It is model-edge (ensemble
vs best book), which is a **different instrument** from Path A's line-shop edge.

---

## Both paths are pre-match only

Neither bets in-play. `coolbet_placer.load_qualified_inplay_bets` exists but is an
**admin override** outside the daemon flow (INPLAY-SHELVED-REVIVE-GATE). Any
"2.80 was an inplay floor" recollection is wrong — 2.80 is the pre-match 1x2 odds
floor (Path A gate 4 / Path B gate 6).

## Config (env, `.env`)

| Env | Meaning | Default |
|---|---|---|
| `COOLBET_MIN_ODDS` | 1x2/default odds floor (O/U=1.80, AH/DNB ungated via `_MIN_ODDS_BY_MARKET`) — **both placers** | 2.80 |
| `COOLBET_RECORD_ALLOWED_MATURITY` | maturity allowlist — **Path B only** | calibrated |
| `COOLBET_MIN_EDGE` / `COOLBET_MIN_REMAINING_EDGE` | global + live edge prefilter — **Path B** | 0.03 |
| `COOLBET_STAKE` | flat stake € | 10.0 |
| per-market edge floors | `_MIN_EDGE_BY_MARKET` (Path B) | 1x2 0.13 · o/u 0.08 · ah 0.05 · dnb 0.05 |
| per-market odds floors | `_MIN_ODDS_BY_MARKET` (both) | 1x2 2.80 · o/u 1.80 · ah/dnb 1.00 |

Per-bot real-money on/off is **not** env — it is the DB table `coolbet_placer_bots`
(`ui_place_enabled` per bot), toggled by a superadmin at `/admin/shadow-bots`
(COOLBET-PLACER-CONTROL). The old `COOLBET_UI_MODEL_EDGE_OU` env flag was removed;
the toggle replaces it. `COOLBET_UI_PLACE_OU=1` (line-shop O/U restore) still exists.

## Not to be confused with

- **`/admin/shadow-bots` page** — mostly a DISPLAY reading `shadow_bets` (many
  experimental bots), where the operator eyeballs picks for MANUAL placement.
  Its `bot_coolbet_value_v1` rows ARE Path A's real-money source; the other
  experimental bots on that page are not placed by anything. **It also carries
  the `Coolbet UI Placer — Control` panel** (COOLBET-PLACER-CONTROL): the
  superadmin toggle that flips `coolbet_placer_bots.ui_place_enabled` and so
  governs which bots this Path-A placer stakes real money on.
- **`/picks` / `/value-bets`** — the 👥 PICKS customer product. Different cohort,
  different purpose.

## Known-open / not-yet-validated

- **`COOLBET-REALMONEY-EDGE-GATE-RECONCILE` (the big one).** Real money is gated
  by `bot_coolbet_value_v1`'s flat **3%** line-shop edge, which has never been
  walk-forward validated the way the model floors (1x2 13% / O/U 8%) were. Decide
  which instrument governs real placement — the line-shop 3% (edge measured at
  Coolbet's own price, arguably the right basis) or the model per-market floors —
  and make it ONE explicit, validated decision instead of an accident of which
  daemon has `--execute`. Until then, our edge-floor tuning does not touch the
  money we stake.
- **Per-market ODDS floor is validated (2026-09-08, `2D-GATE-PER-MARKET-ODDS-FLOOR`)**
  and reaches both placers. Joint edge×odds sweep (executable `simulated_bets`
  ~4.2k, idealized 1x2 104k / O/U 182k, CLV bands): 1x2 profit peaks at odds≥2.8
  (2D gate beats both 1D gates: +€1063 vs edge-only +€563 vs odds-only −€18),
  O/U at odds≥1.8 (+€1663 vs €544 at the old global 2.8 floor). NB validated on
  Path B's `simulated_bets`, not on Path A's line-shop picks.
- **Asian Handicap has no fold-robust floor** — `AH-VIABILITY-REVIEW`. (Path A
  doesn't bet AH anyway.)
- **Which bots place is now a runtime toggle (`COOLBET-PLACER-CONTROL`, 2026-09-08).**
  `coolbet_placer_bots` (DB) ∩ code-level `PLACEABLE_BOTS` is the effective
  real-money allowlist; the placer reads it each run and **fails closed** (places
  nothing) if the read errors. `bot_coolbet_value_v1` seeded ON, `bot_coolbet_ou_model_v1`
  seeded OFF. Superadmin flips it at `/admin/shadow-bots`. The launchd plist can
  move to `--all-enabled` (place every enabled bot in one pass) — not yet done.
- **Real-money automation posture:** Path A already places real money when its
  plist is loaded and the kill switch is clear; Path B is paper. Both are paused
  right now (Imperva tarpit — `launchctl list` shows no Coolbet jobs).
