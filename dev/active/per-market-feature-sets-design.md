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
⚠️ *Corrected 2026-09-24: that 300 days is the decay of their shot-CONVERSION model with odds as a
regressor (90 days without odds), not a memory for shot-volume or goal ratings. In the faithful GAP
replication the memory is the learning rate λ, tuned per input (shots+corners λ≈0.7; goals λ→0.01
after 2014 — i.e. switched off).*

**DROP from this head:** `pinnacle_implied_home/draw/away`,
`opening_implied_home/draw/away`, `h2h_win_pct`, `referee_home_win_pct`,
`league_draw_rate_ytd` — nine 1x2-specific inputs currently fed to it.

⚠️ **AND FEED IT SHOTS, NOT GOALS.** Wheatcroft (54,437 matches, 10 leagues,
Bonferroni-corrected p<0.0001): GAP ratings fed shots+corners returned **+535
units**; the same ratings fed **goals returned −631, negative in 10 of 10
leagues**. The mechanism is measured separately: team goal rates are essentially
unpredictable beyond the league mean (MAE 1.01 vs a 1.02 baseline; away goals
0.87 vs 0.85, i.e. *worse than the mean*), while shots off target is 1.86 vs 3.77
(these MAE figures are from Wheatcroft, arXiv:2001.09097, not 2101.02104 — corrected 2026-09-24).
**Our `goals_for_avg_*` features are precisely the input the literature says is
worst.**

### BTTS head — the dependence market (~7)

The one head where the low-score correlation genuinely matters. Dixon-Coles's ρ
moves (0,0), (1,0), (0,1), (1,1); for O/U 2.5 all four are Under so ρ does
**nothing**, but for BTTS they **split 3:1** (0-0/1-0/0-1 = No, 1-1 = Yes).

| feature | status |
|---|---|
| `min_lambda` = min(λ_home, λ_away) | **new** — BTTS is governed by the WEAKER attack |
| `clean_sheet_pct_home/away` | ~~exists in `team_season_stats` at 96.87% fill~~ **⚠️ LEAKS — corrected 2026-09-24:** 96.87% is ROW fill of season-aggregate snapshots fetched mostly in May 2026; only **7.6%** of matches have a snapshot taken before kickoff. **Derive walk-forward from `matches` scores instead.** |
| `failed_to_score_pct_home/away` | same leak — derive walk-forward from `matches` scores |
| low-score dependence term (ρ or copula) | **new** |
| `market_implied_btts_yes` | exists |

> **BTTS verdict 2026-09-24 ([[#119]] research, `dev/active/sharp-anchor-v2-btts-ah-research/`):** do not build this head. A Dixon-Coles price derived from Pinnacle 1x2 + O/U 2.5 (ρ≈−0.10) tracks the soft consensus at r=0.987 and adds +0.00006 nats (t=1.0) to it, so a market-free BTTS model has nothing left to find — expected α = 0. The AH model route was closed the same day on the same basis.

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
| **team totals** | MARGINAL λ of one team; Dixon-Coles ρ **cancels out of both marginals exactly** [D], so independent Poisson is structurally right | ~7: own attack × opp defence (Maher), shots for/opp against, venue split, `failed_to_score_pct` + opp `clean_sheet_pct` (derive walk-forward from `matches`; the `team_season_stats` copy leaks — see BTTS head), `league_avg_goals`, league dispersion | **no literature**; and team goal rates barely beat the league mean (away 0.87 vs 0.85 — worse) | ~0 |
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

## RESULT — harness-repaired re-runs (2026-09-23): verdict unchanged; with more data, shots DO help a little

Both runs: CHECK R passed; all six arms FAIL (Holm m=6).

| window (cutoff) | test n | covered n | C goals LL | D shots LL | E goals+shots LL | market LL |
|---|---|---|---|---|---|---|
| 2026-08-20 | 9,169 | 2,952 | 0.6746 | 0.6752 | 0.6737 | **0.6673** |
| 2026-05-01 | 23,309 | 6,798 | 0.6776 | **0.6761** | **0.6734** | **0.6684** |

* **On the 2.3× larger window, shots beat goals** (D < C, better calibrated: ECE 0.017 vs 0.025) and
  goals+shots is best — the direction the literature predicts, which the small August window could
  not resolve. Still ~0.005 nats behind the market.
* Closest to a blend gain: E on the full May universe, p = 0.071 raw (Holm → far from 0.05);
  A (52 + prices, now trained on all countries) on the August covered subset, p = 0.111.
* **Honest money test is tiny:** requiring Pinnacle within 30 min of the Coolbet quote leaves ~150
  priced fixtures. RAW −4.2% to −6.5% CLV; BLEND / MARKET fire 0-10 times — no signal either way.
  Fresher Pinnacle O/U polling (#090 b) is the prerequisite for any O/U money test.

Remaining #089 step: the faithful Wheatcroft replication on football-data.co.uk history. ✅ Done 2026-09-24 — see its result section below.

## Pre-registration — #090 (a) PRICE-MOVE SIGNAL (2026-09-23, BEFORE building)

Found by the internal review: the residual of our walk-forward goals rating against Pinnacle's EARLY
de-vigged O/U 2.5 price correlates +0.12 with Pinnacle's subsequent move (n=8,085, stable in both
halves and every tier). Not a standalone bet (the close absorbs it; at Coolbet's 8% margin it cannot
win alone) — the question is whether it can TIME or FILTER picks.

* signal r = logit(P_rating(over)) − logit(p_early), P_rating = Poisson P(total > 2.5) with
  λ = ht_expected_total + h2_expected_total (the stored #084 walk-forward ratings); p_early = Shin
  de-vig of Pinnacle's FIRST complete O/U 2.5 pair; move = logit(p_close) − logit(p_early), close =
  latest complete pair strictly before kickoff, and only fixtures where the close is a LATER
  snapshot than the early one.
* **Test 1 (does it predict the move, out of sample):** date-ordered split; OLS move ~ r fitted on the
  first half, scored on the second; PASS = the held-out slope > 0 with one-sided p < 0.01, and the
  held-out top-quintile move toward the signal > +0.5pp in probability.
* **Test 2 (does it improve picks):** on every scored O/U 2.5 leg (`clv_sharp_legs`, one row per bet),
  split by whether r AGREES with the pick's side; PASS = mean clv_sharp(agree) − clv_sharp(disagree)
  > 0 with one-sided p < 0.05 (Welch), reported per ledger.
* **Expected:** Test 1 PASSES (+0.12 measured); Test 2 shows a modest positive gap, not enough to turn
  a losing ledger positive on its own.

### Result — #090 (a) (2026-09-23, `scripts/ou_price_move_signal.py`, committed before the first run)

17,801 finished fixtures with an early AND a later complete Pinnacle O/U 2.5 pair (median 28 h
between the early pair and kickoff).

| | train half | held-out half |
|---|---|---|
| corr(r, move) | +0.073 | **+0.119** |
| slope of move on r | +0.0188 | **+0.0239 ± 0.0021, t = +11.3** |

* **Test 1: PASS, with a caveat on size.** The slope passes easily. The top quintile moved
  **+0.75pp** toward overs and the bottom quintile −0.02pp, so the pre-registered bar (> +0.5pp) is
  met — **but half of that is drift**: the whole held-out half moved +0.34pp toward overs regardless of
  the signal. The drift-free size is **±0.39pp per side**. Negative out-of-sample R² on the raw fit
  is the same drift (the intercept); with the train slope and a demeaned intercept, OOS R² = +1.35%.
* **Post-hoc control (not part of the bar): the rating is doing the work, not mean-reversion.** r
  contains −logit(p_early), so a noisy early price that reverts would look like signal with any
  rating. Replacing the rating with a walk-forward league mean of early prices gives **t = +0.9**
  against the rating's +11.3. In a joint regression the rating keeps t = +11.3 alongside the early
  price and the league mean. The same holds for early prices ≥ 6 h out (t = +11.2).
* **Test 2: PASS overall, not on the published ledger.** Mean clv_sharp of scored O/U 2.5 legs:

  | ledger | agree | disagree | gap |
  |---|---|---|---|
  | ALL | −6.14% (n=1,455) | −8.18% (n=1,450) | **+2.04pp, t = +4.6** |
  | shadow_bets | −6.95% | −9.29% | +2.33pp, t = +4.2 |
  | simulated_bets | −4.01% | −5.11% | +1.10pp, t = +1.5 |
  | picks_forward_test | −2.29% | −2.68% | +0.39pp, t = +0.7 (NS) |

**Independent review (2026-09-23) — Test 1 confirmed, Test 2 CORRECTED DOWN.**
* No leakage: the rating walk is predict-then-update in date order. In 21.5% of fixtures the team
  played between the early price and kickoff (the rating knows a result the early price did not), but
  that is only 71 held-out fixtures, and without them t is unchanged (+11.3).
* Test 1 survives clustering (league-day t = +10.4, league t = +8.9), per-day drift removal
  (t = +11.4) and early ≥ 6 h (±0.43pp).
* **Test 2 is inflated by side mix.** Agreeing legs are mostly overs, which lose less. Within OVERS
  the gap is **0.00pp** (−3.76% vs −3.76%); within unders +1.62pp. Controlling for side, odds and bot,
  the agree effect is **+0.88pp (t = 3.4)**, not +2.04pp. The pool also held one in-play bot
  (`bot_inplay_slowstate_v1`, 269 legs at −36%). Test 2 is not independent of Test 1 either: legs that
  agree gain because of the same move Test 1 measures.

**Reading.** As expected. The goals rating knows something Pinnacle's opening O/U price does
not yet reflect and its close does — which is also why every α test against the CLOSE returned 0.
But the move it predicts is about 0.4pp of probability, and legs that agree with it still lose ~6%
against the close. A filter would cut volume in half and improve clv_sharp by ~0.9pp (controlled; zero for overs),
and would not produce a positive stream. **Not wired into any gate.** What it does establish: an O/U edge from
this rating can only exist at EARLY prices, before Pinnacle moves — so any future O/U money test
must be run at the opening price, not at T-2h.

## Pre-registration — #089 FAITHFUL WHEATCROFT REPLICATION (2026-09-24, BEFORE building)

Question: was our shots-vs-goals null (#089 arm D, #077) because our construction was unfaithful, or
because the effect does not exist against a sharp price? Method spec (from reading Wheatcroft 2020,
*IJF* 36(3), in full): four additive GAP ratings per team (home/away × attack/defence), eqs (1)-(2),
floored at 0, no decay; forecast `logit p = α + β1·(H_i^a + H_i^d + A_j^a + A_j^d) + β2·m`, where m is
the market term, so the market is ALWAYS in the model.

**Data.** football-data.co.uk, his 10 leagues (E0 E1 E2 E3 EC SC0 SP1 I1 F1 D1), 2005/06-2025/26,
already on disk (`data/raw/football_data_co_uk/main`, gitignored). EC has no shots from 2016/17 on,
so it drops out of the Pinnacle-era cells.

**Inputs (S).** Shots + corners per team (his best), and goals as the control, with identical code.

**Faithful to the paper:**
* ratings keyed by (league, team). At season start a team new to a league inherits the mean last
  rating of the teams that left that league.
* (λ, φ1, φ2) chosen by bounded Nelder-Mead on the mean log-loss of the forecast over ALL prior
  seasons, refit between seasons, pooled across leagues, starting at (0.44, 0.49, 0.6). The tuning
  uses the M1 market term, as he did.
* A match is eligible only when both teams have played ≥ 6 league games that season before it, and it
  is not among either team's last 6.
* M1 is his market term: m = 1/max odds for over 2.5 (BbMx>2.5 before 2019/20, Max>2.5 after), not
  de-vigged.

**Deviations, stated up front:**
* The burn-in season is 2005/06, not 2000/01.
* The logistic is fitted by maximum likelihood, not least squares.
* It is refit weekly on all prior eligible matches, not every matchday.
* For the Pinnacle markets the term is logit(Shin de-vig), not the raw probability.

**The family: 6 cells, Holm m = 6.** Input {S+C, goals} × market:
* **M1** — 1/max odds, 2006/07-2018/19 (his period plus one season).
* **M2** — de-vigged Pinnacle PRE-CLOSE (`P>2.5`, collected Fri/Tue), 2019/20-2025/26.
* **M3** — de-vigged Pinnacle CLOSE (`PC>2.5`), 2019/20-2025/26.

Statistic per cell: ΔLL = mean[LL(market-only logistic) − LL(full logistic)] over eligible fixtures, in
nats (positive = the rating adds to the market). Significance comes from a block bootstrap by week
(2,000 draws), one-sided. **PASS = ΔLL > 0 with Holm-adjusted p < 0.05.**

**Fidelity check (interprets the family, not part of it).** On M1, S+C ΔLL > goals ΔLL, i.e. his
headline reproduces. If it does NOT, our implementation or data differ from his, and an M3 failure
cannot be read as "the effect is gone".

**Money (reported, not a pass bar).** Level stakes wherever p̂ > 1/odds on over or under, at:
* max odds and avg odds (both eras);
* Pinnacle pre-close odds, with CLV against the Shin-de-vigged Pinnacle close.

**Expected before running:**
* M1: S+C ΔLL small and positive (~0.0005-0.001 nats), and larger than goals — the fidelity check
  passes.
* M2: close to zero, perhaps faintly positive.
* M3: ≈ 0, FAIL.
* Goals: ≈ 0 or negative everywhere.
* Money: positive at max odds only in the early seasons, negative at avg odds, and non-positive CLV
  against the Pinnacle close.
* **What would change the plan:** S+C passing M3. That would be the first input in six months to
  add anything to a sharp close, and would justify building it into a live O/U head.

### Result — #089 faithful Wheatcroft replication (2026-09-24, `scripts/wheatcroft_replication.py`)

88,477 matches, 10 leagues, 2005/06-2025/26; parameters re-tuned every season on prior seasons only.
The first full run had a bug found by the independent review: promoted and relegated teams inherited
ZERO ratings, because the inheritance ran before the walk. Fixed, the smoke test now fails on the old
code, and the whole run was redone. The numbers below are from the fixed run; the verdict did not change.

| input | market term in the model | n | ΔLL (nats) | Holm p | |
|---|---|---|---|---|---|
| shots+corners | M1: 1/max odds, 2006-19 (his set-up) | 37,204 | **+0.00063** | 0.021 | **PASS** |
| shots+corners | M2: Pinnacle pre-close, 2019-26 | 15,461 | +0.00013 | 0.90 | fail |
| shots+corners | M3: Pinnacle close, 2019-26 | 15,487 | −0.00006 | 1.00 | fail |
| goals | M1 | 37,204 | −0.00005 | 1.00 | fail |
| goals | M2 | 15,461 | −0.00015 | 1.00 | fail |
| goals | M3 | 15,487 | −0.00014 | 1.00 | fail |

**Fidelity check: REPRODUCED.** Against his own market term, shots+corners add +0.00063 nats (he
reported ~0.0008) and goals add nothing. The tuner switches goals off: λ falls from 0.32 to ~0.06-0.08.

**Where the effect goes (review, fixed parameters): about half is era, half is market.**
* Shots+corners on 1/max odds: 2006-19 +0.00072 (p = 0.000); 2013-19 alone +0.00034 (p = 0.16);
  2019-26 +0.00032 (p = 0.10).
* On Pinnacle pre-close +0.00015; on the Pinnacle close −0.00005.
* Logit vs raw-probability form makes no difference.
* The market was already learning shots before 2019, which is his own "levelled off" caveat. The
  sharp close takes what is left.

**Money (reported, not a pass bar).**
* Shots+corners at max odds: +2.34% (2006-12), +0.68% (2013-19), +0.28% (2019-26).
* At average odds: −1.08%, −2.39%, +1.34% (n = 833).
* At Pinnacle's pre-close price: −3.41%, and CLV against the Pinnacle close is −1.60%.
* ⚠️ The +0.46% CLV at max odds is **line shopping, not the model.** A market-only logistic betting the
  same way at max odds scores +0.80%, and goals score +0.71%.

**Verdict.**
* Our earlier shots-vs-goals null (#089 arm D, #077) WAS partly an unfaithful build. Built his way, the
  published effect exists.
* But it had halved by 2013-19 and is zero against Pinnacle's close. **Do not build a shots-fed O/U
  head.** This closes the last structural O/U question: feature shape (#089 arms), input (shots vs
  goals, faithfully) and timing (#090 (a)) all end at the same place — whatever our ratings know,
  Pinnacle's close already prices.

## Pre-registration — #118 xG GAP RATINGS, TOP-10 xG LEAGUES, O/U 2.5 (2026-09-24, BEFORE building)

The owner's question: now that xG is backfilled (#111), does an xG-fed rating, fitted only on the
leagues that carry xG, add anything to Pinnacle? It is the first league-SUBSET test on our own data.

**Literature and prior evidence, stated first:**
* Structural shape: totals are SUM-shaped, so the forecast regressor is the GAP rating sum, as in #089.
* xG measures shot QUALITY, where Wheatcroft's shots+corners measure volume. The expectation is
  xG ≥ shots > goals as an input.
* But these are the MOST efficient markets there are. xG for these leagues has been public for years
  (Understat, FBref/Opta).
* #089 showed Wheatcroft's shot signal fading to zero against the Pinnacle close in largely these
  same leagues.
* #090 (a) showed our goals rating predicts Pinnacle's early→close MOVE (±0.4pp) but not the close
  itself.
* No published result shows an xG rating beating a sharp closing line.

**Data (our DB).**
* Leagues: EPL, Championship, La Liga, Serie A (ITA), Bundesliga, Ligue 1, Eredivisie, Primeira Liga,
  Süper Lig, Belgian Pro League.
* Matches: finished, from 2023-07-01; seasons are Jul–Jun.
* Inputs from `match_stats`; goals from `matches`.
* Pinnacle `over_under_25`, pre-kickoff and not in-play:
  * **EARLY** = the first complete over/under pair;
  * **CLOSE** = the latest complete pair (sides ≤ 2 min apart), no older than 60 min at kickoff,
    strictly before kickoff.
  * Both are Shin de-vigged, and the market term is logit(p_over).

**Method (the #089 harness, reused).**
* Ratings: four additive GAP ratings per team, keyed by (league, team). A team new to a league
  inherits the mean of the teams that left it.
* Tuning: (λ, φ1, φ2) by Nelder-Mead on prior seasons, with the EARLY market term.
* Forecast: logit P(over) = α + β1·GAPsum + β2·market, refit weekly on all prior eligible matches.
* Eligibility: both teams have played ≥ 6 league games that season, and every input is present.
* **The same fixtures are scored for every input.**
* Burn-in: 2023/24. Test: 2024/25 to date.

**Deviations from #089, stated up front:**
* No "last 6 games" exclusion, because the current season's remaining schedule is not held.
* The market term is logit(de-vigged Pinnacle), not 1/max odds.
* A 2023/24 burn-in, which is short.

**Family: 6 cells, Holm m = 6.** Inputs {xG, shots+corners, goals} × markets {EARLY, CLOSE}.
Statistic: ΔLL = mean[LL(market-only) − LL(market + rating)], in nats. Block bootstrap by week, 2,000
draws, one-sided. **PASS = ΔLL > 0 with Holm-adjusted p < 0.05.**

**Money (reported, not a pass bar).** Level stakes at Pinnacle's EARLY price wherever p̂ beats it,
with CLV against the de-vigged close. A market-only control is run with the same rule (ANALYSIS_GOTCHAS
§75).

**Expected before running:**
* xG ΔLL ≥ shots+corners > goals.
* Against EARLY: a small positive gain, possibly passing (the #090 (a) move).
* Against CLOSE: ≈ 0, FAIL for every input.
* Power: roughly 5-6k scored fixtures, below the 7-8k needed to see a Wheatcroft-sized effect — so an
  EARLY null would be inconclusive, not negative.

**What would change the plan:**
* **xG passing CLOSE** would be the first input to add to the sharp close, and justifies an xG O/U
  head for 👥 PICKS.
* **xG passing only EARLY** is a timing edge: a 🤖 OWN question of whether we can bet the early price
  at a book that has not yet moved.

**Amendment to #118, 2026-09-24 — recorded BEFORE any CLOSE cell was scored.** The first quick run
showed the EARLY cells: all three inputs had ΔLL < 0 (xG −0.00011, shots+corners −0.00021, goals
−0.00013; n = 3,797). It could not score CLOSE: only 453 fixtures had a pre-kickoff pair ≤ 60 min
old.

**Cause:** provenance, not coverage. Our historical Pinnacle O/U rows (before ~July 2026) were
ingested from football-data.co.uk (`scripts/ingest_football_data_csvs.py`):
* FD's pre-close `P>2.5` is stored as `is_opening`, stamped kickoff − 7 d.
* FD's closing `PC>2.5` is stored as `is_closing`, stamped AT kickoff.

So the strict `timestamp < kickoff` filter dropped every closing row.

**Amended CLOSE:** the `is_closing` Pinnacle pair (FD's Pinnacle close) where one exists; otherwise
the pre-registered rule (latest complete pair ≤ 60 min old, strictly before kickoff), which covers the
live era from ~July 2026.

**EARLY is unchanged:** the first complete non-closing pair. For history that is FD's pre-close price,
collected on a Friday or Tuesday — so EARLY here is the same market term as #089's M2, not an opening
price.

### Result — #118 (2026-09-24, `scripts/xg_gap_top_leagues.py`): NULL, every cell

10,829 matches across the 10 leagues; 5,984 test-period eligible (2024/25 onward). Scored:
3,793 against EARLY (football-data's Pinnacle pre-close) and 3,601 against CLOSE (Pinnacle close).

| input | vs EARLY ΔLL (tuned / fixed θ) | vs CLOSE ΔLL (tuned / fixed θ) |
|---|---|---|
| xG | −0.00037 / −0.00012 | −0.00036 / −0.00019 |
| shots+corners | −0.00094 / −0.00020 | −0.00084 / −0.00009 |
| goals | −0.00031 / −0.00014 | −0.00024 / −0.00005 |

**All six cells FAIL** (Holm p = 1.00). The tuned run is WORSE than the fixed one: with a single burn-in
season to tune on, λ and φ ran to their bounds. Both agree anyway.

**The pipeline is live.** Market-free, rating alone vs climatology (LL 0.6932):
* xG **+0.0088** (corr with over +0.137);
* goals +0.0057;
* shots+corners +0.0040.

**xG IS the best input, as expected.** But Pinnacle's pre-close alone scores LL 0.6729, far better than
the xG rating alone (0.6844), and adding the rating to it adds nothing.

**Money at Pinnacle's pre-close price** (level stakes where p̂ beats it):
* xG −2.33% ROI / −2.86% CLV;
* market-only control −3.88% / −2.98%.

Nothing clears the control on CLV.

**Verdict.** In the big leagues, API-Football xG carries real information about goals, and Pinnacle
already prices all of it, at both pre-close and close. **No xG O/U head for these leagues.**

A league subset does not change the #089 answer. The softer leagues where a public-data input might
still be under-priced are exactly the ones with little Pinnacle history in our DB (MLS 180, Brazil
122, Argentina 128, Norway 104 fixtures), so they cannot be graded against a sharp price yet.

**Found on the way — ANALYSIS_GOTCHAS §77.** Historical Pinnacle rows are football-data ingests:
* `is_opening` rows are FD's Friday/Tuesday pre-close price, stamped kickoff − 7 d. They are NOT an
  opening price.
* `is_closing` rows are stamped exactly AT kickoff, so a `timestamp < kickoff` filter silently drops
  every historical close.
