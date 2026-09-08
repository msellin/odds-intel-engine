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

| Market | Model source | Settlement data (status) | Pinnacle anchor | Real blocker |
|---|---|---|---|---|
| 1x2, O/U 2.5/3.5 | goals model (LIVE) | final score ✅ | ✅ | none — live |
| **BTTS** | derivable from goals model — P(both score) | final score ✅ | ❌ **none** (AF Pinnacle feed has no BTTS) | **anchor** — need soft-book consensus anchor |
| **Double chance** | derivable from 1x2 — P(home∪draw) | final score ✅ | via 1x2 devig ✅ | *economics* (retired for losing), not data |
| **Asian Handicap** | derivable from goals model — P(margin ≥ line) | final score ✅ | ✅ (top Pinnacle market) | *economics* — 7.09% recreational margin; Coolbet full/half lines only |
| **Corners** | NEW corner-rate Poisson | `match_stats.corners_*` **31% overall / 75% in AF-coverage=TRUE leagues** | ✅ (corners_ou_90/95, corners_1h) | **collection backfill** over cov=TRUE leagues (not an AF ceiling for bettable leagues) — CORNERS-SETTLEMENT-DATA-GAP |
| **Cards** | NEW card-rate model (+ referee) | `match_stats.*cards_*` **~32%** (same stats row as corners); settlement uses `/fixtures/events` count | limited | **collection backfill** (same cov=TRUE fix as corners) — §51 |
| **1H (1x2, O/U)** | NEW first-half goals model | **HT SCORE — not stored** ❌ but AF-native: `/fixtures` `score.halftime.{home,away}`, full history, zero extra calls | ✅ (`1x2_1h`, `team_total_1h`, `over_under_1h`) | **half-time goals** — add `matches.ht_score_*` col + extract (CSV-HT-GOALS; AF is the cheap route). NB **HT/FT has NO Pinnacle anchor** (not among AF's 19 Pinnacle bet types) |
| **Team totals** | derivable from goals model | final score ✅ | ✅ (`team_total_*`) | economics/validation only |
| **Player props** | NEW player model | player events + lineups (partial) | mostly none | biggest data lift — defer |

## The three takeaways

1. **Goals-based markets are almost free.** BTTS, DC, AH, team-totals are
   projections of the one goals model we already run — no new model, no new
   settlement data. Their only gaps are *anchor* (BTTS) or *economics* (AH/DC).
2. **Stat markets (corners, cards) need a historical BACKFILL, not new plumbing.**
   We have the columns, the odds, a Pinnacle anchor, and the enrichment gate is
   already correct (skips AF-false-coverage leagues). Corners land for **31%**
   of finished fixtures overall but **75% inside AF-coverage=TRUE leagues** — the
   gap is ~13k cov=TRUE fixtures that finished outside a backfill window (K League
   34%, Veikkausliiga 18%). One `/fixtures/statistics` call per fixture fills
   corners AND cards together. The `statistics_fixtures=false` tail (~59% of
   fixtures) is a real AF ceiling → football-data CSV (HC/HY/HR) is the fallback.
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
  ANALYSIS_GOTCHAS AH-HANDICAP-HOME-PERSPECTIVE). An early fake +142% was a bug,
  not an edge.

**Reconciliation with bot_2d_audit's first pass:** its "btts_all +12%" and
"ah_away_dog +2%" were both OVERTURNED here — the first is raw/uncalibrated and
confined to the 6-week window; the second was the AH grading-sign bug.

**Data limitation for the executable question:** Coolbet odds for these markets
only exist from ~Aug 2026 (~6 weeks), so the *executable* basis has no real
cross-regime holdout yet. Only the idealized (best-of-books mirage) basis has
depth — and it loses. **No phase-2 odds-reachability check is warranted:** nothing
cleared the bar. If BTTS is ever revisited, it needs (1) a Coolbet BTTS
price-fidelity audit and (2) far more executable history first.
