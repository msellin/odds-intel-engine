# Market Data Map — what each market needs to become a bettable model

First artifact of MARKET-EXPANSION (filed 2026-09-08). Maps every candidate
market to the **three data streams** a viable bot needs, grounded in what we
actually store today. "Can we model it, settle it, and anchor its edge?"

## The framework — every market needs three streams

1. **Sharp anchor** (Pinnacle) — to compute honest edge/CLV. Without it you
   cannot distinguish a real edge from noise (this is why BTTS is stuck).
2. **Settlement/outcome data** — the post-match ground truth, which is *also*
   the model's training label. No label → cannot train or grade.
3. **Model features** — predictive inputs. For goals-based markets these are
   already derivable from the bivariate-Poisson goals model we run.

## The map (as of 2026-09-08)

| Market | Status | Model source | Settlement data (status) | Pinnacle anchor | Real blocker |
|---|---|---|---|---|---|
| 1x2, O/U 2.5/3.5 | ✅ **LIVE** | goals model (LIVE) | final score ✅ | ✅ | none — live |
| **BTTS** | ❌ **DEAD — do not build** | derivable from goals model — P(both score) | final score ✅ | ❌ **none** (AF Pinnacle feed has no BTTS) | **NO EDGE (2026-09-08 diag, n≤41k):** BTTS is barely predictable by anyone — even the full market consensus is AUC 0.59 (≈coin); our model 0.54 adds *zero* info beyond the price. Not a fixable derivation/anchor gap. |
| **Double chance** | ❌ **DEAD — no model possible** | *arithmetic* off 1x2 — P(home∪draw)=P(H)+P(D) | final score ✅ | via 1x2 devig ✅ | **Deterministic collapse of 1x2** — a separate DC model is incoherent (it can't beat the 1x2 model on DC). Loses everywhere on OOS ceiling (−10→−12%). Retired. |
| **Asian Handicap** | ❌ **DEAD — market owns it** | derivable from goals model — P(margin ≥ line) | ✅ (top Pinnacle market) | final score ✅ | **MARKET-BEATS-MODEL (2026-09-08 diag, n=74k obs):** AH margins ARE predictable, but within-line market AUC 0.70–0.75 vs our model 0.55 (adds zero info); + 7% recreational vig wall; Coolbet full/half lines only. A dedicated margin model would have to *beat* 0.72 — no evidence we can. |
| **Corners** | ⛔ **AF CEILING (backfill tried, failed)** | NEW corner-rate Poisson | `match_stats.corners_*` **~32%, stuck** | ✅ (corners_ou_90/95, corners_1h) | **AF ceiling, NOT a collection gap** — backfill of 19,677 cov=TRUE fixtures missing corners returned stats for only **748 (96% empty)**: the `coverage_statistics_fixtures` flag is league-level & optimistic; AF lacks per-fixture stats for the rest. Non-AF source (football-data CSV `HC`) needed to grow it — CORNERS-SETTLEMENT-DATA-GAP |
| **Cards** | ⛔ **AF CEILING (same)** | NEW card-rate model (+ referee) | `match_stats.*cards_*` **~32%, stuck** (same stats row as corners) | limited | **same AF ceiling as corners** (748/19,677 fillable); `/fixtures/events` card count is the settlement path but the training-label coverage can't grow via AF — §51 |
| **1H / 2H** | 🟠 **naive model DEAD (market owns it); data kept** | first-half Dixon-Coles Poisson (`scripts/first_half_edge_test.py`) | **HT/2H STORED ✅** `matches.ht_score_*`+`h2_score_*`, forward-wired + daily sweep | ✅ (`1x2_1h`, `over_under_1h_*`) ; no native 2H line | **RE-TEST w/ 5× fit (35k train, 3.2k test): 1H market SHARP (AUC 0.66), naive model 0.54, adds ZERO info — NOT a starved-fit problem, the model genuinely can't compete (AH-like). Only a feature-rich dedicated 1H model could close the 0.12 gap — long shot, PARKED. HT/2H data kept (valuable regardless). Descriptive 2H skew (55.3%) real but market-known. §54.** |
| **Team totals** | 🟡 derivable, unvalidated | derivable from goals model | final score ✅ | ✅ (`team_total_*`) | economics/validation only |
| **Player props** | ⏸ defer | NEW player model | player events + lineups (partial) | mostly none | biggest data lift — defer |

## The three takeaways

1. **Goals-based markets are almost free.** BTTS, DC, AH, team-totals are
   projections of the one goals model we already run — no new model, no new
   settlement data. Their only gaps are *anchor* (BTTS) or *economics* (AH/DC).
2. **Stat markets (corners, cards) are an AF CEILING — CORRECTED 2026-09-08.**
   The AF audit predicted a cheap ~13k cov=TRUE backfill would lift corners to
   ~40%. Empirically it did NOT: fetching all 19,677 cov=TRUE fixtures missing
   corners returned stats for only **748 (96% empty)**. The
   `coverage_statistics_fixtures` flag is league-level and optimistic; AF simply
   lacks per-fixture stats for the rest. Corners/cards stay ~32% and can only grow
   via a NON-AF source (football-data CSV `HC`/`HY`/`HR`). This is a real ceiling,
   not a collection gap.
3. **1H needs one dataset: half-time goals.** We store HT stats but not HT
   score. Pinnacle already prices 1H, so once HT goals are collected the model
   is the same machinery as full-match.

## Ranked "what to fetch"

- **Nothing to fetch** → BTTS/DC/AH/team-totals (need an *anchor decision* for
  BTTS; an *economics* call for AH/DC).
- **Fetch better match-statistics coverage** → unlocks corners AND cards at once.
- **Fetch half-time goals** → unlocks the whole 1H family.
- **Fetch player events + lineups** → player props (largest, defer).

## Audit findings (MARKET-DATA-AF-AUDIT — resolved 2026-09-08)

Read-only audit against the AF v3.9.3 PDF + live DB counts. Full report in git
history / task notes. Headline: **most "fetch better" needs are AF-native and
cheap; the true ceilings are narrow.**

**Cheap AF-native wins (low/zero extra quota):**
1. **HT goals** — `/fixtures` `score.halftime.{home,away}`, full history, **zero
   extra calls** (already in a response we pay for). We never read it
   (`fixture_to_match_dict` reads only `goals`). Add `matches.ht_score_home/away`
   + extract → unlocks the whole 1H family's settlement + labels. Highest
   value-per-effort. Read the object directly, do NOT derive from events (10-37%
   of matches carry no events).
2. **Corners/cards backfill** over `coverage_statistics_fixtures=TRUE` leagues —
   the gate is right; ~13,438 cov=TRUE fixtures were simply never enriched
   (enrichment only looks at yesterday/today, so summer/off-window leagues fell
   through). One `/fixtures/statistics` call fills corners AND cards together.
3. **Parse 4 already-received Pinnacle types** (AH-1st-Half, Corners AH, Cards
   AH, Correct Score 1H) — zero extra quota; widens anchors. Watch the
   MARKET-LINE-ENCODING-LOSSY hazard (name-encoding collides, e.g. "125"=1.25 & 12.5).
4. **`CAPTURE_EXACT_SCORE` history backfill** — Pinnacle's ~70-scoreline Exact
   Score grid is **the only Pinnacle-native BTTS anchor** (compute P(both score)
   from the sharp joint distribution). Currently OFF for volume.

**True AF ceilings (need another source):**
- Corners/cards for `statistics_fixtures=false` leagues (~59% of fixtures) →
  football-data CSV path.
- **Pinnacle BTTS marginal** — never sent by AF (only 19 Pinnacle bet types via
  bulk `/odds`; BTTS + double_chance absent) → Exact-Score derivation or soft consensus.
- **HT/FT** — no Pinnacle anchor at all.
- **Player-prop sharp anchors** — essentially absent; largest lift, defer.

**Correction folded in:** the old "~17%" / "AF genuinely lacks Saudi·Ecuador·
K League·Veikkausliiga" claim was wrong — all four are `statistics_fixtures=TRUE`
(Saudi 99%, Ecuador 93% already collected); true overall coverage is ~31%.

## Edge-sweep verdict (MARKET-EDGE-SWEEP-EXISTING-DATA — resolved 2026-09-08)

> **⚠️ BASIS CORRECTION (2026-09-08, later same day).** The ROI figures below use
> the *best-of-accessible* price + an edge gate, which re-creates the line-shop
> selection artifact and is biased ~4–7pp LOW — it shows even our profitable O/U
> 2.5 as −1.6% (see ANALYSIS_GOTCHAS §55). Re-judge on the SINGLE-BOOK Coolbet
> executable basis: **AH/DC stay negative → DEAD holds (softer). BTTS shifts to
> ~marginal — not a clean ROI-dead; its real blocker is NO Pinnacle anchor +
> model AUC ≈ coin (discrimination), which still says "not worth it."** The
> discrimination facts below are solid; the best-of-books ROI magnitudes are not.


Multi-dimensional held-out-OOS sweep (isotonic calibrated on TRAIN only, applied
to untouched TEST; edge-floor × odds-floor × tier; walk-forward folds), on the
canonical accessible book set {Coolbet, Betano, Unibet, Epicbet}, pre-match only.

**All three goals-derivable candidate markets are DEAD — do NOT build models for
them.** The decisive number is the calibrated ROI on the *idealized best-of-books*
price (which structurally **inflates** ROI — you pick whichever accessible book is
most mispriced), on the held-out TEST split:

| edge floor | BTTS | Double Chance | Asian Handicap |
|---|---|---|---|
| ≥0% | −5.6% (n=3827) | −9.7% (n=6489) | −9.6% (n=6593) |
| ≥10% | −2.1% (n=1793) | −11.4% (n=4662) | −10.2% (n=5007) |
| ≥20% | −3.0% (n=858) | −11.9% (n=3413) | −10.9% (n=3802) |

They fail *even the ceiling*; the executable reality is worse.

**Validation (2026-09-08, after the CSVs were questioned):** the BTTS diagnostic
was independently re-derived straight from the DB (model prob from `predictions`,
market from `odds_snapshots`) — it reproduced the scratch CSV exactly (model-prob
corr 0.994, odds corr 1.000, outcomes 100%). The AH diagnostic was rebuilt from
the DB with outcomes graded from raw scores (no reliance on the CSV `won` column).
Both verdicts survive at maximum available scale — BTTS's data ceiling is 41,020
matches (only ~41k finished matches carry a BTTS model prediction; ~28k carry BTTS
odds), AH's is ~22,720; the model AUC is flat (~0.54 BTTS, ~0.55 AH) across every
sample size, so it is not a small-n artifact. (100k+ history exists only for the
core 1x2/O/U markets, not these.)

- **BTTS — THIN at best, do not build.** The only non-negative frame (BTTS-yes,
  odds ≥2.6, +54% pooled) exists only in a 6-week Coolbet window with a 52%
  win-rate at 3.14 odds (breakeven 31.9%) — a price-fidelity artifact, not an
  edge. Dies under calibration (−3.1%). **Discrimination diagnostic (2026-09-08,
  n=16,505 held-out): NOT a fixable derivation problem.** Our model's BTTS AUC is
  0.540 vs the market's 0.588 (we under-rank), and adding our model to the market
  price gives ZERO incremental info out-of-sample (nested-logistic TEST log-loss
  0.6788→0.6791, model coef 0.089 vs market 0.846). A dedicated BTTS head could
  close the 0.540→0.588 gap but the market itself is barely above a coin (0.588),
  so the best realistic outcome is "match a market we can't even anchor" (no
  Pinnacle BTTS). It's genuine near-efficiency + intrinsic BTTS unpredictability,
  not a wrong-model artifact.
- **Double Chance — DEAD.** Negative everywhere, both price bases, all selections.
- **Asian Handicap — DEAD.** Negative everywhere after a grading-sign fix (see
  ANALYSIS_GOTCHAS §53). An early fake +142% was a bug, not an edge.
  **Discrimination diagnostic (2026-09-08, DB-rebuilt + self-graded from raw
  scores, n=74,021 home-cover obs / 18,500 matches, all-book consensus):** unlike
  BTTS, AH margins ARE predictable — but the MARKET owns it. Within-line market
  AUC 0.70–0.75 vs our model 0.55 (Δ=−0.16), and the model adds ZERO incremental
  info OOS (nested-logistic 0.5363→0.5365, model coef 0.028 vs market 1.114). A
  dedicated margin model could lift our 0.55, but the bar is the market's ~0.72
  and we show no signal it lacks. Not worth building.

**Reconciliation with bot_2d_audit's first pass:** its "btts_all +12%" and
"ah_away_dog +2%" were both OVERTURNED here — the first is raw/uncalibrated and
confined to the 6-week window; the second was the AH grading-sign bug.

**Data limitation for the executable question:** Coolbet odds for these markets
only exist from ~Aug 2026 (~6 weeks), so the *executable* basis has no real
cross-regime holdout yet. Only the idealized (best-of-books mirage) basis has
depth — and it loses. **No phase-2 odds-reachability check is warranted:** nothing
cleared the bar. If BTTS is ever revisited, it needs (1) a Coolbet BTTS
price-fidelity audit and (2) far more executable history first.

## O/U extra lines 1.5 / 3.5 (TIER A — tested 2026-09-08, `scripts/ou_lines_edge_test.py`)

The goals model already emits over/under 1.5 & 3.5 predictions (same machinery as
the live 2.5) — no new model/data. Tested calibrated model-edge on the SINGLE-BOOK
Coolbet executable price (the correct basis, §55), edge≥8%, held-out TEST:

| line | Coolbet-exec ROI @edge≥8% | fold-robust? | verdict |
|---|---|---|---|
| O/U 2.5 (control, live) | +5.6% | no (n≈3.4k, ~6mo Coolbet history) | our baseline |
| **O/U 3.5** | **+7.8%** | no (folds −14/+36/−3) | **candidate — mirrors 2.5; run as paper/shadow bot, gate on robustness** |
| O/U 1.5 | −18% | no | dead |

Key caveat: **nothing is fold-robust yet, including the live 2.5**, because Coolbet
history is only ~6 months. 3.5 should accrue as a paper bot and flip to real money
only once fold-robust — same discipline as every other line.
