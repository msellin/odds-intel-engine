# Per-market feature sets — the design

Parent row: **[[#089]]**. Research inputs: the two 2026-09-23 literature reviews
(summarised on the row) and `docs/MODELLING_DATA_AUDIT_2026_09_16.md`.

---

## The principle, in one line

**1x2 lives on the DIFFERENCE of scoring rates. Totals live on the SUM. BTTS
lives on the low-score dependence.** Karlis & Ntzoufras (2009) model the
difference via the **Skellam distribution** — the sum is *integrated out and
discarded*. A difference-shaped feature set is, by construction, silent about
totals. That is not an opinion about our code; it is the published structure.

Our vector today has an explicit `elo_diff` and **no sum feature at all**.

---

## Rule 0 — SPLIT DOWNWARD, not sideways

The strongest counter-evidence in the review, and it governs everything below.
In the 2023 Soccer Prediction Challenge (~300k training matches, 51 leagues), a
**40-feature engineered set** — selected from 205 candidates by filter+wrapper,
i.e. exactly how a 52-feature vector comes to exist — scored **0.2416 RPS**
against **0.2085 for a two-number rating**, same model class. Nothing beat the
bookmaker consensus (0.2063).

**So the goal is not five feature sets of 52. It is five sets of 8-15.** If a
proposed feature cannot be justified per-head, it is cut, not moved.

---

## The proposed sets

### Shared core — the same for every head (~6)

Small on purpose. Everything here is ≥85% populated today.

| feature | why shared |
|---|---|
| `elo_home`, `elo_away` | the raw strengths; each head combines them its own way |
| `rest_days_home`, `rest_days_away` | fatigue affects goals and results alike |
| `league_tier`, `season_progress` | context, and the only two at 100% fill |

**Deliberately NOT in the core:** `elo_diff`. It is a 1x2 feature that happens to
live in the shared vector, and that is the defect in miniature.

### 1x2 head — DIFFERENCE-shaped (~8)

| feature | status |
|---|---|
| `elo_diff` | exists |
| `form_ppg_diff` | **new** — we store home/away separately and never difference them |
| `starter_rating_diff` | **new** — from lineups ([[#086]]), joins startXI to ratings |
| `attackers_diff` | **new** — F-count in startXI, home − away |
| `h2h_win_pct` | exists — **move here**, it is a win-rate and belongs nowhere else |
| `referee_home_win_pct` | exists — **move here** |
| `league_draw_rate_ytd` | exists — **move here** (draw structure is 1x2-only) |
| home advantage | implicit in Elo; consider explicit |

**Time decay: 30-90 day half-life** (Wheatcroft & Sienkiewicz).

### O/U head — SUM-shaped (~10)

This is where the real change is. **Every term is a sum or a rate, never a
difference.**

| feature | status |
|---|---|
| `expected_total_goals` = λ_home + λ_away | **new — THE headline feature we have never had** |
| `elo_sum` | **new** |
| `attack_sum` = attack_home + attack_away | **new** — needs attack/defence strengths, not one Elo |
| `defence_sum` | **new** |
| `shots_for_sum`, `corners_sum` | **new** — ⚠️ coverage 54,374, see the constraint below |
| `shots_off_target_sum` | **new** — the single most *predictable* statistic (MAE 1.86 vs 3.77 baseline) |
| `attackers_total` = F_home + F_away | **new** — from lineups |
| `league_avg_goals` | computed today as a signal, read by nothing ([[#085]]) |
| `ht_goals_rate_sum`, `h2_goals_rate_sum` | **new** — from [[#084]], 98.3% coverage |

**Time decay: ~300 day half-life** — totals want far longer memory than 1x2
(Wheatcroft & Sienkiewicz, 53,447 O/U forecasts). We share one decay today.

**DROP from this head:** `pinnacle_implied_home/draw/away`,
`opening_implied_home/draw/away`, `h2h_win_pct`, `referee_home_win_pct`,
`league_draw_rate_ytd` — nine 1x2-specific inputs currently fed to it.

⚠️ **AND FEED IT SHOTS, NOT GOALS.** Wheatcroft (54,437 matches, 10 leagues,
Bonferroni-corrected p<0.0001): GAP ratings fed shots+corners returned **+535
units**; the same ratings fed **goals returned −631, negative in 10 of 10
leagues**. The mechanism is measured separately: team goal rates are essentially
unpredictable beyond the league mean (MAE 1.01 vs a 1.02 baseline; away goals
0.87 vs 0.85, i.e. *worse than the mean*), while shots off target is 1.86 vs 3.77.
**Our `goals_for_avg_*` features are precisely the input the literature says is
worst.**

### BTTS head — the dependence market (~7)

The one head where the low-score correlation genuinely matters. Dixon-Coles's ρ
moves (0,0), (1,0), (0,1), (1,1); for O/U 2.5 all four are Under so ρ does
**nothing**, but for BTTS they **split 3:1** (0-0/1-0/0-1 = No, 1-1 = Yes).

| feature | status |
|---|---|
| `min_lambda` = min(λ_home, λ_away) | **new** — BTTS is governed by the WEAKER attack |
| `clean_sheet_pct_home/away` | **exists in `team_season_stats` at 96.87% fill, read by nothing** |
| `failed_to_score_pct_home/away` | same |
| low-score dependence term (ρ or copula) | **new** |
| `market_implied_btts_yes` | exists |

### Draw — NOT a head, and not an edge target

Foulley (2021) decomposes Brier by outcome class: draw skill **1.4% for the
model** and **3.0% for the bookmaker**, c-statistic **0.62 for both**. The market
has no draw discrimination either. No replicated draw-specific feature exists in
the literature. Classifiers essentially never pick draws (Dixon-Coles argmax: 0
of 354). **Price draws off the 1x2 distribution and do not chase them.**

---

## Two design decisions that need making before any of this is built

### 1. Do the heads see MARKET PRICES at all?

Our vector currently carries `pinnacle_implied_over25/under25`,
`opening_implied_*` and `bookmaker_disagreement`. The literature is unambiguous
that **market odds are the strongest single input** — adding them improved every
combination Wheatcroft tested (best without odds **+0.80%, not significant**;
with odds **+1.85%, significant**).

⚠️ **But our α harness measures the blend weight of model against de-vigged
Pinnacle.** A model that has Pinnacle's price as a feature is partly
re-predicting the market, and α stops meaning what the pre-registration says it
means. `residual_test_ou`'s own docstring says *"no market shrinkage anywhere —
blending against Pinnacle before testing against Pinnacle is the trap the 1x2
pre-registration already caught"*, yet market-implied features sit in the vector.

**This tension must be resolved explicitly, not inherited.** The defensible split
is two variants per head — *with* market features for production pricing, and
*without* for the α measurement — and never to quote an α from the first.

### 2. Coverage gates the O/U design

| input | coverage | consequence |
|---|---|---|
| final scores | **175,452** | λ from scores is available everywhere |
| half-time scores | **172,439** | the 1H/2H terms are nearly free |
| goal minutes | 128,827 | game-state terms at 73% |
| **shots + corners** | **54,374** | ⚠️ **the literature's best input is our thinnest** |
| lineups | 25,091 and climbing | starter terms are late-window only |

**The single most important design constraint:** the feature the literature most
supports (shots, not goals) is available on **31% of our matches**, and only for
leagues AF covers. A shots-fed O/U model is a *narrower* model, not a broader one.
Decide deliberately whether the O/U head is (a) a wide goals-fed model, (b) a
narrow shots-fed model on 54k matches, or (c) both with a coverage switch — and
say which before measuring, not after.

---

## How this gets judged

Unchanged, and non-negotiable: `residual_test.py` / `residual_test_ou.py`, the
same pre-registered bar — blend log-loss < market log-loss **AND α > 0.02** on
the realistic arm. **Promote on α, never ROI** (§8).

**Expectation, stated before the work:** the review is blunt that α = 0 against a
de-vigged sharp line is the *normal published result*, that nothing in the 2023
Challenge beat bookmaker consensus, and that every profitable published result
requires best-of-books execution — which we do not have. **This design is worth
building because it closes the last structural question, not because it is likely
to find alpha.** If a correctly-shaped, correctly-sized, shots-fed O/U model also
returns α = 0, that is the strongest negative result this project will ever have.

---

## Scope gap — this design covers the markets we MODEL, not the ones we COLLECT

Flagged by the owner 2026-09-23: *"with all those new data we should try to check
into other markets as well, or did you research only 1x2 and ou market features?"*

**Correct — the literature reviews were scoped to 1x2, totals, BTTS, draws and
correct score.** They did not cover Asian handicap, corners, cards, team totals
or the 1H markets. That was my framing, and it was too narrow.

**And the gap matters, because we already hold SHARP prices on all of them.**
Pinnacle-priced markets, last 90 days, matches covered:

| market | matches | note |
|---|---|---|
| **asian_handicap** | **18,486** | **more coverage than 1x2** — and no model |
| 1x2 | 18,304 | modelled |
| over_under_25 | 17,029 | modelled |
| over_under_35 / _15 / _45 | 13,517 / 9,119 / 5,327 | only 2.5 modelled |
| **team_total_1h_home/away_05** | 6,863 / 6,607 | no model |
| **team_total_home/away_15** | 6,287 / 5,384 | no model |
| **1x2_1h** | 3,439 | no model |
| **corners_1h_ou_45, corners_ou_95/100** | ~2,700-3,000 each | no model |

A Pinnacle price is what makes a market **measurable** — it is the anchor every
α test and every CLV number in this project uses. So these are not speculative
markets; they are markets where we could run exactly the same pre-registered test
we run for 1x2 and O/U, today.

### What the per-market principle says about each

* **Asian handicap** is a DIFFERENCE market — it is the 1x2 head's structure with
  a continuous line instead of three buckets. It should share the 1x2 feature set
  almost exactly, and Hegarty & Whelan (2024, *IJF*) find the **AH market is
  efficient and shows no favourite-longshot bias while 1x2 does** (average loss
  3.6% vs 7.8%). Cheaper to lose in, harder to beat.
* **Team totals** are a *marginal* market — λ_home alone, not the sum and not the
  difference. **A third shape**, and the one our current architecture is least
  able to express. We already fit `home_goals` and `away_goals` heads, so this is
  the closest thing to a free market.
* **Corners and cards** need their own rate model entirely. We store corners at
  96.3% fill and have never modelled them; cards come from `match_events` at
  80.8%. Note the reviews found NO published feature-importance work for either.
* **1H markets** fall out of [[#084]] by construction — a half-time layer prices
  `1x2_1h` and `team_total_1h_*` with no extra modelling.

### The honest caveat before anyone gets excited

More markets is more *surface*, not more edge. Four α = 0 results and a
literature that says α = 0 is normal apply here too — and **adding markets
multiplies the multiple-comparison problem**. Testing eight markets and reporting
the best one is exactly the trap `ANALYSIS_GOTCHAS §47` and the permutation
designs in [[#073]] exist to prevent. **Any multi-market sweep must carry a
family-wise correction from the start**, not as an afterthought.
