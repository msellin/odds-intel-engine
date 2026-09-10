# Betting Architecture — how picks are generated and how bets are made (multi-bookmaker)

**Single source of truth for the whole betting data flow**, across BOTH directions
(👥 PICKS = what customers see, 🤖 OWN = what we stake) and ALL bookmakers (Coolbet
live; Unibet in progress; future books). Written 2026-09-09 for COOLBET-PICK-TABLE-AUDIT
after a full code+DB trace, because the flow had drifted into "which table do picks/bets
come from?" confusion. If you change generation, a surface, or a placement path, update
this file in the same commit.

Companion docs: `docs/SYSTEM_MAP.md` (the index + the two edges), `docs/COOLBET_OWN_BETTING.md`
(the Coolbet real-money placer specifics), `docs/BOOK_AGNOSTIC_EDGE_ENGINE.md` (the trigger
engine design). This doc is the **book-agnostic** umbrella they hang under.

---

## 0. The one-paragraph summary

One model pipeline generates picks **once per fixture** and writes them to **`simulated_bets`**,
pricing each at the **best odds across the books we can actually bet** (`ACCESSIBLE_BOOKMAKERS`).
Those rows feed **everything customers see** (`/picks`, Telegram, `/performance`, landing).
For **our own real money**, a second layer re-projects a filtered subset into **`shadow_bets`**
(the placeable bots) which the **real-money UI placer** stakes at one book. A parallel
**trigger engine** (`pick_triggers` → `pick_trigger_matcher`) is the *book-agnostic* future:
it already computes book-independent price windows and evaluates each book's own price — it is
the thing a multi-bookmaker setup grows from. The mess this doc exists to remove: **three market
spellings, two placers reading two tables, a `real_bets` ledger that mixes real+paper, and a
public `/performance` that shows `simulated_bets` (not the rows we actually stake).**

---

## 1. GENERATION — how a pick is produced (one path, one table)

**Entry point:** `workers/jobs/daily_pipeline_v2.py::run_morning()` (scheduler jobs ⑤ Betting 06:00
and ⑨ Betting Refresh; a 30-min `_shadow_run` mirrors it in shadow mode). `workers/jobs/betting_pipeline.py::run_betting()` is the wrapper.

| Step | What happens | Where |
|---|---|---|
| Odds basis | For each market/selection, take the **MAX price across `ACCESSIBLE_BOOKMAKERS`** (a best-of-books line-shop, NOT one book, NOT a vig consensus). Record which book held it as `recommended_bookmaker`. | `daily_pipeline_v2.py:2234-2236` |
| Calibrate | Raw model prob → `cal_prob = calibrate_prob(...)` (isotonic/Platt), shrunk toward the **Pinnacle** sharp anchor. | `:3502-3512` |
| Edge | **`edge = cal_prob − 1/odds`**, where `odds` = the best-accessible price above. | `:3524` |
| Gate (generation) | Per-**bot**, per-**tier**, per-**market** `edge_thresholds` + tier bumps + Pinnacle-disagreement veto. **NOT the 13%/8% floor** — that's placement-side (§4). For 1x2 the floor is split **fav vs long** (`:3375`): a **home** pick with odds `< 2.0` uses `1x2_fav` (bot_v10 tier-1 = **8%**); **every draw, every away, and any home pick ≥ 2.0** uses `1x2_long` (**12%**) — a favourite–longshot-bias correction (more edge demanded on longshots, where the model calibrates worse). So there is no flat 10% floor; 9%-edge picks you see are home favourites clearing 8%. | `:3375-3384`, `:62-995` |
| Write | `store_bet()` → **`INSERT INTO simulated_bets`**, under **every** bot in `BOTS_CONFIG` (each its own `bot_id`; `bot_v10_all` is the flagship). Columns: `odds_at_pick` (=best-accessible), `edge_percent`, `calibrated_prob`, `recommended_bookmaker`. | `:3821` → `supabase_client.py:2110` |

**The two edges** (see SYSTEM_MAP §1): the **model edge** above (`cal_prob − 1/book_odds`) is the
generation/placement ruler. The **sharp edge** (`P_sharp − 1/book_odds`, P_sharp = Shin-de-vigged
Pinnacle) is a *second* anchor used only by the trigger engine (§3b). They are different rulers —
never compare their numbers.

**Anchor note:** Pinnacle drives calibration, the veto, CLV and de-vig, but **Pinnacle is not
placeable** (not in `ACCESSIBLE_BOOKMAKERS`). `PRICE_REFERENCE_BOOKMAKERS = ACCESSIBLE ∪ {Pinnacle}`.

`ACCESSIBLE_BOOKMAKERS = {Coolbet, Betano, Unibet, Epicbet}` (`daily_pipeline_v2.py:1072-1130`).
Pinnacle/Marathonbet/10Bet/888Sport removed (EMTA-blocked); `Unibet-Kambi` removed (feed-divergent).

---

## 2. THE TABLES — what holds picks and bets (five + one)

| Table | Written by | Read by | Role |
|---|---|---|---|
| **`simulated_bets`** | the generation pipeline (§1), all bots | **/picks · Telegram · /performance · landing** + the paper daemon | The **primary pick ledger**. One row per bot×fixture×market×selection. Market spelled `o/u` / selection `over 2.5`. Bankroll/EV aware. |
| **`shadow_bets`** | mirror jobs (§3) + trigger matcher (§3b) + BET-TIMING monitor | via the view ↓ | Secondary ledger holding the **placeable** model-edge bot rows + all trigger/shadow bots. Market re-spelled `over_under_25` / selection `over`. |
| **`shadow_bets_unique`** (VIEW) | — (DISTINCT ON bot×match×market×selection; migration 298) | **the real-money UI placer** + `/admin/shadow-bots` | Canonical dedup read of `shadow_bets`. |
| **`pick_triggers`** | Stage A (`pick_triggers.py`) | Stage B matcher | **Book-independent** price windows per fixture×market×selection×anchor. The multi-book core (§3b). |
| **`real_bets`** | the real UI placer (real, balance-confirmed) **+ the paper daemon (`record=True,execute=False` → phantom) + manual reconciliation** | `/performance` overlay (admin), leaderboard | **Placement ledger — dual-purpose.** A row alone does NOT prove money moved. |
| `coolbet_placement_attempts` | the UI placer (`coolbet_ui_placer.record_attempt`) | dedup + audit | The **only** proof of a real stake: `outcome='placed'`. |

---

## 3. RE-PROJECTION — simulated_bets → shadow_bets (the placeable bots)

Two mirror jobs (added 2026-09-08) route the calibrated model's own picks through the proven
Coolbet placer. They **carry the best-accessible edge/price straight through** (do NOT recompute
against a specific book — they trust the placer to re-check the live book price downstream):

- `workers/jobs/coolbet_model_ou_shadow.py` → bot `bot_coolbet_ou_model_v1`, `EDGE_FLOOR=0.08`,
  vocabulary convert `over 2.5 → over_under_25/over` (2.5/3.5 only).
- `workers/jobs/coolbet_model_1x2_shadow.py` → bot `bot_coolbet_1x2_model_v1`. **FAVLONG-CUTS-2026-09-09:
  HOME-UNDERDOGS ONLY** — `selection=home AND odds≥2.80 AND edge≥0.10` (home-favs lose, aways aren't
  fold-robust, draws are a sharp edge; §57 + BETTING_GATE_DECISIONS "1x2 by type"). No vocabulary conversion.

Also writing `shadow_bets`: `ou35_model_shadow.py`, `corners_paper_bot.py`, and the pipeline's
`bulk_store_shadow_bets()` (BET-TIMING-MONITOR — every bot at every refresh, flat €10).

## 3b. THE TRIGGER ENGINE — the book-agnostic paper engine (built; Coolbet + Unibet-Site, 8 bots)

This is the component a multi-bookmaker setup grows from. It is already designed correctly; it is
just not populated with non-Coolbet books.

- **Stage A — `workers/jobs/pick_triggers.py`** writes **book-INDEPENDENT** windows into
  `pick_triggers`: `min_odds = max(1/(cal_prob − edge_floor), odds_floor)`, `max_odds = min_odds×1.6`.
  Floors imported from `coolbet_placer._min_edge_for/_min_odds_for` (can't drift). **Two anchors
  side by side**, `strategy` column = `model_*` (calibrated model) or `sharp_*` (Shin-de-vig Pinnacle).
  Markets: 1x2 + O/U 2.5.
- **Stage B — `workers/jobs/pick_trigger_matcher.py`** joins each book's latest `odds_snapshots`
  against the window and writes `shadow_bets`, **recomputing edge at that book's OWN price**
  (`edge = cal − 1/price`, `:82`), one bot per `(book × market × anchor)`. `BOOK_MARKET_BOTS`
  now enumerates **Coolbet + Unibet-Site** (8 bots; Unibet added 2026-09-09, Stage 3b). Adding a
  further book = more rows in that dict — the edge math is already per-book, cohort is book-aware.

---

## 4. SURFACES — exactly what each reads and gates on

| Surface | Table | Cohort / gate | Edge floor | Price basis | Real money? |
|---|---|---|---|---|---|
| **/picks** (`upcoming-picks.ts`) | `simulated_bets` | maturity `['calibrated']` public / `+beta,active` signed-in; retired excl.; date window | **NONE** (dedup highest-edge; shows a per-pick break-even `min_odds`; `placeMinOdds` 13/8+2.8/1.8 admin-only) | `odds_at_pick` | no |
| **Telegram** (`daily_pipeline_v2:3992` + `notify/telegram.py`) | `simulated_bets` (the `_tele_bets` just-written rows) | **each bot's OWN config threshold**, all cohorts (broader than /picks); admin alert + Pro broadcast | per-bot | `odds_at_pick` | no |
| **/performance + landing** (`engine-data.ts:3448`) | **`simulated_bets` ONLY** | `['calibrated','beta','active']`, markets `1x2/o/u/over_under_25/btts`, `result∈(won,lost)` | **NONE** on headline | `odds_at_pick_live` (executable, MAX-across-accessible), unplaceable rows excluded, flat €10 | **no — excludes `real_bets`** |
| **/admin/shadow-bots** | `shadow_bets_unique` | placeable + all shadow bots | placer floors | live | shows the real-money bots |

**Two disagreements this creates (the "messy" symptom):**
1. **/performance shows `simulated_bets` (bot_v10_all etc.), NOT the `shadow_bets` rows we actually
   stake.** Public track record ≠ real-money track record (except the admin `real_bets` overlay).
2. **Floors disagree by surface:** /picks = none, /performance = none, Telegram = per-bot,
   placer = 13/8. `PICKS-GRADING-ROLLOUT` is the intended reconciler (A=13/8, pin /performance to A).

---

## 5. BET-MAKING — the three placement paths + both books' schedules

| Path | Reads | Schedule | Real money? | Writes |
|---|---|---|---|---|
| **Paper daemon** (`coolbet_mac_daemon` → `coolbet_placer.load_qualified_bets`) | `simulated_bets` | every 30 min (was stale since 2026-08-23 — verify) | **NO** — `execute=False`; but `record=True` writes **phantom `real_bets`** | `real_bets` (phantom) |
| **Real-money UI placer** (`place_coolbet_ui.py --all-enabled --execute` → `coolbet_ui_placer.place_and_record`) | `shadow_bets_unique`, for `PLACEABLE_BOTS ∩ coolbet_placer_bots(ui_place_enabled)` | launchd hourly **06:00–21:00 UTC** | **YES** (balance-confirmed) | `real_bets` (real) + `coolbet_placement_attempts` |
| **Unibet placer** (`unibet_placer.place_bet`) | **no pick table — args only** | **none — manual** | manual only | `real_bets` |

`PLACEABLE_BOTS = {bot_coolbet_ou_model_v1, bot_coolbet_1x2_model_v1}`. Gates (Coolbet placer):
maturity → per-market edge floor (`_MIN_EDGE_BY_MARKET`: 1x2 0.13 / o/u 0.08) → per-market odds
floor (`_MIN_ODDS_BY_MARKET`: 1x2 2.80 / o/u 1.80) → live-edge re-check → single-leg → account-verify
dedup. Pre-match only.

**Readiness surface (READ-ONLY) — "can I place real money right now?"**
`workers/automation/coolbet_control.placement_readiness()` aggregates every
placement gate into one dict: `can_place_now` (bool) + `blockers` (list). It is
`True` only when NOT `placement_paused`, NOT `daemons_paused`, `session_healthy`,
JWT valid (`jwt_exp_at` in the future), ≥1 bot `ui_place_enabled`, and the Mac
daemon tick is fresh (≤60 min). The decision is the pure helper
`_evaluate_readiness(state, bots, now)` (DB-free, unit-tested). CLI:
`python3 -m workers.automation.coolbet_control --status`. It is surfaced in the
daily Telegram summary as a `PLACEMENT READY ✅ / BLOCKED ⛔ (reasons)` line
(`coolbet_daily_summary`). This surface **only reports** the state the placer's
own gates already enforce — it never places, toggles, or changes a floor.
Smoke: `COOLBET-PLACEMENT-READINESS`.

---

## 6. WHERE IT'S HARD-COUPLED TO ONE BOOK (what multi-book must generalize)

1. **Placement gates + placer stack are Coolbet-only** (`coolbet_placer.py`, `coolbet_ui_placer.py`,
   the whole `coolbet_*` daemon/session stack).
2. **Mirror shadow jobs carry the best-accessible price**, not a per-book price — they assume the
   Coolbet placer re-checks the live Coolbet price downstream. Wrong basis for a second book.
3. **`pick_trigger_matcher.BOOK_MARKET_BOTS` = Coolbet only** — the one component already per-book.
4. **`AH-NO-QUARTER`** filter hard-codes Coolbet's full/half-line support.
5. **Edge is computed on the MAX-across-accessible price** in the primary pipeline; only the trigger
   engine computes a true per-book edge.

---

## 7. TARGET — the multi-bookmaker design

The end state the owner asked for: **generate once, price per book, place once at the best clearing
book, with one placement-of-record — adding a 3rd book is config, not a rewrite.**

```
                    ┌─ generation (unchanged): model → cal_prob, per fixture ─┐
                    ▼                                                          │
   pick_triggers  (book-INDEPENDENT window per fixture×market×selection×anchor) │  ← Stage A, exists
                    ▼                                                          │
   per-book matcher: for each book in BOOKS, evaluate its OWN odds_snapshots    │  ← Stage B, exists
      against the window → per-book edge → candidate (book, price, edge)        │     (Coolbet only today)
                    ▼
   UNIFIED best-price ROUTER: across all books whose candidate clears the gate, │  ← best_price_router.py (execute-wired 2026-09-10; real money owner-gated)
      pick the BEST price → route to THAT book's placer → place ONCE            │     (to build)
                    ▼
   placer REGISTRY: {Coolbet: coolbet_ui_placer, Unibet: unibet_placer, …}      │  ← per-book executor
                    ▼
   ONE placement-of-record  (real_bets, with `bookmaker` + a proof flag)        │  ← no phantom rows
```

**Gate on PER-PLACEABLE-BOOK edge, not best-accessible (owner, 2026-09-09).** Today the
mirror jobs gate on `simulated_bets.edge_percent` = edge at the MAX odds across ALL accessible
books (Coolbet, Betano, Unibet, Epicbet) — but we only place at some of them. Two picks get
missed: (1) the best book is Betano/Epicbet @≥13% but Coolbet is <13% → mirror passes, placer
live-re-check skips → placed nowhere; (2) best-accessible is Coolbet @<13% → not mirrored →
shown on /picks, placed nowhere. Both are picks a PLACEABLE book might have taken. The router
fixes it: for each pick, compute the edge at EACH placeable book's OWN live odds; place at any
book that clears the floor (edge ≥ 13%/8% ⟺ odds ≥ that book's `min_odds`), choosing the best
such price. Gate = `max(edge@Coolbet, edge@Unibet) ≥ floor`, on live per-book odds — NOT the
best-accessible edge. This is also what lets a soft book rescue a sub-floor reference pick
(Derby: 11% at reference, Grade-A at Unibet 3.50). (The /picks 8–13% display gap is a separate,
honesty concern → PICKS-GRADING-ROLLOUT.)

**Router decision rule (owner, 2026-09-09) — verbatim, the invariant to enforce:**
for each `(match, market, selection)`:
1. both books have a gate-clearing price → place **once**, at the **better** price;
2. only one book has a price (or only one clears) → place **once**, at that book;
3. **never two bets on the same `(match, market, selection)`** — a cross-book exposure
   check runs before every placement (`match_exposure`/`real_bets` span books), so once
   it is placed at either book the other is blocked.

**Coordination — how the "two placers" decide who bets (owner Q, 2026-09-09):** they DON'T
coordinate. There is **one router (the decider) and two dumb executor arms**
(`coolbet_ui_placer`, `unibet_placer`). Both arms run on the same Mac (both drive
CDP-Chrome), so the router is one local process calling two functions — no lock, no
inter-process messaging, no time-window race. Per pick: read both books' fresh odds → keep
those that clear the gate → if none, skip → if exposure already exists on
`(match,market,selection)`, skip → else place at the **max-odds** candidate's executor (tie →
a fixed, configurable book preference) → write ONE placement-of-record. Edge cases fall out:
one book's session down = it has no fresh odds = the router places at the other book (if it
clears) + fires that book's lockout alert, so a down book never costs the bet; both down =
skip + alert. Double-bet is impossible (one decision, one record, cross-book exposure check).
The rejected alternative — two independent placers coordinating via a DB claim
(`UNIQUE(match,market,selection)` + `INSERT ON CONFLICT DO NOTHING`) — is first-come, not
best-price, and adding a two-phase odds window to fix that just adds race surface. One router
deciding is strictly simpler and more robust.

Design rules for the target:
- **One canonical market vocabulary** (`1x2`/home,draw,away · `over_under_25`/over,under · …) used by
  every writer and reader. Kill the `o/u`↔`over_under_25`↔`over 2.5` triple-spelling. A single
  `canon_market()`/`canon_selection()` helper, smoke-tested, is the source.
- **Per-book edge** is the basis for placement selection (the trigger matcher already does this);
  the best-accessible line-shop stays only for the *published /picks* number (👥), never for 🤖 routing.
- **One placement-of-record.** `real_bets` holds only genuinely-placed bets, tagged with `bookmaker`
  and a proof flag (a `coolbet_placement_attempts`-style confirmation per book). Paper stays in a
  paper table, never in `real_bets`.
- **Placer registry**, not a Coolbet-only path: the router picks the book, looks up its placer,
  each placer fails closed independently, dedup is cross-book (`match_exposure` already reads
  `real_bets` across books).
- **Per-book config** (books list, gates, line-support like AH-NO-QUARTER, PLACEABLE set) lives in
  one registry keyed by book, so a new book is a config row.

---

## 8. MIGRATION PLAN — current → target (staged; real-money steps are OWNER-GATED)

Each stage is independently shippable and testable. **Stages touching real money or `/performance`
require explicit owner go before the cutover.**

| # | Stage | Risk | Owner-gate |
|---|---|---|---|
| 1 | ✅ **DONE 2026-09-09** — `workers/canonical_market.py` is the one source: `market_family()` (floor-key family, = the old `_canon_market`) + `ou_selection_to_storage()` (line encoding, = the old `_convert`). `coolbet_placer._canon_market` and `coolbet_model_ou_shadow._convert` now delegate to it; smoke `CANONICAL-MARKET-VOCAB` proves behaviour-preservation. (Frontend `coolbet-edge.ts` stays a documented mirror — TS can't import the Python module.) | low | no |
| 2 | ✅ **DONE 2026-09-09** — `real_bets.placed_real` (migration 325): TRUE=real (UI-placer balance-confirmed / reconciled manual bet), FALSE=paper (daemon record=True/execute=False), NULL=legacy. Every writer tags at write time (`store_real_bet` param; `coolbet_placer` ×3 = `execute`; UI placer = True; reconcile = TRUE). Real placer's dedup + admin overlays (`getRealBets`/`getPlaceableBets`) exclude `placed_real IS FALSE` — a paper row can no longer block a real bet. Backfill: 123 proven-real tagged TRUE, 847 legacy left NULL (not falsely marked paper — some are real manual bets). Public /performance unaffected (reads simulated_bets). Owner chose tag-not-delete. Smoke `REAL-BETS-PLACED-REAL`. | med | ✅ owner-approved |
| 3 | **Unibet PAPER build-out (NOT a real-money change).** **3a ✅ DONE 2026-09-09** — broad `Unibet-Site` odds sweep: `unibet_odds_feed.run_bulk` enumerates site events per country (root quickbrowse → country RNs → country lobby, all via injected fetch WITH the SPA headers), fuzzy-matches DB fixtures, injected-fetches each contest-page → `store_book_odds_snapshots('Unibet-Site')`. Rate-limited (1.2s, cap 180, abort on repeated blocks), fail-safe. Scheduled on the Mac (`com.oddsintel.unibet-site-odds`, :15/:45; non-disruptive injected fetch). **SELF-HEALS the session (2026-09-10):** `run_bulk` calls `unibet_browser_sync.ensure_logged_in()` before each sweep — auto-logins via the modal if logged out (rate-limited 30min), verified working through DataDome; no manual login needed unless SMS/2FA or creds-missing (then a deduped alert). Validated: 100 rows / 20 fixtures / 0 blocks; broad Site-vs-Kambi divergence now flowing. Smoke `UNIBET-SITE-SWEEP`. **3b ✅ DONE 2026-09-09**: added the 4 Unibet twins to `pick_trigger_matcher.BOOK_MARKET_BOTS` (reads `Unibet-Site`; book-aware cohort `unibet_trigger`; migration 326 + registry + map). Live: 12 paper picks emitted. The DRAW edge the model can't see surfaces on the sharp anchor here (§57). **3c ✅ DONE 2026-09-09**: `resolve_event_url(home,away,date)` — search API + fuzzy match → constructible `/…/<slug>/<contestKey>` URL (routes on the contestKey; validated live → contest-page 200). The placer's executor arm can be handed a resolved URL for any fixture; actual real-money placement stays Stage 5/owner-gated. **NOT auto-wired to place.** Coolbet real-money 2-bot set untouched. | low-med (all paper) | no (paper only) |
| 4 | **DATA-GATED PROMOTION (future, owner decision).** Once a candidate bot (a Coolbet/Unibet trigger, a line-shop bot) has enough SETTLED paper picks to calibrate and compare against the 2 live Coolbet bots, the owner decides: promote / merge / route / leave paper. Only what the owner promotes ever reaches a placer. | — | **yes — the whole point is the owner's call** |
| 5 | 🔄 **EXECUTE WIRED 2026-09-10 (dry-testable; real money still owner-gated)** — `workers/automation/best_price_router.py`: per real-money candidate, reads both books' fresh odds, gates each at its OWN price (edge≥BOT_THRESHOLDS + odds≥floor), routes to the better clearing book, cross-book dedup (`placed_real IS NOT FALSE`). Validated on the Derby case (→ Unibet @3.50, +13.6%) + edge cases. **`route()` now has three modes:** (a) default DRY-RUN report; (b) **`stage=True` DRY-TEST-IN-ACTION** — dispatches each routed pick to the winning book's executor arm in its OWN stage mode (Coolbet `coolbet_ui_placer.stage_bet`, Unibet `resolve_event_url`→`unibet_placer.place_bet`), driving the real live slip and STOPPING before the place click (no money moves); (c) `execute=True` REAL MONEY, **double-gated** — refused to report-only unless env `ROUTER_ALLOW_REAL` is truthy, so a stray execute can't move money. Each arm re-reads LIVE odds and gates on min_odds at dispatch. Selection→outcome mapping: 1x2 home/away→team, draw→`X`; o/u→`Üle`/`Alla`. `--limit N` caps how many picks are driven. Smokes BEST-PRICE-ROUTER + BEST-PRICE-ROUTER-EXECUTE-WIRING. **MONITOR NOW LIVE (2026-09-10):** the router runs REPORT-ONLY on a schedule (launchd `com.oddsintel.best-price-router-monitor`, :20/:50; `best_price_router.monitor()`) — it checks BOTH books on every real-money candidate, logs the routing, and Telegram-alerts when a pick is better/ONLY at Unibet (which the Coolbet-only placer misses). No money moves. This makes 'always check both' real before the cutover. **Remaining for real cutover:** owner sets `ROUTER_ALLOW_REAL`, a placer registry per book, one placement-of-record, and a dry-run window review — build ONLY for bots promoted in Stage 4. **⚠️ when the Unibet arm runs unattended it needs the same `_ensure_session_live` heartbeat as the Coolbet daemon (COOLBET-SESSION-FREEZE-FIX). PARTIALLY DONE:** the odds feed now self-revives the Unibet session via `ensure_logged_in()` each sweep, so the injected-fetch session stays logged in; the placer arm should reuse that helper. | high (real money, both books) | **yes, per cutover** |
| 6 | **Collapse the two placers** — one source, paper/real split by an explicit flag not by table. Do only after Stage 5 is live and stable. | high | **yes** |

**Where do the "Unibet UI-placer bots" fit? (owner Q, 2026-09-09).** Coolbet has TWO
bot families: the **placeable real-money bots** (`bot_coolbet_1x2_model_v1`,
`bot_coolbet_ou_model_v1` — best-accessible edge, fed by mirror jobs, placed by the UI
placer) and the **trigger bots** (`bot_coolbet_trigger_*` — per-book edge, paper). The
Unibet equivalents are NOT a second parallel placeable set that places independently
alongside Coolbet — that would double-bet the same fixture at two books. Instead:
- **Paper precursor = 3b.** The Unibet trigger bots' `model_*` strategy measures the
  model edge at **Unibet's own price** — the Unibet counterpart of the Coolbet placeable
  bots' signal (and a better basis than the best-accessible mirror bots). It accumulates
  paper for validation.
- **The Unibet UI placer (3c) is the executor**, the counterpart of `coolbet_ui_placer`
  — not a bot.
- **End state = unified best-price routing (§7).** ONE bot per market
  (`bot_model_1x2` / `bot_model_ou`) checks both books and places ONCE at the better
  price; the Coolbet + Unibet placers are execution arms the router calls; there is NO
  standalone Unibet PLACEABLE set. Today's 2 Coolbet placeable bots are the interim and
  get replaced by the unified per-market bots when the router (Stage 5) lands.
- **Interim-only alternative** (if Unibet ever had to place before the router): its own
  PLACEABLE set + cross-book dedup (`match_exposure`/`real_bets` already read across
  books, so no double-bet — but "which book" is first-come, not best-price). Not the plan.

**Bot lifecycle — TWO families, only one is data-gated (owner, 2026-09-09; corrects an
earlier draft):**

- **Family 1 — the `/picks` real-money bots (NOT data-gated).** The placeable bots
  (`bot_coolbet_1x2_model_v1`, `bot_coolbet_ou_model_v1`) ARE the current `/picks`/model
  output, executed at a book. They were never promoted through a data gate — `/picks` is
  the validated product. So **extending them to Unibet is not data-gated** either: same
  `/picks` signal, executed at the better-priced book. The target here is **one
  book-agnostic bot per market** (`bot_model_1x2`, `bot_model_ou`) placed by the
  **best-price router** across {Coolbet, Unibet} — the near-term real-money goal, which
  does NOT wait on trigger accumulation. (Still owner-gated on the usual real-money
  discipline: the Unibet executor proven + a dry-run before it stakes.)
- **Family 2 — the trigger-engine bots (DATA-GATED).** `bot_*_trigger_*` (model + sharp),
  per-book soft-odds scanning, Coolbet AND Unibet, run **paper**, accumulating settled
  picks. Promoted to a placer ONLY when (a) enough settled history to calibrate + compare,
  AND (b) the owner decides. Stage 3b builds the Unibet paper triggers; they do NOT stake.

So: Family 1 = the `/picks` product, best-price-routed across books (near-term real money,
one bet per selection); Family 2 = paper research bots, data-gated. The Unibet UI placer is
the execution arm for BOTH — it stakes Family-1 routed bets once live, and never stakes a
Family-2 bot until that bot is promoted.

**Guardrail (unchanged):** never flip `execute`, change a real-money floor, add a bot to a
PLACEABLE set, or repoint a placer's source without explicit owner authorization + a dry-run +
fold-robust evidence.

---

## 9. THE INCONSISTENCIES THIS DOC EXISTS TO REMOVE (checklist)

- [x] Three market spellings (`o/u` / `over_under_25` / `over 2.5`) → one canonical vocab (Stage 1 ✅ 2026-09-09, `workers/canonical_market.py`).
- [x] `real_bets` mixes real + phantom-paper + manual → proof-tagged via `placed_real`, paper excluded from dedup + overlays (Stage 2 ✅ 2026-09-09, migration 325).
- [ ] `/performance` shows `simulated_bets`, not what we stake → grade-pin + real overlay clarity (Stage 2 + PICKS-GRADING).
- [ ] Two placers read two tables → one trigger-sourced path (Stages 3,6).
- [ ] Single-book coupling in 5 places (§6) → placer registry + per-book config (Stages 4,5).
- [ ] Floors disagree by surface → PICKS-GRADING-ROLLOUT (A=13/8, pin /performance to A).
