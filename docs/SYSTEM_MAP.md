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
| Why that floor | the model is *noisier* than the market, so a small edge is mostly model error — demand a big one to filter noise | ~~Pinnacle is *near-true*, so a 3% overlay is a **real** 3%~~ **FALSE — see the correction below. The 3% floor is currently UNSUPPORTED.** |
| Fires… | when our model disagrees a LOT with the book | rarely — Coolbet ≈ Pinnacle, so beating it by 3%+ is uncommon |
| Known failure | at 13% it still adverse-selects longshots → −21% OOS (the trigger bot) | over-strict floor (e.g. 13%) → never fires |

**A 3% sharp edge and a 13% model edge filter to roughly the same strictness** — they
just measure against different rulers. Putting a model floor on a sharp edge (or vice
versa) is the classic mistake; the `Anchor` column in the bot tables below says which
ruler each bot uses.

**How the two edges are LABELLED in public** (MODEL-EDGE-LABEL, 2026-09-15). Both
edges land in the same Telegram channel and on the same `/picks` page, so the label
has to say which ruler the number was measured against — otherwise a reader compares
a 16% and a 3% and concludes the 16% is five times better, when they are not the same
quantity at all:

| Surface | Model-anchored bots (e.g. `bot_v10_all`) | Sharp-anchored publisher |
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

Anchor = which edge it uses (§1). "Money" = whether it can stake **real** money
(must be in `PLACEABLE_BOTS`) or is **paper**. Floors shown are the gate a pick must
clear. This table is generated from the registry — do not hand-edit rows; edit the
registry and regenerate.

### Real-money capable · Coolbet UI placer

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
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
| **`bot_trigger_1x2_sharp_tight_v1`** | 1x2 | sharp | **2%** | 1.01 (**odds ≤ 2.50**) | paper | **INSTRUMENT, not a strategy** (SHARP-TIGHT-INSTRUMENT-2026-09-15). The one OWN configuration two independent research rounds agreed was worth measuring and neither thought was worth a euro. It exists because the original 70,200-cell sweep *could not express it*: that grid swept a constant expected-ROI floor (`P×odds−1`) while this gate is a constant probability-difference floor (`P−1/odds`), and since `roi_edge = prob_edge × odds` the latter is a **curve in odds** — no constant-floor cell can represent it (§42). Swept correctly it is the only survivor: n=225, ROI +17.07%, CI [+4.18,+29.95], no losing fold, OOS +23.40%. **But both rounds judge it luck**: a 12-day effect (+0.99% n=79 pre-09-02 vs +25.76% n=146 after; Coolbet alone on a constant 37-day pool does the same), and margin-corrected own-book CLV of −5.4% to −7.6% beside those ROIs, against ~−7.2% for a random leg — i.e. the selection buys ~1.9pp of CLV, real but far short of the 7–8% vig. Pooled over Coolbet/Epicbet/Unibet-Site in ONE bot because the result was measured pooled. **Promotion requires margin-corrected own-book CLV > 0 at n≥300; ROI may never promote it at any value** (per-bet sd ≈1.3 ⇒ a true +3% ROI needs ~15,600 bets). Pre-registration: `dev/active/own-sharp-tight-preregistration.md`. |

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
> analogous gap in the placer's `load_picks` is still open — see §4c.

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
| ~~`bot_corners_paper_shadow_v1`~~ | corners | sharp | 0% | — | **RETIRED 2026-09-14** | Margin-corrected own-book CLV **−4.86%**, CI [−5.59,−4.12], **t=−12.93, n=467** (migration 351). Carried for months as "unjudgeable" because its settler believed no corners closing anchor existed — **false**: Pinnacle prices corners on 2,045 fixtures / 43 lines in 30d, covering 95% of Coolbet's corners slate and 87% of Epicbet's, and the bot already de-vigged that same line to SELECT. The closes were in `odds_snapshots` the whole time; 568 settled picks went unjudged and an unanchored +9.60% ROI sat on the dashboard looking like evidence. Also priced against `'Unibet'` (the AF feed, 33.1% phantom-high, dead since 2026-09-12) rather than `'Unibet-Site'`. |
| ~~`bot_team_total_paper_shadow_v1`~~ **RETIRED** | team totals | sharp | 0% | — | paper | Best Epicbet/Betano/Unibet full-match team-total price vs de-vigged Pinnacle line (sharp edge ≥0%). USE-COLLECTED-MARKETS: a market we collect but never modelled; settles from the final score (no coverage gap). Paper, accruing forward. ⚠️ **RETIRED in the DB** (migrations 336/348, BOT-RETIREMENT-ON-CLV); kept struck-through for history. Struck 2026-09-15 — the drift test was registry→map only and could not see a map row for a bot the registry had dropped. |
| ~~`bot_1h_1x2_paper_shadow_v1`~~ **RETIRED** | 1H 1x2 | sharp | 0% | — | paper | Best Epicbet/Betano/Unibet first-half 1X2 price vs Shin-de-vigged Pinnacle 1H triple (sharp edge ≥0%). USE-COLLECTED-MARKETS: a 3-way market we collect but never modelled; settles from the HT score (no gap). Paper, accruing forward. ⚠️ **RETIRED in the DB** (migrations 336/348, BOT-RETIREMENT-ON-CLV); kept struck-through for history. Struck 2026-09-15 — the drift test was registry→map only and could not see a map row for a bot the registry had dropped. |

### In-play slow-state rig (paper) — OWN Phase 1b

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_inplay_slowstate_v1` | in-play O/U 2.5 + 1x2 | none (book's own de-vigged prob) | — | **cap 2.20** | paper | **LIVE arm.** Two LOCKED slow-state triggers at Epicbet's on-screen price: T1 0-0 at 35'–54' → UNDER 2.5; T2 two-goal lead at 70'–89' → the leader. Both only through a price ≤ 2.20 (the long side carries ~14% relative in-play margin vs ~4% short — INPLAY_STRATEGY_CANDIDATES). Primary metric: realised hit-rate minus the book's de-vigged prob, cluster-robust on fixture; CLV inadmissible in play. **STOP at n=1,000 if the lift is negative; decide at n=3,000.** Board + bot: `workers/jobs/inplay_collector.py` (Mac launchd KeepAlive) → `inplay_book_quotes`; read: `scripts/inplay_slowstate_eval.py`. |
| `bot_inplay_slowstate_afctl_v1` | in-play O/U 2.5 + 1x2 | none | — | cap 2.20 | paper | **CONTROL arm.** Same triggers at the same instant priced off API-Football's live aggregate (median 40 s stale). Live − control = the value of the fresh board. Two bots because `shadow_bets_unique` de-duplicates on (bot, match, market, selection). |

> Epicbet only, deliberately: Coolbet's in-play margin is tighter (4.96%/5.16% vs 6.41%/6.44%) and Coolbet has the placer, but its board sits behind Imperva and the request volume is what escalates the wall (RELIABILITY_LEDGER §6/§10). Trigger findings transfer across books; price findings do not. Coolbet in-play collection is a follow-up with its own Imperva budget.

### Internal model / strategy validators (paper)

| Bot | Market | Anchor | Edge floor | Odds floor | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_v10_all` | mixed | model | — | — | paper | The calibrated reference bot: v10 model across target leagues, tier-adjusted thresholds. Honestly calibrated, +11–13% — the yardstick other bots are read against. |
| `bot_high_roi_global_v2` | 1x2 | — | — | — | paper | 1x2 home/away in Spain/Australia/Iceland, odds 1.50–5.50. Internal paper strategy validator. |
<!-- bot_1x2_specialist, bot_dnb_specialist, bot_summer_specialist RETIRED 2026-09-09 (migrations 323/324) and removed from bot_registry.py:116-119 — do not re-add. -->
<!-- NB: bot generation stores best-of-books odds for these general bots (recommended_bookmaker), NOT the Coolbet/Unibet executable price — the SHADOW-PAGE-ROI-INFLATED gap; per-book executable ROI/CLV is the EXECUTABLE-SHADOW-EVAL work. -->

### Pre-registered PICKS forward test (published, not staked)

| Bot | Market | Anchor | Edge floor | Odds | Money | What it does |
|---|---|---|---|---|---|---|
| `bot_sharp_forward_test_v1` | 1x2 + O/U 2.5 | **sharp** | **3%** (multiplicative) | **cap 4.0** | published, not staked | **The picks readers actually see.** `P_shin × best_book_price − 1 ≥ 3%`, odds ≤ 4.0, anchor and bet quote within 60 min. **No daily selection cap** — `TOP_N = 8` was dropped 2026-09-15 (PICKS-NO-DAILY-CAP, owner: *"if possible lets not cap daily picks at all"*); a 60/day runaway breaker is all that remains. **No model output at all.** Flat 1 unit, no Kelly, no bankroll. |

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
   pre-registered: STOP at n=200 if margin-corrected CLV < −2%, STOP at n=400 if
   it is < 0, promote or kill at n=800 on the ROI CI. Primary instrument is
   margin-corrected CLV, not ROI — per-bet return variance is ~1.32, so
   confirming a true +3% ROI at 80% power needs ≈15,600 bets.

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

**Three gates now stand between a bad price and a pick** (all added 2026-09-20):

| gate | where | what it catches |
|---|---|---|
| `anchor_sanity.is_anchor_sane` (ratio > 1.5625× vs Pinnacle) | `best_price_router._latest_book_odds` **and** `pick_trigger_matcher` | 20 of the 25. Weak on O/U, where prices are compressed into ~1.2–3.0 and a wrong fixture rarely trips a ratio test. |
| `BotConfig.edge_ceiling` (8% on `sharp_devig`) | `pick_generator` | all 31, including every O/U one. Works in edge space, so market compression does not blunt it. |
| `pick_triggers.OUTLIER_MULT` (`max_odds = min_odds × 1.6`) | `pick_trigger_matcher` (always had it) **and now `pick_generator`** | 14 of the 25. The generator is a *clone* of the sharp path that had silently dropped this half of the window. |

None dominates the others: the ratio guard works in price space, the ceiling in
edge space, the outlier cap in odds space, and the latter two cross near
`cal_prob ≈ 0.18`. Keep all three.

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
| **/picks** — WHICH BOTS APPEAR | cohort rule | **BOTH families**, unioned in the view `picks_public_all` (migration 361, PICKS-SHOW-BOTH-BOTS 2026-09-16). Sharp arm = `picks_forward_test` where `arm='live'`; model arm = `simulated_bets` for bots with **`bots.show_on_picks = true`** (today: `bot_v10_all` only), pre-match singles, not retired, no combos, no in-play. The gate lives in the DATABASE for the same reason `arm='live'` does — a view cannot forget it. **Before this, /picks read only the sharp ledger**, so `bot_v10_all` published to Telegram and showed on /performance while being invisible on the page the channel links to; `show_on_picks` had existed since migration 356 and was read by nothing. ⚠️ **`edge` in this view means two different things** — see the `edge_kind` column and §1. The page switches its label on it (`Edge vs sharp` / `Model edge`) and so does the break-even tooltip, because `fair_prob` is `p_sharp` in one arm and `calibrated_prob` in the other. Pinned by `PICKS-SHOW-BOTH-BOTS`. |
| **/performance** — WHICH BOTS APPEAR | cohort rule | **`calibrated` or `beta` only** (`PUBLIC_MATURITY_LABELS` in `odds-intel-web/src/lib/bot-aggregates.ts`, applied in three places: the cached leaderboard, the aggregate-bets toggle path, and the hero count — they must agree or the hero reads *N strategies live* above a table of 2). `experimental` bots are the **shadow fleet — OWN-direction work**, and their surface is `/admin/shadow-bots`. `bot_sharp_forward_test_v1` is the one exception: it is injected BELOW the filter from `picks_forward_test_summary`, because it is the bot whose picks readers receive. **REVERSED TWICE, 2026-09-15 → 16:** the filter was dropped on the 15th (*"this page is the measurement surface"*) and restored on the 16th once it was seen to list 13 shadow bots with zero settled bets between them. The 15th's premise was also false — `bot_v10_all` is `calibrated` and was never hidden by it; its absence from **/picks** is a separate gap (`bots.show_on_picks`, migration 356, **still unread by any code**). |
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

> ### ✅ PLACEMENT-GATE — 2026-09-15 (OWN Phase 0). Read before the tables below.
>
> **FIVE functions can reach a money primitive** (the Coolbet place click, the
> Unibet "Tee panus" click, the Coolbet API bet POST), and every one now calls
> **one fail-closed gate** before it does: `workers/automation/placement_gate.py`
> (`assert_run_may_place` at run level, `assert_may_place` per pick). The gate
> checks, in order, `placement_paused` (KILL switch, now fails CLOSED),
> `real_money_armed` (ARMING switch, migration 354, default FALSE, owner-set only),
> `effective_allowlist()` = `PLACEABLE_BOTS` ∩ `coolbet_placer_bots.ui_place_enabled`,
> the kickoff cutoff and the daily caps. Any exception ⇒ refuse.
>
> | executor | where the gate runs | what it replaced |
> |---|---|---|
> | Coolbet UI placer | `place_coolbet_ui.main()` (run level, before browser/lock) and `coolbet_ui_placer.stage_bet` **before `select_outcome`** | a single `is_placement_paused()` read AFTER the stake was typed, which fell OPEN on a DB error |
> | Best-price router (Coolbet + **Unibet-Site**) | `route()` run level; `_dispatch_unibet` before `unibet_placer.place_bet` | `ROUTER_ALLOW_REAL` env var only — which was SET in `.env`, so the router ran in real mode every 30 min; it iterated `PLACEABLE_BOTS`, never the DB toggle |
> | API placer + manual-place drain (VPS, every 10 s) | `coolbet_placer.place_all_bets`; `place_bet_by_id` routes through `MANUAL_PLACE_EXECUTE = False` | an inline pause read (this was the only executor that had one) |
> | In-play API placer (`coolbet_placer.place_all_inplay_bets`) | run level when `execute` — added 2026-09-15 evening after the Phase 0 verifier found it ungated | nothing: it could stamp `placed_real=TRUE` (and post) with no pause/arming read |
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
- ⚠️ **`placement_paused` does NOT gate the Telegram public channel** — not any more
  (PICKS-PUBLISH-DECOUPLED-FROM-OWN-PAUSE, 2026-09-15, migration 353). Publishing has
  its own flag, `publishing_paused`, set only by `/pausepicks`. The two are separate
  because a 🤖 OWN decision to stop staking is not a 👥 PICKS decision to stop
  publishing: on 2026-09-14 the OWN-path verdict flipped `placement_paused` and armed
  a silent customer-feed outage nobody chose. Publishing makes no Coolbet call and
  writes no `real_bets` row, so it is safe while placement is down — and
  `is_publishing_paused()` falls *open* on DB error for that reason. Full table:
  `WORKFLOWS.md` § Pause semantics.
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
