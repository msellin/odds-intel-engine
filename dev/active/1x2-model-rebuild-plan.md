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
