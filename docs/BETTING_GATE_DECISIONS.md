# Betting Gate Decisions — the single source of truth for edge/odds floors

**Read this before changing any real-money edge or odds floor, and before running
"a quick backtest" to re-decide one.** This doc exists because we kept running
*different* backtests that gave *different* answers and kept changing the floors —
the fix is a fixed method + a recorded decision, not another ad-hoc run.
(Established 2026-09-09 after the 1x2 10-vs-13 churn.)

## The canonical method (use THIS, nothing else, for a floor decision)

Run `scripts/edge_floor_backtest.py --market <m> --folds 3`. A floor is
**decided on the EXECUTABLE basis** ("bots' actual picks", ideally the
active/calibrated slice), and it is **acceptable only if it is fold-robust —
positive in EVERY walk-forward fold**, not merely positive pooled.

**What NOT to decide on** (every one of these produced a *different* answer and a
needless change in the past):
- **The idealized / best-of-books basis.** It shows +50–150% ROI (O/U) because
  taking the best of many books selects the most-mispriced book — the line-shop
  mirage (§55, §52). Its *monotonicity* (higher floor → higher ROI) is the only
  signal; its *magnitude* is fantasy and its "robust ✓" is not executable.
- **Total profit at flat stake.** More volume at a lower floor makes more total €
  while being *less* ROI-robust. That's a volume objective, not an edge decision.
  (This is exactly how BOT-CONFIG-GOLDEN-MIDDLE briefly argued for 0.10.)
- **A small recent window** (e.g. one bot's last ~370 picks). Favorable regimes
  make a non-robust floor look robust. Use the full executable universe.

**Rule: no floor change without re-running the above on the executable basis AND
updating the table below in the same commit.** If the executable basis and the
idealized basis disagree, the executable basis wins.

## Decisions (as of 2026-09-09)

| Market | Edge floor | Odds floor | Verdict on the executable basis | Evidence |
|---|---|---|---|---|
| **1x2** | **13%** | **2.80** | **13% fold-robust; 10% is NOT** (negative fold: all-bots f1 −1.8%, calibrated f2 −3.6%). Keep 13%. | `edge_floor_backtest --market 1x2`: exec all-bots ≥13% +8.3% ✓ / ≥10% +3.1% ✗; calibrated ≥13% +15.9% ✓ / ≥10% +15.1% ✗ |
| **O/U 2.5** | **8%** | **1.80** | **8% fold-robust** (and robust down to ~5%). Keep 8%. | `edge_floor_backtest --market o/u`: exec all-bots ≥8% +13.8% ✓ / calibrated ≥8% +18.5% ✓; 13% breaks (f3 −5.3%) |
| Asian handicap | — (not placed) | — | No fold-robust cell at any floor → not placed | see AH-VIABILITY-REVIEW (closed) |

### 11% and 12% also tested (2026-09-09) — neither is fold-robust
Owner asked whether 11% or 12% would let us keep more 1x2 volume without dropping to
13%. Re-ran `edge_floor_backtest` logic with the floor set widened to {9,10,11,12,13,14}%,
folds 3/4/5, both executable slices + the idealized basis. Result: **13% remains the
lowest fold-robust floor.** 11% fails (all-bots f3 −3.1%, calibrated f2 −8.8%). 12% is a
*false pass* — barely positive on one folds=3 fold (+0.2%), collapses to −25.4% at
folds=4, and fails the calibrated slice (f2 −6.0%). The idealized 104k-fixture basis
passes everything ≥8% (best-of-books mirage, §52/§55) — executable basis wins, and it
says 13%. Volume forgone by staying at 13%: ~+18% picks at 12%, ~+41% at 11%, each with
a demonstrated negative fold. **Do not lower below 13%.**

### The 1x2 10-vs-13 tradeoff, recorded so it isn't re-litigated
10% makes **more total profit** (more volume — it fires ~45% more often, which is
why the live 1x2 bot "finds 0" at 13% on a quiet day) but is **not fold-robust**
(a negative fold on the executable basis). 13% is **lower volume, higher and
robust ROI**. On the executable basis that governs real money, **13% wins.** If we
ever want the volume, it is a deliberate *volume-for-robustness* trade and an
owner decision — not a "the backtest said 10%" change, because the backtest that
said 10% used total-profit or a small window, not fold-robust executable ROI.

## Why the same question kept giving different answers (the actual bug)

Three axes were varying silently between runs, and none was written down:
1. **Basis** — executable (real) vs idealized best-of-books (inflated mirage).
2. **Metric** — fold-robust ROI vs pooled ROI vs total profit.
3. **Sample** — full executable universe vs one bot's recent ~370 picks.

Pick different values on those axes and you get −21%, +3%, +8%, +24%, or +93% for
what feels like "the same test." Fixing the axes (executable · fold-robust ROI ·
full universe) makes the answer stable and reproducible. That is the whole point
of this doc.

## Sharp-anchor trigger floors (paper, 2026-09-09)

The sharp-anchor trigger bots (`bot_coolbet_trigger_sharp_1x2_v1` / `_ou_v1`) gate on
a **different edge** — `P_sharp − 1/book_odds` (de-vigged Pinnacle), not the model —
so the 13%/8% model floors do NOT apply to them. A sharp edge is measured against a
near-true line, so its floor is necessarily small.

| Sharp bot | Edge floor | Odds floor | Rationale |
|---|---|---|---|
| sharp 1x2 | **3%** | **1.01** (off) | 3% overlay vs Pinnacle is real (max observed +6.6%); 13% would never fire. Odds floor is a no odds floor (experimental — observing all bands), NOT the model twin's 2.80 — the model's high odds floor is an anti-longshot guard for model over-confidence, which does not apply to a sharp anchor (whose value is often at favourite prices). |
| sharp O/U 2.5 | **3%** | **1.01** (off) | same reasoning; the model twin's 1.80 would exclude sharp edges on shorter prices. |

These are **paper** starting floors, not validated on the executable basis (that's the
whole reason the bots run — to measure whether the sharp anchor finds anything). They
are owner-adjustable and sourced from `workers/jobs/pick_triggers.py`
`_SHARP_MIN_EDGE_BY_MARKET` / `_SHARP_MIN_ODDS_BY_MARKET`. When there is enough
settled volume, decide a real floor with the canonical method above (on the sharp
basis) and record it here. See `docs/SYSTEM_MAP.md` §1 for the two-edges distinction.

## Related
- `scripts/edge_floor_backtest.py` — the canonical tool (executable + idealized +
  walk-forward folds + the STALE-BEST-ODDS / DISTINCT-ON guards).
- ANALYSIS_GOTCHAS §52 (line-shop mirage), §55 (single-book vs best-of-books).
- `docs/BOOK_AGNOSTIC_EDGE_ENGINE.md` — the trigger engine (a *different* selection
  whose −21% is the model-vs-Coolbet adverse-selection problem, not a floor issue).
- The placer reads these floors from `coolbet_placer._MIN_EDGE_BY_MARKET` /
  `_MIN_ODDS_BY_MARKET`; the mirror jobs mirror them; the smoke test
  `BOT-CONFIG-GOLDEN-MIDDLE` pins the 1x2 value.

## 1x2 fav/long split — favourites are a robust loser (FAVLONG-SPLIT-FLOOR-BACKTEST, 2026-09-09)

The "13% is the best 1x2 floor" result was measured on POOLED 1x2. Splitting by the
generation cut (fav = home pick odds <2.0; long = draws/aways/home ≥2.0) via
`scripts/favlong_floor_backtest.py` (same walk-forward `_sweep`, executable price, 3 folds):

| Side | n (all / cohort) | Best fold-robust floor | ROI at 13% | Shape |
|---|---|---|---|---|
| **FAV** (home <2.0) | 137 / 49 | **NONE robust at any floor** | −36% / −17% | negative everywhere; **worse as the floor rises** (high-"edge" favourites are the biggest model errors) |
| **LONG** (draw/away/home ≥2.0) | 1772 / 370 | **13% robust ✓** (also 15/18) | +10.8% / +17.0% | carries all the profit |
| POOLED | 1909 / 419 | 13% robust ✓ | +8.3% / +15.8% | positive only because longs are 93% of picks |

**Conclusion:** the model has **no real edge on home favourites** — they are a fold-robust
loss at every floor, and higher favourite "edge" is noise, not signal. The pooled 13% floor
hides this because longshots dominate the count. **We currently place + publish home-favourite
1x2 bets that clear 13% and they lose.** Candidate remediation (OWNER-GATED — changes real-money
placement + the published record): exclude home favourites from placement (or set their floor
unreachably), keep longs at 13% (explore 15%). Confirm first with the pure odds-band cut
(odds<2.0 either side) since this cut mirrors generation and pools away-favourites into 'long'.

## 1x2 by SELECTION TYPE — the fold-robust edge is home-underdogs only (2026-09-09)

FAVLONG-SPLIT-FLOOR-BACKTEST, extended to all four 1x2 types × floors × sample sizes
(cohort + idealized 10k/25k/35k) + an odds×edge cut on the placed (odds≥2.80) universe.
Interactive matrix: the floor-by-type artifact. Verdict per type:

| Type | Verdict | Evidence |
|---|---|---|
| **home-fav** (home <2.0) | **exclude** — loses at every floor we bet | idealized edge only at 5–8%, dead ≥10% (−7→−12%); cohort negative. Already excluded from real money by the 2.80 odds floor; the change is to the PUBLISHED record. |
| **home-underdog** (home ≥2.80) | **BET — floor 10%** | the one fold-robust engine. On odds≥2.80: robust in BOTH bases from ~8%; 10% is the sweet spot — cohort +21% (n206), idealized +24% (n1198), ~50% more volume than 13%. The 2.80 odds floor already strips the losing low-odds picks. |
| **draw** | **not a model bet — route to sharp triggers** | model bets 0 draws (under-rates them, never clears 12% — §57). The idealized draw edge (8–12% band) is a SHARP/soft-book-mispricing edge vs de-vig Pinnacle, not a model edge → sharp-anchored trigger bots' territory. |
| **away** | **exclude** — no fold-robust edge | idealized away robust at NO floor/size; the cohort's +43→+105% is 10–18 bets of luck. Unreliable → don't stake. |

### PER-SELECTION RE-RUN 2026-09-11 — the pooled floor's remaining job is to admit losers

Owner: *"we shouldn't have the pooled edge floor anymore as it was introducing
home favs."* Re-ran the same methodology **per selection** rather than fav-vs-long
(`scripts/favlong_floor_backtest.py --by-selection`, executable price
`COALESCE(odds_at_pick_live, odds_at_pick)`, walk-forward 3 folds,
active/calibrated cohort n=425). The fav/long split could not answer the question
because LONG lumps AWAYS in with home-underdogs — the two selections it turns on.

| selection | n | ROI @13% (the pooled gate) | robust at any floor? |
|---|---|---|---|
| **home-UNDERDOG** (≥2.80) | 236 | +16.0% (and **+21.0% @10%**, n=212) | **✓ at 0-13% and 18%** |
| **home-MID** (2.00–2.80) | 122 | **−12.0%** | ✗ never — every floor has a losing fold |
| **home-FAV** (<2.00) | 49 | **−17.3%** | ✗ never |
| **AWAY** | 18 | +104.9% | flagged ✓ but see below |
| **DRAW** | 0 | — | model bets no draws (as documented) |

**Two findings.**

1. **The 10% home-underdog floor is emphatically validated** — +21.0% on 212
   executable bets, positive in all three folds (12.1 / 23.1 / 26.7), and the
   sweet spot of the sweep. Nothing to change there.

2. **A band belongs to NEITHER existing cut.** FAVLONG-CUTS defines home-fav as
   `<2.00` and home-underdog as `≥2.80`, so **home picks between 2.00 and 2.80
   fall through to the pooled floor** — and that band is the largest losing group
   we have: n=122, −12.0% at the pooled gate, with **no robust floor at any
   level** (it degrades as the floor rises: +13.3% @8% → +3.2% @12% → −12.0%
   @13%). Nobody had named it. Smoke `FAVLONG-PER-SELECTION` now asserts the
   selection cuts are exhaustive and non-overlapping so a band cannot silently
   fall through again.

**On AWAY: do not read the ✓.** n=18 total, and the "robust" rows are n=10 and
n=8 with fold ROIs of +1.0% / +92.5% / +169.2% — one or two long-odds winners.
This is the identical artefact FAVLONG-CUTS already called out ("the cohort's
+43→+105% is 10–18 bets of luck"), and the robustness flag is not meaningful at
that sample size. No evidence either way; the conservative call stands.

**So the pooled 13% floor now has no defensible job on the publication side:**
every group it admits is either measurably losing on executable pricing
(home-MID −12.0%, home-FAV −17.3%) or carries no evidence (AWAY n=18, DRAW n=0).
Real money is unaffected either way — the 2.80 odds floor plus the home-only
mirror already restrict placement to home-underdogs.

**Cost of retiring it: 20 of 152 published 1x2 picks over 90d (13%).** NB an
earlier read of "47%" was measured across ALL `simulated_bets` including
non-published bots; on what actually reaches the channel it is 13%.

**STILL OWNER-GATED, and deliberately not implemented yet** — "retire the pooled
floor" means four different things to its four consumers, one of which would die
silently:

| consumer | effect of `_MIN_EDGE_BY_MARKET['1x2'] = None` |
|---|---|
| `min_edge_for_pick` fallback → signaler / loaders / live re-evals / router | non-home-underdog 1x2 excluded — **the intent** |
| `pick_triggers._emit_model_anchor` (line ~204) | **kills the `model_1x2` trigger family outright** (paper research bots). Needs its own explicit floor first. |
| `coolbet_prekickoff_alert` (line ~166) | selection-blind `_min_edge_for` — would exclude all non-underdog 1x2 |
| `coolbet_placer` log line (~614) | `None * 100` → **TypeError**; must be made None-safe |
| `gen_frontend_floors.py` → `ENGINE_MIN_EDGE_BY_MARKET['1x2']` | becomes `null`; the auto-place badge mirror `COOLBET_AUTO_MIN_EDGE_BY_MARKET['1x2']` expects 0.13 |

Recommended shape when approved: **per-selection floors, not a single pooled
number** — i.e. express the policy as "home-fav: excluded, home-mid: excluded,
away: excluded, draw: sharp-only" rather than one value that happens to gate four
different populations. That matches the standing design rule (callers pass WHO
THEY ARE, never a floor) and makes the home-MID band impossible to overlook again.

**Resulting real-money 1x2 policy (OWNER-GATED — changes placement + published record):**
`bet 1x2 iff selection=home AND odds ≥ 2.80 AND edge ≥ 10%` (home-underdogs only). Exclude
home-favs + aways; draws handled by the sharp trigger family (paper). O/U 2.5 unchanged (8%).
Expected effect: higher ROI AND more bets, concentrated on the robust engine. The pooled
"13% is best" result was correct only because home-underdogs dominate the pooled count and
dragged the favourite/away noise positive.

**IMPLEMENTED 2026-09-09 (FAVLONG-CUTS, owner-approved).** Real-money 1x2 = home-underdogs
@10%, odds≥2.80. Changed: `coolbet_model_1x2_shadow` (mirror) → home + odds≥2.80 + edge≥10%;
`place_coolbet_ui.BOT_THRESHOLDS['bot_coolbet_1x2_model_v1']` 0.13→0.10 (the real-money gate);
`bot_registry` edge_floor 0.13→0.10; frontend PER-BOT `coolbet-edge.ts BOT_EDGE_THRESHOLDS['bot_coolbet_1x2_model_v1']` 0.13→0.10.
**PER-MARKET-EDGE-MIRROR-FIX-2026-09-10:** the frontend also has a POOLED, selection-agnostic mirror
`COOLBET_AUTO_MIN_EDGE_BY_MARKET['1x2']` (the auto-place badge, via `autoMinEdgeFor(market)`); commit 2d86b9c
erroneously dropped THAT to 0.10 too, which made the badge greenlight 1x2 of any selection at 10%. Reverted
to 0.13 — it must mirror the engine's pooled floor, not the per-bot one. The pooled/paper
`_MIN_EDGE_BY_MARKET['1x2']` stays 13% (trigger windows, all-selection; the paper daemon that also read this floor is retired 2026-09-10). Home-favs
already excluded by the 2.80 odds floor; aways/draws excluded by the home-only mirror. O/U unchanged.
Takes effect on the next placer run (bot is toggled ON). Publication side (/performance, grades) →
PICKS-GRADING.

**SIGNAL-PLACER-1X2-ALIGN (2026-09-10).** The Telegram SIGNAL path
(`coolbet_placer.load_qualified_bets`) was still gating 1x2 on the pooled 13%
floor, so home-underdogs in the 10–13% band were placed with real money but never
signaled (Stevenage v Luton, Home @3.48, +12%). Fixed with a selection-aware
signal floor `_signal_min_edge_for`: 1x2 home-underdog (`selection=home AND
odds≥2.80`) → 10% (shares `COOLBET_MODEL_1X2_EDGE_FLOOR` with the mirror);
everything else → the pooled `_min_edge_for`. **The pooled `_MIN_EDGE_BY_MARKET['1x2']`
is unchanged at 13%** — only the signal path gained the home-underdog carve-out, so
draws/aways (not fold-robust at 10%) and the trigger windows are untouched. Home-favs
still fall on the pooled 13% floor for signals and stay excluded from real money by
the 2.80 odds floor; dropping home-favs from SIGNALS too is a separate published-picks
call (see line 116, OWNER-GATED) and was not done here.
