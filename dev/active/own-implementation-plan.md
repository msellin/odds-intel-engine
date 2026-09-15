# OWN implementation plan — 2026-09-15

**Source:** `docs/OWN_STRATEGY_AUDIT_2026_09_15.md` (reviewed version, §0 changelog).
**Direction:** 🤖 OWN, with a small 👥 PICKS honesty batch (Phase 4).
**Owner decisions required before Phase 1b and Phase 2 start** — marked ⚖️.

## Principles (from the audit and both reviews)

1. Nothing measured is positive. Phases 1a/1b are *measurement* builds with hour
   budgets and pre-registered stops, not strategies. If a stop fires, the phase
   closes; nobody re-cuts the rule.
2. A number that changes a decision ships with the script that produced it.
3. Every executor that can move money calls one gate first, and the gate fails
   closed. No executor may reach a browser or API before the gate returns.
4. Bot/registry/SYSTEM_MAP move together (`SYSTEM-MAP-REGISTRY-NOT-DRIFTED`).
5. Every task gets a `--filter`-runnable smoke test; never run the full suite locally.

---

## Visibility invariant (owner requirement, 2026-09-15) — applies to every phase

**Every OWN pick is visible to the owner on `/admin/shadow-bots` from the first row
it writes, and nothing OWN ever reaches `/picks` or `/performance`.** Verified in code:

- `/admin/shadow-bots` "Upcoming picks" reads `shadow_bets_unique` for every
  non-retired bot with `result='pending'` and kickoff in the future
  (`page.tsx:937-1012`), with live Coolbet / Unibet / Epicbet prices per row. A new
  bot needs NO frontend change to appear — it needs a `bots` row and its first
  `shadow_bets` write.
- `/picks` and `/api/v1/upcoming` read only `picks_forward_test_public`.
- `/performance` and `/api/v1/track-record` read only `simulated_bets` and hide
  `maturity_label = 'experimental'`; the column is CHECK-constrained to
  `{experimental, beta, calibrated, retired}` (mig 352, `MATURITY-LABEL-CANONICAL`).

**Rules for every OWN bot in this plan** (`bot_trigger_1x2_sharp_tight_v1`,
`bot_inplay_slowstate_v1`, and any future one):
1. `maturity_label = 'experimental'`, never promoted by a migration to `beta` or
   `calibrated` without an explicit owner decision recorded in `PRIORITY_QUEUE.md`.
2. Writes `shadow_bets` only. **Never** `simulated_bets`, never `picks_forward_test`.
3. Not in `PLACEABLE_BOTS` until Phase 3.
4. Smoke `OWN-BOTS-OFF-CUSTOMER-SURFACES` (add in Phase 0): for every bot named in
   `bot_registry.py` with direction OWN — maturity is `experimental`, zero
   `simulated_bets` rows, zero `picks_forward_test` rows, and the `/performance`
   filter string `!== "experimental"` still exists in the web repo.

**Display additions so the owner can decide per pick:**
- Phase 1a: show `decision_quote_age_min` (from `last_seen_at`) and a FRESH/STALE
  badge on each upcoming row; stale rows are shown greyed, never hidden.
- Phase 1b: in-play rows show minute, score, the on-screen book price and the
  de-vigged book probability; they use the same section, flagged `IN-PLAY`.
- Phase 2: a "Promotions" panel on the same page listing open promos with computed
  EV and the optimal selection, read from `promo_terms` / `promo_ledger`.
- The manual-log path is `/admin/place` (already exists) — every hand-placed bet
  goes through it so it lands in `real_bets` and gets settled and CLV-scored.

---

## Phase 0 — placement safety (P0, ~1 day code + 15 min operator)

**Goal:** `paused` means no host can stake; proven by mutation test.

### 0.A `workers/automation/placement_gate.py` (new, ~4 h)

```python
class PlacementRefused(RuntimeError): ...
def assert_may_place(*, bot_name: str, book: str, pick, stake: float, now=None) -> None
```
Checks, in order, all **fail-closed** (any exception ⇒ refuse):
1. `read_placement_paused_strict()` — new; raises on DB error. Keep
   `is_placement_paused()` for observability callers (`health_alerts.py`,
   `coolbet_daily_summary.py`) but flip its error default to `(True, "unreadable")`.
   Same for `is_daemons_paused()`. **Leave `is_publishing_paused()` fail-open** —
   mig 353's reasoning is correct for publishing; add a comment in the gate saying
   the two must never be unified (`RELIABILITY_LEDGER` §9b).
2. Real-money arming — replace the env var `ROUTER_ALLOW_REAL` with a DB column
   `coolbet_session_state.real_money_armed` (mig 354). The gate refuses when it is
   FALSE. Env var kept as an *additional* refusal for one release, then removed.
3. Allowlist — call `effective_allowlist()` (move it, `PLACEABLE_BOTS`,
   `ui_place_enabled_bots` into the gate module; leave re-export shims in
   `place_coolbet_ui.py` so smoke `COOLBET-PLACER-CONTROL` keeps passing).
4. Caps — reuse `spent_today()` and `exposure_conflict()`; do not reimplement.
5. Cutoff — `KICKOFF_CUTOFF_MIN`.

### 0.B Call sites (~2 h)
| executor | where | change |
|---|---|---|
| UI placer run-level | `scripts/place_coolbet_ui.py::main()` after `effective_allowlist()` | gate once; abort run on refusal |
| UI placer per-pick | `coolbet_ui_placer.py::stage_bet` **before** `select_outcome` | gate; remove the late `is_placement_paused` read at :1607 |
| Router | `best_price_router.py::route()` before the pick loop, and inside `_dispatch_unibet` before `unibet_placer.place_bet` | gate; iterate `effective_allowlist()` not `PLACEABLE_BOTS` (:426) |
| API placer | `coolbet_placer.py::place_all_bets` (:1952) and **`place_bet_by_id` unconditionally** (:2704) | replace inline check with the gate |

### 0.C Operator actions (15 min, ⚖️ owner)
- `launchctl bootout gui/$(id -u)/com.oddsintel.coolbet-ui-placer` and
  `…/com.oddsintel.best-price-router`; move both plists to `local/launchd/paused/`
  with a README line. Reload only after Phase 3.
- Fix the UI-placer plist header comment (20/€200 → 80/€800).

### 0.D Ledger repair (~2 h)
- `scripts/reconcile_placed_attempts_to_real_bets.py`: for every
  `coolbet_placement_attempts` row with `outcome='placed' AND execute_mode` and no
  `real_bet_id`, insert the `real_bets` row from the attempt (odds, stake, market,
  selection, `placed_real=TRUE`, notes `reconciled-from-attempt <id>`). Idempotent.
- `settlement._settle_real_bets_for_matches`: also settle any `placed_real` row whose
  match is `finished` with a score regardless of the run window. One-shot run to
  clear the three 2026-09-11 rows.

### 0.E `coolbet_control --status` (~1.5 h)
Add a host-side section: loaded launchd agents with `--execute` in argv,
`real_money_armed`, `ROUTER_ALLOW_REAL` presence, VPS drain-job registered (from
`pipeline_runs`). Print one final line `CAN_STAKE: yes/no` that is `no` only when
every executor would refuse.

### 0.F Smoke tests
- `PLACEMENT-GATE-FAIL-CLOSED` — monkeypatch `execute_query` to raise; assert the
  gate refuses; restore the patch (ledger "monkeypatch never restored").
- `PLACEMENT-GATE-ALL-EXECUTORS` — source-inspect that the four call sites invoke
  `assert_may_place` before any browser/API call.
- `ROUTER-NO-ALLOWLIST-BYPASS` — with `real_money_armed=TRUE` and every
  `ui_place_enabled=false`, `route(execute=True)` dispatches nothing.
- `REAL-BETS-ATTEMPTS-RECONCILED` — no `placed` attempt without a `real_bets` row.
- `REAL-BETS-SETTLE-ANY-FINISHED` — settle path has no run-window restriction.

**Docs in the same commit:** `SYSTEM_MAP.md` §4 (three executors, one gate),
`COOLBET_OWN_BETTING.md` (gate stack), `RELIABILITY_LEDGER.md` (new pattern: "two
safety reads pointing opposite ways"; "an env var's absence is not a pause"),
`PRIORITY_QUEUE.md`.

---

## Phase 6 — `/admin/shadow-bots` rework (≈ 43 h web + 6 h engine; runs in parallel with 1a, right after Phase 0)

**Why it is its own phase.** The owner's requirement is "read, understand, follow,
decide per pick". A 2026-09-15 page review (agent, read-only) found the page cannot
support that today: ~20 queries per load pulling the whole 14k-row ledger and up to
30k `odds_snapshots` rows into Node; a **raw** own-book CLV coloured green/red around
0 when break-even is ≈ −7% (the book's margin); a Promote/Retire pill gated on
Pinnacle CLV while the engine's pre-registration gates on margin-corrected own-book
CLV at n≥300 (two different tests); a `SHADOW_BOTS` list of 29 bots where the registry
has 13, with `FAMILIES` grouped around two retired bots; a `ForwardTestPanel` that is
never rendered but whose query still runs; 640-character tooltips describing a
Kambi feed the code no longer reads; `edge_percent` fetched and never shown; and
**no path from an upcoming shadow pick to `real_bets`** — `/admin/place` reads
`simulated_bets`, a different ledger, so a hand-placed OWN pick cannot be logged
today except by the paused UI placer's account reconciliation. The
`CoolbetDaemonsPause` control is rendered nowhere.

**Information architecture (replaces the page):**
1. **Safety strip** (sticky): `placement_paused` · `publishing_paused` ·
   `daemons_paused` (+ liveness) · armed executors (`ui_place_enabled` count) ·
   today's placed / 80 and staked / €800 · `CAN_STAKE` from Phase 0.E. Each chip is
   the control; arming requires an explicit confirm. Wire the orphaned
   `CoolbetDaemonsPause` here.
2. **Today's picks** (the decision table), columns in order: KO (local, relative) ·
   Match · Pick (canonical label) · Bot · Best **placeable** price with book chip
   (CB/UB; EB greyed until a placer exists) · **Age** of that quote · **Break-even**
   (`1/p_anchor`) · Gate floor · **Live edge %** recomputed at the shown price
   against the bot's own anchor · **Verdict** · Action. One colour carrier, the
   Verdict chip: `PLACE` (live price ≥ gate floor, age < 30 min, bot not paused) ·
   `THIN` (above break-even, below gate) · `SKIP` (below break-even, stale, or
   unplaceable book) · `BLOCKED` (bot off / placement paused / KO < 3 min). Sort:
   verdict then kickoff. Action `Place €X` writes **`real_bets`** via the existing
   `/api/admin/real-bet` route with `placed_real=NULL, notes='manual via shadow-bots'`
   so it settles and CLV-scores; `Skip` with a one-click reason. In-play rows (1b)
   and FRESH/STALE (1a) land in this table, not in new sections.
3. **Which bots work** (scoreboard): one row per **registry-active** bot (imported,
   not hardcoded): n settled · margin-corrected own-book CLV ± CI · CLV t · ROI
   (dimmed, footnote "per-bet sd ≈ 1.3; ~15,600 bets to confirm +3%") · Verdict
   derived verbatim from the pre-registration (`COLLECTING n/300` · `PROMOTE` if CLV
   CI > 0 · `RETIRE` if CLV < −2% · `OBSERVE`). Delete the ROI-based pill.
4. **Promotions panel** (Phase 2) below the scoreboard.
5. **Delete:** `ForwardTestPanel`/`ForwardTestArm` + their query; retired entries in
   `SHADOW_BOTS`; dead `BOT_BADGES`; `FAMILIES`; Discipline-check strip (move to a
   weekly script); "Including retired" card; Kambi tooltip text; unrendered selects.

**Engine side (~6 h):** a view `shadow_bets_own_book_clv` exposing per-row
margin-corrected own-book CLV via `closing_book_margin()` semantics, so the page
reads a number instead of recomputing it in JS over the full ledger. Also fix the
`docs/SYSTEM_MAP.md` §2 trigger table, which still lists five bots the registry
retired on 2026-09-14 (the drift test checks registry→map, not map→registry —
extend it both ways).

**Component split:** `page.tsx` (~120 lines, layout) · `lib/shadow-bots/queries.ts`
(typed, cached 60 s) · `lib/shadow-bots/verdict.ts` (+ tests) · `components/shadow-bots/
{safety-strip,picks-table,picks-row,scoreboard,place-action}.tsx`.

**Smoke (web config + engine):** `SHADOW-BOTS-REGISTRY-DRIVEN` (bot list equals
`active_names()`), `SHADOW-BOTS-VERDICT-IS-PREREG` (verdict thresholds equal the
pre-registration constants), `SHADOW-BOTS-PLACE-WRITES-REAL-BETS`,
`SYSTEM-MAP-REGISTRY-NOT-DRIFTED` extended to map→registry.

---

## Publish-time change for the PICKS forward test (small, 👥, do with Phase 4)

The publisher runs at **10:00 UTC** (`scheduler.py::job_publish_picks_forward_test`).
Its docstring gives two reasons: "after the 04:00 morning chain and several odds
refreshes", and "the backtest was measured on quotes at least 4 h out, so
publishing later than we measured would be publishing a different rule". Neither
forbids **earlier**. At 07:00 UTC the morning chain is done, Coolbet/Epicbet/AF have
each swept ≥4 times, and the alignment rule (anchor and book quote within 60 min)
is unaffected. Earlier publication also covers the 10:00–12:00 UTC kickoffs that
10:00 publication misses. Publish time is not part of the pre-registered rule
(edge ≥3%, odds ≤4.0, align ≤60 min, anchor overround ≤4%, top 8/day), so this is
an operational change, not a new `rule_version` — record the date in the
pre-registration file's changelog. **Move to 07:00 UTC; do not add a second daily
run** (top-8-by-edge would then be ranked over two different pools). Note the job
had **never completed once** until the 2026-09-15 `SCHEDULER-PUBLISHER-NEVER-RAN`
fix — check `pipeline_runs` shows a row tomorrow.

---

## Phase 1a — sharp-tight instrument, freshness stamp (~1.5 days)

**Hypothesis being made measurable:** the mc-CLV vs prob-edge slope (+1.31 stale /
+0.35 fresh) — which is true on quotes we actually saw on screen.

1. **Dedup-on-change + seen stamps** on the three own-book writers
   (`coolbet_explorer.py`, `epicbet_explorer.py`, `unibet_odds_feed.py`): if the price
   for `(match, book, market, selection, line)` equals the last stored row, update a
   new `last_seen_at` instead of inserting; on change insert with `first_seen_at`.
   Mig 355 adds the two columns (nullable; backfill `first_seen_at = timestamp`).
2. **Retention exemption** in `prune_old_simple`: keep the full pre-KO path for
   `bookmaker IN ('Coolbet','Epicbet','Unibet-Site')` for 60 days. Cost check first:
   these three are ~2.8M rows / 45 days today.
3. **Instrument freshness rule**: `bot_trigger_1x2_sharp_tight_v1` records
   `decision_quote_age_min` (from `last_seen_at`) on each `shadow_bets` row (reuse
   `pair_gap_hours` semantics or add a column in mig 355) and **refuses legs whose
   quote is >60 min old**. Update the pre-registration file with this amendment,
   dated; it is a measurement fix, not a rule change, and says so.
4. **Evaluation script** `scripts/sharp_tight_slope.py`: mc-CLV vs prob-edge slope
   on fresh legs only, cluster-robust CI, placebo arm (odds-40ile demeaned), prints
   zero-crossing and legs/day. Committed with its first output.

**Stop / promote (pre-registered here):** at n=300 fresh legs — slope CI includes 0
⇒ RETIRE; slope CI excludes 0 AND zero-crossing ≤ +6pp AND ≥2 legs/day ⇒ Phase 3
candidate (owner decision). ROI never promotes.

**Smoke:** `OWN-BOOK-DEDUP-ON-CHANGE` (writer emits one row per price change),
`OWN-BOOK-RETENTION-EXEMPT`, `SHARP-TIGHT-FRESHNESS-REFUSES-STALE`.

---

## Phase 1b — in-play slow-state rig at Coolbet + Epicbet (~3 days, ⚖️ owner go/no-go)

**Gate before starting:** read `oufid.jsonl` (O/U fidelity AF vs Epicbet). Record the
result in `dev/active/inplay-strategy-discovery-context.md`. If AF O/U is unfaithful,
the paper bot starts with 1x2-derived triggers only.

1. **Collector on the Mac** (residential IP; VPS needs FlareSolverr and the explorer
   already caps sidebets at 250/sweep for that reason). Generalise
   `workers/jobs/inplay_epicbet_collector.py` to a `--book coolbet|epicbet` runner;
   Coolbet in-play endpoints from `workers/automation/coolbet_inplay.py` (read-only
   parts). Cadence 30–60 s, ≤15 concurrent fixtures, rediscovery every 5 min.
2. **Table** `inplay_book_quotes` (mig 356): `fixture_id, book, captured_at, minute,
   score_home, score_away, af_age_s, markets JSONB`. Prune job modelled on
   `job_prune_live_snapshots`: keep 90 days.
3. **Runner**: `KeepAlive` launchd agent (pattern: `coolbet-odds-snapshot`), not a
   cron; heartbeat row in `pipeline_health_state` so a dead collector alerts.
4. **Paper bot** `bot_inplay_slowstate_v1`: triggers (locked): (T1) 0-0 at 35'–54',
   back under 2.5 if book price ≤ 2.20; (T2) two-goal lead at 70'–89', back the
   leader if price ≤ 2.20. Writes `shadow_bets` with `recommended_bookmaker` = the
   book, `odds_at_pick` = on-screen price, plus `book_implied_prob_devig`. Register
   in `bot_registry.py` + `SYSTEM_MAP.md` same commit. Settles on final score via the
   existing resolver registry.
5. **Control arm**: same triggers priced from the AF live aggregate at the same
   second, written with `strategy_profile='af_control'`.
6. **Evaluation script** `scripts/inplay_slowstate_eval.py`: primary = realised
   hit-rate minus de-vigged book implied prob on the selected set, cluster-robust on
   fixture; secondary = realised ROI; prints n, CI, required n for +2.5pp at 80%.

**Stop / decide (pre-registered here):** n=1,000 — lift point estimate < 0 ⇒ STOP
and close in-play. n=3,000 — CI excludes 0 upward ⇒ Phase 3 candidate at the book
that produced it (Coolbet has a placer; Epicbet would need one, 2–3 days, only then).
Expected duration 4–7 months of paper.

**Smoke:** `INPLAY-COLLECTOR-HEARTBEAT`, `INPLAY-SLOWSTATE-TRIGGERS-LOCKED`,
`INPLAY-SLOWSTATE-PRICE-IS-BOOK-NOT-AF`.

---

## Phase 2 — promotions ledger (~0.5 day code + owner time, ⚖️ owner opens accounts)

1. **Terms table** (mig 357) `promo_terms`: book, promo_type, min_odds, max_stake,
   stake_returned, rollover_x, single_use, valid_to, source_url, captured_at. Owner
   fills it from each book's current T&Cs.
2. `scripts/promo_ev.py`: input book + promo row + the selection's consensus quote;
   fair prob via `workers/model/devig.py::shin_devig` on a ≥4-book consensus; EV for
   boost / free-bet (SNR) / acca-insurance **under the stated terms**; prints EV,
   optimal side, and "unhedgeable" variance.
3. **Ledger** (same mig) `promo_ledger`: promo_terms_id, fixture, selection, fair_prob,
   price, stake, ev_eur, taken_at, realised_pnl, settled_at.
4. Monthly `scripts/promo_review.py`: realised vs Σ EV, sd, kill flag on two
   consecutive months > 1.5 sd below.

**Smoke:** `PROMO-EV-FORMULAS` (fixed inputs → fixed EV), `PROMO-LEDGER-EV-BEFORE-BET`
(no ledger row without `ev_eur`).

---

## Phase 3 — real-money gate (policy, no build beyond Phase 0)

Add to `coolbet_ui_placer.stage_bet` and any future placer: log `max_accepted_stake`
(read from the slip's stake-limit field where the UI shows it) into
`coolbet_placement_attempts`. Smoke `PLACEMENT-LOGS-MAX-STAKE`.

---

## Phase 4 — 👥 PICKS honesty batch (~3 h, odds-intel-web)

1. `/performance`: banner "Model-era ledger, rule retired 2026-09-13/14; prices are
   best-of-accessible, ≈ −3.5…−5.7% at a single placeable book" with links; or
   remove the headline and keep the leaderboard behind the banner (⚖️ owner).
2. `/api/v1/track-record`: add `meta.edge_basis` and `meta.rule_status`.
3. `/admin/shadow-bots`: import floors from `src/lib/generated/engine-floors.ts`.
4. Telegram webhook `route.ts:666`: drop the Pro/Elite upsell string.
Web smoke via `scripts/web_smoke_test.py` config: `PERF-MODEL-ERA-BANNER`,
`TRACK-RECORD-EDGE-BASIS`.

---

## Phase 5 — cull (~half a day, last)

Delete `inplay_bot.py`, `coolbet_inplay.py` (after 1b reuses its read-only parts),
`place_all_inplay_bets`; drop the 4 inactive `BOOK_MARKET_BOTS` entries; remove
`WIDE_CONFIGS`, the retired line-shop O/U stop, `_ODDS_TOLERANCE`; either wire or
delete `PIN_CROSS_DRIFT_VETO_ENABLED`; web: remove Stripe/tier dead code and deps,
fix README. Close the CS2 and tennis rows in `PRIORITY_QUEUE.md` with reasons.
Grep-ripple every removal through `docs/ *.md`.

---

## Sequencing

```
Phase 0 (P0) ──► 0.C operator unload ──► Phase 1a ──► Phase 1b (⚖️) ──► Phase 3 policy
                                     └──► Phase 2 (⚖️ accounts) — parallel, owner-driven
Phase 4 (PICKS) — independent, any time
Phase 5 (cull) — after 1b's bot lands
```

## Risks

| risk | mitigation |
|---|---|
| Gate refactor breaks the UI placer flow while paused | mutation tests + a `--dry-run` end-to-end on one pick before the plists are reloaded |
| Dedup-on-change hides a scraper that stopped updating | `last_seen_at` staleness alert in `coolbet_odds_freshness` |
| Coolbet in-play collector triggers Imperva escalation | ≤15 fixtures, 60 s cadence first week, `RELIABILITY_LEDGER` §6/§10 tells apply |
| Promo terms change silently | `valid_to` + monthly re-capture task |
| Multiple-testing on in-play triggers | triggers locked at two; any new trigger needs a wide-window + split-half check before a row in any doc |
