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
| **Corners** | NEW corner-rate Poisson | `match_stats.corners_*` ⚠️ **~17% coverage** | ✅ (corners_ou_90/95, corners_1h) | **settlement-data coverage** (CORNERS-SETTLEMENT-DATA-GAP) |
| **Cards** | NEW card-rate model (+ referee) | `match_stats.*cards_*` ⚠️ **~17% coverage** | limited | **settlement-data coverage** (§51) |
| **1H (1x2, O/U, HT/FT)** | NEW first-half goals model | **HT SCORE — not stored** ❌ (we store HT *stats*, not HT *goals*) | ✅ (`1x2_1h`, `team_total_1h`) | **half-time goals** (CSV-HT-GOALS) |
| **Team totals** | derivable from goals model | final score ✅ | ✅ (`team_total_*`) | economics/validation only |
| **Player props** | NEW player model | player events + lineups (partial) | mostly none | biggest data lift — defer |

## The three takeaways

1. **Goals-based markets are almost free.** BTTS, DC, AH, team-totals are
   projections of the one goals model we already run — no new model, no new
   settlement data. Their only gaps are *anchor* (BTTS) or *economics* (AH/DC).
2. **Stat markets (corners, cards) need one thing: coverage.** We have the
   columns, the odds, and a Pinnacle anchor; the post-match stat lands for only
   ~17% of fixtures. Fix that and settlement + training labels appear together.
3. **1H needs one dataset: half-time goals.** We store HT stats but not HT
   score. Pinnacle already prices 1H, so once HT goals are collected the model
   is the same machinery as full-match.

## Ranked "what to fetch"

- **Nothing to fetch** → BTTS/DC/AH/team-totals (need an *anchor decision* for
  BTTS; an *economics* call for AH/DC).
- **Fetch better match-statistics coverage** → unlocks corners AND cards at once.
- **Fetch half-time goals** → unlocks the whole 1H family.
- **Fetch player events + lineups** → player props (largest, defer).

## Open audits (2026-09-08)

- **MARKET-DATA-AF-AUDIT** — for each "fetch/fetch-better" need, does AF actually
  have it (just needs implementing/fixing) or is AF the ceiling (need another
  source)?
- **MARKET-EDGE-SWEEP-EXISTING-DATA** — for the goals-derivable markets (BTTS/DC/
  AH), run a multi-dimensional, held-out-OOS sweep over ~100k fixtures to see if
  a real edge exists; then check whether the required odds are actually
  reachable at Coolbet/Unibet (UI-safer) before building anything.
