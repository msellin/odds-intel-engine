# 1X2 model rebuild — plan, research answers, pre-registration

Parent row: **[[#141]]** `1X2-MODEL-REBUILD-2026-09-24` in `PRIORITY_QUEUE.md`.
Tasks: `dev/active/1x2-model-rebuild-tasks.md`. Context: `dev/active/1x2-model-rebuild-context.md`.

Written 2026-09-24, **before any training run**, per CLAUDE.md "Research before you train".

---

## 0. Why this exists — the audit (2026-09-24, read-only)

| # | Finding | Evidence |
|---|---|---|
| a | Weekly retrain comparison has exited 2 every week since 09-03 — no email, no verdict | VPS journal 2026-09-20 03:13: `v20260830: no honest holdout … weekly_eval_and_compare.py exit 2`. The candidate trains through the run date, so the honest window is empty by construction. |
| b | Production 1X2 `v20260830` predates the ELO-FORM-LEAK fix (61cd38b4); no bundle on disk carries `feature_fill_values` (fix `0510943b`, 09-21), so serving zero-fills while training mean-filled | `data/models/soccer/*/` listings; `xgboost_ensemble.py:401-405` |
| c | 53% of ~175k training rows have neither ELO/form nor a market price (100% of 2017-22, 67% of 2023-25); we predict for 875 leagues regardless | per-era coverage query, 2026-09-24 |
| d | On the 09-21+ honest holdout v20260920 is worse than prod (+0.082 LL, 95% CI +0.034..+0.129); both far behind the de-vigged market (0.933 vs 1.018 on priced rows) | scratch `eval1x2.py` |
| e | On data-poor rows prod ≈ base rate; the 2017+ retrains are worse than base rate (1.25 vs 1.08) | same, bucketed |
| f | Served 1X2 probability is ~99.6% Platt(Pinnacle): `shrinkage_alpha_t1_1x2 = 0.0037`, t2–t4 = 0 (fit 09-23) | `model_calibration` |
| g | Feature set: 15+ columns under 20% filled, four sum-shaped O/U/BTTS columns in a 1X2 head, no time decay, `opening_implied_*` is 1/odds from whichever book posted first with margin in | agent audit |

**The root design fault:** the 1X2 model reads team strength out of `match_feature_vectors`, whose
columns were only populated for a fraction of history. Team strength does not need that table —
**final scores exist for 175k matches (100%) and half-time scores for 98%**, and every
published strong 1X2 input is a walk-forward rating computed from exactly those scores.

---

## 1. Research answers (required before training)

### 1.1 Structural shape — DIFFERENCE
1X2 is a function of the goal **difference** (Karlis & Ntzoufras 2009, Skellam: the sum is
integrated out). Features must be difference-shaped. Already established in
`dev/active/per-market-feature-sets-design.md` §"The principle".

### 1.2 What works for 1X2 in the literature
* **Ratings beat engineered vectors.** Hvattum & Arntzen (2010, IJF): an ordered-logit on a
  single Elo difference beat every covariate-rich alternative they tried, and lost only to the
  bookmakers. 2023 Soccer Prediction Challenge: a two-number rating 0.2085 RPS vs a 40-feature
  engineered set 0.2416; bookmaker consensus 0.2063 (per-market design doc, Rule 0).
* **Pi-ratings** (Constantinou & Fenton 2013) — separate home/away ratings updated on the
  goal-difference error, with diminishing returns on big margins. The 2017 Soccer Prediction
  Challenge winner (Hubáček, Šourek & Železný 2019, *Machine Learning*) used pi-ratings as the
  core input to gradient-boosted trees.
* **Dynamic Poisson attack/defence** (Maher 1982; Dixon & Coles 1997 with time-decay weighting)
  gives λ_home, λ_away and hence Skellam 1X2 probabilities with no classifier at all.
* **Time decay 30–90 day half-life for match outcome** (Wheatcroft & Sienkiewicz — see the
  2026-09-24 correction in CLAUDE.md about what their 300 days means).
* **Draws are not a target** (Foulley 2021): price draws off the 1X2 distribution.
* **Past goals are a weak input for GOALS markets** (Wheatcroft); for 1X2 the goal DIFFERENCE
  is the standard rating input and is what Elo/pi/Poisson ratings all consume. Shots-based
  ratings are the better input where shots exist (31% of matches) — secondary arm only.

### 1.3 Known negative results to not re-derive
* α = 0 vs a de-vigged sharp closing line is the **normal published result**; nothing in the
  51-league 2023 benchmark beat the bookmaker consensus.
* Our own 1X2 residual test (09-14/09-16): α = 0.0000 — but on a zero-filled, leak-era harness,
  binary home-win only. **Not a fair measurement of a rating model; this plan re-measures it
  3-way.**
* Pinnacle's price in thin leagues is a placeholder (ANALYSIS_GOTCHAS §(m)); the residual test
  measured an 8.49% overround on our universe. If any α exists it is most plausible there —
  pre-registered as a SECONDARY split, not the decision.

### 1.4 Market prices as features — resolved explicitly
Two variants per arm where relevant: **no-market** (the α measurement) and **market-in**
(production pricing only; never quote its α). Same rule as the O/U arms.

### 1.5 Coverage — the owner's rule
*"We shouldn't model leagues for which we don't have the feature data, and vice versa."*
Implemented as: train and serve only where **both teams have ≥ N prior rated matches**
(N pre-set to 8, sensitivity 4/15 reported). Rows below the gate get NO model prediction
(market-only or skipped), and are excluded from training. Coverage is reported per league tier.

---

## 2. Pre-registration — 1X2 rating arms

### 2.1 Data
* Universe: every `matches` row `status='finished'` with scores, 2022-01-01 onward (~174k).
* Features: computed in-script, walk-forward, **processed one kickoff-date at a time — features
  for a date are read from state BEFORE any of that date's results update it** (no same-day
  leak).
* Market: Pinnacle 1X2 triple, **latest snapshot strictly pre-kickoff**
  (`minutes_to_kickoff > 0`, not live, odds > 1.01), proportional de-vig; Shin as robustness.
  This is the closing line — the hardest available benchmark.

### 2.2 Arms — the α family is FIXED at 6 from the first run (Holm m = 6)
| arm | input | model |
|---|---|---|
| E | goal-margin Elo difference + home advantage | multinomial logit |
| PI | pi-rating expected goal difference | multinomial logit |
| DP | dynamic Poisson λ_home, λ_away (FT goals, decayed) | Skellam direct → Platt-free |
| D8 | ~8 difference features: Elo diff, pi diff, DP λ diff, HT-rating λ diff, form-ppg diff (from scores), rest-days diff, league home-win rate, league draw rate | multinomial logit |
| DX | same 8 | XGBoost, depth ≤ 3, early stopping on a pre-test validation slice |
| DXS | DX + shots-rating diff where shots exist (NaN otherwise, native XGB missing) | XGBoost |

Reported but **outside** the α family: DXM (DX + de-vigged Pinnacle, market-in), and
PROD (`v20260830` raw head exactly as served, zero-fill) as the reference.

Hyper-parameters (Elo K, half-lives, pi learning rates) are chosen on a **validation slice
inside the training period** only, never on the test window.

### 2.3 Two windows, two questions
1. **Better than production?** Train ≤ 2026-08-30 (same information as `v20260830`), test
   2026-08-31 .. latest settled. Metric: 3-way log-loss and RPS on the SAME rows, paired
   bootstrap 95% CI. Reported overall and per coverage bucket and per tier.
   **PASS = log-loss lower than PROD with CI excluding 0, overall AND on the gated rows.**
2. **Does it know something Pinnacle doesn't? (α)** Train ≤ 2026-05-31, test 2026-06-01 ..
   latest, Pinnacle-priced rows only. α = weight on the model in α·model + (1−α)·market,
   fitted per class-shared on the first chronological half of the test, scored on the second.
   **PASS = blend LL < market LL AND α > 0.02, Holm-corrected p (paired bootstrap on per-match
   LL difference) < 0.05, m = 6.** Same bar as every earlier α test.

### 2.4 Expected outcome, stated before running
* Q1: **most rating arms beat PROD** (≈80% confident) — PROD is a leak-era, zero-filled 67-feature
  head that is ≈ base rate on data-poor rows.
* Q2: **all six arms FAIL α overall** (≈85% confident), consistent with the literature. A
  positive α, if any, is most plausible in tier 3–4 leagues where Pinnacle's overround is high
  (secondary split, not decisive).
* Therefore the likely shippable result is: a **better-calibrated, full-coverage rating model**
  that replaces the XGB head for the model arm and for anything priced where Pinnacle is absent,
  with the served probability still anchored to the market where the market exists.

### 2.5 If more data is needed
Cold-start (teams/leagues with little history — 700+ leagues were added in 2026) is the most
likely limit. Remedy, in order: (1) measure accuracy vs history depth; (2) if depth is the
limit, backfill prior seasons' results from API-Football `/fixtures?league&season` (1 call per
league-season, budget 150k/day) into a **research cache first**, never straight into `matches`
until the effect is measured; (3) football-data.co.uk (free results + Pinnacle closing odds for
~22 European leagues since 2012) as a long external validation set.

---

## 3. Phases
1. Harness `scripts/ab_1x2_rating_arms.py` + data cache (read-only against the DB).
2. Tune on validation slice; run Q1 and Q2 once each; record results below.
3. Iterate only on NEW pre-registered arms (appended to this doc with date before running).
4. If a winner exists: productionise — ratings job + serving path + weekly retrain with
   `--cutoff` (fixes audit (a)) + coverage gate. Owner confirms before the served model changes.

## 4. Risks
* Shared checkout — other sessions edit `workers/scheduler.py`, `WORKFLOWS.md`,
  `scripts/smoke_test.py`; stage only own hunks.
* Same-day leakage in walk-forward features (guarded by date-batched updates; a smoke test pins it).
* Team-ID fragmentation (same club, several `teams` rows) would silently cold-start ratings —
  measure before trusting depth numbers.

---

## RESULTS
_(appended after each run, dated)_

### 2026-09-24 — tuning (validation slice only: fit 2022-07..2026-02, score 2026-03..05, gated rows)
Round 1 (single-feature multinomial logit per rating; DP scored directly). Validation LL:
best Elo 1.0290 (K 45, HFA 60), pi 1.0293 (lr 0.1, γ 0.9), **dynamic Poisson 1.0252** (lr 0.03,
league lr 0.003), HT rating 1.0404, SOT rating 1.0596, form 1.0356 (half-life 120 d). Several optima
sat on a grid edge, so round 2 extends only those grids (`--tune-extend`). The Q1/Q2 test windows
have not been read. Noted during cache build: 34% of Pinnacle's non-live 1X2 rows (176k of 517k)
carry a timestamp AFTER kickoff and are excluded by the `timestamp < kickoff` bound.
Round 2 (`--tune-extend`): converged at elo_k 45, pi_lr 0.1 / γ 0.9, dp_lr 0.03, dp_league_lr 0.003,
ht_lr 0.06, sot_lr 0.08, form half-life 240 d (flat vs 120). Saved to `data/models/_research/1x2/tuned_params.json`.

### 2026-09-24 — RESULT Q1 (vs production): **PASS, every arm**
Train ≤ 2026-08-30 (120,300 gated rows), test 2026-08-31..09-24, 12,632 rows with a PROD prediction
(7,687 gated). Log-loss (lower better), Δ vs PROD with paired-bootstrap 95% CI:

| | ALL (12,632) | GATED (7,687) | UNGATED (4,945) | Pinnacle-priced (6,511) |
|---|---|---|---|---|
| BASE rate | 1.0683 | 1.0673 | 1.0699 | 1.0718 |
| **PROD** v20260830 raw head | 1.0711 | 1.0538 | 1.0978 | 1.0653 |
| SERVED ensemble (stored) | 1.1577 (n 5,956) | 1.1449 | 1.2054 | 1.1541 |
| AF's own prediction | 1.4255 | 1.3657 | 1.5245 | 1.3499 |
| E (Elo) | 1.0339 −0.037 | 1.0281 −0.026 | 1.0428 | 1.0396 |
| PI (pi-ratings) | 1.0323 −0.039 | 1.0255 −0.028 | 1.0429 | 1.0384 |
| **DP** (dynamic Poisson, no fit) | **1.0277 −0.043** [−0.051,−0.036] | 1.0202 −0.034 | **1.0392** | 1.0368 |
| **D8** (8 diffs, logit) | 1.0285 −0.043 | **1.0188 −0.035** [−0.043,−0.027] | 1.0436 | **1.0365** |
| DX (8 diffs, XGB) | 1.0326 | 1.0238 | 1.0462 | 1.0383 |
| DXS (+ shots) | 1.0327 | 1.0238 | 1.0465 | 1.0378 |
| DXM (+ Pinnacle T-2h, market-in) | 0.9864 (n 6,252) | 0.9906 | 0.9705 | 0.9864 |
| Pinnacle close | — | — | — | 0.9813 |

Per tier (gated): D8 beats PROD in all five (t0 1.031 v 1.074, t1 1.010 v 1.042, t2 1.036 v 1.065,
t3 1.028 v 1.082, t4 1.056 v 1.109). XGB never beats the logit on the same 8 features; shots add nothing.
Production's raw head is WORSE than the constant base rate overall — only its gated rows beat it.

### 2026-09-24 — RESULT Q2 (α vs Pinnacle close): **FAIL, every arm — as pre-registered**
Train ≤ 2026-05-31 (105,381 gated), test 14,053 gated Pinnacle-priced rows (α fit 7,026 / scored
7,027). Mean overround 8.29%. Market LL 0.9846; arms 1.029–1.037; **α = 0.000 on all six**.
The rating models trail the closing line by ~0.045 nats. Same verdict as the literature.

### 2026-09-24 — side finding: the STORED 1X2 prediction is worse than uniform
`predictions` source='ensemble' v20260830 = 0.635·Poisson + 0.365·XGB (`ensemble_prediction`).
Scored on settled matches since 08-31: ensemble 1.1437, **Poisson leg 1.1473 — worse than 1/3-1/3-1/3
(1.0986)**; mean stored probs H 0.366 / D 0.311 / A 0.323 vs actual 0.438 / 0.244 / 0.318. The
Poisson leg (`daily_pipeline_v2.py` ~1530-1630, last-10 goals, fuzzy team names) is the defect.
Calibration and the Pinnacle pull happen downstream at bet time, which is why placed bets are not
this bad — but every consumer of `predictions.model_probability` for 1X2 reads the squashed number.

## Pre-registration — ROUND 2 (2026-09-24, written BEFORE the round-2 runs)
Motivation: Q1 showed accuracy depends on history depth (DP LL 1.056 at 1-3 prior matches vs 1.011
at 60+) and 97% of low-depth test rows are in leagues first seen in 2026. Arms:

| arm | change vs round 1 |
|---|---|
| H-DP / H-D8 | same models, ratings warmed with prior-season results from `scripts/fetch_1x2_history_cache.py` (AF `/fixtures?league&season`, cache only). Extra rows update rating state only; they are never train or test rows, so test sets are identical. |
| D8+ | D8 plus the DP model's own log-odds (`log(dp_ph/dp_pa)`, `dp_pd`) as features — stacking the best single rating into the logit |
| H-D8+ | both |

**Adoption rule (fixed now):** a round-2 arm replaces round-1 D8 only if it beats D8 on the
VALIDATION slice (fit ≤ 2026-02-28, score 2026-03..05) AND on the Q1 window (2026-08-31..). The Q1
window has been seen once, so it is a confirmation, not a selector; the first genuinely unseen check
is the forward window from 2026-09-25, recorded when ≥ 2,000 settled rows exist.
**Expected:** history warm-up improves the ungated bucket materially (≥ 0.01 LL) and gated rows a
little; D8+ is within ±0.003 of D8. α vs Pinnacle stays 0.

### 2026-09-24 — RESULT ROUND 2: **H-D8+ adopted** (beats D8 on validation AND Q1, per the rule)
Fetched 258,222 prior-season results (1,965 AF calls; 220,989 not already in `matches`). Features now
built with a deterministic (kickoff, match_id) mergesort — round 1 used an arbitrary tie order; the
re-run D8 numbers below supersede it (differences ≤0.002).

| arm | validation ALL | Q1 ALL | Q1 base-gated | Q1 base-ungated |
|---|---|---|---|---|
| DP / D8 / D8+ (no history) | 1.0443 / 1.0472 / 1.0405 | 1.0276 / 1.0285 / 1.0253 | 1.0200 / 1.0187 / 1.0162 | 1.0394 / 1.0436 / 1.0392 |
| H-DP / H-D8 / **H-D8+** | 1.0137 / 1.0133 / **1.0113** | 1.0080 / 1.0051 / **1.0037** | 1.0066 / 1.0052 / **1.0034** | 1.0101 / 1.0050 / **1.0040** |

Leak / fairness checks: every `matches` row has an AF fixture id; 11 history rows duplicate a stored
match by date+teams (same-day, so applied only after that date's features). **History cut at
2026-01-01 (prior seasons only, production-reproducible): H-D8+ 1.0078**; cut at the Q1 train cutoff:
1.0038 — so ~85% of the gain is prior-season history, the rest in-season fixtures of tracked leagues
we never stored; in-window history adds nothing.

### 2026-09-24 — FINAL (H-D8+, prior-season history only) vs PROD on the Q1 window
| bucket | n | H-D8+ | PROD | Δ (95% CI) | RPS |
|---|---|---|---|---|---|
| ALL | 12,640 | **1.0078** | 1.0711 | −0.0632 [−0.0704, −0.0558] | 0.2139 vs 0.2296 |
| base-gated | 7,690 | 1.0070 | 1.0538 | −0.0467 [−0.0544, −0.0388] | 0.2130 vs 0.2264 |
| base-ungated | 4,950 | 1.0091 | 1.0980 | −0.0889 [−0.1022, −0.0752] | 0.2153 vs 0.2345 |
| Pinnacle-priced | 6,517 | 1.0256 | 1.0653 | −0.0397 [−0.0486, −0.0312] | (Pinnacle close 0.9812) |

Tiers 0-4: 1.007/0.996/1.036/1.062/1.049 vs PROD 1.082/1.058/1.064/1.152/1.101. Calibration H/D/A
0.440/0.239/0.321 vs actual 0.438/0.231/0.331. **Exploratory α (outside the pre-registered family):**
0.000 on 18,114 priced matches; tier 2 α 0.13 (blend 1.0169 vs market 1.0175), tier 3 α 0.155 but blend
worse out-of-sample — no evidence of an edge over Pinnacle, as predicted.

**Decision:** ship H-D8+ as `r1x2_d8plus_v1` in SHADOW (migration 412, `workers/jobs/rating_1x2_shadow.py`,
05:30/17:30 UTC). Promotion into the served 1X2 blend = owner decision on the forward record.

## Pre-registration — ROUND 3 (2026-09-24, written BEFORE any round-3 run)
Owner: *"the model isn't only about beating Pinnacle … we WILL build an even better 1x2 model"*. The goal
is the most accurate 1X2 probability on EVERY match — the ~50% Pinnacle does not price matter most for
both picks and our own bets. Round 3 has three parts; the baseline for all of them is **H-D8+**
(round-2 winner, full history). Two research agents (web literature; our own bookmaker/data inventory)
feed parts 3b and 3c, which are pre-registered in their own dated sections before they run.

### 3a — small modelling ideas (no new data)
| arm | change vs H-D8+ |
|---|---|
| NEWC | newcomer prior: a team's first rating in a league starts at the league's 25th-percentile Elo and bottom-quartile Poisson strength instead of mean-50 / zero (promoted and new teams are weaker than average) |
| ORD | ordered logit (home > draw > away on one latent scale) instead of multinomial |
| TIERX | D8+ features interacted with league tier (tier dummies × features) |
| DECAY | training rows weighted by recency (half-life 365 d) |
| ENS | average of D8+ logit and the dynamic-Poisson probabilities, weight chosen on validation |
| C | L2 strength of the logit tuned on validation (C ∈ {0.1, 0.3, 1, 3}) |

**Selection:** each arm is scored on the VALIDATION slice (fit ≤ 2026-02-28, score 2026-03-01..05-31).
Arms that beat H-D8+ there by ≥ 0.001 are combined, and the combination is confirmed ONCE on the Q1
window (2026-08-31..). Adopted only if it beats H-D8+ on both. Expected: each arm moves log-loss by
≤ 0.003; NEWC most likely to help (cold start was the round-2 lesson).

### 2026-09-24 — RESULT 3a: every arm FAILS the ≥ 0.001 bar — score-only inputs are saturated
Validation slice (22,179 rows), H-D8+ baseline 1.0113: C ∈ {0.1,0.3,3} ±0.0000; DECAY hl 365 −0.0003,
730 −0.0002; TIERX +0.0007; **ORD +0.0050 (worse)**; ENS with DP −0.0001; NEWC Elo offset 100/150
−0.0001, Poisson newcomer prior ±0.0000. Nothing adopted; the Q1 window was not consulted. This agrees
with the web-research finding (Hubáček et al. 2022: Berrar, pi, bivariate/double Poisson, Weibull all
within 0.0002 RPS on 91,155 matches — a ceiling for score-only models). Remaining gains must come from
information scores do not carry: bookmaker consensus (3b), lineups/player strength (3c).
The NEWC parameters stay in `ratings_1x2.py` with defaults that reproduce the adopted model exactly.

## Pre-registration — ROUND 3b: COMBINED model (ratings + bookmaker consensus + Pinnacle), 2026-09-24, BEFORE the run
Inputs from the two research agents: (web) score-only models share a ceiling; the bookmaker consensus is
the strongest single input (Robberechts & Davis RPS 0.2020 vs best rating 0.2035; 2023 challenge: nothing
beat the consensus). (Data audit) the equal-weight de-vigged consensus of our non-Pinnacle books matches
Pinnacle (0.9781 vs 0.9783, n 18,877) and on no-Pinnacle matches beats our rating model by 0.070 log-loss;
multi-book history is usable from 2026-05-01 (~22k matches with ≥1 book).

**Books in the consensus:** 1xBet, Marathonbet, Bet365, William Hill, Betano, Betfair (sportsbook),
BetVictor, Coolbet, Epicbet, Unibet-Site, Tonybet, and — history only, they no longer quote — Dafabet,
10Bet, Unibet, Superbet, 888Sport, BetWin, Betfred. **Excluded:** Pinnacle (its own input), SBO (worst
accuracy, 15% margin), Unibet-Kambi (retired, off-site prices), Avg/Max (synthetic), Betfair Exchange
(exchange, separate), junk names. Each book's triple is proportionally de-vigged; triples whose home
probability is > 0.25 from the other books' median are dropped (catches transposed/wrong-fixture rows,
~0.1%); the consensus is the mean in log-odds space; `n_books` kept.

**Sources per match:** R = rating model H-D8+ (logit coefficients fitted ≤ 2026-04-30, so every
combiner training row is out-of-sample for it); C = consensus; P = Pinnacle (proportional de-vig).
**Arms:**
| arm | rule |
|---|---|
| R | rating only (baseline = current shadow model) |
| RULE | P if present, else C, else R |
| COMB | multinomial logit per availability group {P&C, P only, C only, none} on the log-odds (vs draw) of the available sources + log(n_books) |
| COMB+AF | COMB with API-Football's own prediction % as an extra source (77.5% coverage) |

**Price timing — two variants, both reported:** CLOSE (last pre-kickoff price; upper bound, matches a
prediction refreshed near kickoff) and OPEN (opening price; what a morning prediction can always see).
**Windows:** combiner fitted 2026-05-01..07-31, selected on 08-01..08-30; confirmed ONCE on the Q1 window
2026-08-31.. with the combiner refitted on 05-01..08-30.
**Adoption:** COMB (or COMB+AF) is adopted if it beats RULE overall on the Q1 window with the paired CI
excluding 0, and is not worse than RULE in any availability bucket by more than 0.002.
**Expected:** COMB ≈ RULE on P&C rows (the market already carries the rating information: α = 0), a
small gain on C-only rows with few books (rating adds where the consensus is thin), overall log-loss
≈ 0.95–0.97 vs 1.008 for R. AF adds ≤ 0.002, mostly on the "none" group.

### 2026-09-24 — RESULT 3b SELECTION (fit 05-01..07-31, score 08-01..08-30; Q1 window NOT yet read)
| CLOSE prices | n | R | RULE | COMB | COMB+AF |
|---|---|---|---|---|---|
| ALL | 13,886 | 0.9971 | 0.9770 | **0.9749** (−0.0022 vs RULE, CI −0.0032..−0.0010) | 0.9755 |
| P&C | 6,813 | 1.0182 | 0.9837 | 0.9827 | 0.9844 |
| C only | 1,378 | 0.9555 | 0.9240 | **0.9138** (−0.0102) | 0.9182 |
| none | 5,695 | 0.9819 | 0.9819 | 0.9803 | **0.9786** (−0.0032) |
OPEN prices: same shape (ALL R 0.9971 / RULE 0.9799 / COMB 0.9786 / COMB+AF 0.9788; none: COMB+AF 0.9748).
API-Football's prediction helps ONLY where no book prices the match and hurts where the market exists.
**Selection (made here, before the confirmation run): COMB-HYB = COMB, with the AF inputs used in the
"none" group only.** It is confirmed once on the Q1 window against RULE under the pre-registered rule.

### 2026-09-24 — RESULT 3b CONFIRMATION (Q1 window 08-31..09-24, 12,640 matches, combiner fit 05-01..08-30): **COMB-HYB ADOPTED**
| CLOSE | n | R | RULE (P→C→R) | COMB-HYB | COMB-HYB − RULE (95% CI) |
|---|---|---|---|---|---|
| ALL | 12,640 | 1.0049 | 0.9810 | **0.9763** | −0.0048 [−0.0065, −0.0029] |
| P&C (RULE = Pinnacle alone) | 6,517 | 1.0243 | 0.9812 | **0.9795** | −0.0017 [−0.0033, −0.0002] |
| C only | 2,500 | 0.9834 | 0.9749 | **0.9619** | −0.0130 [−0.0187, −0.0070] |
| none (ratings + AF) | 3,623 | 0.9849 | 0.9849 | **0.9803** | −0.0045 [−0.0082, −0.0008] |
OPEN prices: ALL 0.9792 vs RULE 0.9816 (−0.0025 [−0.0041, −0.0008]); no bucket worse than RULE.
Passes the pre-registered rule (beats RULE overall, CI excludes 0; no bucket worse by > 0.002).

**Read-outs.** (1) vs the shipped XGBoost head (1.0711 on the same window): **−0.095 log-loss, −8.9%**.
(2) On matches Pinnacle prices, ratings + consensus + Pinnacle beats **Pinnacle alone** by 0.0017 (CI
excludes 0) — the first positive combined-vs-Pinnacle result in this project, though it uses other books'
CLOSING prices, so as a betting edge it is only as good as the price timing (OPEN: −0.0014, CI touches 0).
(3) API-Football's prediction adds information only where no book prices the match — matches the owner's
note that it is worse than uniform on its own. (4) Tonybet's Sportradar fair probabilities
(`book_fair_probs`, 305 fixtures) are a candidate extra book once they have history.

## Pre-registration — ROUND 3c: PLAYER / LINEUP strength (2026-09-24, BEFORE the run)
Evidence: team + player ratings together significantly beat either alone (Arntzen & Hvattum 2021);
a player-rating model returned significant profits against bookmaker 1X2 prices (Holmes & McHale 2024).
Data: `scripts/fetch_fixture_details_cache.py` — `/fixtures?ids=` (20 fixtures per call with nested
lineups, players, statistics), ~19.7k calls for 394,654 fixtures (our finished matches since 2022 +
rating_history_results), owner-approved 2026-09-24 ("use all today's quota minus headroom"); cache only.

**Feature (walk-forward, date-batched like the ratings):** each player's rating = minutes-weighted,
exponentially decayed mean of API-Football's per-match rating (players with ≥ 20 minutes), shrunk to a
prior with weight k = 3 matches; XI strength = mean over the starting XI. Features: `xi_diff` (home XI −
away XI) and `xi_delta_home/away` (this XI − the team's own recent XI average — rotation / missing
regulars). Two variants of which XI is used: **ACTUAL** (the confirmed XI — what a prediction ~1 h before
kickoff sees) and **PREV** (the team's previous XI — what a morning prediction sees).

**The family, fixed at 4 (Holm m = 4), each vs the same model without the XI features:**
| id | model | XI | prices |
|---|---|---|---|
| L1 | rating model H-D8+ | ACTUAL | — |
| L2 | combined COMB-HYB | ACTUAL | OPEN |
| L3 | combined COMB-HYB | ACTUAL | CLOSE |
| L4 | combined COMB-HYB | PREV | OPEN |
Selected/tuned (half-life ∈ {180, 365} d) on 08-01..08-30, confirmed ONCE on 08-31.. . **PASS = Δ log-loss
< 0 with Holm-adjusted p < 0.05.** Rows without lineups keep the no-XI prediction (coverage reported).
**Expected:** L1 passes clearly (lineups carry what score ratings lag on); L2 small gain; L3 ≈ 0 (closing
prices already contain the lineup news); L4 ≈ 0.

**ROUND 3c — SELECTION result (2026-09-24 ~19:35 UTC, fit < 08-01, score 08-01..08-30), recorded BEFORE the confirm run.**
Fetch finished first: 11,133 calls, 2 fixtures not returned by AF. *Implementation fix found in selection, applied
before confirm:* the combiner's `design()` adds a 0/1 presence flag per extra column, and on rows with NO XI data
that flag acted as a league-coverage intercept — the first selection run showed L4 −0.0031 with its whole gain on
rows where both XI variants were missing (−0.0071 on 5,180 "neither" rows, +0.0002 where only PREV existed). That
contradicts the pre-registration ("rows without lineups keep the no-XI prediction"), so `ab_1x2_lineups.py` now
sets the no-XI prediction on every row without the arm's XI feature. Re-run (Δ log-loss, Holm m=4):
| arm | hl 180 | hl 365 |
|---|---|---|
| L1 rating +XI actual | −0.0012 (Holm .210) | **−0.0018 (Holm .026)** |
| L2 comb OPEN +XI actual | −0.0003 | −0.0005 (.129) |
| L3 comb CLOSE +XI actual | −0.0003 | −0.0005 (.129) |
| L4 comb OPEN +XI prev | −0.0002 | −0.0004 (.129) |
**Chosen half-life: 365 d** (better on all four arms). Confirm = `--confirm --half-life 365`, run ONCE.

**ROUND 3c — CONFIRM result (run once, 2026-09-24 ~19:45 UTC; fit < 08-31, score 08-31..09-24, n = 12,640): ALL FAIL.**
| arm | without | with | Δ | raw p | Holm |
|---|---|---|---|---|---|
| L1 rating +XI actual | 1.0037 | 1.0042 | +0.0005 | .728 | 1.0 |
| L2 comb OPEN +XI actual | 0.9792 | 0.9792 | +0.0000 | .525 | 1.0 |
| L3 comb CLOSE +XI actual | 0.9763 | 0.9763 | +0.0001 | .633 | 1.0 |
| L4 comb OPEN +XI prev | 0.9792 | 0.9795 | +0.0003 | .925 | 1.0 |
XI coverage 38.3% (actual) / 59.4% (prev). Expectation was L1 pass / L2 small / L3, L4 ≈ 0 — L1's selection-window
gain (−0.0018) did not replicate. **Verdict: an API-Football player-rating XI adds nothing to either model out of
sample; A3 (production player strength) is NOT built.** The fetched cache still holds per-team shots / shots on
target / xG for ~395k fixtures — the input for the next candidate round (shot/xG-based ratings, Wheatcroft), which
needs its own pre-registration and, because 08-31..09-24 has now been used by rounds 1, 2, 3b and 3c, a forward
confirmation or a family-wise correction across rounds.

## Pre-registration — BACKTEST B2: NEW+ with outlier-style rules (2026-09-24 ~19:20 UTC, BEFORE the run)
Why: backtest B (`scripts/backtest_1x2_new_bots.py`) gave NEW+ only 14 picks (ROI −36% [−76, +10]; CLV +13.1% on
the 8 with a Pinnacle price), nearly all favourites at odds 1.6–2.0. That is structural, not football: NEW+ inherited
`bot_v10_1x2`'s ABSOLUTE-probability-point thresholds (fav < 2.0 easier than long), + 5 pp at T3+, and
`min_prob` 0.30 — the same % price outlier clears a pp threshold far more easily at short odds. NEW+ is in effect
a consensus-outlier strategy (Kaunitz et al.), whose natural edge unit is expected value, p × odds − 1.
Owner prior (2026-09-24): across our bots the best gains came at odds above 2.0 — tested as its own arm (N4).

**Rules common to all arms:** NEW+ probability (`r1x2_comb_v1`, OPEN-price variant, walk-forward, as in B);
edge = p × odds − 1; Pinnacle price REQUIRED at decision time (drops the no-reference picks, e.g. the Iran draws);
no `min_prob`; no tier bump; all books (Telegram audience, owner 2026-09-24); price = opening, bounded
`timestamp < kickoff`; one pick per match (the best-EV selection); all other B gates unchanged.
Window FIXED 2026-08-31..2026-09-24 — the same window B already looked at, so this is NOT a fresh out-of-sample
test; the forward run from 2026-09-25 is.

| id | EV threshold | odds range |
|---|---|---|
| N1 | ≥ 3% | 1.30–6.00 |
| N2 | ≥ 5% | 1.30–6.00 |
| N3 | ≥ 8% | 1.30–6.00 |
| N4 | ≥ 5% | 2.00–6.00 (owner prior) |

**Primary readout:** mean CLV vs de-vigged Pinnacle close > 0, one-sided bootstrap p, **Holm m = 4, PASS at
adjusted p < 0.05**. Reported descriptively (not tested): n, hit rate, flat ROI with 95% CI, odds-band split
(1.30–1.99 / 2.00–2.99 / 3.00–6.00), book split, and the "every selection at best open price" null per band.
**Expected:** more picks than B (hundreds for N1); CLV positive but shrinking as the threshold falls; ROI inside
noise at any n this window gives. A pass here licenses a shadow bot, not a public claim.

## Pre-registration — BACKTEST B3: configuration GRID, all three models (2026-09-24 ~19:25 UTC, BEFORE the run)
Owner request: "use all 3 models — baseline, NEW, NEW+ — and run them in every possible dimension, thousands of
configurations". This is an EXPLORATORY search; its honesty comes from the design below, not from the best row.

**Grid (~12k configs):** model {baseline `bot_v10_1x2` probs (calibrated, as live), NEW `r1x2_d8plus_v1`, NEW+
`r1x2_comb_v1` OPEN} × edge unit {pp: p − 1/odds; EV: p·odds − 1} × threshold {pp: .02 .04 .06 .08 .10 .12;
EV: .02 .04 .06 .08 .12 .16} × odds range (min ∈ {1.30, 1.60, 2.00, 2.50}, max ∈ {3.00, 4.50, 6.00, 10.00}, min <
max) × selection {all, home, draw, away} × Pinnacle price required {yes, no} × book set {all, API-Football only,
direct sweepers only (Coolbet, Unibet-Site, Epicbet, Tonybet)}. One pick per match per config (best edge). All other
B gates as live. Window 2026-08-31..09-24, opening prices, CLV vs de-vigged Pinnacle close.

**Honesty, fixed now:**
1. **Split selection/confirmation:** rank configs on 08-31..09-12 (kickoff), min n = 30 picks with CLV; take the top
   10 by mean CLV; test each ONCE on 09-13..09-24; one-sided bootstrap p, **Holm m = 10**, PASS at adj p < 0.05.
2. **Data-snooping test on the full window:** Hansen SPA (stationary block bootstrap over kickoff DATES, ≥ 2,000
   resamples, fixed seed) of the best config's mean CLV against the "every selection at best open price, same odds
   range" null. Reports whether the best of the grid beats what the best of the grid would show by chance.
3. Reported descriptively: per-model CLV/ROI surfaces (threshold × odds band), the share of configs with CLV > 0
   per model vs the null's share, and every config row in the CSV. **No single config is promoted from this run**;
   a config that passes (1) becomes a pre-registered shadow bot judged forward from its creation date.
**Expected:** baseline ≈ null everywhere (it is Platt(Pinnacle)); NEW positive only in a favourites pocket, if at
all, and not surviving (1); NEW+ positive CLV at low thresholds on soft books, SPA borderline. B2 (above) keeps
its own pre-registered verdict regardless of B3.

## RESULTS — backtests B, B2, B3 (2026-09-24 ~20:10 UTC; `scripts/backtest_1x2_new_bots.py`, outputs gitignored in `data/models/_research/1x2/backtest/`)
**#065 swap check (applies to every baseline number):** the stored ensemble's XGBoost leg (~16% of the blend) was
home/away swapped before 50ec7347 (corr with Pinnacle home −0.134 before, +0.625 after); un-swapped on 1,001/1,003
matches in the shared loader. Baseline B moves CLV −2.9% → −0.75% [−4.8, +3.3], n=26.

**B (live rules as-is, opening prices):** baseline 26 picks CLV −0.75%; NEW 118 picks CLV +1.1% [−1.1, +3.1] (favourites
+5.2% [+2.3, +8.1] n=27, 3.00+ −4.0%); NEW+ 14 picks, ROI −36%, CLV +13.1% on only 8. NEW+'s inherited pp thresholds +
min_prob 0.30 push it to short odds and starve it of picks — the reason for B2.

**B2 (pre-registered N1–N4): all four PASS** (Holm p < 4e-4). N1 EV≥3% n=1,638 CLV +1.27% [+0.86, +1.69];
N2 EV≥5% n=1,050 +2.00%; N3 EV≥8% n=557 +3.12% [+2.35, +3.88]; N4 EV≥5% odds 2–6 n=860 +1.33%. ROI +7–12%, CIs
touch zero. CLV positive below odds 3.00 only; 3.00–6.00 ≈ 0 (still above its −5.2% null). **Book split is the
caveat:** API-Football-fed books carry most of it (N2 +2.66%); our own sweepers (Coolbet, Unibet-Site, Epicbet,
Tonybet) N2 +1.08% [+0.20, +1.94], N3 +2.65% [+1.42, +3.89], N1/N4 CI crossing zero. AF-fed opening quotes can be
phantom-high vs the book's own site (KAMBI-FEED-DIVERGENCE; AF 'Unibet' 33% above unibet.ee) — takeability is
unproven. Same window as B, so not fresh out-of-sample.

**B3 grid (13,824 configs):** split test — top 10 on 08-31..09-12 = only TWO distinct pick sets (NEW+ home pp≥4 odds≥1.6
AF books; NEW+ away EV≥8% odds 1.3–3.0 AF books), both PASS on 09-13..09-24 but on n = 15 and 24. SPA vs the
pre-registered null p = 0.000 (null is weak: −1.5..−5%); vs zero (NOT pre-registered) p = 0.000. Share of configs with
CLV > 0: baseline 5.1%, NEW 7.6%, **NEW+ 75.8%**. Surfaces: baseline negative everywhere (it is Platt(Pinnacle));
NEW positive only at pp ≥ 10–12 with small n; NEW+ positive almost everywhere, rising with threshold, better at
short odds, draws the exception, AF books +3.5% vs own sweepers +1.4% per config.
One interpretation to note: in B3 the grid threshold REPLACES the tier table / fav-long split / T3+ bump / data-tier
bump; every other B gate (incl. min_prob 0.30) is kept.

**Verdict:** NEW+ as a consensus-outlier bettor shows real, repeatable positive CLV on this window, strongest at our
own sweepers only for EV ≥ 5–8%. Per pre-registration nothing is promoted: the next step is a forward shadow bot
(N2/N3-style rules) plus a takeability check of opening outlier quotes. Baseline and NEW have no bettable pocket.

## Pre-registration — B4: NEW+ EV outlier shadow bots (2026-09-24 ~20:40 UTC, BEFORE they exist)
Two `experimental` paper bots, judged FORWARD only, from their first pick (≥ 2026-09-25):
`bot_combined_1x2_ev5_v1` ("1x2 NEW+ EV5") and `bot_combined_1x2_ev8_v1` ("1x2 NEW+ EV8") = B2 arms N2 / N3 as live rules:
NEW+ probability (`r1x2_comb_v1`, as is); edge = p × odds − 1 ≥ 5% / 8%, flat across tiers (no tier table, no T3+ bump);
Pinnacle price required; no min_prob; odds 1.30–6.00; one pick per match (best EV); all other live gates as the NEW+ twin
(Pinnacle veto, mid-band, sharp gate, odds movement, alignment, Kelly, staking, cohort). Prices = the live book set.
**Readout (owner 2026-09-24):** review checkpoints at 20, 50 and 100 settled picks on `/admin/bots` — CLV vs de-vigged
Pinnacle close first, ROI second; 20 is a sanity check, not a verdict. **Expected** (from B2, same-window, optimistic):
EV5 CLV ≈ +1–2%, EV8 ≈ +2–3%, lower at our own sweepers; roughly 30–40 / 15–20 picks a week.
