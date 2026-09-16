# Modelling data audit — what we have, what we lack, what we can build
**2026-09-16 · 🤖 OWN + 👥 PICKS · commissioned by the owner after O/U and 1x2 both measured residual α = 0**

> **The one-line finding.** The models are not underperforming because the
> architecture is old. They are underperforming because **nine features carry
> them.** Everything else in the 91-column feature table is under 90% populated,
> most of it under 10%, and the two heads share one feature set built for a
> question only one of them asks.

---

## 0. Why this audit exists

`residual_test.py` closed model-anchored 1x2 at α = 0.0000. `residual_test_ou.py`
(2026-09-16) closed O/U the same way: α = 0.0000 on every arm and every line,
model AUC 0.5796 against the market's 0.6011, residual AUC **0.4429 — below
0.5**, meaning the market wins the disagreements.

Two zeros from one shared feature set is not two independent results. It is one
result about the feature set. Hence this audit.

---

## 1. What the model actually runs on

91 columns exist in `match_feature_vectors`. On 30,890 finished matches in the
last 90 days, this is the population:

| band | count | columns |
|---|---|---|
| **≥90%** | 15 | and 6 of those are targets/metadata (`match_outcome`, `total_goals`, `over_25`, `match_date`, `built_at`, `lineup_confirmed`) |
| 50–90% | 28 | market prices, league position, form |
| **<50%** | 48 | injuries, weather, referee, xG, player ratings, every drift/steam feature |

**The nine real predictors at ≥88%:**
`elo_home`, `elo_away`, `elo_diff`, `form_ppg_home`, `form_ppg_away`,
`form_momentum_home`, `form_momentum_away`, `rest_days_home`, `rest_days_away`,
plus `season_progress` and `league_tier` as context.

That is Elo + recent form + rest. It is a defensible 1998 model. It is not a
model that beats a closing line in 2026.

**The near-empty columns the model nominally has** (last 90 days):

```
ensemble_prob_draw/away   0.0%     injury_severity_home/away  0.0%
overnight_line_move       0.0%     injury_count_home/away     1.9%
news_impact_score         1.9%     injury_severity_score     ~2.5%
xg_overperf_home/away    ~4.2%     team_avg_player_rating    ~6.7-9.9%
referee_over25_pct        7.7%     referee_cards_avg          7.4%
weather_* (4 cols)        8.6%     h2h_win_pct               30.7%
pinnacle_implied_over25  48.1%     market_implied_home       53.0%
```

An XGBoost fed a column that is 96% missing does not learn "unknown". It learns
the missingness pattern, which is a proxy for league tier and coverage, not for
football.

### 1a. A defect found while auditing — three features fed as zero

`pinnacle_implied_home` / `_draw` / `_away` are in the model's `feature_cols.pkl`
but **have no column in `match_feature_vectors`**. They live in `match_signals`
(`train.py:581` builds them from `odds_snapshots`; `daily_pipeline_v2` writes
them as signals). Any evaluation reading only mfv resolves them to `None`, and
`build_X` turns `None` into `0.0` — so the model is silently handicapped on the
three features carrying the market's own 1x2 price.

**RESOLVED 2026-09-16 — both tests fixed and re-run.** Same universe each time
(O/U n=7,273; 1x2 n=7,775, identical base rate and overround), so the only thing
that changed is what the model was fed:

| test | model AUC before → after | residual AUC before → after | α |
|---|---|---|---|
| 1x2 (deciding arm) | 0.6437 → **0.6632** | 0.3791 → 0.4189 | 0.0000 both |
| O/U (deciding arm) | 0.5788 → 0.5796 | 0.4443 → 0.4429 | 0.0000 both |

The defect was real and material for 1x2 — **+0.0195 AUC** from three columns —
and changed **neither verdict**. That is precisely why it could have sat there
indefinitely: a silently handicapped model that still FAILS looks identical to a
fair model that fails. Both closures now rest on a fair test.

It also tells us something useful: **the model can use market features when it is
given them.** That is evidence about the feature pipeline, not about the model
family — and it is the strongest single argument for the rest of this audit.

---

## 1b. The feature contract — and can the gaps be filled?

`scripts/model_feature_contract_audit.py` (2026-09-16) runs the **actual
production code path** (`_build_row_from_mfv`) over a sample of real matches and
compares, **per row**, what the model received against what the sources hold.
Re-run it before adding any feature; it exits non-zero on a finding.

**It found exactly one wiring bug, and it was live.** `pinnacle_implied_home` /
`_draw` / `_away` were zero-filled on **132 of 200** sampled matches where the
signal existed, with their `_missing` indicators pinned to 1 on **100%** —
asserting "no market price" on every match since the v10 schema. Fixed: the row
builder now fills any declared feature the mfv row lacks from `match_signals`,
driven by what is missing rather than a hardcoded list. Audit is now clean.

> Two earlier versions of the audit were **wrong**, and the reason is worth
> keeping: "zero on every sampled match" is not evidence. The sample is the most
> RECENT matches, which is exactly where a backfilled-later column is legitimately
> empty. v1 called four features dead on that basis; v2 cross-checked against each
> column's non-zero count over the window and still called them dead, because an
> aggregate says the data exists *somewhere*, not for *these rows*. Only the
> per-row comparison separates a wiring bug from a coverage gap.

### Can the coverage gaps be filled?

| feature | now | ceiling with data we ALREADY hold | verdict |
|---|---|---|---|
| `line_velocity` | 32.5% | **52.1%** — matches with ≥3 Pinnacle 1x2 snapshots | **FILLABLE, ~20pp.** The computation is not running on everything eligible. Cheapest win here. Hard ceiling is Pinnacle coverage (55.4%). |
| `league_clv_efficiency` | 41.4% | a per-league aggregate — applies to any match in a league we have CLV for | **LIKELY FILLABLE** — needs a check of which leagues it skips and why. |
| `xg_overperf_home/away` | 4.0% | ~15% (xG present on 46% of the 32.6% of matches that have `match_stats`) | **BARELY.** Even fully exploited it reaches ~15%. Real fix is xG procurement (Tier D). |
| `injury_severity_score_*` | 2.5% | `match_injuries` covers **0.8%** of finished matches | **NOT FILLABLE.** The source is empty. Any "key player out" feature is currently fiction. |
| `team_avg_player_rating_*` | 12.8% | `match_player_stats` covers **4.7%** | **NOT FILLABLE** from what we hold. |

Note `injury_severity_home` / `_away` are written by nothing at all (0% in
`match_signals`) — but they are **not** in the model's `feature_cols`, so they
cost nothing today. `injury_severity_score_*`, which IS a model feature, is the
one at 2.5%.

**The propagate job is healthy.** `mfv_v3_signals_propagate` (23:30) moves
`match_signals` → mfv columns and the two match everywhere checked — so every
gap above is upstream (the signal was never computed) or a genuine data gap, not
a lost hand-off. That was worth confirming rather than assuming: the same job's
absence caused `season_progress` to sit at 0% for 90 days in June 2026.

---

## 1c. What we never even listed — the outward-looking pass

§1a and §1b were **inward-looking**: they compared what the model declares
against what it receives. That is a closed loop, and it structurally cannot find
an input nobody ever thought to add. The owner asked the right follow-up — *"are
these only features the model needs or is missing, or are there things we
haven't even listed, never collected, that would help?"* — so this section looks
the other way.

### Category 1 — we compute it, store it, and throw it away

**50 of the 90 signals written in the last 60 days are not in the model's
feature list at all.** Several have *better* coverage than any feature it has:

| signal | coverage | why it matters |
|---|---|---|
| `league_over25_pct` | **75.1%** | the league's own over-2.5 base rate — the single most obvious O/U prior, and the O/U model does not have it |
| `league_avg_goals` | **75.1%** | same, in goals rather than a rate |
| `league_btts_pct` | **75.1%** | |
| `rest_days_norm_home/away` | **83%** | rest normalised for league schedule; the model uses only raw `rest_days` |
| `league_elo_range` / `_variance` | 77.4% | competitive balance — a tight league produces different scorelines |
| `away_team_turf_games_ytd` | 87.4% | |
| `form_slope_home/away` | 58.8% | direction of form; the model has only level (`form_ppg`) and `form_momentum` |
| `fixture_urgency_home/away` | 59.6% | |
| `odds_volatility` | 59.0% | |
| `market_implied_home/draw/away` | 51.4% | the broad-market consensus — the model sees only Pinnacle |
| `ah_bookmaker_disagreement` | 51.4% | |
| `sharp_consensus_*` | 36.2% | |
| `h2h_avg_goal_diff` | 41.0% | |
| `squad_disruption_home/away` | ~22% | |

For contrast: the model's own best real features sit near 90% and **48 of its 52
are under 50%**. We are feeding it sparse columns while withholding denser ones
we already have on disk.

This is the cheapest finding in the whole audit — no collection, no
procurement, no new pipeline. It is a feature-list change.

#### Which of the 50 are actually worth adding (screened 2026-09-16)

`scripts/candidate_signal_screen.py` ranks every unused signal by coverage,
univariate rank-AUC against **both** targets, and correlation against the
features the model already has. 400 days, 78,358 settled matches, values read
only from captures **before kickoff**.

Univariate screening cannot see interactions, so this decides what to TRY, not
what works — the residual harness still decides that.

**Worth adding — goals side:**

| signal | n | AUC over-2.5 | max \|r\| vs existing |
|---|---|---|---|
| `league_avg_goals` | 32,713 | **0.6165** | 0.70 (`pinnacle_implied_over25`) |
| `league_over25_pct` | 32,610 | **0.6127** | 0.69 |
| `league_btts_pct` | 32,620 | 0.5803 | 0.60 |

For scale: the O/U head's own AUC is **0.5796** and Pinnacle's is **0.6011**.
A single league base-rate column out-ranks our model. It is not leakage — the
pre-kickoff bound moved the number by 0.0000 — it is simply that leagues differ
enormously in scoring rate and the O/U head was never told which league it is
looking at beyond `league_tier`.

**Worth adding — outcome side:**

| signal | n | AUC home-win | max \|r\| vs existing |
|---|---|---|---|
| `pinnacle_ah_line_move` | 20,542 | **0.6207** | 0.62 (`opening_implied_home`) |
| `h2h_avg_goal_diff` | 21,979 | 0.6215 | 0.78 (`h2h_win_pct`) |
| `pinnacle_ah_line` | 20,571 | 0.3527 (inverted 0.647) | 0.71 |

`pinnacle_ah_line_move` is the pick of these: the strongest association that is
not mostly a restatement of something we already feed.

**Rejected on evidence, including three I proposed before measuring:**

| signal | why |
|---|---|
| `rest_days_norm_home/away` | AUC 0.4997 / 0.5021 — **no univariate signal**, despite the 83% coverage that made me highlight it |
| `away_team_turf_games_ytd` | AUC 0.5043 at 53% coverage — nothing |
| `form_slope_home/away` | 0.4986 / 0.5006 — nothing, and r≈0.47 with `form_momentum` anyway |
| `market_implied_home/draw/away` | r = **0.94–0.98** with `opening_implied_*` — the same number under another name |
| `league_draw_pct` | r = 0.87 with `league_draw_rate_ytd`, already in the model |
| `squad_disruption_*` | 9% coverage, AUC 0.50 |

That is the value of screening before adding: three of the candidates I named in
§1c from coverage alone carry no signal at all, and the densest of them
(`away_team_turf_games_ytd`, 87.4%) is worthless. Coverage told us what the model
COULD read; only this tells us what is worth reading.

#### The A/B result — the screen was right about association and wrong about usefulness

Two bundles trained on the identical 156,746 matches with the identical cutoff
(2026-08-20) and identical flags, differing **only** in `--include-signals`
(67 vs 75 columns — the four features plus their `_missing` indicators). Both
scored through the same residual harness that produced the shipped α = 0.0000.

**Deciding (REALISTIC) arm:**

| market | metric | control | +4 signals |
|---|---|---|---|
| O/U 2.5 | model AUC | **0.5851** | **0.5620** |
| | α | 0.0150 | **0.0000** |
| | residual AUC | 0.4711 | 0.4254 |
| 1x2 | model AUC | **0.6571** | **0.6711** |
| | α | 0.0000 | 0.0000 |
| | residual AUC | 0.4208 | 0.4488 |

**Neither passes.** The bar is blend log-loss < market **and** α > 0.02; α is
0.0000 on the treatment in both markets, and the market's AUC (0.6028 / 0.7004)
is untouched.

But the two markets moved in **opposite directions**, and that is the finding:

* The three `league_*` features — chosen *for* O/U on univariate AUC 0.6165 —
  made O/U **worse** (−0.023 AUC, α from 0.0150 to 0).
* `pinnacle_ah_line_move` — chosen for 1x2 — moved 1x2 the right way (+0.014
  AUC, residual AUC 0.4208 → 0.4488, closer to the 0.5 of no-information).

**Why the O/U features backfired — the leading hypothesis.** The signals cover
**22.6% of the training rows but ~42% of the evaluation window**. A gradient
booster handed a column that is absent on three quarters of its training data
learns to split on the `_missing` indicator, which encodes *era and coverage*
rather than football. When coverage shifts between train and serve, that learned
rule misfires. This is the same failure the audit's headline describes — sparse
columns teaching the missingness pattern — arriving from the other direction.

**What it settles.** Univariate association does not survive contact with the
fitted model: `league_avg_goals` out-ranks our O/U head standalone (0.6165 vs
0.5796) and still degrades it when added. The screen ranks what to TRY; only the
harness decides, and here it said no.

**What it supports.** The same four features help one target and hurt the other.
That is direct empirical support for `SPLIT-FEATURE-SETS-1X2-VS-GOALS` — a
single shared feature set cannot be right for both heads, and this is the first
measurement showing it rather than arguing it.

**Not shipped.** `FEATURE_COLS` is unchanged for production; the block is opt-in
behind `--include-signals` and both A/B bundles are experimental. Re-try only
with the coverage gap closed (backfill the league aggregates historically) or
inside a goals-specific feature set.

#### A reproducibility hazard found while checking the A/B

The shipped `v20260914_clean_cut0820` bundle scored α = 0.0000 on O/U; my
`ab_control_cut0820` — **identical flags, identical cutoff** — scored 0.0150.
Chased rather than waved off, because an unexplained difference between two runs
that should agree is exactly where a bug hides.

It is not a bug. **11.6% of pre-cutoff feature rows were rebuilt between the two
training dates.** A `--cutoff` bounds match DATE, not feature COMPLETENESS, so
the nightly `mfv_v3_signals_propagate` and friends keep changing history under a
fixed cutoff.

Three consequences worth carrying:

* **Training is not reproducible across days**, even with an identical cutoff and
  identical flags. Anyone re-running a past experiment will get a different
  bundle and should not treat the difference as a finding.
* **α = 0.0000 and α = 0.0150 are the same number**, separated by two days of
  backfill. That reinforces the verdict rather than complicating it: neither is
  distinguishable from zero.
* **A matched control is mandatory, not tidiness.** Comparing a new bundle
  against a previously-shipped one measures backfill as much as it measures the
  change. The A/B above is valid only because control and treatment were trained
  four minutes apart.

### Category 2 — we collect the raw data and have never made a signal from it

Tables the feature pipeline has never read. It reads only `matches`,
`odds_snapshots`, `match_signals`, `predictions` and `match_feature_vectors`;
these appear solely in their own fetchers:

| table | size | what it would give |
|---|---|---|
| `match_events` | **1,839,736 rows / 81% of matches** | game-state scoring, goal timing, red-card timing — Tier B of §3 |
| `team_transfers` | **1,427,923 rows** | squad churn and continuity |
| `team_coaches` | **60,532 rows** | manager tenure and regime change. `manager_change_*_days` exists as a signal but reaches only ~200 matches — the raw table is far richer |
| `venues` | 2,858 rows, **2,658 with lat/lon** | travel distance, altitude, capacity, surface. All derivable with a great-circle formula and no new data |
| `player_sidelined` | 12,285 rows | |

### Category 3 — genuinely never collected

Only this category costs money, and it should be argued for on evidence rather
than assumed:

- **xG at scale.** ~15% today. The standard modern goals input.
- **Pre-match starting XI.** `match_player_stats` is at 4.7%; a "key player out"
  feature is currently fiction.
- **Shot-level / possession-chain data.** Never held in any form.
- **Player-level injury feeds.** `match_injuries` covers 0.8%.

### Order this changes

Category 1 is nearly free and should go **before** any procurement argument.
Category 2 is derivation work on data already paid for. Category 3 is the only
one that needs a purchase decision, and the Dixon-Coles result should inform it.

---

## 2. What we have as raw material

| source | coverage of finished matches | what it gives |
|---|---|---|
| `matches` final scores | **171,494 / 100%** (113,676 in last 2y) | goals for/against, results — everything a goals model needs |
| `odds_snapshots` | 45.2M rows | market prices, 13 books, movement |
| `match_events` | **138,971 / 81.0%** | 345,418 goals **with minute stamps**, 452,896 yellows, 30,356 reds, 947,552 subs |
| `team_elo_daily` | 253,648 rows | already used |
| `league_standings` | 212,377 rows | position, points (only 60% reaches mfv) |
| `match_stats` | 55,879 / **32.6%** | shots 94%, SoT 95%, corners 96%, possession 94% *within covered rows*; **xG only 46% within** → ~15% overall |
| `match_weather` | 23,621 / 13.8% | |
| `match_player_stats` | 8,047 / **4.7%** | |
| `referee_stats` | 5,705 rows | |
| `match_injuries` | 1,432 / **0.8%** | effectively nothing |

**The asymmetry that decides the strategy:** final scores are at 100% and goal
timings at 81%, while xG is at ~15% and injuries at ~1%. Any model that depends
on the sparse tables inherits their coverage. Any model built on goals runs on
the whole corpus.

---

## 3. What we can derive from what we already hold

Ordered by (coverage × expected value), highest first. **None of these need new
data collection.**

### Tier A — buildable at ~100% coverage, from final scores alone

1. **Dixon-Coles attack/defence strengths.** Per-team attack and defence
   parameters with a low-score correlation correction and exponential time decay.
   Needs only (home, away, score, date) — 171k matches. This is the canonical
   goals model and it is the single biggest gap: we have never had one, despite
   `MODEL_WHITEPAPER` §5.1 claiming goal-line markets are weighted higher
   *because* of "the Poisson/Dixon-Coles model" (banner-corrected 2026-09-16).
2. **Opponent-adjusted goal rates.** Current `goals_for_avg_*` is a raw average
   at 81% — it does not know whether those goals came against the champion or
   the bottom club. Adjusting by opponent defence strength is free.
3. **Home/away split strengths.** Teams differ in home advantage; one global
   constant loses that.
4. **Score-line distribution features** — draw propensity, clean-sheet rate,
   both-teams-score rate, computed from the same 171k.

### Tier B — buildable at ~81%, from `match_events` goal minutes

5. **Game-state scoring rates.** Goals scored/conceded while leading, level, or
   trailing. This is the highest-value O/U feature we could construct and we
   have the raw data: a team that collapses when chasing produces a different
   total than its average suggests.
6. **First-half vs second-half scoring profile** per team — directly relevant to
   the 1H markets we already collect and do not model.
7. **Late-goal propensity** (75'+) — matters for both O/U and in-play.
8. **Red-card timing effects** — 30,356 reds with minutes; a red at 20' and one
   at 88' have opposite consequences and are currently one number.

### Tier C — buildable at ~33%, from `match_stats`

9. **Shot-based strength (SoT, conversion).** Lower variance than goals, which
   is why it beats goals in-sample. But at 33% coverage it can only be a
   *secondary* model for the leagues we cover, never the primary.
10. **Corner and card rates** — for the corner/card markets we now collect at
    Unibet-Site but have never modelled.

### Tier D — needs new data, and should be justified before anyone buys it

11. **xG at full coverage.** Currently ~15%. This is the standard modern input
    and we effectively do not have it.
12. **Lineups / player availability.** `match_injuries` is at 0.8%;
    `match_player_stats` at 4.7%. Any "key player out" feature is currently
    fiction.
13. **Referee tendencies at scale** — 7.7% today.

---

## 4. What I would actually do, in order

| # | step | why it is first | cost |
|---|---|---|---|
| 1 | **Dixon-Coles baseline for O/U**, scored through `residual_test_ou.py` | Same harness, directly comparable α, 100% coverage, no new data. If this also returns α = 0, the question is closed on evidence instead of on a broken calibrator. | ~2-3 days |
| 2 | **Re-run `residual_test.py` with the three market features joined** | The 1x2 α = 0 was measured with the model handicapped. Cheap, and it decides whether step 4 is worth anything. | ~2 hours |
| 3 | **Game-state and goal-timing features from `match_events`** | The best O/U signal we can build from data already held, at 81%. | ~3 days |
| 4 | **Split the feature sets.** One set for 1x2, one for goals. | Two heads sharing one outcome-engineered feature set is the structural defect behind both zeros. | ~2 days |
| 5 | Decide on xG procurement — **only if 1-4 show a non-zero α** | Otherwise we would be buying data for a model family that has not demonstrated it can use it. | — |

**The bar does not move.** Every one of these still has to beat a closing line
whose AUC is 0.601 at ~6.5% vig. Two α = 0 results say our current model does
not. They do not say the market is beatable — nothing here promises that, and no
step above should be sold internally as if it did.

---

## 5. What this audit does NOT claim

- It does not claim a better model exists. It claims the current one has not
  been given a fair chance, which is a different and weaker statement.
- It does not claim xG would fix it. Coverage is the reason we cannot know.
- It does not reopen automated betting. That is closed on market access
  (`OWN_PATH_VERDICT_2026_09_14`), and no model quality changes the 5.66%
  overround we would have to pay.
