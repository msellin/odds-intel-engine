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

* **Asian handicap** is a DIFFERENCE market — but **only the 0 and ±0.5 lines
  are pure difference, and those add nothing over 1x2** (−0.5 IS P(home win); 0
  is P(H)/(P(H)+P(A))). Lines of ±1 and wider depend on the TOTAL too, because
  under Skellam the goal difference has mean λh−λa and **variance λh+λa**.
  *[CORRECTED 2026-09-23 — this line previously said AH "should share the 1x2
  feature set almost exactly", which is wrong for every line we would actually
  price beyond ±0.5.]* Efficiency, with the citation fixed (the earlier line
  conflated two papers): Hegarty & Whelan, *IJF* 41(2) 2025, 84,230 matches,
  **average-of-books** closing odds — AH loses **3.6%** vs 1x2 **7.8%**, no
  favourite-longshot bias; and the same authors, *Rev. Behavioral Finance*
  16(5) 2024, on **Pinnacle** — AH is NOT uniform across lines: whole-goal lines
  ~2.97%, **half-goal lines ~4.8%**. We can bet only full and half lines, so the
  line type we can use is the most expensive one.
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

---

## Pre-registration — O/U arms (2026-09-23, written BEFORE the first run)

Owner's framing, which sets the bar: *"in the end we want working models, not
hundreds of bad picks"* — the consensus arm fires many picks and is not
profitable, and model bets must not repeat that.

**Script:** `scripts/ab_ou_feature_arms.py`. Cutoff 2026-08-20 (train strictly
before, test on/after). Market O/U 2.5 only.

### The family — fixed at six, so the correction cannot get easier later

| arm | inputs | question it answers |
|---|---|---|
| **A** | shipped 52 (incl. market prices) | control |
| **A0** | A minus every market-derived input | does the 52 know anything without the market's help? |
| **C** | 11 SUM-shaped, goals-fed, no market | does the right SHAPE and SIZE help? |
| **CM** | C + Pinnacle 1x2 and O/U implied probs | owner's question: prices in the model — better, or a copy of the market? |
| **D** | shots-fed sum-shaped | waits for the [[#078]] columns backfill |
| **E** | C with D's shots terms where they exist | same gate as D |

Arm C: `exp_total` (λh+λa, recovered exactly from the stored walk-forward #084
ratings), `ht_share_expected`, `lam_min`, `lam_max`, `elo_sum`, `elo_absdiff`,
`league_goals_wf`, `league_over25_wf` (decayed, 300-day half-life, same-day
matches never see each other), `rest_days_home/away`, `league_tier`.

### (a) Is the model correct — PASS bar (unchanged from every earlier test)

On the **REALISTIC** harness arm: α > 0.02 **AND** blend log-loss < market
log-loss **AND** Holm-adjusted p < 0.05 over **m = 6**, where p is one-sided on
the per-fixture log-loss improvement of the blend over the market in the held-out
half. The optimistic arm is printed and does not decide (the lesson of [[#084]],
where the two arms disagreed about the sign).

**Reading A/CM:** an arm handed Pinnacle's price can post α > 0 partly by
re-predicting the market. So "prices in or out" is decided by the PAIRS —
A vs A0, CM vs C — on model-alone log-loss and on (b), never by one α alone.

### (b) Does it make money — the backtest, reported for every arm

Held-out half only; decision at **T-2h** (last price ≥120 min before kickoff);
executable price **Coolbet only** (§55); judged on **CLV vs de-vigged Pinnacle
close** (§8), with ROI and **bets/day** beside it. Floors 3 / 5 / 8%. Three
strategies: **BLEND** (what we would ship), **RAW** (model alone — today's
`bot_v10_ou` shape), **MARKET** (α = 0 — the sharp-anchor baseline). A model
strategy only counts if it beats MARKET on CLV at the same floor.

⚠️ A/CM's price features come from `match_signals` rows of unrecorded capture
time, possibly later than T-2h — their backtest rows may be optimistic.

### What happens next depends on the result

* **An arm passes (a) and beats MARKET on (b):** a selectivity study (CLV by
  disagreement band, expected bets/day stated up front), then a PAPER bot. No
  real money before a paper window.
* **Nothing passes:** no model bot. That is the answer, not a reason to loosen
  the bar.

### Expected result, stated before running

α = 0 on A, A0 and C — the normal published result against a sharp line and
the fifth-time result here. CM's α may be > 0 but should NOT beat A0/C on
backtest CLV at T-2h if the market-price features are doing the work. RAW
strategies should show NEGATIVE CLV at every floor (they are today's losing
bot shape); MARKET at T-2h may show small positive CLV — that is the
sharp-anchor edge, not a model edge.

---

## Research landed 2026-09-23 — AH, team totals, 1H, corners, cards

Scratch reports from three literature agents, merged here. [V] = primary text
read, [A] = abstract only, [D] = derivation.

| market | shape | proposed size | beatability evidence | expected α |
|---|---|---|---|---|
| **AH** | difference; ±1+ lines also need the total [D] | ~8: `elo_diff`, explicit home adv, `exp_total` (wide lines), split home/away strengths from `league_standings` (99.77%, read by nothing), shots diff, rest diff, line type | the best-documented EFFICIENT football market [V] | 0 |
| **team totals** | MARGINAL λ of one team; Dixon-Coles ρ **cancels out of both marginals exactly** [D], so independent Poisson is structurally right | ~7: own attack × opp defence (Maher), shots for/opp against, venue split, `failed_to_score_pct` + opp `clean_sheet_pct` (96.87%, unused), `league_avg_goals`, league dispersion | **no literature**; and team goal rates barely beat the league mean (away 0.87 vs 0.85 — worse) | ~0 |
| **1H markets** | same three shapes on λ_1H ≈ s·λ_FT; s ≈ 0.44 [D from Maia 2023 V] — **measure s on our 172k HT scores** | ~6, a thin layer on the FT heads: FT λs, team + league 1H share, dependence term (0-0 dominates 1x2_1h), red-card/game-state priors | **no literature** pre-match; Pinnacle 1x2_1h only 3,439 matches → underpowered | unknown, low power |
| **corners** | overdispersed (var/mean 1.186 vs goals 1.040, Yip et al. JORS 2024 [V]); total = SUM, team = marginal, handicap = difference | ~8: team corners for/against (4), Pinnacle-implied expected goals (+16.7% corners per expected goal), implied supremacy (drives the SPLIT, not the total), league corner mean + dispersion | small literature; the "profitable" results are one soft-book **under** bias (Betfair EPL under 10.5, p=0.001) that failed on transfer to the Bundesliga; best CV R² 0.017. **Our own corners bot: −4.86% CLV, t=−12.9, n=467** | ~0 |
| **cards** | team yellows are UNDER-dispersed (var/mean 0.79-0.97, Philipson *JRSS A* 2026, 7,203 matches [V]) and home/away POSITIVELY correlated (τ 0.08-0.18) — so NB and bivariate-Poisson are the wrong defaults; two marginals + copula, total derived. 65.8% of yellows in 2H | ~9: league-season base rate, referee multiplier shrunk to league (0.81-1.23×), team received rates (shrunk), de-vigged 1x2 closeness q(1−q), home flag (0.88×), derby, match stakes, team fouls (33% — secondary) | the closest published analogue (Hargreaves & Powell 2022, with Sky Bet) found **no out-of-sample skill** (0.4920 vs 0.4998 constant, n.s.); **no literature** on card markets vs a closing line | ~0 |

**Cards — our own data has three defects to fix before any card head:**
`referee_cards_avg` is built from `match_stats` (~33% coverage) and filled on
7.4% of matches — rebuild from `match_events` (81%) with shrinkage; AF events
may include coach/bench cards that books do NOT count (label inflation,
unverified); and a late-filled referee name would leak in backtest (check fill
vs hours-before-kickoff). Book settlement rules differ (Pinnacle yellow=1,
red=2; bet365 booking points 10/25) — the label must follow the book we bet.

**Two things this changes:**

1. **Half-life is not settled.** Dixon & Coles (1997) optimised ξ on match
   OUTCOMES and got ~373 days [A] — which contradicts the 30-90 days this doc
   gives the 1x2 head. Totals at ~300 days (W&S) is not in conflict. Treat the
   1x2 half-life as a parameter to sweep, not a fact.
2. **The corner "edge" is a soft-book pricing bias, not model alpha** — if it
   is real it belongs to 🤖 OWN placement (Coolbet/Epicbet unders vs Pinnacle
   close) as its own pre-registered check, not to a corners model head.

Referee, weather, corner time-decay and 1H efficiency: **no literature found** —
recorded as the answer, per the CLAUDE.md rule.

---

## RESULT — O/U arms A / A0 / C / CM (2026-09-23, first run)

n = 9,127 held-out fixtures; CHECK R passed (in-process scorer reproduces
`residual_test_ou.py` on arm A). Mean-fill scoring decides; A with the harness's
zero-fill shown for scale.

| arm | α (REALISTIC) | model LL | model AUC | blend LL vs market 0.6737 | p | Holm p (m=6) | verdict |
|---|---|---|---|---|---|---|---|
| A (52, with prices) | 0.140 | 0.6764 | 0.5952 | 0.6735 | 0.233 | 1.000 | FAIL |
| **A0 (52, no prices)** | **0.000** | **0.6901** | **0.5104** | 0.6737 | — | 1.000 | FAIL |
| **C (11, sum-shaped, no prices)** | 0.140 | **0.6809** | **0.5787** | 0.6736 | 0.472 | 1.000 | FAIL |
| CM (C + prices) | 0.315 | 0.6760 | 0.5966 | 0.6736 | 0.456 | 1.000 | FAIL |

**(a) No arm passes.** Three post α > 0.02, but the blend improves log-loss by
~0.0001 and no p comes near 0.05 even before correction. Sixth α-test, same
answer: nothing beats Pinnacle.

**The prices question, answered by the pairs:**
* **A vs A0: the 52's apparent skill WAS the market price.** Take the prices out
  and the 52-feature model is near coin-flip (AUC 0.510, LL 0.6901).
* **C vs A0: shape and size matter a lot.** Eleven sum-shaped features without
  prices (AUC 0.579, LL 0.6809) are far better than 52 features without prices
  (0.510, 0.6901). That is the #089 hypothesis confirmed — for model quality,
  not for edge.
* **CM vs C: adding prices makes the model look better (LL 0.6809 → 0.6760) by
  moving it toward the market, but never past it (0.6737).** Prices in the model
  improve its resemblance to the market, not its edge. The rule "no prices in
  the model we test" stands.

**(b) Money — T-2h, Coolbet price, CLV vs de-vigged Pinnacle close, 2,994 fixtures:**

| strategy | bets/day | CLV | t | reading |
|---|---|---|---|---|
| **RAW model, any arm** | 13–108 | **−4.2% to −7.2%** | −8 to −63 | the "hundreds of bad picks" mode — decisively negative, worst for the market-free arms (A0 RAW: 108/day at −7.2%) |
| MARKET baseline (α=0, sharp anchor) | 2–8 | −0.5% / +0.5% / **+3.2%** at 3/5/8% | ≤1.5 | the only thing close to positive; not yet significant |
| BLEND, A / C / CM | 2–8 | within ~±1.2pp of MARKET | ≤2.3 | indistinguishable from the baseline; CM 8% +4.4% t=2.3 is one cell of ~36 and carries the capture-time caveat |

ROI is negative almost everywhere at these n (~40-150 bets per cell) and is not
evidence either way (§8).

**What this means, and what it does NOT:**
* A raw model bot on O/U — any feature set — loses ~4-7% CLV per bet. **Never
  ship a model-alone O/U bot.** This is the mechanism behind today's consensus-
  style volume: without the market anchor, the model disagrees with Pinnacle
  constantly and is wrong when it does.
* The model adds nothing measurable on top of the market. The only candidate
  edge in this data is the **sharp-anchor 8% floor at T-2h** (+3.2%, 2.3
  bets/day, t=1.5) — which is #024's territory, not a model's.
* **Not closed:** arm D/E (shots-fed) after the #078 backfill. C's big jump over
  A0 says shape matters; shots are the one input the literature ranks above
  goals. Expected result stays α = 0.

---

## Pre-registration — O/U arms D/E + before/after (2026-09-23, written BEFORE the run)

Owner: *"can we also measure the effect of the 089? test before and after?"* The #078 columns
backfill finished 2026-09-23 (53,309 matches: shots in/outside the box, off target, …), so the
shots-fed arms can now be built on complete data.

**Before/after is ONE run, not two.** The backfill changed the data under the first run, so its
numbers are not a valid "before". All six arms are trained in the same minute on the same rows:
BEFORE = C (sum-shaped, goals-fed) with A / A0 / CM as controls; AFTER = D and E.

* **D** (~10, shots-fed): walk-forward opponent-adjusted expected TOTALS — shots, shots on target,
  shots off target, shots inside the box, corners — each λ_home + λ_away from `HalfRatings`
  (the #084 construction: IPF-seeded on the oldest 180 days, then predict-then-update in date
  order, same-day matches never see each other), plus `league_goals_wf`, `league_over25_wf`,
  rest days × 2, `league_tier`.
* **E**: C + D's five shot totals (16).

**Two universes, stated now:** (1) SHOTS-COVERED fixtures (the rating exists) — all six arms,
and it is where D and E are DECIDED; (2) the full universe — A, A0, C, CM, E (D is not defined
there). Same harness and CHECK R as the first run. **Bar unchanged:** REALISTIC α > 0.02 AND blend
< market AND Holm p < 0.05, **m = 6** (family fixed at the first run). **Also reported (handover):
ECE** (10-bin calibration error of the Platt-calibrated model on the held-out half) beside
log-loss and α; not part of the bar.

**Expected:** D and E α = 0 (sixth O/U zero), but D's model-alone log-loss and AUC BETTER than C's
on the covered subset — shots beat goals as an input (Wheatcroft) — without beating the market.
RAW strategies still negative CLV.

## RESULT — arms D/E + before/after (2026-09-23): all FAIL, and shots did NOT beat goals

One run, six arms trained on the same rows. CHECK R passed. Universe 9,163 fixtures; shots-covered
subset 2,951 (32%). **Verdict (Holm m=6): all six FAIL** — the sixth O/U α-test in a row with
nothing beyond the market. C/E reach α 0.13-0.19 on the full universe but the blend does not beat
the market (p ≈ 0.6-1.0).

**Before → after on the SAME 2,951 fixtures, model alone (REALISTIC):**

| arm | LL | AUC | ECE |
|---|---|---|---|
| C — goals-fed (before) | **0.6742** | **0.5943** | 0.0233 |
| D — shots-fed (after) | 0.6767 | 0.5834 | 0.0228 |
| E — C + shots | 0.6736 | 0.5937 | 0.0219 |
| CM — C + prices | 0.6672 | 0.6128 | 0.0208 |
| market | 0.6671 | 0.6134 | — |

**Shots did not help — against both the literature (Wheatcroft) and the pre-stated expectation.**
D is worse than C; E ≈ C. Three honest caveats: (1) the shots rating is our generic
`HalfRatings` IPF + online update, not Wheatcroft's exact GAP construction; (2) the covered test
set is small (2,951); (3) CORRECTED 2026-09-23 by the method review: Wheatcroft PROFITED only at MAXIMUM odds across many books
(1.57% margin) and LOST at average odds (6.8%) with every input; we judge against de-vigged Pinnacle, harder still. **CM (prices in) ≈ the market itself**, confirming again that prices
make the model a copy of the market, not better than it.

**Money (covered subset, T-2h, Coolbet):** every RAW strategy −3.7% to −6.4% CLV at 6-42 bets/day;
every BLEND ≈ MARKET (−2.9% to +1.9%, n.s.). No arm beats the MARKET baseline.

**What this closes:** O/U prediction by feature SHAPE, SIZE and INPUT (goals vs shots) is
exhausted for this model class. The remaining model route is a different OBJECTIVE — [[#090]]
(decorrelation from the market, Benter logit blend, judged on clv_sharp) — and its small
per-market feature sets draw on the parked candidates in [[#080]] / [[#086]].

### Was D a fair test of the published shots method? No — method review, 2026-09-23

A review of Wheatcroft (2020, IJF 36(3), read in full) against our implementation found D was NOT a
replication, so "shots did not beat goals" is inconclusive rather than negative:
1. **Not a like-for-like swap** — C carries Elo and goals-based strength; D has no strength input but shots.
   Wheatcroft changed ONLY the input.
2. **Underpowered** — the model-alone comparison is on the held-out half, ~1,476 fixtures; the published
   effect (~0.0008 nats) needs ~7-8k.
3. **Wrong part of the season** — the 2026-08-20 cutoff puts the whole test in weeks 1-5, exactly the
   window Wheatcroft EXCLUDES (no bets until both teams have played 6 games; promoted teams inherit ratings).
4. **Untuned learning rate** — his shots λ ≈ 0.44 vs goals ≈ 0.08-0.12; ours 0.06 for everything.
5. **Mostly imputed training values** — most training rows have no match stats.
6. **Different model** — he regresses ONE summed rating plus the market price in a logistic regression;
   we feed five overlapping totals to XGBoost with no market.
His own result: +0.8%/bet only at max odds, flat-to-negative from 2014-17 ("the market started pricing
shots"). Expected against a Pinnacle close: ~nothing. A faithful replication spec is in the review
(scratchpad `research_shots_method.md`) — run it on football-data.co.uk history for his 10 leagues first,
which separates "our version was unfaithful" from "the effect no longer exists".

## Pre-registration — HARNESS-REPAIRED re-run (2026-09-23, BEFORE the run)

From the internal review: (1) arms now train on EVERY country (`exclude_tier_c_countries=False` —
27% of the test set came from countries no arm trained on); (2) the backtest close is STRICTLY
pre-kickoff (11% of closes were in-play); (3) a decision is a Coolbet quote ≥120 min out with
Pinnacle's price no more than 30 min older (it was a median ~11 h older — only 407 of 4,389
fixtures in a two-week sample qualify, so the money section will be small and honest).
Everything else — arms, bar (Holm m=6), CHECK R — unchanged. Run at the original cutoff
(2026-08-20) and at 2026-05-01 (~2.5× the test set; test spans season end + start, so the
weeks-1-5 problem is diluted, not removed). **Expected: all verdicts unchanged (FAIL); the
country fix may narrow the model-vs-market gap slightly.** The faithful Wheatcroft replication is
a separate step.
