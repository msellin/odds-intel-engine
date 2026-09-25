# SYSTEM MAP — the one place that explains picks, bots, and every %

> **Direct live-odds books (4, into `odds_snapshots`; Tonybet added 2026-09-23, pre-match, placeable):** **Coolbet** (Imperva → VPS via Estonian egress since 2026-09-23), **Unibet-Site** (DataDome → VPS, logged-out Chrome, since 2026-09-23), **Epicbet** (Cloudflare → VPS via FlareSolverr, the most robust; 119 markets, EPICBET-ODDS-INGEST-2026-08-27) — plus AF's 13-book feed. We COLLECT 15 market families but model/pick on only 7 (see USE-COLLECTED-MARKETS in PRIORITY_QUEUE).


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
| Why that floor | the model is *noisier* than the market, so a small edge is mostly model error — demand a big one to filter noise | ~~Pinnacle is *near-true*, so a 3% overlay is a **real** 3%~~ **FALSE — see the correction below. The 3% floor is currently UNSUPPORTED.** |
| Fires… | when our model disagrees a LOT with the book | rarely — Coolbet ≈ Pinnacle, so beating it by 3%+ is uncommon |
| Known failure | at 13% it still adverse-selects longshots → −21% OOS (the trigger bot) | over-strict floor (e.g. 13%) → never fires |

**The model edge is a DERIVATION, never a stored fact** (EDGE-IS-DERIVED-NOT-STORED,
2026-09-22). `cal_prob − 1/odds` is a function of the two numbers beside it, so any
column holding it separately can drift from them — and did. `simulated_bets.edge_percent`
was `numeric(5,2)`: two decimals on a number whose floors are themselves specified to two
decimals, so Postgres rounded every write up to a whole percentage point and
`stored >= floor` was true across the entire band `[floor − 0.005, floor)`. **114 picks
all time (26 in 90d, 14 in 30d) cleared a floor their real edge missed; 0 were wrongly
rejected** — it could only ever err in our favour on paper and against us in reality.
Migration 367 widened the column, `store_bet` now derives the value it writes from the
same price and probability in the same row, and every gate re-derives on read through
`coolbet_placer.model_edge`. **Never gate, publish or compare on a stored model edge;
derive it.** (The SHARP edge is a different quantity — multiplicative — and must not be
routed through that helper.)

**A 3% sharp edge and a 13% model edge filter to roughly the same strictness** — they
just measure against different rulers. Putting a model floor on a sharp edge (or vice
versa) is the classic mistake; the `Anchor` column in the bot tables below says which
ruler each bot uses.

**How the two edges are LABELLED in public** (MODEL-EDGE-LABEL, 2026-09-15). Both
edges land in the same Telegram channel and on the same `/picks` page, so the label
has to say which ruler the number was measured against — otherwise a reader compares
a 16% and a 3% and concludes the 16% is five times better, when they are not the same
quantity at all:

| Surface | Model-anchored bots (e.g. `bot_v10_1x2`) | Sharp-anchored publisher |
|---|---|---|
| Telegram line | `📈 Model edge: +X%` (`workers/automation/coolbet_signaler.py`) | `📈 Edge vs sharp line: +X%` (`scripts/publish_picks_forward_test.py`) |
| `/picks` column | `Model edge` | `Edge vs sharp` (both from `EDGE_LABEL[p.edge_kind]` in `picks/page.tsx`, fed by `picks_public_all.edge_kind`) |
| Channel bio | says the channel carries **two** bots and names both methods | — |

Machine-checked by smoke test `EDGE-LABELS-DISTINCT`: a bare `📈 Edge:` in either
publisher fails CI.

> ### ⚠️ CORRECTED 2026-09-14 — the sharp floor's stated justification is false
>
> Three independent audits settled this. **Pinnacle is a genuinely good price — but it
> is not *near-true* on the fixtures our bots actually fire on**, and the 3% floor was
> derived from the premise that it is.
>
> **What is TRUE about Pinnacle** (don't over-correct — an earlier version of this
> doc claimed Pinnacle was the *widest* book in the feed, and that was wrong):
> * Paired on the same fixtures at the same moment, Pinnacle is narrower than 14 of 16
>   books. The "9.18% vs Coolbet 7.79%" table compared each book on *its own* fixture
>   population — it measured coverage breadth, not sharpness (the repo's own
>   `ANALYSIS_GOTCHAS` §10 trap). On the fixtures Coolbet covers, Pinnacle reads 7.79%.
> * On majors it reads **3.55% median** (Serie A 3.31, La Liga 3.31, EPL 3.36), and its
>   `is_closing` rows sit at **3.86%**.
> * It beats all 16 AF books on paired log-loss, 16 of 16, and beats the 15-book
>   consensus on 60.1% of fixtures (z≈6.4).
>
> **What is FALSE — and why the floor does not follow:**
> * On *our slate* the median is **~9.2%**, with 58.9% of fixtures at ≥9%. Pinnacle
>   prices obscure leagues at 9–13%; that is real Pinnacle behaviour on low-limit
>   markets, not feed degradation. A 3% overlay on a 9%-margin triple is not a real 3%.
> * Worse, the sharp trigger bots **adverse-select the widest quotes**:
>   `bot_coolbet_trigger_sharp_1x2_v1` fires at a 10.02% median Pinnacle overround out
>   of a 7.73% bettable pool. The "overlay" is partly manufactured by the anchor's own
>   margin.
> * Once de-vigged, Pinnacle is statistically **indistinguishable** from Coolbet,
>   Epicbet, Unibet-Site, Betano and Bet365 on paired log-loss (every |t| < 1.1).
>
> **The load-bearing defect is TIME ALIGNMENT.** The sharp selection rule selects on
> staleness: median anchor↔bet quote gap is 0 min across all candidate legs but
> **289 min among the legs the rule picks**. Align the two quotes and the apparent edge
> collapses. Measured on the publish rule (edge≥3%, odds≤4.0): **+8.47% unaligned →
> +5.5% aligned**, CI [−0.7, +11.7]. Roughly 3pp of it was pure soft-book staleness.
>
> **Status of the 3% floor: UNSUPPORTED, not disproven.** No demonstrated edge; point
> estimate positive but the CI includes zero. Any figure quoted against this anchor
> must state its time alignment.
>
> Related: `clv` is a **raw price ratio with no de-vig**
> (`settlement.py:613`), so break-even CLV equals the closing book's margin —
> `EV ≈ (1+clv)/(1+m) − 1`, m ≈ 7.6% measured. Verified to 0.04pp on 18,759 bets.

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

Anchor = which edge it uses (§1). "Money" = whether the registry declares it a **real**-money
bot or **paper**. ⚠️ Since #139 phase A (2026-09-24) which bots CAN stake is not this column: it is
the placement-path rule (`placement_gate.placement_path_reason` — pre-match `shadow_bets` priced at
Coolbet / Unibet-Site; 11 active bots today, incl. the sharp-trigger bots marked paper here), and which
MAY stake is the audited per-bot `coolbet_placer_bots` switch on /admin/bots, every row seeded OFF (§4). Floors shown are the gate a pick must
clear. This table is generated from the registry — do not hand-edit rows; edit the
registry and regenerate.

### Real-money capable · Coolbet UI placer

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| ~~`bot_ou35_model_v1`~~ **RETIRED** | O/U 3.5 | model | 8% | 1.80 | paper | Model-edge O/U 3.5 vs Coolbet's own 3.5 price (own isotonic calibration). Paper. +7.8% not-robust, accruing forward.  ⚠️ **RETIRED 2026-09-25** (migration 438, owner) after the first 'review this bot' flag (#155): 460 settled, sharp-anchor CLV −4.5% (upper 95% −4.0%). Picks stay in the totals (#157). |
| `bot_coolbet_1x2_model_v1` | 1x2 | model | **10%** | 2.80 | **REAL** | **FAVLONG-CUTS-2026-09-09: HOME-UNDERDOGS ONLY** at model edge ≥10% & odds ≥2.80. Home-favs lose, aways aren't fold-robust, draws are a sharp edge the model can't see (§57) → the one robust 1x2 engine is home-underdogs, robust to 10%. Real money, per-bot toggle. (NB the pooled/paper `_MIN_EDGE_BY_MARKET['1x2']` stays 13% — see below.) |
| `bot_coolbet_ou_model_v1` | O/U 2.5 | model | 8% | 1.80 | **REAL (toggled OFF)** | Places our calibrated model's O/U picks at Coolbet's own price when model edge ≥8% & odds ≥1.80. ⚠️ **`ui_place_enabled=false` since 2026-09-13** (migration 335, OU-CALIBRATOR-DOMAIN-MISMATCH §5b): every pick it staked came from a Platt curve fitted on raw ensemble probs and applied to Pinnacle-shrunk probs, so the edge was manufactured. n=32, −€139.30, −43.5% ROI, CLV −5.7% (t=−4.6). Still in `PLACEABLE_BOTS` — the code boundary is unchanged, only the runtime toggle. |

### Trigger engine · model vs sharp anchor (paper)

> **⭐ ALL SHARP TRIGGERS NOW REFUSE A STALE DECISION QUOTE (SHARP-TRIGGERS-REFUSE-STALE,
> 2026-09-20).** `pick_trigger_matcher.FRESHNESS_MAX_AGE_MIN` caps the decision quote at
> **60 min for every strategy** — `sharp_1x2`, `sharp_ou25` and `sharp_1x2_tight`. It used
> to gate the tight instrument ONLY; the others took whatever the last sweep left in
> `odds_snapshots`, however old, and on 2026-09-19 `bot_coolbet_trigger_sharp_1x2_v1`
> raised a pick at 01:15 UTC off an 18:15 quote — **seven hours stale, mid-outage**.
>
> Not a tuning choice. `edge` is computed against a price, a price nobody could take is
> not a price, and the error is DIRECTIONAL: CLV scores the decision quote against the
> close, so an old quote the market has moved away from scores as a **win**. Measured
> within the age-recorded era (so it is not confounded with time):
>
> | bot | fresh ≤60m CLV | stale >60m CLV |
> |---|---|---|
> | `bot_coolbet_trigger_sharp_1x2_v1` | −1.50% (n=62) | **+5.30%** (n=15) |
> | `bot_unibet_trigger_sharp_1x2_v1` | −5.04% (n=42) | +0.37% (n=25) |
> | `bot_coolbet_trigger_sharp_ou_v1` | −5.21% (n=11) | **+7.35%** (n=8) |
> | `bot_unibet_trigger_sharp_ou_v1` | −3.90% (n=10) | +2.62% (n=10) |
> | `bot_trigger_1x2_sharp_tight_v1` *(already gated)* | −3.89% (n=138) | **n=0** |
>
> Stale positive, fresh negative, every bot; the already-gated one has no stale legs at
> all. Small n on the stale side (8–25) — the DIRECTION justifies the gate, not the size.
>
> ⚠️ **This puts a discontinuity at 2026-09-20 in those four bots' series**, and they are
> accumulating toward the pre-registered n=300 — roughly 25–40% fewer legs for the Unibet
> arms on recent days. Deliberate: the excluded legs were never actionable. **Split on
> this date when reading their CLV.** Nothing is backfilled or deleted, and
> `decision_quote_age_min` is on every leg since 2026-09-15, so the same cut is
> reproducible over the history. Smoke `SHARP-TRIGGERS-REFUSE-STALE` pins that EVERY
> matcher strategy is gated — not a list of names, because a list passes when someone
> adds a fifth strategy with no ceiling, which is how the first four ended up ungated.

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_coolbet_trigger_sharp_1x2_v1` | 1x2 | sharp | 3% | 1.01 | paper | Sharp twin: fires when Coolbet's 1x2 price beats the de-vigged Pinnacle line by ≥3% (no odds floor — experimental). Paper. Head-to-head vs the model twin. |
| ~~`bot_coolbet_trigger_ou_v1`~~ **RETIRED** | O/U 2.5 | model | 8% | 1.80 | paper | Fires when Coolbet's O/U 2.5 price lands in the MODEL trigger window (model edge ≥8% at Coolbet's own odds). Paper. OOS backtest +4.3% not-robust. ⚠️ **RETIRED in the DB** (migrations 336/348, BOT-RETIREMENT-ON-CLV); kept struck-through for history. Struck 2026-09-15 — the drift test was registry→map only and could not see a map row for a bot the registry had dropped. |
| `bot_coolbet_trigger_sharp_ou_v1` | O/U 2.5 | sharp | 3% | 1.01 | paper | Sharp twin: fires when Coolbet's O/U 2.5 price beats the de-vigged Pinnacle line by ≥3% (no odds floor — experimental). Paper. Head-to-head vs the model twin. |
| `bot_unibet_trigger_sharp_1x2_v1` | 1x2 | sharp | 3% | 1.01 | paper | Unibet 1x2 sharp trigger. **Where the DRAW edge the model can't see should surface** (soft-book mispricing vs de-vig Pinnacle, §57). Paper. |
| ~~`bot_unibet_trigger_ou_v1`~~ **RETIRED** | O/U 2.5 | model | 8% | 1.80 | paper | Stage 3b — Unibet O/U 2.5 model trigger. Paper twin of the Coolbet O/U trigger. ⚠️ **RETIRED in the DB** (migrations 336/348, BOT-RETIREMENT-ON-CLV); kept struck-through for history. Struck 2026-09-15 — the drift test was registry→map only and could not see a map row for a bot the registry had dropped. |
| `bot_unibet_trigger_sharp_ou_v1` | O/U 2.5 | sharp | 3% | 1.01 | paper | Unibet O/U 2.5 sharp trigger. Paper. |
| `bot_trigger_1x2_sharp_v1` | 1x2 | sharp | 3%–**8%** | 1.01 | paper | Book-agnostic SHARP 1x2 trigger. The 3% floor is set EXPLICITLY, not inherited: a sharp edge is measured against a near-true line and is never comparable to a model floor (a 13% overlay on Pinnacle is nearly unobservable — max seen +6.6% — so the bot would simply never fire). **There is now also an 8% CEILING**, for the mirror-image reason — above the observed maximum overlay an "edge" is a broken price. **⚠️ n=0: all 25 picks voided 2026-09-20**, see §2a. |
| ~~`bot_trigger_ou_model_v1`~~ **RETIRED** | O/U 2.5 | model | 8% | 1.80 | paper | Book-agnostic MODEL O/U 2.5 trigger. Replaces the two O/U model twins above. ⚠️ **RETIRED in the DB** (migrations 336/348, BOT-RETIREMENT-ON-CLV); kept struck-through for history. Struck 2026-09-15 — the drift test was registry→map only and could not see a map row for a bot the registry had dropped. |
| `bot_trigger_ou_sharp_v1` | O/U 2.5 | sharp | 3%–**8%** | 1.01 | paper | Book-agnostic SHARP O/U 2.5 trigger. **⚠️ n=0: all 6 picks voided 2026-09-20**, see §2a. |
| **`bot_trigger_1x2_sharp_tight_v1`** | 1x2 | sharp | **2%** | 1.01 (**odds ≤ 2.50**) | paper | **INSTRUMENT, not a strategy** (SHARP-TIGHT-INSTRUMENT-2026-09-15). The one OWN configuration two independent research rounds agreed was worth measuring and neither thought was worth a euro. It exists because the original 70,200-cell sweep *could not express it*: that grid swept a constant expected-ROI floor (`P×odds−1`) while this gate is a constant probability-difference floor (`P−1/odds`), and since `roi_edge = prob_edge × odds` the latter is a **curve in odds** — no constant-floor cell can represent it (§42). Swept correctly it is the only survivor: n=225, ROI +17.07%, CI [+4.18,+29.95], no losing fold, OOS +23.40%. **But both rounds judge it luck**: a 12-day effect (+0.99% n=79 pre-09-02 vs +25.76% n=146 after; Coolbet alone on a constant 37-day pool does the same), and margin-corrected own-book CLV of −5.4% to −7.6% beside those ROIs, against ~−7.2% for a random leg — i.e. the selection buys ~1.9pp of CLV, real but far short of the 7–8% vig. Pooled over Coolbet/Epicbet/Unibet-Site (+ Tonybet from 2026-09-24, sweeper-odds audit; venue kept per row) in ONE bot because the result was measured pooled. **Promotion requires margin-corrected own-book CLV > 0 at n≥300; ROI may never promote it at any value** (per-bet sd ≈1.3 ⇒ a true +3% ROI needs ~15,600 bets). Pre-registration: `dev/active/own-sharp-tight-preregistration.md`. |
| **`bot_unified_gate_1x2_paper_v1`** | 1x2 | model | **10% flat** | **2.80** | paper | **INSTRUMENT, not a strategy** (UNIFIED-GATE-INSTRUMENT-2026-09-22, [[#033]]). Every 1x2 selection — **home, draw AND away** — at a FLAT 10% model edge and odds ≥ 2.80, across both placeable books. **The flat floor IS the hypothesis**: the registry's selection-aware floor is 10% home / 13% draw+away, and inheriting it would make the instrument test the very thing it is meant to be compared against. **Why it must exist:** the owner's rule — *"keep draw and away and home underdogs; odds 2.8+ and the 10% floor ensure nothing suspicious gets past"* — has never been testable, because the calibrated cohort at odds ≥ 2.80 is **236 HOME out of 240** (our own home-only mirror stopped generating draws and aways). Every draw/away figure quoted so far, including *"draws are −31.5% over n=95"*, rests on a ~98%-home population and answers a different question. More analysis on those rows cannot fix that; only new rows can. **No edge ceiling** — model-anchored, where a 20% edge is ordinary (ceilings belong on sharp-anchored bots, where fair value is near-true). Publishes nothing (`show_on_picks` FALSE), stakes nothing, not in `PLACEABLE_BOTS`. ⚠️ **Could not have been built correctly before 2026-09-22**: until SHARP-FLOOR-STACKED-ON-MODEL-FLOOR was fixed ([[#007]]) the router re-imposed the selection-aware floor over any explicit `edge_floor`, so draws and aways would have run at 13% while the config said 10% — it would have produced clean-looking numbers answering the wrong question. Pre-registration: `dev/active/unified-gate-instrument-preregistration.md`. |

> **RETIRED 2026-09-14 (migration 336) — the three MODEL-anchored 1x2 trigger
> bots.** `bot_coolbet_trigger_1x2_v1`, `bot_unibet_trigger_1x2_v1` and
> `bot_trigger_1x2_model_v1` are gone from this table. On placeable books they
> ran CLV **−9.16% (t=−14.5, n=277)**, **−8.68% (t=−7.1, n=309)** and **−8.44%
> (t=−12.2, n=381)**, and a search over edge floors (5/8/10/13/15%), odds floors
> (2.2/2.8/3.2) and each selection alone produced **no** configuration that is
> CLV-positive in all three walk-forward folds. Their SHARP twins remain and are
> the system's best performers on the same fixtures and prices — which is the
> cleanest evidence here that the **anchor**, not the market or the book, is what
> separates a winning bot from a losing one.
>
> **The four MODEL-anchored O/U trigger bots are NOT retired**, deliberately.
> Every settled pick they own falls inside the OU-CALIBRATOR-DOMAIN-MISMATCH
> window (2026-09-03 → 2026-09-13), so excising it leaves them with zero
> evidence — not weak evidence, none. Staged at
> `dev/active/HELD_retire_model_anchored_ou_losers.sql` pending era-3 volume.
>
> ⚠️ **A DB retirement only became self-enforcing on 2026-09-14.** Before that,
> `_bot_id()` in both `pick_generator` and `pick_trigger_matcher` looked up
> `bots WHERE name=%s` with no `retired_at` check, so a retired bot kept writing
> `shadow_bets` — it vanished from the page and carried on underneath. Both
> lookups now require `retired_at IS NULL` (RETIRED-BOTS-KEPT-GENERATING). The
> analogous gap in the placer's `load_picks` was closed 2026-09-24 (#131) — see §4c.

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
| ~~`bot_corners_paper_shadow_v1`~~ | corners | sharp | 0% | — | **RETIRED 2026-09-14** | Margin-corrected own-book CLV **−4.86%**, CI [−5.59,−4.12], **t=−12.93, n=467** (migration 351). Carried for months as "unjudgeable" because its settler believed no corners closing anchor existed — **false**: Pinnacle prices corners on 2,045 fixtures / 43 lines in 30d, covering 95% of Coolbet's corners slate and 87% of Epicbet's, and the bot already de-vigged that same line to SELECT. The closes were in `odds_snapshots` the whole time; 568 settled picks went unjudged and an unanchored +9.60% ROI sat on the dashboard looking like evidence. Also priced against `'Unibet'` (the AF feed, 33.1% phantom-high, dead since 2026-09-12) rather than `'Unibet-Site'`. *(Still writes out-of-sample rows; v2 from 2026-09-24 line-shops all four own books — v1's Betano/"Unibet" gate was effectively Betano-only.)* *(#162 W1.3, 2026-09-26: its own settler is deleted — the generic shadow settler now grades its corners legs from match_stats and writes the own-book close + CLV; team-total and 1H-1x2 likewise.)* |
| ~~`bot_team_total_paper_shadow_v1`~~ **RETIRED** | team totals | sharp | 0% | — | paper | Best Epicbet/Betano/Unibet full-match team-total price vs de-vigged Pinnacle line (sharp edge ≥0%). USE-COLLECTED-MARKETS: a market we collect but never modelled; settles from the final score (no coverage gap). Paper, accruing forward. ⚠️ **RETIRED in the DB** (migrations 336/348, BOT-RETIREMENT-ON-CLV); kept struck-through for history. Struck 2026-09-15 — the drift test was registry→map only and could not see a map row for a bot the registry had dropped. *(v2 from 2026-09-24, sweeper-odds audit: line-shops all four own books — Coolbet, Epicbet, Unibet-Site, Tonybet; v1's "Unibet" never matched the loaded 'Unibet-Site' rows and Betano is no longer accessible)* |
| ~~`bot_1h_1x2_paper_shadow_v1`~~ **RETIRED** | 1H 1x2 | sharp | 0% | — | paper | Best Epicbet/Betano/Unibet first-half 1X2 price vs Shin-de-vigged Pinnacle 1H triple (sharp edge ≥0%). USE-COLLECTED-MARKETS: a 3-way market we collect but never modelled; settles from the HT score (no gap). Paper, accruing forward. ⚠️ **RETIRED in the DB** (migrations 336/348, BOT-RETIREMENT-ON-CLV); kept struck-through for history. Struck 2026-09-15 — the drift test was registry→map only and could not see a map row for a bot the registry had dropped. *(v2 from 2026-09-24, sweeper-odds audit: line-shops all four own books — Coolbet, Epicbet, Unibet-Site, Tonybet; v1's "Unibet" never matched the loaded 'Unibet-Site' rows and Betano is no longer accessible)* |

### In-play slow-state rig (paper) — OWN Phase 1b

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_inplay_slowstate_v1` (collector **MOVED TO THE VPS 2026-09-22**) | in-play O/U 2.5 + 1x2 | none (book's own de-vigged prob) | — | **cap 2.20** | paper | **LIVE arm.** Two LOCKED slow-state triggers at Epicbet's on-screen price: T1 0-0 at 35'–54' → UNDER 2.5; T2 two-goal lead at 70'–89' → the leader. Both only through a price ≤ 2.20 (the long side carries ~14% relative in-play margin vs ~4% short — INPLAY_STRATEGY_CANDIDATES). Primary metric: realised hit-rate minus the book's de-vigged prob, cluster-robust on fixture; CLV inadmissible in play. **STOP at n=1,000 if the lift is negative; decide at n=3,000.** Board + bot: `workers/jobs/inplay_collector.py` (Mac launchd KeepAlive) → `inplay_book_quotes`; read: `scripts/inplay_slowstate_eval.py`. |
| `bot_inplay_slowstate_afctl_v1` | in-play O/U 2.5 + 1x2 | none | — | cap 2.20 | paper | **CONTROL arm.** Same triggers at the same instant priced off API-Football's live aggregate (median 40 s stale). Live − control = the value of the fresh board. Two bots because `shadow_bets_unique` de-duplicates on (bot, match, market, selection). |

> Epicbet only, deliberately: Coolbet's in-play margin is tighter (4.96%/5.16% vs 6.41%/6.44%) and Coolbet has the placer, but its board sits behind Imperva and the request volume is what escalates the wall (RELIABILITY_LEDGER §6/§10). Trigger findings transfer across books; price findings do not. Coolbet in-play collection is a follow-up with its own Imperva budget.

### Internal model / strategy validators (paper)

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| ~~`bot_v10_all`~~ | — | — | — | — | **SPLIT 2026-09-22** | **RETIRED BY SPLIT, not by performance** (migration 375, [[#040]]). Every row it owned was re-attributed by market to the two bots below, so its record is not lost — it is disaggregated. |
| `bot_v10_1x2` | 1x2 | model | — | — | paper | The 1x2 half of the old reference bot. De-vigged Pinnacle CLV **+2.50%** (n=335, 95% CI [+0.41, +4.60]), ROI **+7.3% at executable prices** (n=400; the stored-`pnl` figure is +12.80%, inflated by the `odds_at_pick` high-water basis — STALE-BEST-ODDS, see [[#074]]) — `calibrated`. ⚠️ **Read the record as three months, not five:** monthly CLV runs May −0.87%, Jun −2.15%, Jul **+8.35%**, Aug **+8.24%**, Sep **+7.25%**, so the whole positive pooled figure is July-onward (n=142 vs n=193 before it). |
| `bot_v10_1x2_newplus_v1` | 1x2 | model (combined) | — | — | paper | **"Match result — new model"** ([[#152]], 2026-09-25, owner). Twin of `bot_v10_1x2` on the NEW+ model (`r1x2_comb_v1`, used as is): EV ≥ 3% flat, **odds 1.30–3.00** (LANES cap 2026-09-25: confirm CLV +2.66% vs +0.60% uncapped), min_prob 0.30. (Until 2026-09-25 it SKIPPED VIP-held picks via a re-derived `vip_exclude`; since [[#164]] it records them and they are HELD BACK until kickoff — see *VIP FIRST* below.) `bot_v10_1x2` is unchanged because its LIVE record is CLV +6.6% / +6.0% / +1.8% Jul/Aug/Sep (n 178) while the backtest that preferred this rule priced the old one at opening quotes (ANALYSIS_GOTCHAS #84). Compared live after 50–100 settled picks. `testing`, on /performance via `bots.show_on_performance` (migration 427). |
| `bot_rating_1x2_v1` | 1x2 | model (rating) | — | — | paper | **"1x2 market NEW"** (added 2026-09-24, [[#141]], owner request). A **twin of `bot_v10_1x2`** — same tier thresholds, odds 1.30–4.50, min_prob 0.30, cohort, Pinnacle veto, meta gate and staking — whose 1X2 probability comes from the walk-forward **rating model** (`rating_1x2_predictions`, `r1x2_d8plus_v1`, gated rows only), used **as is**. It skips `calibrate_prob` on purpose: with `shrinkage_alpha_*_1x2` ≈ 0 that step returns Platt(Pinnacle), which makes `bot_v10_1x2` in effect a *market* bot, so the pair is a clean model-vs-market A/B. Rating model on the 08-31..09-24 holdout: log-loss 1.008 vs 1.071 for the old head, but **α vs Pinnacle = 0** — read its ROI only alongside CLV. `experimental`: `simulated_bets`, not public, no placement path. MODEL_WHITEPAPER §4.4. |
| `bot_combined_1x2_v1` | 1x2 | model (combined) | — | — | paper | **"1x2 market NEW+"** (added 2026-09-24, [[#141]] round 3b, owner request). Same twin of `bot_v10_1x2` as `bot_rating_1x2_v1`, priced by the **COMBINED** model (`r1x2_comb_v1`: ratings + de-vigged 18-book consensus + Pinnacle, API-Football only where no book prices the match; 0.9763 vs 1.0711 log-loss on the 08-31..09-24 holdout). Because the probability is built mostly from market prices, a pick means the quoted book sits away from the consensus (the consensus-outlier strategy, Kaunitz et al.), not that our model disagrees with the market — read it on CLV. The pair of NEW bots separates the two questions. `experimental`, `simulated_bets`, no placement path. MODEL_WHITEPAPER §4.4b. |
| `bot_combined_1x2_ev5_v1` | 1x2 | model (combined) | — | — | paper | **⭐ VIP bot ([[#148]], `bots.vip`, `VIP_BOTS`)** — live picks only to Pro/Elite Telegram DMs + the private VIP channel (`TELEGRAM_VIP_CHAT_ID`), each labelled EV8 / EV5; excluded from the public channel; pending rows hidden from anon/authenticated by RLS (migration 420). **"1x2 NEW+ EV5"** (added 2026-09-24, [[#141]] B4, pre-registered in `dev/active/1x2-model-rebuild-plan.md`). The combined model as a consensus-outlier bettor in its natural unit: **EV = p × odds − 1 ≥ 5%, flat across tiers**, Pinnacle price required, no min_prob, odds 1.30–6.00, one pick per match; stored `edge` stays probability points (store_bet derives it). Backtest B2 CLV +2.0% (n=1,050; +1.1% at our own sweepers), same window as B so optimistic. Owner reviews at 20 / 50 / 100 settled picks. `experimental`, `simulated_bets`, no placement path. **r2 (#162 W7.7/W7.8, 2026-09-25): runs EXACTLY the pre-registered B2 rule** — `exact_rule` skips the ~8 inherited legacy gates (mid-band, 0.12 veto, sharp home gate, odds-movement veto, ALN-1, min-Kelly stake drop, meta-model, PIN-cross-drift) that dropped 35 B2-clearing candidates vs 26 accepted in 7 days — and holds ONE pick per match across runs (it once held home AND away on one fixture). Expect roughly double the volume; before/after = `rule_version` r1 vs r2. |
| ~~`bot_combined_1x2_ev8_v1`~~ **RETIRED 2026-09-25** | 1x2 | model (combined) | — | — | paper | **"1x2 NEW+ EV8"** (added 2026-09-24, [[#141]] B4). As EV5 with EV ≥ 8%. Backtest B2 CLV +3.1% (n=557; +2.7% at our own sweepers). `experimental`, `simulated_bets`, no placement path. **Retired 2026-09-25 (migration 444, owner):** a strict subset of the VIP bot `bot_combined_1x2_ev5_v1` — every EV8 pick is already in the VIP ledger with its EV8 tag, so the EV8-vs-EV5 comparison is a split of that ledger (VIP detail view), not a second bot. |
| `bot_ou_sharp_early_v1` | ou (1.5/2.5/3.5) | sharp (Pinnacle) | — | — | paper | **⭐ VIP bot #2 (O/U, owner 2026-09-25; migration 424)** — live picks only to Pro/Elite DMs + the private channel, EV8/EV5 labels, public once settled. **"O/U EARLY"** ([[#149]], 2026-09-25; `dev/active/market2-model-plan.md` rounds O1–O3). A soft book's O/U quote beats Pinnacle's power-de-vigged fair price by EV 5–15% (the 15% cap guards against misposted lines) while ≥ 12 h before kickoff; one pick per (match, line), best EV; every publishable book. Why not a model: O1's combined O/U model lost to Pinnacle on Pinnacle-priced rows. Backtest O3 T3: CLV +7.5% / +6.9%, ROI +10.4% / +10.8% (Aug n=383 / Sep n=459). Job `workers/jobs/ou_sharp_outlier.py` :14/:44. `experimental`, `simulated_bets`, no placement path. |
| `bot_ou_sharp_2anchor_v1` | ou (1.5/2.5/3.5) | sharp (Pinnacle) + consensus | — | — | paper | **"O/U TWO-ANCHOR"** ([[#149]]). As O/U EARLY without the 12 h rule; the book must ALSO beat the leave-one-out consensus of the other books by ≥ 2% EV. Backtest O3 T2: CLV +6.6% (Aug, n=420) / +4.2% (Sep, n=480). `experimental`, `simulated_bets`, no placement path. |
| ~~`bot_v10_ou`~~ **RETIRED 2026-09-24 (migration 399, owner decision on [[#077]])** | O/U 2.5 | model | — | — | paper | ⚑ **RETIREMENT RECOMMENDED 2026-09-23 ([[#077]])** — four independent measurements now return α = 0 for O/U (XGBoost head, double-Poisson, the full residual harness, and a shots+corners rating), no odds floor helps ([[#073]]), and it has published nothing since 2026-09-13. Owner's call; it is harmless where it stands. The O/U 2.5 half. De-vigged Pinnacle CLV **−3.85%** (n=181, 95% CI [−5.01, −2.69]) against ROI −0.5% — **`beta`, deliberately NOT `calibrated`**, because this page's own legend sells `calibrated` as proven and a CI entirely below zero cannot carry it. Survives gotcha 39: negative in **all 5 months** (−1.76% to −4.84%) and **all 7 model versions** (−0.97% to −6.12%), so it predates OU-CALIBRATOR-DOMAIN-MISMATCH (migration 335) — that bug made a bad half worse, it did not create it. Has published nothing since 2026-09-13. |
| `bot_v10_ou_comb_v1` | ou (1.5/2.5/3.5) | model (combined O/U) | EV ≥ 3% | 1.30–3.00 | paper | **"Goals over/under — new model"** ([[#152]], owner 2026-09-25: *"unretire bot_v10_ou — it gets the new ou model"*; migration 443). `bot_v10_ou`'s return on the combined O/U model `ou_comb_v1` (`workers/model/combined_ou.py`, table `ou_model_predictions`; served p = Pinnacle where priced, else combined; used as is — no `calibrate_prob`, no data-tier bump): EV = p×odds−1 ≥ 3% flat across tiers, O/U 1.5/2.5/3.5 over and under, odds 1.30–3.00, min_prob 0.30, **one pick per match** (best EV). Picks O/U EARLY holds or would take (EV 5–15% vs Pinnacle, ≥ 12 h out) are recorded and **held back until kickoff** by `store_bet`'s VIP guard ([[#164]]). **TESTING**: sent to /picks + public Telegram, own record, not in the headline (`show_on_picks`, `show_on_performance`). **Why a twin, not the old row re-activated:** `bot_ledger` counts every `simulated_bets` row of a bot, and `bot_v10_ou` holds 252 settled old-ensemble picks (sharp CLV −3.9%, n 138, 95% upper bound −2.65%) — re-activating it would raise the [[#155]] review flag on day one and sell the old model's record as this rule's (ANALYSIS_GOTCHAS #84). Backtest (08-31..09-24 at open, #152 step-3 machinery extended to 1.5): **61 picks (~2.4/day), sharp CLV +2.0% [+1.0, +3.0]**, ~7% in VIP's range; same window the model was chosen on, so the forward record decides. |
| `bot_high_roi_global_v2` | 1x2 | **model** | 6%/9% by tier | 1.50–5.50 | paper | 1x2 home/away in Spain/Australia/Iceland. **ANCHOR CORRECTED 2026-09-22** — it was recorded as `none` ("internal strategy validator"), which reads as *no fair-value basis*. False: `daily_pipeline_v2` gives it `edge_thresholds` (`1x2_fav` 0.06 / `1x2_long` 0.09) — the **same model edge `bot_v10_1x2` uses** — then filters by league, side and odds band. A filter over model picks is still MODEL-anchored. It mattered because the /performance method chip renders straight off this field, so a customer surface was telling readers this bot priced against something it does not. `ANCHOR_NONE` now means what it says: no model and no sharp reference, i.e. the in-play rig pricing off the book's own de-vigged probability. |
<!-- bot_1x2_specialist, bot_dnb_specialist, bot_summer_specialist RETIRED 2026-09-09 (migrations 323/324) and removed from bot_registry.py:116-119 — do not re-add. -->
<!-- NB: bot generation stores best-of-books odds for these general bots (recommended_bookmaker), NOT the Coolbet/Unibet executable price — the SHADOW-PAGE-ROI-INFLATED gap; per-book executable ROI/CLV is the EXECUTABLE-SHADOW-EVAL work. -->

### ⭐ VIP FIRST — free picks are held back when VIP holds them ([[#164]], owner 2026-09-25)

**The VIP bots** (`bots.vip` = `VIP_BOTS` — kept equal by smoke `VIP-BOTS-MATCH-DB` + a scheduler startup warning, `vip_guard.vip_registry_drift`, #162 W8.8: `bot_combined_1x2_ev5_v1` "1x2 NEW+ EV5", `bot_ou_sharp_early_v1`
"O/U EARLY"; plus their `hide_pending` twins EV8 / TWO-ANCHOR) sell their live picks to Pro/Elite before kickoff
and **never give a pick up**. **Every FREE bot** (any other bot — the `show_on_picks` model bots `bot_v10_1x2`,
`bot_high_roi_global_v2`, `bot_v10_1x2_newplus_v1`, and the published forward-test arms `live` / `consensus_anchor`)
still RECORDS its pick exactly as its own rule decides — its record and the pre-registered test are unchanged —
but the pick is **HELD BACK** (not on /picks or the watchlist, not sent to the public Telegram channel, not listed
as pending on /performance or `/api/performance/bot-legs`, hidden from anon by RLS) **until kickoff** when it is:

| | Rule | Source |
|---|---|---|
| (a) VIP-HELD | a VIP / hide_pending bot has a PENDING pick on the same match + market + selection | the ledger (`simulated_bets`), never re-derived |
| (b) IN VIP'S RANGE at the free pick's decision time and price | 1X2: NEW+ (`rating_1x2_predictions` r1x2_comb_v1) EV = p × odds − 1 ≥ the EV5 bot's own floor (5%), inside its odds range 1.30–6.00 · O/U: `ou_sharp_outlier.early_rule()` — EV vs Pinnacle's power-de-vigged latest price 5–15%, odds 1.30–6.00, ≥ 12 h to kickoff | the VIP bot's OWN config / function, imported, never copied |

(b) is what ends *"free first, VIP later"*: if VIP would take this price now, the free copy waits for kickoff.

**Where it lives — one module, every writer, every surface filters the data.** `workers/utils/vip_guard.py`
decides; `store_bet` (every `simulated_bets` pick), the forward-test `claim()` and the `/picks` board writer stamp
`held_back_until` (= kickoff) + `held_back_reason` on the row; a VIP pick written AFTER free picks on the same
selection stamps them too (`hold_back_followers`). Surfaces filter on the column (migration 439):
`picks_public_all`, `picks_board_public`, `picks_forward_test_public`, the `simulated_bets` anon policy, the
signaler's candidate query (group-wide), and the web's pending views via `bot_ledger_display.held_back`. After
kickoff nothing is sent — a held-back pick simply appears on /picks and in the record. Fails CLOSED
(`guard_error` = held back). **Retired:** the pipeline's `vip_exclude`, which re-derived the VIP rule at the free
bot's CURRENT price with hard-coded constants and SKIPPED the pick — after a price move it let VIP-held picks
through, and three other public paths had no check at all (the #164 leak: 10 in 18 h, 3 sent to Telegram).
**Retroactive (2026-09-25):** pending free picks that broke the rule were held back; ones already sent or settled
keep their record and carry `vip_rule_breach = true` (never deleted, never unsent). Smoke `VIP-FIRST-HOLD-BACK`.

### Pre-registered PICKS forward test (published, not staked)

| Bot | Market | Anchor | Edge floor | Odds | Money | What it does |
|---|---|---|---|---|---|---|
| ~~`bot_sharp_forward_test_v1`~~ **RETIRED 2026-09-24 — split by market into the two rows below ([[#122]], migration 402); same rule, same pre-registered test** | 1x2 + O/U 2.5 | **sharp** | **3%** (multiplicative) | **cap 4.0** | published, not staked | **The picks readers actually see.** `P_shin × best_book_price − 1 ≥ 3%`, odds ≤ 4.0, anchor and bet quote within 60 min. **No daily selection cap** — `TOP_N = 8` was dropped 2026-09-15 (PICKS-NO-DAILY-CAP, owner: *"if possible lets not cap daily picks at all"*); a 60/day runaway breaker is all that remains. **No model output at all.** Flat 1 unit, no Kelly, no bankroll. |
| `bot_sharp_1x2_v1` | 1x2 | **sharp** | **3%** (multiplicative) | **cap 4.0** | published, not staked | **The 1x2 half of the sharp picks** — owns `picks_forward_test` rows with arm='live' AND market='1x2'. clv_sharp +2.27% (n=70) at the split. |
| `bot_sharp_ou_v1` | O/U 2.5 | **sharp** | **3%** (multiplicative) | **cap 4.0** | published, not staked | **The O/U 2.5 half of the sharp picks** — arm='live' AND market='over_under_25'. clv_sharp +0.67% (n=22) at the split. Retirement trigger: clv_sharp CI entirely below 0 at n ≥ 100. |
| ~~`bot_consensus_anchor_v1`~~ **RETIRED 2026-09-23 — split by grade into the two rows below ([[#095]], migration 380)** | 1x2 + O/U 2.5 | **sharp (consensus)** | **3%, ceiling 8%** | cap 4.0 | published, not staked | **The SECOND published arm ([[#068]], 2026-09-22).** Identical rule to the row above in every guard — 3% floor, 60-min alignment, cap 4.0, ratio 0.20, 45-min lead, 14h lookahead — differing in exactly ONE variable: fair value is a **de-vigged consensus of ≥5 bookmakers** instead of a single sharp line. **Why it exists:** the sharp arm's pre-registered ≤4% anchor-overround gate admitted **0 of 173** Pinnacle-priced markets on 2026-09-22 and the channel went dark for two days. That gate is pre-registered, so it was NOT relaxed — this runs beside it and the sharp arm stays byte-identical. **Why a consensus is a legitimate anchor** (measured, n=11,419 matches / 45d): a consensus EXCLUDING our AF-"Pinnacle" predicts as well as that feed (log-loss 0.98339 vs 0.98401, t=+1.76), while the feed's median closing overround is 10.24% against those books' 7.95% — wider than the books it is supposed to be sharper than. **The 8% CEILING is not optional** and the sharp arm must never gain one: `edge = p·odds−1` is maximised by a WRONG price, and 5 of the first 15 qualifying legs cleared 8% against a 7–11 book consensus. **Reported SEPARATELY** on /performance — two rules, two records; pooling them would describe neither. Ledger: `picks_forward_test` WHERE `arm='consensus_anchor'`. **GRADED B/C since 2026-09-23 ([[#094]])** — C = tier-0 league, OR another of Pinnacle/Marathonbet/Betfair/1xBet/SBO sees no edge at the published price, OR edge > 6%. On a 56-day replay (n=677) C returned −25.6% and B +10.6% (B's holdout −3.4%, so B is not proven). **A label, not a gate:** every pick still publishes with its grade on the Telegram message; `grade`/`grade_reasons` columns (migration 379) let the arm be split into two bots later. Evidence: `docs/PUBLISHED_PICKS_GRADING_2026_09_23.md`. |
| `bot_consensus_b_v1` | 1x2 + O/U 2.5 | **sharp (consensus)** | 3% under every credible de-vig (v2), ceiling 6% | **1.20–1.60** | published, not staked | **Grade B — STRONGEST, `beta`** (re-tiered 2026-09-23, [[#098]], migration 381 — the letters shifted DOWN; grade A is reserved for model picks). Every [[#094]] check passes AND odds 1.20–1.60. The only rule positive in all three samples: ours 56 d +17.8% (48), unseen May–Jul +14.7% (29), Beat the Bookie 2015–16 +9.8% (696, Holm p<1e-4); mechanism = favourite-longshot bias. ~1/day. Ledger: `grade='B'`. **Split in the views, not the ledger** — one arm keeps the `(match, market, selection, arm)` de-dupe when a grade flips between runs. |
| `bot_consensus_c_v1` | 1x2 + O/U 2.5 | **sharp (consensus)** | 3% under every credible de-vig (v2), ceiling 6% | cap 4.0 | published, not staked | **Grade C — STANDARD, `testing`** ([[#098]]). Every check passes, odds outside 1.20–1.60. Positive but unproven (+2.9% unseen n=150, +3.3% external n=6,380). The bulk of the channel. Ledger: `grade='C'`. |
| `bot_consensus_d_v1` | 1x2 + O/U 2.5 | **sharp (consensus)** | 3% under every credible de-vig (v2), ceiling 8% | cap 4.0 | **recorded, NOT published** | **Grade D — WEAK, never sent since 2026-09-23** ([[#098]]). Tier-0 league / a second panel book disagrees / edge > 6%. Claimed to the ledger so the record stays checkable; `workers/scheduler.py` skips the send. Loses on our data (−25.6% / −2.8% unseen). Its earlier picks were sent as grade C and stay on /performance; `picks_public_all` hides D rows with no `telegram_message_id`. |
| `bot_sharp_aligned_v1` | 1x2 + O/U 2.5 | **sharp** | 3% (multiplicative) | cap 4.0 | **recorded, NOT published** | **Twin of the sharp picks ([[#161]], 2026-09-25, migration 434), `experimental`.** Live v4 in every gate PLUS: at our own direct books (Coolbet, Unibet-Site, Epicbet, Tonybet) the book's quote and the Pinnacle anchor quote must be **≤ 5 min apart** (API-Football books arrive in Pinnacle's fetch, gap 0). A failing leg is dropped, never re-routed. Tests the audit's +3.9% (≤ 5 min) vs −0.6% (5–60 min) split at our books. Ledger `arm='sharp_own_book_aligned'`, own `rule_version`, evidence in `twin_gate`. Never sent; not in `PUBLISHED_ARMS`; every public view's arm allow-list excludes it. Readout at n=50/100 vs `live` and the junk control (`picks_forward_test_checkpoint --twins`). |
| `bot_consensus_pinconf_v1` | 1x2 + O/U 2.5 | **sharp (consensus)** | 3% under every credible de-vig (v2), ceiling 8% | cap 4.0 | **recorded, NOT published** | **Twin of the consensus picks ([[#161]], migration 434), `experimental`.** Consensus v2 in every gate (grades B/C/D recorded as the parent) PLUS: where a fresh **tight Pinnacle** anchor exists (`workers/utils/anchor.py` `pinnacle_tight`: one-fetch set, ≤ 60 min, overround ≤ 4%) the leg must ALSO have **EV ≥ 0% vs Pinnacle**; no tight Pinnacle → unchanged. Tests the audit's −2.1% vs sharp close for consensus picks at our books. Ledger `arm='consensus_pin_confirmed'`. Never sent. Readout vs `consensus_anchor` and the junk control. |

**This row is unlike every other bot in this map, in four ways.** Read them
before using any number attached to it.

1. **It writes NOTHING.** No `simulated_bets`, no `shadow_bets`. Its ledger is
   `picks_forward_test`; the bot row is a handle so the strategy is not silent,
   and `picks_forward_test_shadow` (migration 345) is a READ-ONLY projection in
   `shadow_bets` shape for anything that wants to consume it. Migration 342's
   header has the reasoning: both bet tables are bot-scoped with staking
   semantics, and every bot-cohort query ever written would have absorbed these
   rows without knowing what they were.
2. **Its floor is not comparable with any model floor on this page.** 3% SHARP
   is `P_shin × price − 1`, a multiplicative edge against a de-vigged line. The
   13%/8% model floors are differences in PROBABILITY POINTS against our own
   calibrated model. Different quantity, different arithmetic. §1 is about
   exactly this.
3. **4.0 is a CAP, not a floor.** Above it the edge collapses into longshot
   noise. Every other "odds" number in this map is a minimum.
4. **It has NO demonstrated edge and claims none.** Backtest +5.5% ROI, 95% CI
   **[−0.7, +11.7]** — the interval includes zero, and the window that produced
   it is the same window that chose the rule's odds cap and alignment tolerance.
   That number must never be published as a record. Stopping rules are
   pre-registered: promote or kill at n=800 on the ROI CI. **AMENDED 2026-09-25
   ([[#156]], before n=200):** at n=200 and n=400 the instrument is **sharp-anchor
   CLV** (`leg_clv_sharp.clv_sharp`, else ≥5-book `clv_cons`; thin consensus
   excluded) and the test is **relative to the junk-anchor control** — CONTINUE only
   if the market-stratified live − control difference clears a one-sided bootstrap
   at p < 0.025, else STOP. The originally registered test (margin-corrected OWN-BOOK
   CLV < −2% / < 0) could not separate the live arm from the control (−0.4pp
   [−1.9, +1.2]) because an outlier-picking rule's own-book close is ≈ −margin by
   construction (ANALYSIS_GOTCHAS §85); it is still computed and reported, and
   decides nothing. Calculation: `python3 -m scripts.picks_forward_test_checkpoint`
   (read-only). Primary instrument is CLV, not ROI — per-bet return variance is
   ~1.32, so confirming a true +3% ROI at 80% power needs ≈15,600 bets.

A **junk-anchor negative control** runs alongside it and is never published: the
same rule with the Pinnacle anchor shuffled to a different fixture, expected to
lose roughly the vig. If it makes money the harness is broken and the live arm
means nothing. ⚠️ **Read it as a HARNESS check only, never as a matched null for
the live arm.** A shuffled anchor changes WHICH legs pass the floor, so the two
arms select different populations — measured 2026-09-14 on the backtest, the
real arm sat a median +15% above the sharp line and the junk arm at 0%, with 10×
the legs. Comparing their ROIs directly reads as "junk beats real" when it is a
tail selection against a near-flat-back; and the junk arm's rejections are
almost all NEGATIVE (its false-positive rate measured 0.0%), so its
"cells excluding zero" rate is not the null for a positive live result.
`docs/OWN_SWEEP_VERIFICATION_2026_09_14.md`. ⚠️ The 2026-09-14 junk rows are DEGENERATE — the first
implementation relabelled the live picks instead of re-selecting, so they
duplicate the live arm exactly. They carry `rule_version` ending
`+DEGENERATE_JUNK_DAY1` (migration 343) and must be excluded from any control
analysis.

⚠️ **Never sum a number across `rule_version`.** A rule change starts a NEW
test with a new start date and its own n — v1 (`sharp_edge_v1_2026_09_14`) was
CLOSED at n=8 when v2 added the 20% book/anchor price-ratio cap. Both summary
views group by `rule_version` for this reason (migration 346); pooling them would
carry a closed test's n into a running one and fire a checkpoint early on a mix
of two rules.

Surfaces: `/picks` and `/api/v1/upcoming` (live arm only, via
`picks_forward_test_public`); `/admin/shadow-bots` (both arms and all rule
versions, via `picks_forward_test_arm_summary`). Settled by
`settlement.py::settle_picks_forward_test` on all three settlement cadences.
Rule locked in `dev/active/picks-forward-test-preregistration.md`, pinned by
smoke `PICKS-FORWARD-TEST-RULE-LOCKED`.

---

### 2a. ⚠️ The sharp bots' track record was VOIDED on 2026-09-20 — read this before quoting any sharp number

`SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES-2026-09-20`. `bot_trigger_1x2_sharp_v1`
published **+549.9% ROI / €1,319.80 on 24 settled picks**. It was not an edge.
**All 25 of its picks, and all 6 of `bot_trigger_ou_sharp_v1`'s, were priced off
book quotes belonging to a DIFFERENT FIXTURE.** Beitar Jerusalem was stored at
18.00 in a match Pinnacle priced 1.67; Southern District at 101.00 against a
true 1.83, and "won". Both bots are now **n=0**.

**Why these bots and not the others — this is the part worth internalising.**
Base-rate contamination across all cohorts is under 3%. These bots hit 80%+
because **their edge definition is literally "this book disagrees hugely with
Pinnacle"** — they do not stumble onto mis-mapped prices, they *search for
them*. Every gate in this system is `edge = p − 1/odds`, a LOWER bound, and a
wrong price only ever inflates the edge. So the fault clears every floor we
own and can never trip one. A model-anchored bot meets the same bad rows and
ignores them; an anchor-anchored bot is a magnet for them.

**Upstream cause (still open):** `coolbet_placer.fuzzy_match_event` scores our
home and away with `max()` over BOTH sides of a candidate event, so nothing
forces them onto different sides; `unibet_odds_feed` draws candidates from a
whole COUNTRY's lobby; `parse_contest` maps `1/X/2` off the *book event's*
sides and never re-orients them against our fixture; and the ±6h date tolerance
admits a different kickoff (measured gaps 1h00–2h55). One wrong event poisons
**every market** on that fixture, not just 1x2.

**Added 2026-09-22 — the list above is missing the main Coolbet feed.**
`coolbet_matching.match_event_to_af:180-182` has the same shape as
`fuzzy_match_event`: it scores `direct` and `swapped` team pairings, takes
`max(...)`, and returns only the score — **the winning orientation is thrown
away** — and that is the matcher behind `run_board_sweep`. `run_league_sweep`'s
inline matcher (`coolbet_explorer.py:1600`) is worse still: `token_sort_ratio`
on a concatenated `"home vs away"` string is order-insensitive by definition.
Every side-mapper then trusts the book's own `1`/`X`/`2`
(`unibet_kambi.parse_betoffers:258-261`,
`epicbet_explorer.parse_event_markets:680-691`,
`coolbet_explorer.parse_market:727-740`). **The one feed that re-orients
correctly is `coolbet_ui_placer.py:1170-1178`**, which matches each rendered
outcome label against OUR fixture's home/away and writes nothing for a side that
matches neither — the pattern the others should copy. See [[#001]].

**Four gates now stand between a bad price and a pick** (three added 2026-09-20,
the fourth 2026-09-22):

| gate | where | what it catches |
|---|---|---|
| `anchor_sanity.is_anchor_sane` (ratio > 1.5625× vs Pinnacle) | READ: `best_price_router._latest_book_odds` **and** `pick_trigger_matcher` | 20 of the 25. Weak on O/U, where prices are compressed into ~1.2–3.0 and a wrong fixture rarely trips a ratio test. **No Pinnacle line → falls back to the median raw price of ≥4 other books (#113, 2026-09-23; covered 418 of 569 placeable quotes on non-Pinnacle fixtures at wiring); fails open only below that quorum.** |
| `BotConfig.edge_ceiling` (8% on `sharp_devig`) | `pick_generator` | all 31, including every O/U one. Works in edge space, so market compression does not blunt it. |
| `pick_triggers.OUTLIER_MULT` (`max_odds = min_odds × 1.6`) | `pick_trigger_matcher` (always had it) **and now `pick_generator`** | 14 of the 25. The generator is a *clone* of the sharp path that had silently dropped this half of the window. |
| `mirror_guard.drop_mirrored_1x2` (1x2 triple transposed vs a 4+-book consensus) | **WRITE**: `store_odds`, `store_book_odds_snapshots`, `coolbet_explorer.store_coolbet_snapshots_for_match`, `fetch_odds.fetch_af_odds` | the transposed-triple class specifically: 33 of 251,923 triples over 120 days (1 in 7,600), including all 4 quotes that picks were actually struck on. |

None dominates the others: the ratio guard works in price space, the ceiling in
edge space, the outlier cap in odds space, and the latter two cross near
`cal_prob ≈ 0.18`. Keep all four.

**Why the fourth gate is not just a tighter version of the first**
(`1X2-HOME-AWAY-INVERSIONS`, 2026-09-22). The anchor guard is a *ratio* test
against *one* book, applied at *read* time, and a mirror walks through all three
of those choices:

- **It fails open with no anchor, deliberately** — and the one inverted pick that
  was never voided (`bot_unibet_trigger_1x2_v1`, Birkirkara v Hibernians,
  2026-09-12, home @ 3.20 against a true ~2.05) is on a fixture **Pinnacle never
  priced**. Seven other books did. A consensus sees it; an anchor cannot.
- **A ratio test has little power on a moderate mirror.** Balzan v Sliema stored
  away at 3.40 against a true 2.12 — ratio 1.604 against the 1.5625 threshold. It
  passed by 2.5%. The fault is obvious in *structure* and marginal in *ratio*.
- **It runs at read time**, and `odds_snapshots` has **eight production INSERT
  sites with no shared choke point**. A row that never lands cannot be inherited
  by a consumer that forgot to ask — which is §4 of `RELIABILITY_LEDGER.md`.

Refused triples go to `odds_snapshots_quarantined` with a dated
`quarantine_reason`, never deleted. Audit tool: `scripts/audit_mirrored_1x2.py`
(report-only by default). Smoke `MIRROR-GUARD`.

**THE VOID HAD TO BE MADE TO SURVIVE THE NIGHT.** The first cleanup did not
stick: `settlement.resettle_wrongly_voided_bets` re-grades every void on a
finished match, and it skipped only the exact string `void_reason =
'quarantine'`. A descriptive reason was not protected, so all 31 rows were
resurrected within hours, `void_reason` cleared to NULL, and the +549.9% bot was
back on the board by morning. (The `KAMBI-CRITERION-CONTAMINATION` rows that
looked like a working precedent had survived only because their matches are
postponed with NULL scores.) The predicate is now a PREFIX — `LEFT(void_reason,
10) <> 'quarantine'` — so a deliberate quarantine keeps both its protection and
its explanation. **If you ever void rows deliberately, the reason MUST start
with `quarantine:` or this pass will undo you.** Reversible reasons
(`postponed`, `no_ht_score`) still re-grade, which is the pass's actual purpose.
Smoke `QUARANTINE-VOIDS-SURVIVE-THE-RESETTLER` executes the re-settler's own
query against the live DB and asserts none of these rows is reachable — a source
check cannot see this, and the first attempt at the fix used `LIKE 'quarantine%'`
whose literal `%` is consumed by psycopg2 parameter interpolation and raises
IndexError at runtime.

**The guard existed and was in the wrong place.** It was the "§9 outlier guard"
in `scripts/anchor_book_sharpness_research.py` and *only* there — neither
production pricing path consulted the anchor at all. That is the repeat failure
shape in `RELIABILITY_LEDGER.md`: a second code path inheriting no gates.

**What is NOT affected:** `real_bets` — 143 real-money rows, **zero** on a
cross-matched price. And the published sharp arm (`picks_forward_test`, 91
picks) is clean; it already stored `anchor_bookmaker`/`anchor_odds` per pick,
which is the pattern the rest of the system should copy.


## 3. What each % means on each screen

Same-looking numbers, different meaning per screen. This is the glossary.

| Where you see it | The number | What it actually is |
|---|---|---|
| **/picks** — "Edge vs sharp" | **sharp edge** | `P_shin × best_book_price − 1`, a MULTIPLICATIVE edge against the Shin-de-vigged Pinnacle line. **REPOINTED 2026-09-14** (PICKS-PAGE-SHOW-FORWARD-TEST): this column used to be a MODEL edge (`cal_prob − 1/odds`, a probability-point difference) from `simulated_bets`. Different ruler AND different arithmetic — the two are not comparable, and a number that shrank from +8.5% to +5.3% across that date did not get worse. Gate: edge ≥ 3%, odds ≤ 4.0, anchor/bet quote within 60 min, **no daily selection cap** (dropped 2026-09-15). No cohort split, no session branch, no `.limit(300)` cohort truncation. **Window (PICKS-SHOW-WHOLE-DAY, 2026-09-15):** kickoffs from **midnight UTC today** through +48h — not a rolling 24h lookback. So the page carries the WHOLE of today's card, settled and in-play rows included and badged, and drops yesterday at midnight rather than trailing it around the clock. Both earlier shapes were wrong in opposite directions: a rolling window showed yesterday's losers before the morning batch published, and hiding started fixtures threw away the day's own picks at kickoff. |
| **/picks** — "min X.XX" (public) | break-even | `1/P_shin` — below this the pick is −EV **against the sharp line**. Was `1/cal_prob` (−EV under our model) until 2026-09-14. Same purpose, different estimator; separate functions in separate modules so the two can never be mixed. |
| **/picks** — "place ≥ X.XX" (admin only) | ❌ **REMOVED 2026-09-14** | The admin placement-trigger hint went with the model path — there is no model probability on this page to derive one from, and the OWN betting path closed the same day (`docs/OWN_PATH_VERDICT_2026_09_14.md`). The floors themselves are unchanged and still live in `coolbet_placer.py`; only this display is gone. |
| **/picks** — "Running result" | **live forward test** | ROI = `SUM(pnl)/COUNT(*)` at a flat 1 unit over settled (won+lost) picks, with its n and a 95% CI, from `picks_forward_test_summary`. The +5.5% BACKTEST is never rendered here: its CI includes zero and it was computed on the window that chose the rule's own parameters. Smoke `PICKS-FORWARD-TEST-SURFACE`. |
| **/picks** — per-pick "CLV" | raw price ratio | `odds/closing_odds − 1` at the pick's OWN book, **no de-vig**. Break-even on it is that book's margin (7.8–11.3% depending on the book), NOT zero. The margin-corrected figure `(1+clv)/(1+m)−1` is the decision variable and is what the "Closing-line value" summary shows. |
| **/performance + /admin/bots** — per-bot **ROI** and **CLV** ([[#159]], 2026-09-25, owner-approved) | **ONE definition, one view** | Every per-bot figure on both pages (and `dashboard_cache.bot_breakdown`, and the /performance hero, and `/api/v1/track-record`) comes from the PRIVATE view **`bot_performance`** (migration 433) over `bot_ledger`'s record legs — no page computes its own. **ROI = FLAT 1 unit** on two labelled price bases: **PUBLIC** `roi_public` (/performance, hero, cache, API) at `odds_at_pick_available` = the best price AVAILABLE when the pick was made on ALL publishable books (latest quote per book at/before `pick_time`, dead feeds out, floored at the our-books price); **OWN** `roi_own` = `bot_scoreboard.roi_unit` (/admin/bots, "at our books") at `odds_at_pick_live` (Coolbet / Epicbet / Tonybet / Unibet-Site). A leg with no quote is priced at the recorded odds and COUNTED (`n_public_recorded` / `n_own_recorded`). **Stakes are FLAT for every bot since [[#155]]** (owner 2026-09-25 — `compute_stake` returns `FLAT_STAKE_EUR` = €10; migration 441 restated every `simulated_bets` row, original Kelly stake kept in `stake_kelly_original`; a trigger coerces any non-flat stake), so there is no separate stake-weighted ROI and stored `simulated_bets.pnl` = `10 × pnl_unit_public` (settlement prices pnl at the same public price; `pnl_price_basis` flags recorded-price legs). The /performance detail view reads its header from the same request as its legs (uncached `bot_performance` row), shows an **EV** column (`model_prob × odds − 1`) for EV-unit bots (`edgeUnit=ev` in the generated floors), and for VIP bots an **EV8 / EV5 split** from `bot_performance_ev_band` (same legs + expressions; bands sum to the row). **CLV = the pick's price × the SHARP-ANCHOR close − 1**: `leg_clv_sharp.p_close` (fresh Shin-de-vigged Pinnacle, ≤ 60 min before kickoff) else `p_close_cons` (≥5-book consensus), thin excluded, \|clv\| > 1 a data fault, never in-play — `clv_public` on /performance, `clv_anchor_*` (at our books) on /admin/bots, with n and the Pinnacle/consensus mix. Forward-test rows are the [[#158]] record (parity with `picks_forward_test_bot_record` pinned by smoke `ONE-ROI-CLV-PARITY`). **Retired as a shown CLV/ROI:** `simulated_bets.clv`, `clv_pinnacle(_devig)` / `bot_scoreboard.clv_pin_*` (no close-age limit, recorded price, circular pre-mid-July — ANALYSIS_GOTCHAS §83), the stake-weighted `execPnl` recompute, stored `pnl` (smoke `LEGACY-CLV-PNL-NO-NEW-READERS`). One producer writes both price columns AT PICK TIME (`workers/utils/pick_price.py`, from `store_bet` + the 30-min `job_backfill_live_prices`; smoke `PICK-PRICE-AT-PICK-TIME`). |
| **/performance** — forward-test bots' "CLV" ([[#156]], 2026-09-25) | **sharp-anchor CLV** | For `bot_sharp_1x2_v1`, `bot_sharp_ou_v1`, `bot_consensus_b/c/d_v1`: the main figure is `odds × p_close − 1` against the fresh Shin-de-vigged **Pinnacle** close (`clv_sharp`), else a ≥5-book **consensus** close (`clv_cons`), shown with its n and the Pinnacle / consensus mix; 3–4-book thin consensus never enters. Read server-side from the PRIVATE view `picks_forward_test_anchor_clv` (migration 430, service_role only — `leg_clv_sharp` stays off anon). The own-book margin-corrected figure is the labelled secondary ("vs the book's own close") — negative by construction for these rules (ANALYSIS_GOTCHAS §85). Each row is scored on its **CURRENT `rule_version`** (as `bot_scoreboard`); earlier versions are named on the row, never pooled; the bet list is scoped to the same version. Grade-D picks that were never sent are excluded from the summary views (migration 430). Smoke `FORWARD-TEST-SHARP-ANCHOR-CLV-ON-PERFORMANCE`. **Since [[#158]] (2026-09-25, owner-approved)** the current record ALSO counts earlier-rule SENT picks that pass the current rule on **pick-time data only** (consensus rebuilt from `odds_snapshots` as of `published_at` with the publisher's own functions; `scripts/recheck_forward_test_picks.py` → private table `pick_rule_recheck`, verdicts frozen). Row figures + CLV now come from the private view `picks_forward_test_bot_record` (migration 431; same CLV definition); the bet list is scoped on `record_rule_version` (appended to `picks_forward_test_public`). The detail view says "incl. N earlier picks re-checked under vN"; failures sit in the earlier line as "didn't meet today's rule". Consensus v1→v2: B 3/5, C 31/34, D 14/16 passed; sharp v1→v4: 0/8. **The pre-registered test's counts are unchanged** (summary views group on `rule_version` as published). Smoke `RECHECK-FORWARD-TEST-PICK-TIME-ONLY`. |
| **/picks** — WHICH BOTS APPEAR | cohort rule | ⚠️ **CHANGED 2026-09-25 ([[#155]], migration 442): both branches now gate on the bot's STATUS (`bot_distribution.sent_public` — TESTING/BETA/CALIBRATED, not VIP), not on `show_on_picks` (now derived from the status) nor the hard-coded grade-D rule; see "Lifecycle" above. History below.** **BOTH families**, unioned in the view `picks_public_all` (migration 361, PICKS-SHOW-BOTH-BOTS 2026-09-16). Sharp arm = `picks_forward_test` where `arm IN ('live','consensus_anchor')` — and since 2026-09-23 (migration 380, [[#095]]) each consensus row is labelled `bot_consensus_b_v1` or `bot_consensus_c_v1` by its `grade`, with `grade` exposed on the view for the /picks badge (**widened 2026-09-22, migration 368, [[#068]]** — a SECOND published arm anchored on a de-vigged multi-book consensus instead of single-book Pinnacle. It exists because the live arm's pre-registered ≤4% anchor-overround gate admitted **0 of 173** Pinnacle-priced markets on 2026-09-22 and the channel went dark; the gate is pre-registered so it was NOT relaxed. The consensus arm carries its own `rule_version` (`consensus_edge_v1_2026_09_22`; **v2 `consensus_edge_v2_2026_09_24` from 2026-09-24, [[#106]]: the 3% floor must hold under every credible de-vig method — Shin, additive AND power — because on 8-10%-margin soft books the method alone moves an edge by a median 2.1pp**), records its basis in `anchor_bookmaker` as `consensus:N`, and unlike the live arm has an **8% edge CEILING** — [[#007]]'s lesson: `edge = p·odds−1` is maximised by a WRONG price, and 5 of the first 15 qualifying legs cleared 8% against a 7-11 book consensus. `junk_anchor` remains excluded by name: it is a negative control and must never reach a customer surface.); model arm = `simulated_bets` for bots with **`bots.show_on_picks = true`** (today: `bot_v10_1x2` and `bot_v10_ou` — one row until migration 375 split them), pre-match singles, not retired, no combos, no in-play. The gate lives in the DATABASE for the same reason `arm='live'` does — a view cannot forget it. **Before this, /picks read only the sharp ledger**, so `bot_v10_all` (now split) published to Telegram and showed on /performance while being invisible on the page the channel links to; `show_on_picks` had existed since migration 356 and was read by nothing. ⚠️ **`edge` in this view means two different things** — see the `edge_kind` column and §1. The page switches its label on it (`Edge vs sharp` / `Model edge`) and so does the break-even tooltip, because `fair_prob` is `p_sharp` in one arm and `calibrated_prob` in the other. Pinned by `PICKS-SHOW-BOTH-BOTS`. |
| **/performance** — WHICH BOTS APPEAR | cohort rule | ⚠️ **CHANGED 2026-09-25 ([[#155]]): TESTING + BETA + CALIBRATED by status alone (VIP bots too when their status is public, settled only); `show_on_performance` and the "VIP whatever its label" exception are gone; forward-test arm rows follow the same status rule (so `bot_consensus_d_v1`, EXPERIMENTAL, has no row). History below.** **`calibrated` or `beta` only** (`PUBLIC_MATURITY_LABELS` in `odds-intel-web/src/lib/bot-aggregates.ts`, applied in two places since [[#159]] removed the client toggle path: the leaderboard rows and the hero count — they must agree or the hero reads *N strategies live* above a table of 2), plus the VIP bot (#148) and `show_on_performance` testing bots (#152). **Row rule ([[#159]]):** < 5 settled = muted in "In development" for EVERY row, forward-test rows included; rows are grouped by STATUS (calibrated & beta · testing · VIP · in development), never by ROI. `experimental` bots are the **shadow fleet — OWN-direction work**, and their surface is `/admin/shadow-bots`. `bot_sharp_forward_test_v1` **and the three consensus bots `bot_consensus_b_v1` (strongest, beta) / `bot_consensus_c_v1` (standard, testing) / `bot_consensus_d_v1` (weak, not published — re-tiered 2026-09-23, [[#098]])** are the exceptions (**split by grade 2026-09-23, [[#095]]** — previously one `bot_consensus_anchor_v1`; `picks_forward_test_summary` now groups by `grade` too): all are injected BELOW the filter from `picks_forward_test_summary`, because they are the bots whose picks readers actually receive. **CORRECTED 2026-09-22 ([[#068]]):** those views filtered `arm='live'`, so when the consensus arm shipped, **20 picks went to Telegram and /picks with no track record on /performance at all** — PICKS-SHOW-BOTH-BOTS in mirror image, and the reason the rule is now stated as *if it is published, its record is published* (migrations 371-372). The two arms render as SEPARATE panels with their own n, ROI and CLV. **REVERSED TWICE, 2026-09-15 → 16:** the filter was dropped on the 15th (*"this page is the measurement surface"*) and restored on the 16th once it was seen to list 13 shadow bots with zero settled bets between them. The 15th's premise was also false — `bot_v10_all` (now `bot_v10_1x2`) is `calibrated` and was never hidden by it; its absence from **/picks** is a separate gap (`bots.show_on_picks`, migration 356, **still unread by any code**). |
| **/performance** — WORK DONE + RETIRED STRATEGIES ([[#157]], 2026-09-25, owner-approved) | separate from the headline | Three labelled things, never mixed: (1) the **headline** = BETA + CALIBRATED, `retired_at IS NULL` (unchanged); (2) **"The work behind it"** = every strategy ever scored, retired included — PRIVATE view `bot_public_work_done` (picks, settled, distinct selections, strategy counts; 95 strategies / 16,880 picks on 2026-09-25); (3) a **collapsed retired section** — `bot_public_record_group` (one row per FAMILY summing EVERY retired bot, losers included) + `bot_public_record.is_representative` (≤2 per family: retired, ≥150 settled, sharp-anchor close on ≥50% unless in-play, largest sample first — **never by ROI/CLV**). Families = `bot_public_group()` (migration 446: model result · model goals · sharp line-shopping · triggers vs model · triggers vs sharp · forward test · new markets · in-play · acca legs). Every ROI/CLV is `bot_performance`'s (a family = Σ pnl_units_public / Σ settled) — smoke `PERFORMANCE-RETIRED-PARITY`. Flags are counts, never exclusions: `n_swap_window` (model 1X2-derived legs 05-10..09-14, #065 — printed on bot_v10_1x2's detail view), `n_ou_calbug` (09-03..09-13), `n_pre_mid_july` (§83). The family lesson (triggers vs the sharp line ≈ +8% CLV, vs our model ≈ −6%) is one line, family level only. **Also migration 446:** in-play legs are priced at their recorded in-play odds in `bot_ledger` (basis `inplay`; a pre-#159 backfill had left a PRE-MATCH `odds_at_pick_live` on 1,104 of them — inplay_e read +22.9% instead of +3.4%) and their stored pnl re-restated; `pick_price.public_price` returns `inplay` for them. Web: `src/lib/performance-work-done.ts`, `src/components/performance-work-done.tsx`. |
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

> ### 🎯 ONE PLACEMENT FLOOR — 2026-09-25 (#162 W4.3). Every placer (UI placer at the pick price and at
> the live Coolbet price; the router at each book's price and on both arms' live re-check) calls
> `workers/automation/placement_floor.pick_clears`: the STRICTER of the bot's own generator floor and the
> market floor (`min_edge_for_pick` / `_min_odds_for`), plus the bot's own selections, odds/edge ceilings
> and — sharp bots — the 1.6× outlier cap. `BOT_THRESHOLDS` is deleted; the "two policies, both must pass"
> stacking below now lives in that one function. Consequence for the sharp bots: real money needs 10–13 pp
> (1x2) / 8 pp (O/U) against a de-vigged Pinnacle line, above their own 8 pp ceiling / outlier cap, so they
> almost never clear and three have empty windows. Opting a bot out of the market floor is the owner's call
> (`MARKET_FLOOR_OPT_OUT`, empty). Detail: `docs/COOLBET_OWN_BETTING.md` W4.3 banner.

> ### 🎛 CONTROL PANEL — 2026-09-24 (#139 phase A, migration 413). Read first.
>
> `/admin/bots` is THE control surface for own real money (owner decision 3). It shows the six
> separate layers in gate order — **placement path (code rule) → per-bot € switch → placement
> pause → armed → Mac executors (heartbeat) → per-pick gates** — and a computed CAN STAKE line
> (UNKNOWN when any layer is unreadable). The two-name `PLACEABLE_BOTS` set is gone:
> `effective_allowlist()` = `placement_path_bots()` (code rule over the exported `bot_config`,
> fail-closed, stale > 36 h = empty — mirrored on the page as ladder layer 9 "Bot config fresh", #162 W8.4) ∩ `ui_place_enabled_bots()` (the `coolbet_placer_bots`
> eligibility list: ON, not `locked_reason`, not retired). Arming is owner-only and two-step on
> the page (typed `ARM REAL MONEY` + ≥20-char reason, `admin_arm_real_money`); resume is page-only
> with a typed phrase + reason; Telegram `/pause` is a stop-only emergency command. Every write is
> one audited transaction (`admin_set_control` → `control_changes`). Where the tables below say
> `PLACEABLE_BOTS`, read "the placement-path rule".

> ### ✅ PLACEMENT-GATE — 2026-09-15 (OWN Phase 0). Read before the tables below.
>
> **FIVE functions can reach a money primitive** (the Coolbet place click, the
> Unibet "Tee panus" click, the Coolbet API bet POST), and every one now calls
> **one fail-closed gate** before it does: `workers/automation/placement_gate.py`
> (`assert_run_may_place` at run level, `assert_may_place` per pick). The gate
> **#162 W0.2 (2026-09-25, migration 436):** the run-level gate also requires the money-gate contract (`coolbet_session_state.money_gate_contract` ≥ 1 and = code `GATE_CONTRACT`); it is 0 until #162 W4 unifies the placement checks, and the DB refuses a real-money switch ON / arming until then.
> checks, in order, `placement_paused` (KILL switch, now fails CLOSED),
> `real_money_armed` (ARMING switch, migration 354, default FALSE, owner-set only),
> `effective_allowlist()` = `PLACEABLE_BOTS` ∩ `coolbet_placer_bots.ui_place_enabled`,
> the kickoff cutoff and the daily caps. Any exception ⇒ refuse.
>
> | executor | where the gate runs | what it replaced |
> |---|---|---|
> | Coolbet UI placer | `place_coolbet_ui.main()` (run level, before browser/lock) and `coolbet_ui_placer.stage_bet` **before `select_outcome`** | a single `is_placement_paused()` read AFTER the stake was typed, which fell OPEN on a DB error |
> | Best-price router (Coolbet + **Unibet-Site**) | `route()` run level; `_dispatch_unibet` before `unibet_placer.place_bet` | `ROUTER_ALLOW_REAL` env var only — which was SET in `.env`, so the router ran in real mode every 30 min; it iterated `PLACEABLE_BOTS`, never the DB toggle |
> | ~~API placer + manual-place drain (VPS, every 10 s)~~ **DELETED 2026-09-25 (#162 W4.6)** | ~~`coolbet_placer.place_all_bets`; `place_bet_by_id`~~ — code removed, not gated | an inline pause read (this was the only executor that had one) |
> | ~~In-play API placer (`coolbet_placer.place_all_inplay_bets`)~~ **DELETED 2026-09-25 (#162 W4.6)** | — code removed | nothing: it could stamp `placed_real=TRUE` (and post) with no pause/arming read |
> | ~~Orphaned in-play capture (`coolbet_inplay.capture_inplay_snapshot(mode="execute")`)~~ | **DELETED 2026-09-21** (COOLBET-INPLAY-ORPHAN) — a real-money execute branch with zero callers, kept for a consumer removed 2026-06-12 in a direction retired 2026-08-21 at ROI −0.31%. Migration 115 + table kept; smoke `COOLBET-INPLAY-CAPTURE-DELETED` keeps it gone. | nothing |
>
> Corrections to this section as written on 2026-09-11: the counts and claims
> below that describe a "late" pause check, a dead drift gate, and `load_picks`
> without a retirement check are still accurate history; the pause-position
> defect is FIXED by the gate. `PLACEABLE_BOTS`, `ui_place_enabled_bots` and
> `effective_allowlist` now LIVE in `placement_gate.py` and are re-exported by
> `place_coolbet_ui.py`. Both `--execute` launchd jobs were unloaded on the Mac
> (`~/Library/LaunchAgents/paused/`). `coolbet_control --status` prints a host
> view and a final `CAN_STAKE: yes/no`. Smokes: `PLACEMENT-GATE-FAIL-CLOSED`
> (mutation), `PLACEMENT-GATE-ARMED-REQUIRED`, `PLACEMENT-GATE-ALL-EXECUTORS`,
> `ROUTER-NO-ALLOWLIST-BYPASS`, `REAL-BETS-ATTEMPTS-RECONCILED`,
> `REAL-BETS-SETTLE-ANY-FINISHED`, `REAL-BETS-SHADOW-LINK`, `OWN-BOTS-OFF-CUSTOMER-SURFACES`.
> Full plan: `dev/active/own-implementation-plan.md`.

### 4a. There are TWO real-money paths, not one

| Path | Entry | Status |
|---|---|---|
| **Coolbet UI placer** | `scripts/place_coolbet_ui.py --execute` | live, hourly 06-21 UTC |
| **Best-price router** | `workers/automation/best_price_router.py::route` | built; **gated by `placement_gate`** (2026-09-15). ⚠️ The old claim "owner-gated OFF (`ROUTER_ALLOW_REAL` unset)" was FALSE — the var was set in `.env`; the router ran in real mode and staked nothing only for lack of candidates. Its launchd job is unloaded. |

⚠️ **Previously undocumented.** The router can place at **Coolbet OR Unibet-Site**
(`PLACEABLE_BOOKS`), so "Coolbet own-betting" no longer describes the whole surface.
It reuses `place_coolbet_ui`'s gate functions rather than copying them — the only
place in the codebase that pattern is followed.

### 4b. The UI placer, in execution order

**Run-level** (`scripts/place_coolbet_ui.py`)

| # | Gate | Effect if failed |
|---|---|---|
| R1 | `single_run_lock()` flock | SKIP the run |
| R2 | `effective_allowlist() = placement_path_bots() ∩ ui_place_enabled_bots()` (was `PLACEABLE_BOTS ∩ …` until 2026-09-24) — both DB reads **fail CLOSED** | bot forced to **dry-run**, not an error |
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
## Changing a live bot's rule — bump its RULE VERSION ([[#162]] owner decision (b), 2026-09-25)

No twins: a live bot's pick rule (gates, floors, filters, probability source, one-per-match…) is changed
IN PLACE, and every pick records the rule it was made under. Bump `rule_version` (`r1` → `r2` …) on the
bot's `BotSpec` in `workers/registry/bot_registry.py` in the SAME commit as the rule change. The scheduler
writes it to `bots.rule_version` at start-up and every 30 min (`bot_status.sync_rule_versions`), and a
BEFORE INSERT trigger stamps it on every new `simulated_bets` / `shadow_bets` row (migration 453) — every
writer is covered without being edited. Before/after = the same bot's ledger split by `rule_version`;
NULL = made before tagging began (2026-09-25). Smoke `RULE-VERSION-TAGGED`.

## Lifecycle — ONE STATUS DECIDES DISTRIBUTION ([[#155]], owner 2026-09-25)

A bot's **status** (`bots.maturity_label`; `retired_at` = retired) is the ONLY per-bot input that
decides where its picks go. Every public surface reads it; no second per-bot setting can drift from it.

| status | /admin/bots | /performance | /picks + public Telegram | own record | headline totals |
|---|---|---|---|---|---|
| `experimental` | ✅ | — | — nothing sent (not even pending rows are anon-readable) | admin only | — |
| `testing` | ✅ | ✅ row marked TESTING | ✅ sent | ✅ every sent pick counted | — |
| `beta` | ✅ | ✅ | ✅ sent | ✅ | ✅ |
| `calibrated` | ✅ | ✅ | ✅ sent | ✅ | ✅ (strongest evidence) |
| ⭐ VIP (a **channel** on top of a status, "VIP · TESTING") | ✅ | ✅ settled picks only | — (paid channel only) | ✅ | — never |
| `retired` | ✅ retired tab | retired section (#157) | — | picks keep counting in the totals | — |

**Real money is NOT a status** — it is the per-bot € switch on /admin/bots (`coolbet_placer_bots`).
**Anything sent is counted** — a pick that reached the channel stays in the record after a demotion
(`picks_public_all` keeps rows with a `telegram_message_id`).

**Where the one status is read (all derived, parity-tested by smoke `ONE-STATUS-DECIDES-DISTRIBUTION`):**
- SQL: `bot_public_status(label, retired_at)` / `bot_pending_public(...)` and the view `bot_distribution`
  (migrations 437 + 442). `bots.show_on_picks` / `bots.show_on_performance` are **derived by trigger**
  `bots_zz_derive_distribution`; an update that contradicts the status is rejected. The admin
  "Show on /picks" switch is gone (read-only "by status" line).
- /picks: `picks_public_all` (model branch `bd.sent_public`, pending only when `bd.pending_public`;
  forward-test branch by the arm's bot status or already sent).
- Public Telegram: `coolbet_signaler` (any SENT bot in the match·market·selection group — was
  "any calibrated bot"; a sent bot's row supplies the message) and the forward-test publisher
  (`arm_bot_sends` → grade D's bot is EXPERIMENTAL, so recorded, never sent) — both re-checked at
  the send by `send_pick` (#162 W5.3).
- Headline totals: `HEADLINE_BOT_SQL` (settlement `write_dashboard_cache`) and web
  `HEADLINE_MATURITY_LABELS` = BETA + CALIBRATED, VIP excluded.
- /performance: web `PUBLIC_MATURITY_LABELS` = TESTING + BETA + CALIBRATED (lib/bot-status.ts).
- anon RLS on `simulated_bets`: pending rows only via `bot_pending_public` (+ #164 `held_back_until`).
- Python: `workers/utils/bot_status.py`; web: `src/lib/bot-status.ts`.
- **Every customer pick SEND (#162 W5.3, 2026-09-26):** `workers/notify/pick_sender.send_pick` —
  see "One audited pick sender" below.

### One audited pick sender — `send_pick` + `pick_sends` (#162 W5.3, 2026-09-26)

Every pick that reaches a customer — the public channel (@oddsintelpicks), the private VIP channel
(`TELEGRAM_VIP_CHAT_ID`) or the Pro/Elite DMs — goes through ONE function,
`send_pick(channel, bot, pick_table, pick_id, text)` (`send_vip_pick` = DMs + VIP channel). Callers:
`coolbet_signaler` (model bots → public), the forward-test publisher (scheduler job + the manual
`--send`, → public), `daily_pipeline_v2`'s VIP branch and `ou_sharp_outlier` (VIP bots → DMs + VIP
channel). Operator messages (alerts, summaries, the owner's per-pick prompt, the day-one header) are
not picks and do not use it; the in-play DM (`inplay_bot`, retired) is the one exemption, to be routed
here if in-play is ever revived. It:

1. checks `publishing_paused` (`/pausepicks`) — for **every** channel; the VIP senders used to ignore it;
2. checks the bot's distribution: public ⇐ `bot_distribution.sent_public`; VIP channel and DMs ⇐
   `bot_distribution.vip_channel` (map `CHANNEL_DISTRIBUTION`);
3. claims a **`pick_sends`** row (migration 457: channel, bot, pick table + id, match/market/selection,
   message id / recipients, status `sent`/`failed`/`skipped` + reason, timestamps) before the send
   and finalises it after — skips are recorded with their reason (paused, held back, not distributed);
4. dedupes on the unique index `(channel, pick_table, pick_id)` — a restart can no longer double-send
   (the VIP DMs used an in-memory 600 s key), and "which picks were sent where" is a query.

**Failure policy:** pause / distribution unreadable → NOT sent, recorded (fail closed — stricter than
`is_publishing_paused()`, which still falls open for its logging callers); the `pick_sends` write
failing → sent anyway, logged at ERROR, an in-process set stands in for the dedupe (fail open — the
audit must never mute customers); a `sending` row left by a crash mid-send is treated as sent. The
forward test still writes `picks_forward_test.telegram_message_id` (public surfaces read it); the
pre-registered selection rule is untouched. Smoke `ONE-AUDITED-PICK-SENDER`.

**Current statuses (owner 2026-09-25, migration 442):** `bot_v10_1x2` CALIBRATED · `bot_high_roi_global_v2`
BETA · `bot_sharp_1x2_v1`, `bot_sharp_ou_v1`, `bot_consensus_b_v1`, `bot_consensus_c_v1`,
`bot_v10_1x2_newplus_v1` TESTING · `bot_combined_1x2_ev5_v1`, `bot_ou_sharp_early_v1` VIP · TESTING ·
`bot_consensus_d_v1` and every other active bot EXPERIMENTAL.

### Promotion and review — written rules

- **EXPERIMENTAL → TESTING:** owner decision (the bot is ready to be SENT and scored in public). No
  statistical bar, because TESTING is exactly where the public record is gathered.
- **TESTING → BETA:** after **50 settled picks** (owner 2026-09-26 — the same n as the review flag, so a TESTING bot is either promotable or flagged by n = 50)
  with **sharp-anchor CLV > 0** (`bot_performance.clv_public`, the one CLV every page shows). The
  model-bot bar in `docs/BETA_PROMOTION_BAR.md` remains the stricter reference for model bots.
- **BETA → CALIBRATED:** the rule below ("`beta` → `calibrated`").
- **Review flag, never automatic retirement:** at **n ≥ 50** settled with the sharp-anchor CLV 95% CI
  **entirely below 0** (`bot_review_flag`, migration 437), the bot gets a "review this bot" item in the
  admin attention inbox (Overview) and on /admin/bots. The owner decides; retiring is a migration
  (e.g. 438 for `bot_ou35_model_v1`).

## Maturity labels — what each one MEANS, and what promotes a bot

> **CHANGED 2026-09-25 ([[#155]]):** the table below is the #069 history; the status → channel
> mapping above is authoritative. In particular `experimental` bots now include the shadow fleet AND
> any customer bot not yet sent, and `testing` is no longer only a forward test.

Added 2026-09-22 ([[#069]]). Before this, `maturity_label` was a hand-set column
with **no written threshold**: "proven" meant *somebody typed `calibrated`*. The
owner put it plainly — *"we have today the beta, calibrated and testing, although
im not sure if they are uptodate and really what they mean?"* — and all three
observations checked out.

| label | what it asserts | where it shows |
|---|---|---|
| `experimental` | writes `shadow_bets`; nothing is claimed about it | `/admin/shadow-bots` only — hidden from /performance **by design** |
| `beta` | public, real record, **explicitly still accumulating** | /performance, marked as maturing |
| `calibrated` | public and **promoted** — the record is callable, see the rule below | /performance |
| `testing` | a **published** forward test: readers RECEIVE these picks and the rule is pre-registered, but n is not yet callable | /performance, injected below the maturity filter |
| `retired` | no longer runs | hidden |

`testing` became a real database value in migration 375. Until then it existed
**only as a string `/performance/page.tsx` stamped on the injected rows**, so the
page's legend documented three tiers of which one had no backing field — nothing
could query for it, and no test could check it.

### The promotion rules — two gates, not one

There are **two** promotions, and only the first of them was ever written down.

**`experimental` → `beta`: already specified.** `docs/BETA_PROMOTION_BAR.md`
(2026-09-13) is the authority and is NOT restated here. In short: CLV positive on
**placeable books only** at t ≥ 3, n ≥ 334 on that subset, fold-robust, ROI not
significantly negative, and ≥ ~4 weeks of FORWARD data gathered after the bot was
selected. Read that doc before promoting anything — it also records why each
criterion exists, every one of them having been added after a strong-looking
number failed scrutiny.

**`beta` → `calibrated`: this is the gate that had no rule.** `maturity_label` is
a hand-set column, so "proven" meant *somebody typed `calibrated`* — while that
label gates the Telegram channel, the mirror jobs and every web surface. The rule
below is **strictly stronger than the BETA bar**; a bot must still satisfy all of
the BETA bar, plus:

1. **Margin-corrected CLV > 0 with a 95% CI that excludes zero.** The BETA bar
   accepts `t ≥ 3` on CLV vs Pinnacle; this tightens *which* CLV counts. De-vigged
   Pinnacle (`clv_pinnacle_devig`) for model bots, own-book
   `clv_margin_corrected` where it exists. **Raw `clv` is not admissible at this
   gate**: it breaks even at the closing book's *margin* (~8%), not at zero
   (gotcha §70), so a raw-CLV promotion can certify a losing bot.
2. **≥ 30 days spanning no calibration change** (gotcha §39). A window that
   straddles a recalibration measures the recalibration, not the bot.
3. **No single month carrying the result.** State the monthly series next to the
   pooled figure. `bot_v10_1x2` is promoted under this rule and is exactly the
   case it is written for: pooled CLV +2.50%, but May −0.87% / Jun −2.15% /
   Jul +8.35% / Aug +8.24% / Sep +7.25% — a three-month record, and the SYSTEM_MAP
   row says so rather than quoting the pooled number alone.

Why CLV and not ROI at either gate: `ANALYSIS_GOTCHAS §8` — CLV converges
**~200× faster**. An ROI-based promotion rule at any n this system will reach is
a coin flip dressed as evidence, which is precisely what BETA_PROMOTION_BAR's own
headline found ("every ROI confidence interval in this system spans zero").

**Demotion is the same test run backwards.** A `calibrated` bot whose trailing-200
margin-corrected CLV CI falls entirely below zero returns to `beta`. Without this
the labels are a ratchet. It is not hypothetical: it is exactly what the O/U half
of the old `bot_v10_all` did, and why `bot_v10_ou` shipped `beta` on the day it
was created rather than inheriting the parent's `calibrated`.

**What this rule is NOT:** it is not a placement gate. Promotion changes what a
*reader* is told; the placement-path rule ∩ the audited per-bot € switch (since 2026-09-24;
was the hardcoded `PLACEABLE_BOTS`) is what decides whether a euro moves. See the warning immediately below.

- ⚠️ **`maturity_label` does NOT gate the UI placer.** It gates the Mac daemon, the
  Telegram public channel, the mirror jobs and every web surface — not this path.
  Safe because the per-bot € switch is explicit, seeded OFF, audited and set only from
  /admin/bots (was: because `PLACEABLE_BOTS` was hardcoded, until 2026-09-24).
- ⚠️ **`placement_paused` does NOT gate the Telegram public channel** — not any more
  (PICKS-PUBLISH-DECOUPLED-FROM-OWN-PAUSE, 2026-09-15, migration 353). Publishing has
  its own flag, `publishing_paused`, set only by `/pausepicks`. The two are separate
  because a 🤖 OWN decision to stop staking is not a 👥 PICKS decision to stop
  publishing: on 2026-09-14 the OWN-path verdict flipped `placement_paused` and armed
  a silent customer-feed outage nobody chose. Publishing makes no Coolbet call and
  writes no `real_bets` row, so it is safe while placement is down — and
  `is_publishing_paused()` falls *open* on DB error for that reason. Since 2026-09-24
  (#139) the pause stops only the Telegram SEND — the pre-registered ledger, the /picks
  watchlist and the funnel keep recording. Full table:
  `WORKFLOWS.md` § Pause semantics.
- ✅ **`load_picks` now requires `retired_at IS NULL AND is_active`** (2026-09-24, #131
  audit; smoke `PLACER-SKIPS-RETIRED-BOTS`), matching `coolbet_placer.load_qualified_bets`.
  Before that it had no retirement check and was safe only because placement was paused.

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
| Edge floors 0.10 / 0.08 | **6 → 2** | `_MIN_EDGE_BY_MARKET`, `_MODEL_1X2_HOME_FLOOR` (`BOT_THRESHOLDS` deleted 2026-09-25, #162 W4.3 — every placer calls `placement_floor.pick_clears`, the stricter of the bot's own floor and these); the two mirrors' `EDGE_FLOOR` are now *derived* readouts (2026-09-11 PICK-GENERATOR-DELEGATION — the modules hold no gate of their own), and `upcoming-picks.ts` is generated from Python |
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
- ℹ️ **Superseded in part, 2026-09-14 (PICKS-PAGE-SHOW-FORWARD-TEST).** `/picks`
  no longer renders the `place ≥` hint or any model-derived floor — it publishes
  the model-free forward test (§3). `upcoming-picks.ts` and the generated
  `engine-floors.ts` are UNCHANGED and still correct: they are read by the
  model-era surfaces (`/api/v1/track-record`) and pinned by
  `FLOORS-ONE-SOURCE-CROSS-LANGUAGE`. The consolidation recorded below still
  holds; only the /picks display it fed is gone.
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

**The PICKS outlier anchor (#129, 2026-09-24).** In the 👥 PICKS path (`_load_today_from_db`,
which feeds `simulated_bets` → /picks) the ODDS-OUTLIER-FILTER anchor is Pinnacle, else the
median of ≥3 **publishable** books (`is_publishable_book`) — the same set that path prices from
since #005. It used to read the four Estonian books + Pinnacle, so fixtures without Pinnacle
and with <3 Estonian books had no anchor and every pick on them was rejected (the model bots
dried up). 🤖 OWN is unchanged: the live shadow passes filter on `ACCESSIBLE_BOOKMAKERS`, and
OWN bots fed from `simulated_bets` (`pick_generator._candidates_from_pipeline`) re-apply the
pre-#129 rule in `_own_outlier_ok` — Pinnacle, else median of ≥3 Estonian books, 1.25× ceiling —
because #129 lets thinner fixtures into that table. Marathonbet and 1xBet count as ONE source
toward the ≥3-book anchor (identical price 38–59% of the time, vs ~17% for any other pair).

---

## 5. Deep-dive docs (this map links out; it does not duplicate them)

- `docs/BETTING_GATE_DECISIONS.md` — how the model floors (13%/8%) were decided, the canonical backtest method, why runs disagreed.
- `docs/BOOK_AGNOSTIC_EDGE_ENGINE.md` — the trigger engine (Stage A windows + Stage B matcher), the −21% adverse-selection finding.
- `docs/COOLBET_OWN_BETTING.md` — the full own-betting flow and gate stack.
- `docs/ANALYSIS_GOTCHAS.md` — line-shop mirage (§52), single-book vs best-of-books (§55), CLV-vs-ROI variance.
- `workers/registry/bot_registry.py` — the structured source of truth this map is built on.
- `docs/OWN_STRATEGY_AUDIT_2026_09_15.md` — full-system OWN audit: what is measured dead (§3), the armed-under-pause defects (§4 Phase 0), the two remaining strategies with pre-registered stops, and the ceiling.

---
*Last verified against code+DB by `SYSTEM-MAP-REGISTRY-NOT-DRIFTED` on every push — BOTH ways since 2026-09-15: a registry bot missing from this map fails, and a map row naming a bot the registry has dropped fails unless it is struck through (`~~name~~`).*
