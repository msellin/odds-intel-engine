# SYSTEM MAP — the one place that explains picks, bots, and every %

> **Direct live-odds books (3, into `odds_snapshots`):** **Coolbet** (Imperva → Mac), **Unibet-Site** (DataDome → Mac), **Epicbet** (Cloudflare → VPS via FlareSolverr, the most robust; 119 markets, EPICBET-ODDS-INGEST-2026-08-27) — plus AF's 13-book feed. We COLLECT 15 market families but model/pick on only 7 (see USE-COLLECTED-MARKETS in PRIORITY_QUEUE).


**This is the index. Read this first; everything else is a deep-dive it links to.**

It exists because the betting system kept getting *harder* to understand every time
we touched a bot, a floor, or the model — the facts were scattered and no single
place was required to stay true. This is that place, and it is **machine-checked**:
the smoke test `SYSTEM-MAP-REGISTRY-NOT-DRIFTED` fails CI if the structured registry
(`workers/registry/bot_registry.py`) disagrees with the live code or DB, or if a bot
here is missing from this doc. So it cannot rot into fiction.

> **Rule (also in CLAUDE.md):** any change to a bot, an edge definition, a floor, or
> a model version updates BOTH `workers/registry/bot_registry.py` AND this file in the
> **same commit**. The drift test enforces it.

---

## 1. The word "edge" means TWO different things

This one overload caused most of the confusion. There are two edges, measured against
two different yardsticks, and **their percentages are NOT comparable**.

| | **Model edge** | **Sharp edge** |
|---|---|---|
| Formula | `cal_prob − 1/book_odds` | `P_sharp − 1/book_odds` |
| Anchor ("fair value") | our **calibrated model** | **Shin-de-vigged Pinnacle** line |
| Typical floor | **13%** (1x2) · **8%** (O/U) | **~3%** |
| Why that floor | the model is *noisier* than the market, so a small edge is mostly model error — demand a big one to filter noise | Pinnacle is *near-true*, so a 3% overlay is a **real** 3% — no need for it to be big |
| Fires… | when our model disagrees a LOT with the book | rarely — Coolbet ≈ Pinnacle, so beating it by 3%+ is uncommon |
| Known failure | at 13% it still adverse-selects longshots → −21% OOS (the trigger bot) | over-strict floor (e.g. 13%) → never fires |

**A 3% sharp edge and a 13% model edge filter to roughly the same strictness** — they
just measure against different rulers. Putting a model floor on a sharp edge (or vice
versa) is the classic mistake; the `Anchor` column in the bot tables below says which
ruler each bot uses.

`P_sharp` comes from `workers/model/devig.py` (Shin's method — removes proportionally
more margin from longshots than a naive proportional de-vig, which matters for 3-way
1x2). See `docs/BETTING_GATE_DECISIONS.md` for how the model floors were decided.

> **The two anchors are NOT fully independent.** The model *itself* ingests Pinnacle
> as input features (`pinnacle_home/draw/away_odds` + sharp-consensus in
> `workers/model/features.py`), so `cal_prob` is already sharp-**informed** — the
> sharp line is an ingredient of the model number, not a separate opinion. Consequence
> for the trigger head-to-head: where the model and sharp anchors agree, part of that
> is the model "knowing" Pinnacle; the sharp anchor's distinct value is where it
> **disagrees** with the model (Coolbet beats Pinnacle on a pick the model didn't flag).

---

## 2. Every active bot, by family

Anchor = which edge it uses (§1). "Money" = whether it can stake **real** money
(must be in `PLACEABLE_BOTS`) or is **paper**. Floors shown are the gate a pick must
clear. This table is generated from the registry — do not hand-edit rows; edit the
registry and regenerate.

### Real-money capable · Coolbet UI placer

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_coolbet_1x2_model_v1` | 1x2 | model | **10%** | 2.80 | **REAL** | **FAVLONG-CUTS-2026-09-09: HOME-UNDERDOGS ONLY** at model edge ≥10% & odds ≥2.80. Home-favs lose, aways aren't fold-robust, draws are a sharp edge the model can't see (§57) → the one robust 1x2 engine is home-underdogs, robust to 10%. Real money, per-bot toggle. (NB the pooled/paper `_MIN_EDGE_BY_MARKET['1x2']` stays 13% — see below.) |
| `bot_coolbet_ou_model_v1` | O/U 2.5 | model | 8% | 1.80 | **REAL** | Places our calibrated model's O/U picks at Coolbet's own price when model edge ≥8% & odds ≥1.80. Real money, per-bot toggle. |

### Trigger engine · model vs sharp anchor (paper)

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_coolbet_trigger_1x2_v1` | 1x2 | model | 13% | 2.80 | paper | Fires when Coolbet's 1x2 price lands in the MODEL trigger window (model edge ≥13% at Coolbet's own odds). Paper. OOS backtest −21% (adverse selection). |
| `bot_coolbet_trigger_sharp_1x2_v1` | 1x2 | sharp | 3% | 1.01 | paper | Sharp twin: fires when Coolbet's 1x2 price beats the de-vigged Pinnacle line by ≥3% (no odds floor — experimental). Paper. Head-to-head vs the model twin. |
| `bot_coolbet_trigger_ou_v1` | O/U 2.5 | model | 8% | 1.80 | paper | Fires when Coolbet's O/U 2.5 price lands in the MODEL trigger window (model edge ≥8% at Coolbet's own odds). Paper. OOS backtest +4.3% not-robust. |
| `bot_coolbet_trigger_sharp_ou_v1` | O/U 2.5 | sharp | 3% | 1.01 | paper | Sharp twin: fires when Coolbet's O/U 2.5 price beats the de-vigged Pinnacle line by ≥3% (no odds floor — experimental). Paper. Head-to-head vs the model twin. |
| `bot_unibet_trigger_1x2_v1` | 1x2 | model | 13% | 2.80 | paper | Stage 3b — Unibet 1x2 model trigger (reads `Unibet-Site` sweep). Paper twin of the Coolbet 1x2 trigger. |
| `bot_unibet_trigger_sharp_1x2_v1` | 1x2 | sharp | 3% | 1.01 | paper | Unibet 1x2 sharp trigger. **Where the DRAW edge the model can't see should surface** (soft-book mispricing vs de-vig Pinnacle, §57). Paper. |
| `bot_unibet_trigger_ou_v1` | O/U 2.5 | model | 8% | 1.80 | paper | Stage 3b — Unibet O/U 2.5 model trigger. Paper twin of the Coolbet O/U trigger. |
| `bot_unibet_trigger_sharp_ou_v1` | O/U 2.5 | sharp | 3% | 1.01 | paper | Unibet O/U 2.5 sharp trigger. Paper. |
| `bot_trigger_1x2_model_v1` | 1x2 | model | 13% | 2.80 | paper | **MERGE-TRIGGER-BOTS 2026-09-11** — book-agnostic MODEL 1x2 trigger: fires when ANY book we place at prices a modelled fixture into the window. Replaces the two 1x2 model twins above. |
| `bot_trigger_1x2_sharp_v1` | 1x2 | sharp | 3% | 1.01 | paper | Book-agnostic SHARP 1x2 trigger. The 3% floor is set EXPLICITLY, not inherited: a sharp edge is measured against a near-true line and is never comparable to a model floor (a 13% overlay on Pinnacle is nearly unobservable — max seen +6.6% — so the bot would simply never fire). |
| `bot_trigger_ou_model_v1` | O/U 2.5 | model | 8% | 1.80 | paper | Book-agnostic MODEL O/U 2.5 trigger. Replaces the two O/U model twins above. |
| `bot_trigger_ou_sharp_v1` | O/U 2.5 | sharp | 3% | 1.01 | paper | Book-agnostic SHARP O/U 2.5 trigger. |

> **Why eight bots became four, and why all twelve are listed here right now.**
> The eight above are 2 anchors × 2 books × 2 markets, but the **book is a venue,
> not a strategy**: `pick_generator` already compares across every book a bot may
> use and records the winner in `recommended_bookmaker`, so book is a column to
> GROUP BY rather than an identity — and the split would have become **twelve
> bots the moment Epicbet joined**. The four merged configs are on the two real
> axes. The eight are **deliberately not retired yet**: the pooled-vs-per-selection
> calibrator measurement is mid-flight, and retiring them now would make that
> comparison span a bot change AND a calibrator change, answering neither. A
> follow-up migration retires them once `trigger_calibrator_watch` pages its
> verdict, taking the active count from 18 to 10.

### Coolbet own-price paper bots

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_ou35_model_v1` | O/U 3.5 | model | 8% | 1.80 | paper | Model-edge O/U 3.5 vs Coolbet's own 3.5 price (own isotonic calibration). Paper. +7.8% not-robust, accruing forward. |
| `bot_corners_paper_shadow_v1` | corners | sharp | 0% | — | paper | Best Betano/Unibet corners price vs de-vigged Pinnacle corners line (sharp edge ≥0%). Forward paper test on executable corners books. |
| `bot_team_total_paper_shadow_v1` | team totals | sharp | 0% | — | paper | Best Epicbet/Betano/Unibet full-match team-total price vs de-vigged Pinnacle line (sharp edge ≥0%). USE-COLLECTED-MARKETS: a market we collect but never modelled; settles from the final score (no coverage gap). Paper, accruing forward. |
| `bot_1h_1x2_paper_shadow_v1` | 1H 1x2 | sharp | 0% | — | paper | Best Epicbet/Betano/Unibet first-half 1X2 price vs Shin-de-vigged Pinnacle 1H triple (sharp edge ≥0%). USE-COLLECTED-MARKETS: a 3-way market we collect but never modelled; settles from the HT score (no gap). Paper, accruing forward. |

### Internal model / strategy validators (paper)

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_v10_all` | mixed | model | — | — | paper | The calibrated reference bot: v10 model across target leagues, tier-adjusted thresholds. Honestly calibrated, +11–13% — the yardstick other bots are read against. |
| `bot_high_roi_global_v2` | 1x2 | — | — | — | paper | 1x2 home/away in Spain/Australia/Iceland, odds 1.50–5.50. Internal paper strategy validator. |
<!-- bot_1x2_specialist, bot_dnb_specialist, bot_summer_specialist RETIRED 2026-09-09 (migrations 323/324) and removed from bot_registry.py:116-119 — do not re-add. -->
<!-- NB: bot generation stores best-of-books odds for these general bots (recommended_bookmaker), NOT the Coolbet/Unibet executable price — the SHADOW-PAGE-ROI-INFLATED gap; per-book executable ROI/CLV is the EXECUTABLE-SHADOW-EVAL work. -->

---

## 3. What each % means on each screen

Same-looking numbers, different meaning per screen. This is the glossary.

| Where you see it | The number | What it actually is |
|---|---|---|
| **/picks** — "edge" | model edge | `cal_prob − 1/odds` at the price the pick was found. **There is NO edge gate on this page at all** — not 13%, not any value. It publishes every model pick from an eligible bot; the gate is at *placement*. (Corrected 2026-09-11: the old wording "NOT gated to 13% here" implied some other floor applied.) Also undocumented until now: a silent `.limit(300)` truncation, and a cohort split — signed-out sees `calibrated` only, signed-in sees `calibrated+beta+active`. |
| **/picks** — "min odds" (public) | break-even | `1/cal_prob` (edge = 0). Below this the bet is −EV under our model. |
| **/picks** — "place ≥ X.XX" (admin only) | placement trigger | the odds a pick must be offered at to clear the **real-money** floor: `1/(cal_prob − edge_floor)`. ⚠️ floor is **10%** for 1x2 home-underdogs (FAVLONG-CUTS), 8% O/U — and these are **hardcoded in TypeScript** (`upcoming-picks.ts`) with no import path to Python, so an engine floor change never reaches this hint. See §4d. |
| **/shadow-bots** — bot "ROI" | realised | settled paper/real P&L at the executable price. Retired bots' losses are in the "including retired" total only. |
| **/shadow-bots** — bot "CLV" | closing-line value | edge vs the closing line — the leading indicator; ROI is noisier at low n. |
| **`value_v1` / line-shop** — "edge ≥ 3%" | sharp edge | `P_sharp − 1/odds`. A different edge from /picks (§1). |
| **pick_triggers** — `cal_prob` | anchor prob | model prob for `model_*` strategies; **de-vigged Pinnacle prob** for `sharp_*` strategies. |

---

## 4. The real-money gate stack (Coolbet own-betting)

**Rewritten 2026-09-11 after a full gate audit.** The previous version of this
section described gates that do not exist on this path, omitted the largest block
of gates that do, and claimed a single-source-of-truth that is not true. Corrections
are called out inline so the old claims are not silently replaced.

Full detail: `docs/COOLBET_OWN_BETTING.md`. Recurring failure patterns:
`docs/RELIABILITY_LEDGER.md`.

### 4a. There are TWO real-money paths, not one

| Path | Entry | Status |
|---|---|---|
| **Coolbet UI placer** | `scripts/place_coolbet_ui.py --execute` | live, hourly 06-21 UTC |
| **Best-price router** | `workers/automation/best_price_router.py::route` | built, **owner-gated OFF** (`ROUTER_ALLOW_REAL` unset) |

⚠️ **Previously undocumented.** The router can place at **Coolbet OR Unibet-Site**
(`PLACEABLE_BOOKS`), so "Coolbet own-betting" no longer describes the whole surface.
It reuses `place_coolbet_ui`'s gate functions rather than copying them — the only
place in the codebase that pattern is followed.

### 4b. The UI placer, in execution order

**Run-level** (`scripts/place_coolbet_ui.py`)

| # | Gate | Effect if failed |
|---|---|---|
| R1 | `single_run_lock()` flock | SKIP the run |
| R2 | `effective_allowlist() = PLACEABLE_BOTS ∩ ui_place_enabled_bots()` — DB read **fails CLOSED** | bot forced to **dry-run**, not an error |
| R3 | session alive / `cdp_auto_login` | **abort**, Telegram lockout alert |
| R4 | `detect_block()` (Imperva) | **abort** — do not retry or re-login |
| R5 | account verify `fetch_account_holds()` — **fails CLOSED** | every bot forced dry-run for the run |

**Per-pick**

| # | Gate | Value | Effect |
|---|---|---|---|
| P1 | `already_placed(shadow_bet_id)` | — | SKIP, no audit row |
| P2 | pick already on the Coolbet account | — | rejected + audit row |
| P3 | kickoff cutoff | `KICKOFF_CUTOFF_MIN = 3` min | rejected, **no audit row** |
| P4 | per-market odds floor | 1x2 **2.80** / O/U **1.80** | rejected |
| P5 | `exposure_conflict()` — exact dup, same market FAMILY, per-match caps | `MAX_BETS_PER_MATCH=2`, `MAX_STAKE_PER_MATCH=20` | rejected |
| P6 | daily caps | `MAX_BETS_PER_DAY=80`, `MAX_STAKE_PER_DAY=800` | **ABORTS the whole run** |

⚠️ **Correction.** The old step 6 compressed P1-P6 into the phrase "blast-radius caps"
and named none of the numbers. The daily caps were raised 20→80 / 200→800 on
2026-09-05 and this map never recorded it.

**Inside `stage_bet`** (`workers/automation/coolbet_ui_placer.py`)

Search → match → open → read prices → resolve outcome → **min-odds gate** → drift →
stake (verified by read-back) → slip → place. Every exit writes exactly one
`coolbet_placement_attempts` row; nothing here raises to the caller.

| Gate | Note |
|---|---|
| **min-odds** `outcome.odds < min_odds_for(bet, threshold)` | `min_odds_for = 1/(cal_prob − threshold)` — the GATE floor, not break-even (`1/cal_prob`) |
| odds-drift `max_odds_drop_pct` | ⚠️ **defaults to 100.0 and `place_for_bot` never overrides it — this gate is effectively DEAD.** Open decision. |
| `is_placement_paused()` | ⚠️ checked HERE, i.e. *after* the stake is typed — see 4c |
| single-leg `slip_ticket_count() != 1` | refuse |
| balance-delta confirmation | "confirm by evidence, never by absence of an exception" |

### 4c. Corrections to the old step list

- ⚠️ **Old step 3 was wrong.** `place_coolbet_ui.py` checks **neither**
  `placement_paused` nor `daemons_paused` at run level. `placement_paused` is read
  only inside `stage_bet`, *after* search, price read and stake entry — the kill
  switch fires late. `daemons_paused` is not checked on this path at all.
- ⚠️ **Old step 4 described an edge gate this script does not have.**
  `place_coolbet_ui.py` contains **no edge comparison**. The floor reaches money only
  indirectly, as the min-odds translation above. Until 2026-09-11 that translation
  **failed OPEN**: when no floor was computable the gate was skipped and the bet
  placed ungated — including for picks whose probability was at or below the bot's
  threshold, i.e. those that could never clear it at any price
  (`PLACER-EDGE-GATE-FAILED-OPEN`). It now refuses.
- ⚠️ **`maturity_label` does NOT gate the UI placer.** It gates the Mac daemon, the
  Telegram public channel, the mirror jobs and every web surface — not this path.
  Safe today only because `PLACEABLE_BOTS` is hardcoded.
- ⚠️ **`load_picks` has no `retired_at` / `is_active` check**, unlike
  `coolbet_placer.load_qualified_bets`. Same reason it is currently safe.

### 4d. The floors — and where they are NOT the single source

The policy is unchanged and still correct: pooled `_MIN_EDGE_BY_MARKET` = **13% 1x2 /
8% O/U**; the real-money 1x2 bot takes the **FAVLONG-CUTS** exception —
**home-underdogs (home, odds ≥ 2.80) at 10%**, home-favs and aways excluded, draws to
the sharp triggers. Odds floors **2.80 / 1.80**.

Since 2026-09-11 the comparison itself is shared, not just the number:

```python
clears_edge_floor(market, selection, odds, edge)   # workers/automation/coolbet_placer.py
```

Sharing only the FLOOR proved insufficient twice in one day — once via a
selection-blind floor (home-underdogs placed but never signaled), once via a
Decimal-vs-float comparison that silently dropped every pick sitting exactly ON its
floor. See `RELIABILITY_LEDGER.md`.

⚠️ **The old closing claim was false and is withdrawn.** It read: *"The floors in
steps 4-5 live in `coolbet_placer.py` and are the single source the /picks 'place ≥'
hint, the trigger bots, and this map all read."* Audited 2026-09-11:

| Policy | Copies | Where |
|---|---|---|
| Edge floors 0.10 / 0.08 | **6 → 3** | `_MIN_EDGE_BY_MARKET`, `_MODEL_1X2_HOME_FLOOR`, `BOT_THRESHOLDS`; the two mirrors' `EDGE_FLOOR` are now *derived* readouts (2026-09-11 PICK-GENERATOR-DELEGATION — the modules hold no gate of their own), and `upcoming-picks.ts` is generated from Python |
| Odds floors 2.80 / 1.80 | **4 → 2** | `_MIN_ODDS_BY_MARKET`, `MIN_ODDS_FOR_PLACEMENT`; the 1x2 mirror's inlined SQL floor is GONE with its SQL, and `upcoming-picks.ts` is generated |
| Home-underdog rule | **3 → 2 implementations** | `min_edge_for_pick` (Python) and the generated `upcoming-picks.ts` (TypeScript). The shadow-mirror SQL copy is gone: the rule is now `selections=("home",)` on a `BotConfig`, gated by the one Python predicate. |

**Status of each copy (updated 2026-09-11):**

- ✅ **Python is consolidated.** `EDGE-FLOOR-ALL-CALLERS` routed the last three
  holdouts through `clears_edge_floor`: the live re-eval (hand-rolled `<`), the
  in-play path (still on the selection-BLIND `_min_edge_for`), and
  `best_price_router.decide_book` (per-bot threshold only — which made the newest
  real-money path the MOST permissive on aways and home-favs, the exact selections
  FAVLONG-CUTS excluded). Rule: **two policies, both must pass, stricter wins** —
  the per-bot threshold AND the market/selection floor.
- ✅ **The frontend now DERIVES its floors.** `/picks` used to hardcode `0.1/2.8`
  and `0.08/1.8` in TypeScript with no import path to Python, so an engine floor
  change never reached the published "place ≥" hint readers act on. The engine now
  generates `odds-intel-web/src/lib/generated/engine-floors.ts` via
  `scripts/gen_frontend_floors.py`; smoke `FLOORS-ONE-SOURCE-CROSS-LANGUAGE` fails
  CI when it drifts. The generated file also carries a **parity fixture**
  (input → floor, computed by the real Python predicate) because constants
  agreeing while the RULE drifts is how these paths diverged before — and nearly
  did again: a first draft of the TS branch published 3.03 where the engine clears
  at 2.80, caught by sweeping `clears_edge_floor` across cal_prob 0.11-0.60.
- ✅ **The shadow mirrors now derive too** (2026-09-11, the last copies).
  `coolbet_model_1x2_shadow.py` and `coolbet_model_ou_shadow.py` took their
  defaults from re-typed literals (`os.getenv(..., "0.10")` / `"0.08"`) that only
  *happened* to equal the registry, with nothing keeping them in step; the 1x2
  mirror also inlined `>= 2.80` INSIDE its SQL, where no constant could reach it.
  Defaults now derive from `_MODEL_1X2_HOME_FLOOR` / `_MIN_EDGE_BY_MARKET['o/u']`
  / `_min_odds_for('1x2')`, and the odds floor is a bound parameter. Env overrides
  remain for experiments. The o/u mirror **raises** if the registry says the market
  is retired (`None`) rather than substituting a number — a paper bot quietly
  selecting on a resurrected floor is the same silent-wrong-floor failure in
  miniature.
- ✅ **…and then the mirrors stopped holding a mechanism at all**
  (2026-09-11, PICK-GENERATOR-DELEGATION). Deriving the floors removed the
  *drift*; it did not remove the *second copy of the loop*, and the loop was the
  reason the two mirrors diverged in the first place — one pre-filtered on the
  pipeline's odds (dropping Nancy v Reims on Betano's 3.15 while Coolbet was live
  at 3.25 and clearing), the other applied no odds floor at all. Both modules are
  now ~120 lines of entry point and explanation: the mechanism is
  `pick_generator.generate` (one copy, for every bot) and each bot is a
  `BotConfig` in `bot_configs.py`. `EDGE_FLOOR` / `MIN_ODDS` survive as *derived
  readouts* via the generator's own `_floors`, so a number reported there cannot
  differ from the number that gates a pick. Run `python3 scripts/bots_describe.py`
  to see every bot's gates side by side.

**All copies from the 2026-09-11 audit are now closed** — Python callers, the
frontend, and the shadow mirrors. What remains is not duplication but *policy*:
which gates should become configurable (phase 3), and with what bounds.

**Design rule going forward:** callers pass **who they are** (market, selection,
odds) — never their own floor %. Passing floors as arguments keeps every copy and
merely relocates the duplication.

### 4e. Gates BEFORE any of this (generation)

This map used to jump straight from bots to placement. Far more picks are dropped
upstream, in `workers/jobs/daily_pipeline_v2.py`: `ACCESSIBLE_BOOKMAKERS`
(Coolbet/Betano/Unibet/Epicbet), OU-PIN-REQUIRED (no Pinnacle reference ⇒ the O/U
selection is dropped for every book), the outlier multipliers, the Pinnacle veto gap
(0.12; 0.22 AH/DC), the anchor-gap mid-band bump, a hardcoded Scottish-Premiership
skip, per-bot tier/league/market filters, and the min-edge + odds-range gate at
`:3527`. Detail lives in that file; named here so the map stops implying placement is
where filtering begins.

---

## 5. Deep-dive docs (this map links out; it does not duplicate them)

- `docs/BETTING_GATE_DECISIONS.md` — how the model floors (13%/8%) were decided, the canonical backtest method, why runs disagreed.
- `docs/BOOK_AGNOSTIC_EDGE_ENGINE.md` — the trigger engine (Stage A windows + Stage B matcher), the −21% adverse-selection finding.
- `docs/COOLBET_OWN_BETTING.md` — the full own-betting flow and gate stack.
- `docs/ANALYSIS_GOTCHAS.md` — line-shop mirage (§52), single-book vs best-of-books (§55), CLV-vs-ROI variance.
- `workers/registry/bot_registry.py` — the structured source of truth this map is built on.

---
*Last verified against code+DB by `SYSTEM-MAP-REGISTRY-NOT-DRIFTED` on every push.*
