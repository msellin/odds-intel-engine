# 🤖 OWN — full audit and a tested plan, 2026-09-15 (reviewed)

**Scope.** Both repos (`odds-intel-engine`, `odds-intel-web`), the VPS database, and
the 2026-09-13/14 audit corpus, read as one system. Direction: **🤖 OWN first**
(the owner's stated priority 1), 👥 PICKS second. Every number below was either
re-queried from the DB or is cited to a committed script. No code was changed by
this audit. **The implementation plan lives in `dev/active/own-implementation-plan.md`.**

> ## §0 — Adversarial review, same day. Read this first.
>
> Two independent reviewers (a betting quant, a trading-automation architect) were
> asked to break the first version of this document. They did, in places. Every
> correction is applied in the body below; this table is the changelog so a reader
> of the first version knows what moved.
>
> | first-version claim | verdict | what replaced it |
> |---|---|---|
> | "Two things remain with a **positive expectation**: in-play short side, promotions" | **KILLED as stated** | Nothing measured is positive. The in-play source doc says so in as many words. Short side = **lower vig**, not edge. Promotions are *probably* +EV, terms-dependent, unhedgeable. |
> | Epicbet in-play margin 5.56% is "the tightest we can place" | **KILLED** | 5.56% is 1x2 on 7 fixtures. On 43 fixtures Epicbet reads 6.41%/6.44% (1x2/O-U); **Coolbet 4.96%/5.16%**. And Epicbet has no placer. |
> | Phase 1 stop rule: sd ≈ 0.5 at odds 1.6, ±2% at ~2,400 bets | **KILLED** | sd at 1.63 is **0.79**; ±2% needs ~6,000; the PROMOTE rule had **14% power** against a real +2.5%. Rewritten with a hit-rate metric and a proper power calculation. |
> | `bot_v10_all` +12.5% on `/performance`, ≈ −5.5% EV at own books (n=161) | **WEAKENED in method, worse in fact** | The page shows **+7.4%** (exec price, n=640, `simulated_bets`). Own-book rows are 105 not 161. Placeable EV **−3.5% to −5.7%**. Pinnacle-devig CLV on own-book rows **−3.4%/−4.7%** vs **+5.35%** overall — the mirage in one query. |
> | "A live Pinnacle feed would shrink edges" (paired n=92) | **WEAKENED** | That test was **overround-only**; it cannot see per-selection lag. What holds: the ≥9% quotes are real Pinnacle with $200 limits. What does not: any inference about direction. |
> | Sharp anchor "DEAD" at −3.36% (n=72) / −14.2% (n=112) | **WEAKENED as ROI** | se ≈ ±18pp / ±14pp; those are "indistinguishable" per gotcha §60. The structural fact (0 of 109 legs clear 3% when the anchor is a real line) is the evidence and is one day's board. |
> | "Expanding the book universe cannot help line shopping because the books agree to 0.6–1pp" | **CONFIRMED, wrong reasoning** | Each independent-supplier book removes 0.8–1.5pp; best-of-12 incl. Pinnacle/SBO/Betfair reaches 2.15%. Six more EMTA books plausibly land 3–4%. Same conclusion, from the N-curve. |
> | 899 segment cells "DEAD" | **WEAKENED** | The metric (own-book margin-corrected CLV) can only see a mispricing *the same book corrects before kickoff*. A persistently soft segment reads −m by construction. The 27-signal outcome test is sound; the segment sweep is not a test of what it was cited for. |
> | Phase 0: "two `--execute` cron jobs" | **CONFIRMED, under-counted** | A **third executor** runs on the VPS every 10 s (`_drain_manual_placement_queue` → `place_bet_by_id`, paper today) and never reads the pause. And `ROUTER_ALLOW_REAL=1` alone stakes for both placer bots **with the DB toggles OFF** — the router iterates `PLACEABLE_BOTS`, not `effective_allowlist()`. |
> | Phase 0.3: Unibet arm has no pause, caps or cutoff | **WEAKENED** | The router applies caps and cutoff before dispatch. **The pause is the genuine gap.** |
> | Phase 0.6: a second complete real-money path | **WEAKENED** | `place_all_bets` **does** read the pause and is scheduled nowhere; it is a manual path with stale gates, not an armed one. |
> | "8 of 11 `BOOK_MARKET_BOTS` entries are retired" | **KILLED** | 4 of 11 map to inactive bots. Point stands for those four. |
> | Real-money ledger is clean | **NEW DEFECT** | 1 confirmed €10 placement has **no `real_bets` row**; 3 `placed_real` rows sit **unsettled on finished matches** for four days. 143 stakes, not 139. |
> | The sharp-tight instrument is "paper, move on" | **UNDER-WEIGHTED** | It carries the **only measured positive slope** in the corpus (mc-CLV vs prob-edge +1.31, t=4.9, placebo 0.007) — but on stale decision quotes; with ≤60-min freshness the slope reads +0.35 and break-even +21pp. So it is a *measurement* build (freshness stamp), not a strategy. Now Phase 1a. |
> | Missing entirely | **ADDED** | CS2 measured dead 2026-07-31 (−27…−33%, n=205). Tennis has tables and no verdict. Cross-book O/U and AH *line* discrepancies were never measured (kill criterion is 1x2-only). Gambling winnings from EMTA operators are tax-free for individuals. |

---

## 1. The verdict in five sentences

1. **The system as built cannot make money at the three books we can legally
   place at.** Model α = 0; the sharp anchor yields zero legs when required to be a
   real line; best-of-3 line shopping leaves 5.66% residual margin; own-book line
   movement is unexploitable; the flagship bot is −3.5% to −5.7% EV at a placeable
   price while the public page shows +7.4%. These findings survived adversarial
   review; two of them got *worse*.
2. **Real money is paused, but three executors are armed underneath the pause** —
   two `--execute` launchd jobs on the Mac and a 10-second queue drain on the VPS;
   the session kill switch fails OPEN on a DB error; the router bypasses the DB
   allowlist; and the real-money ledger has one missing row and three unsettled
   bets. Phase 0 fixes all of it in about a day.
3. **There is no pre-match strategy left to test at these books**, and nothing
   in-play has measured positive either. The in-play short side is not an edge;
   it is a smaller vig (≈ 4% relative vs ≈ 14% on the long side).
4. **Two hypotheses are not dead because they were never measurable, and one
   lever is probably +EV without any prediction:** (a) the pre-match sharp-tight
   rule, whose positive slope has only been measured on stale decision quotes;
   (b) in-play slow-state triggers at Coolbet, whose on-screen price we have never
   recorded; (c) promotions, boosts and free bets at every EMTA-licensed book.
   The plan makes (a) and (b) measurable with hard hour budgets and treats (c) as
   the only OWN P&L available this quarter.
5. **The expected value of OWN on current evidence is ≤ 0, and the ceiling if a
   +2–3% edge exists is €1–7k/year before account limits** — statistically
   indistinguishable from a losing year at €10 stakes. The owner should decide,
   with that number in front of them, how much more to build.

---

## 2. What the DB says (re-queried 2026-09-15, re-derived by the quant reviewer)

### 2a. Real money, confirmed placements (`real_bets.placed_real = TRUE`, settled)

| | n | staked | P&L | ROI | span |
|---|---|---|---|---|---|
| **all settled** | **139** | €1,390 | **−€97.50** | **−7.0%** | 2026-08-27 → 09-13 |
| `bot_coolbet_value_v1` (line-shop, retired) | 120 | €1,200 | +€2.10 | +0.2% | 08-27 → 09-07 |
| `bot_coolbet_ou_model_v1` (model, off) | 14 | €140 | −€49.60 | −35.4% | 09-09 → 09-13 |
| `bot_coolbet_1x2_model_v1` (model, off) | 5 | €50 | −€50.00 | −100% | 09-10 → 09-12 |

By market and side, same population: 1x2 home +€71.00 (n=33), draw +€59.00 (24),
away −€22.80 (30); O/U 2.5 under −€112.30 (26), over +€0.80 (8); O/U 3.5 under
−€63.20 (8), over −€30.00 (10). The line-shop bot broke even (what a zero-edge rule
at a 7.7% book looks like on a lucky fortnight); **every euro of loss came from the
two model bots in their last five days**, the O/U calibrator window.

**Ledger defects found by review** (`coolbet_placement_attempts WHERE outcome='placed' AND execute_mode` = 143):
- one €10 placement (2026-09-13 16:22, `bot_coolbet_ou_model_v1`, U2.5) has **no
  `real_bets` row** — the "placed but could not write" branch fired;
- three `placed_real` rows are `pending` on finished matches with scores
  (2026-09-11; net −€0.10) — `_settle_real_bets_for_matches` only settles match_ids
  inside the run's window and these fell through.
- 0 of 143 placements carry a ticket id; the balance delta is the *only*
  confirmation. One balance-read failure away from an unconfirmable placement.

Corrected all-time: **143 stakes, €1,430, ≈ −€97.60 on the 142 knowable.** Also 823
settled rows with `placed_real IS NULL` (−€418.85 on €4,760) from the manual /
phantom-paper era — never to be summed with the above.

### 2b. The flagship bot at a placeable price

`bot_v10_all`, last 60 days, `shadow_bets_unique`, settled, with a close at the
bet's own book (n=105 of 161 — the other 56 closed at Unibet/Pinnacle/Betano/10Bet):

| basis | value |
|---|---|
| raw own-book CLV | +1.29% |
| EV at `odds_at_pick`, book's own de-vigged close | **−3.51%** |
| EV at own close | **−5.67%** |
| Pinnacle-devig CLV, own-book-closed rows | **−3.4% (Coolbet) / −4.7% (Epicbet)** |
| Pinnacle-devig CLV, all 260 rows | **+5.35%** |
| `/performance` headline (`simulated_bets` since 2026-05-04, exec price, n=640) | **+7.4%** (high-water basis +11.5%; cache shows 6.6%) |

The last two rows are the whole story: **the flagship's positive Pinnacle CLV lives
entirely in rows whose best price was at a book nobody here can use.** That is
`ANALYSIS_GOTCHAS` §52 in one query and is stronger than the margin arithmetic the
first version of this doc used.

### 2c. Paper fleet, last 60 days, own-book close only

| bot | n | raw own-book CLV | ≈ EV after margin |
|---|---|---|---|
| `bot_unibet_trigger_sharp_1x2_v1` | 76 | +10.96% | ≈ +2% |
| `bot_coolbet_trigger_sharp_1x2_v1` | 74 | +4.41% | ≈ −3% |
| `bot_coolbet_trigger_sharp_ou_v1` | 20 | +6.35% | ≈ 0% |
| `bot_trigger_1x2_sharp_tight_v1` (instrument) | 13 | +0.80% | too early |

One bot marginally positive at n=76 over five days; not a decision.

### 2d. The data we have to work with

| book | 1x2 fixtures / 14d | median obs per series (7d) | reaches us via |
|---|---|---|---|
| Coolbet | 3,872 | **4** | Mac, Imperva, `:03/:33` sweep smeared ~25 min |
| Epicbet | 3,855 | **23** | VPS via FlareSolverr, `:02/:32` |
| Unibet-Site | 1,951 | 7 | Mac, DataDome, `:15/:45` |
| Pinnacle (AF) | 4,353 | 28 | AF `:00/:30`, lagged +0.81pp median overround vs real |

Retention prunes every series to three rows after 7 days (§59). **Coolbet — the
only book with a working placer — is the worst-observed book we have.** In-play:
`odds_snapshots` holds **zero** live rows for any of our three books; the only
in-play history is AF's unattributed aggregate (median 40 s stale). In-play margins
on 43 paired fixtures: **Coolbet 4.96% / 5.16%** (1x2 / O-U), Epicbet 6.41% / 6.44%,
Unibet-Site 7.79% / 6.84%.

### 2e. The PICKS forward test

`sharp_edge_v1` closed at n=8 (−2.97 units); v2/v3 live with their own n. Nothing to
conclude; the surfaces correctly refuse to render the backtest.

---

## 3. Where the edge is NOT — closed, with the evidence and its limits

| idea | verdict | the evidence | its limit (per review) | where |
|---|---|---|---|---|
| Model-anchored 1x2 | **DEAD** | residual α = 0.0000, CI [0, 0.03], four benchmarks incl. our books | none | `residual_test.py`, `c8b729af` |
| Model-anchored O/U | **DEAD — re-measured clean 2026-09-16** | residual α = **0.0000** on all three arms, n=7,273 OOS; model AUC 0.5788 vs market 0.6007; residual AUC 0.4443 (**below** 0.5). Same at O/U 1.5 (n=4,256) and 3.5 (n=5,680). | verdict is on THIS model: it shares the 1x2 feature set, `xg_overperf_home` is 3.9% populated, `referee_over25_pct` 9.8%, and `pinnacle_implied_over25` only 50.8% — a dedicated O/U model is untested, this one is the 1x2 head relabelled | `residual_test_ou.py` |
| Sharp anchor at our books, 3% floor | **DEAD structurally** | gated at ≤4% anchor overround, **0 of 109 legs** clear 3% | ROI figures (−3.4% n=72, −14.2% n=112) have se ±14–18pp — cite the structure, not the ROI | `d4238ec1` |
| A live Pinnacle feed rescues the anchor | **NOT SUPPORTED** | paired n=92: AF is lagged +0.81pp; ≥9% quotes are real, with $200 limits | the paired test was overround-only; per-selection lag is unmeasured | `AF-PINNACLE-NOT-PINNACLE` |
| Best-of-3 line shopping | **DEAD** | best-of-3 overround 5.66% vs 2% kill line | none | `own_path_kill_criterion.py` |
| More EMTA books **for line shopping** | **DEAD** | each independent supplier removes 0.8–1.5pp; best-of-12 incl. unplaceable sharps reaches 2.15% | six more EMTA books ≈ 3–4%, still > 2% | reviewer `bestofn.py` |
| Steam / lag at our books | **DEAD** | follow-through β 0.00–0.14; best of 194 cells −4.16% | none | `own_line_movement.py` |
| Derivative markets | **DEAD** | flat 8.00% margin; no positive CI | none | `own_market_expansion_sweep.py` |
| Segments (899 cells) | **NOT TESTED by that sweep** | metric sees only mispricings the book corrects pre-KO | a persistently soft segment reads −m by construction | `own_segment_signal_search.py` |
| 27 stored signals vs own-book price | **DEAD** | outcome-based, 8,002 fixtures, controls behave | none | same |
| In-play, any side, from AF history | **NOTHING POSITIVE** | every CI contains zero; long side −6…−22% | 1x2 fidelity n=7 fixtures, p90 gap 3.1pp; O/U fidelity **unreported** | `INPLAY_STRATEGY_CANDIDATES` |
| CS2 | **DEAD** | −27…−33% ROI, n=205, flat CLV | — | 2026-07-31 verdict |
| Tennis | **NO VERDICT** | tables exist, no evaluation | close the rows or evaluate | — |
| Cross-book O/U and AH **line** discrepancies | **UNMEASURED** | kill criterion is 1x2-only; §61 deleted the O/U cross-book join | low prior at 7% margins; "unmeasured" ≠ "dead" | — |

---

## 4. The plan (detail and estimates in `dev/active/own-implementation-plan.md`)

### Phase 0 — make "paused" mean paused (🤖 OWN, ~1 day, first)

| # | defect | evidence | fix |
|---|---|---|---|
| 0.1 | `is_placement_paused()` **fails OPEN**; so does `is_daemons_paused()` | `coolbet_state.py:310-332`, `:422-435` | fail CLOSED for both; `is_publishing_paused()` correctly stays open (mig 353) |
| 0.2 | pause read only inside `stage_bet` after the stake is typed | `coolbet_ui_placer.py:1607`; no reference in `place_coolbet_ui.py` | run-level gate at top of `main()`; move the in-flow read before `select_outcome` |
| 0.3 | Unibet arm never reads the pause | `unibet_placer.py` (caps/cutoff are applied by the router) | route through the shared gate |
| 0.4 | router runs `--execute` at :20/:50 and **bypasses the DB allowlist** | iterates `PLACEABLE_BOTS` (`best_price_router.py:426`), never `effective_allowlist()`; `ROUTER_ALLOW_REAL=1` alone stakes with toggles OFF | gate reads the allowlist; unload the plist while OWN is paused |
| 0.5 | UI placer loaded `--all-enabled --execute` hourly; header says 20/€200 (real 80/€800) | `launchctl print`, 84 runs | unload; fix comment |
| 0.6 | manual API placer `place_all_bets(execute=True)` with its own gates | reads the pause (`coolbet_placer.py:1952`); not scheduled | delegate to the shared gate or delete |
| **0.7** | **third executor**: `_drain_manual_placement_queue` every 10 s on the VPS → `place_bet_by_id` (paper today via hardcoded `execute=False`), **never reads the pause** | `scheduler.py:2616`, `coolbet_placer.py:2704-2761` | gate unconditionally |
| **0.8** | **ledger leaks**: 1 placement without a `real_bets` row; 3 unsettled real bets on finished matches | §2a | reconcile from attempts; widen `_settle_real_bets_for_matches` to any finished match |
| **0.9** | `coolbet_control --status` sees only the DB row | `coolbet_control.py:68-150` | add launchd + env + VPS-drain view, or redefine the success measure |

Design: one `assert_may_place()` in a new `workers/automation/placement_gate.py`,
fail-closed, raising `PlacementRefused`; four call sites; reuse `effective_allowlist`,
`spent_today`, `exposure_conflict`, `KICKOFF_CUTOFF_MIN`. Three smoke tests, one of
them a mutation test (DB raises ⇒ every executor refuses). `publishing_paused` stays
outside the gate on purpose.

### Phase 1 — make the two surviving hypotheses measurable (🤖 OWN, budgeted)

**Prior for both: near zero.** These are measurement builds with a hard hour budget,
not strategies. Each has a pre-registered stop and a pre-registered "what would
promote it".

**1a. Pre-match sharp-tight instrument — freshness stamp (≈ 1.5 days).**
The only positive slope in the corpus (mc-CLV vs Pinnacle prob-edge **+1.31,
t=4.9**, placebo 0.007) was measured on decision quotes that were up to 12 h stale;
requiring ≤60 min collapses it to **+0.35** and the break-even to **+21pp**
(`OWN-ANCHOR-GATE-VERIFICATION`). We do not know which number is true because the
writers insert one row per poll with no dedup-on-change and no "seen at" stamp.
Build: (i) dedup-on-change + `first_seen_at/last_seen_at` on own-book writers;
(ii) retention exemption keeping the full pre-KO path for Coolbet/Epicbet/Unibet-Site
for 60 days (cheap: those three are ~2.8M rows/45d); (iii) the instrument records
decision-quote age and refuses legs older than 60 min. **Metric:** margin-corrected
own-book CLV slope vs prob-edge on fresh legs only. **Stop:** at n=300 fresh legs, if
the slope's CI includes zero → RETIRE the instrument. **Promote to Phase 3
candidate:** slope CI excludes zero AND the zero-crossing is ≤ +6pp AND ≥ 2 legs/day
clear it. ROI never promotes it.

**1b. In-play slow-state rig at Coolbet (+ Epicbet), on the Mac (≈ 3 days).**
The AF history cannot answer the in-play question (O/U fidelity unreported; 1x2 p90
gap 3.1pp > any plausible edge). Build a board collector on the Mac (residential IP,
no FlareSolverr load) for Coolbet and Epicbet in-play markets at 30–60 s cadence,
≤15 fixtures, table `inplay_book_quotes` (mig 354) with a prune job; run as a
`KeepAlive` launchd loop, not a cron. A paper bot fires only on **slow-state
triggers through a price ≤ 2.20** (0-0 at 35'–54' → under 2.5; 2-goal leader at
70'–89'), records the **on-screen price at decision** and settles on the final
score. **Primary metric:** realised hit-rate minus the book's own de-vigged implied
probability on the selected set, cluster-robust on fixture (sd ≈ 0.49 vs 0.79 for
returns). **Power:** +2.5pp lift at 80% ⇒ **n ≈ 3,000**. **Stop:** at n=1,000 if the
lift point estimate < 0 → STOP. **Decide:** at n=3,000 on the CI. At 15–25
triggers/day that is 4–7 months. **Control arm:** the same triggers priced off the AF
aggregate at the same second; the gap is the value of the fresh board. **Gate before
building the bot:** read `oufid.jsonl`; if AF O/U is unfaithful, the bot uses only
1x2-derived triggers until the collected board has its own history. Mechanics
constraint stated up front: 5–10 s acceptance delay and event suspension kill any
state-change trigger; only slow-state triggers can survive, and at 45 s cadence we
are the slow party, not the book.

### Phase 2 — promotions, boosts, free bets, acca insurance (🤖 OWN, ongoing, ≈ 0.5 day of code)

Probably +EV, **not** deterministic: real terms decide the sign (min odds pushing
onto the long side, max boosted stakes €5–20, stake-not-returned, rollover multiples
at ~7% margin per cycle, single-use). **No exchange is EMTA-licensed, so nothing
can be hedged** — every free bet is variance-bearing. Build: (i) owner collects each
book's current T&Cs into a terms table; (ii) `promo_ev.py` computes EV from the
Shin-de-vigged consensus fair price **under the stated terms**; (iii) promo ledger
(mig 355) logging EV before the bet and realised P&L after; (iv) monthly
realised-vs-EV check, kill on two consecutive months > 1.5 sd below. This is also
the correct reason to open Olybet / Optibet / Betsafe / Paf / Tonybet / bet365.ee
accounts — more promotions, not tighter prices.

### Phase 3 — the real-money gate (any strategy, ever)

1. Paper first, at the executable price at the book we would use, recorded at
   decision time with a freshness stamp (≤60 min or void).
2. Promote on the strategy's pre-registered primary metric and n.
3. Real money at **€10 flat**, one bot, one book, after Phase 0 mutation tests pass.
4. **Log the maximum stake the book accepts on every placement.** All 143 stakes to
   date were €10 requested / €10 applied; limiting is unmeasured.
5. Weekly realised-vs-EV review; stop at the pre-registered n.

### Explicitly NOT in the plan

Model-anchored staking or retrains "for OWN"; pre-match sharp-anchor staking at any
floor; new bolt-on market bots; a Pinnacle scraper (`pinnacle_movement_research.py`
exists, self-disabled 2026-06-09, spike doc says DO NOT SHIP — the only field worth
taking from the guest API is `limits[].amount`, and only for the PICKS anchor); an
Epicbet placer before Phase 1b passes n=1,000 (2–3 days of Cloudflare + auth + slip
work); Kelly.

---

## 5. The ceiling — say it before spending another week

If a +2–3% edge exists: €10–25 × 15–25 bets/day × 365 ⇒ **€1.1k–6.8k/year** before
variance and before limits. At €10 and 15/day the one-sigma annual band is **±€3k**,
so a successful year is statistically indistinguishable from a losing one.
**Expected value on current evidence: ≤ 0.** Coolbet and Epicbet restrict winners;
promotional volume is what survives restriction. Winnings from EMTA-licensed
operators are tax-free for individuals — no tax drag, but it creates no edge.

Recommendation: **Phase 0 regardless. Phase 2 now. Phases 1a and 1b as two bounded
builds with the stop rules above, and no other OWN research until they report.**

---

## 6. 👥 PICKS — three things wrong on the public surface today (verified file:line)

1. **`/performance` publishes the model-era record with no banner.** Cohort filter
   `performance/page.tsx:229-235`, `CALIBRATED_SINCE = "2026-05-04"`
   (`engine-data.ts:3383`). Headline +7.4% at exec price vs −3.5…−5.7% placeable
   (§2b). `/picks` refuses to link to it (`src/app/picks/page.tsx:13`); `/performance`
   mounts the forward-test panel anyway. Banner it as a closed model-era ledger or
   take it down.
2. **`/api/v1/track-record`** `meta` (`route.ts:318-345`) has `price_basis` but no
   `edge_basis`; `/api/v1/upcoming/route.ts:126` has it. The landing hero reads
   `roi_pct` (`src/app/page.tsx:37,61`).
3. **`/admin/shadow-bots`** hardcodes `oddsFloor: 2.8/1.8/2.8` (`page.tsx:70,81,92`)
   instead of importing `engine-floors.ts`. Telegram webhook still says "available
   on Pro and Elite plans" (`webhook/route.ts:666`).

---

## 7. Codebase hygiene (corrected)

- `workers/jobs/inplay_bot.py` (135,266 bytes, gated by `INPLAY_STRATEGIES_ENABLED`
  in `live_poller.py:565`) and `workers/automation/coolbet_inplay.py`; plus
  `coolbet_placer.place_all_inplay_bets`. **Deferred 2026-09-15 → `INPLAY-BOT-DELETE`** (10 live modules incl. the settlement poller and 139 test lines reference them; not a same-day deletion).
- ~~`pick_trigger_matcher.BOOK_MARKET_BOTS`: 4 of 11 entries map to inactive bots~~ **deleted 2026-09-15 (Phase 5)**.
- ~~`bot_configs.WIDE_CONFIGS`; the line-shop O/U stop~~ **deleted 2026-09-15 (Phase 5)**; `_ODDS_TOLERANCE` is still used by the API placer's legacy flow and stays.
- `pick_generator.generate` / `on_odds_written` / `match_and_emit` /
  `compute_triggers` swallow every exception — the "silent zero" shape.
- `PIN_CROSS_DRIFT_VETO_ENABLED` (`daily_pipeline_v2.py:3697-3708`) — a documented
  veto that counts and places anyway.
- Web: ~700–900 lines of tier/Stripe code with no purchase path; `stripe`/`svix`
  deps; README says Next 15 on Vercel (it is 16.2.4 on pm2). `.env.*` files are
  gitignored (`.gitignore:35`) — confirmed.
- `PRIORITY_QUEUE.md`: 92 ⬜ and 20 🔄 rows.

---

## Reproduce

```bash
python3 scripts/own_path_kill_criterion.py --days 30 --align-min 15
python3 scripts/own_sharp_config_sweep.py --days 150 --diagnostics --bot-clv
launchctl list | grep oddsintel
launchctl print gui/$(id -u)/com.oddsintel.best-price-router | head -40
python3 -m workers.automation.coolbet_control --status
```
Reviewer scratch scripts (not committed): `db.py`, `ev_ownbook.py`, `bestofn.py`
in the session scratchpad — the queries are described inline in §2.
