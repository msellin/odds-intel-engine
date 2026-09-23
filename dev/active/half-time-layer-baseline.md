# #084 HALF-TIME LAYER — the BASELINE, measured before any change

**Captured 2026-09-23, on the owner's instruction: *"measure the starting point
and then after it finishes we can see if and how much it helped"*.**

Everything below is the state of the world BEFORE a single half-time feature
exists. Re-run the identical commands after the work and compare only against
this file — not against memory, and not against a number quoted in a commit
message.

---

## 1. The α baseline — the two numbers that decide whether this helped

Produced by `scripts/residual_test.py` and `scripts/residual_test_ou.py`, bundle
`v20260914_clean_cut0820`, cutoff 2026-08-20, **REALISTIC arm (the one that
decides)**:

| | market log-loss | market AUC | model log-loss | model AUC | **α** | residual AUC |
|---|---|---|---|---|---|---|
| **1x2** | **0.6195** | **0.7041** | 0.6471 | 0.6608 | **0.0000** | 0.4303 |
| **O/U 2.5** | **0.6737** | **0.6038** | 0.6789 | 0.5825 | **0.0000** | 0.4382 |

Shin-de-vig robustness arm: 1x2 market 0.6186 / residual 0.4138; O/U market
0.6738 / residual 0.4317. Both FAIL on both arms.

**Read the residual AUC, not just α.** Both are **below 0.5** — where our model
disagrees with the market, the market is right more often than chance. That is a
harder starting point than α = 0 alone suggests, and any claim of improvement
must move this number toward 0.5 as well.

**What "it helped" would mean, pre-committed:**
* **Real:** α > 0.02 AND blend log-loss < market log-loss on the realistic arm.
* **Directional but not a pass:** residual AUC rises toward 0.50 while α stays 0.
* **No effect:** α stays 0.0000 and residual AUC does not move. ← the expected outcome

## 2. The data baseline — what exists before the work

| asset | count | % of 175,450 finished |
|---|---|---|
| `ht_score_home/away` | **172,439** | **98.28%** |
| `h2_score_home/away` | 172,436 | 98.28% |
| Half-time-derived FEATURES in `match_feature_vectors` | **0** | — |
| `h2_score_*` SELECTs anywhere in the codebase | **0** | — |
| 1H markets in `predictions` | **0** | — |

## 3. The 1H market baseline — what we would be pricing into

`1x2_1h` odds are stored on ~9,226 matches (thin, because
`prune_odds_snapshots` is aggressive) against an OUTCOME side 141,764 matches
deep. **So the model trains on plenty and the backtest window is short** — state
that up front rather than discovering it at review.

Pinnacle-priced 1H markets, last 90 days: `team_total_1h_home_05` 6,863 matches ·
`team_total_1h_away_05` 6,607 · `1x2_1h` 3,439 · `corners_1h_ou_45` 3,025.

## 4. Confounds that will contaminate a naive before/after

⚠️ **Do not compare a new model against the OLD model.** Two things move
underneath this measurement while the work is in progress:

1. **The #078/#081/#088 backfills are running right now.** Lineups went
   11,359 → 25,000+, referee 98,211 → ~100,000, assists 0 → climbing, and the
   `match_stats` shot-location columns go 0 → ~54,000. Feature FILL RATES will
   change under us, and a feature's `_missing` indicator changes meaning when its
   fill changes (gotcha §39 in shape).
2. **A retrain is required for any of this to matter** — the production bundle
   was fitted on the sparse version.

**Therefore the only valid comparison is new-model-vs-MARKET, using the market
columns in the table above as the fixed reference.** The market numbers
(0.6195 / 0.7041 and 0.6737 / 0.6038) are the anchor; our own old model is not.

## 5. Reproduce exactly

```bash
python3 scripts/residual_test.py
python3 scripts/residual_test_ou.py --line 25
python3 scripts/data_coverage_report.py --days 180
```

---

# PROBE RESULT (2026-09-23) — the deciding arm FAILS, and one arm passing is a caution, not a finding

`scripts/probe_half_time_alpha.py`, half-time ratings scored standalone through
`residual_test_ou`'s own imported functions.

| arm | α | market LL | model LL | blend LL | vs market | residual AUC | verdict |
|---|---|---|---|---|---|---|---|
| **de-vig Pinnacle — DECIDES** | **0.0050** | 0.6721 | 0.6870 | 0.6721 | +0.002% | **0.4086** | **FAIL** |
| Shin de-vig — robustness | 0.0600 | 0.6722 | 0.6870 | 0.6719 | +0.038% | 0.4076 | *PASS* |

## The verdict is FAIL, and here is why the PASS must not be promoted

**The pre-registration names the realistic de-vig arm as the decider.** The Shin
arm is a robustness check. Reading the arm that gave the nicer answer is exactly
the cherry-picking the two-arm design exists to prevent, and it would be the same
error as quoting a raw CLV because it looks better than the margin-corrected one.

Three further reasons the PASS is not real:

1. **The improvement is +0.038% of log-loss.** Three hundredths of one percent.
2. **The two arms disagree on identical data.** They differ only in how the
   overround is removed — proportional vs Shin. If a result flips between them,
   the effect is smaller than the uncertainty in the de-vig method itself. That
   is a definition of noise, not a robustness pass.
3. **Residual AUC is 0.4086 and 0.4076 — on BOTH arms.** Where this model
   disagrees with the market, the market is right roughly 59% of the time. A
   model carrying genuine extra information would push that toward 0.50. It is
   the single most diagnostic number here and it is unambiguous.

## What IS worth recording

* It is the **first non-zero α** this project has produced on any arm. Given four
  prior α = 0.0000 results, "0.0050 on the decider, 0.06 on the robustness arm"
  is at least a different shape of failure. Worth noting, worth not
  over-reading.
* The model alone is **AUC 0.5425 against the market's 0.6054** — weaker than the
  existing 52-feature model (0.5825) despite using two inputs. Consistent with
  the correlation dipstick: our rating predicts the realised total at r = 0.111
  where the market manages r = 0.237 on the same matches.

## What this does NOT settle, and why the MFV build still runs

A standalone model failing does **not** mean the feature is worthless as one
input among many — that exact distinction nearly produced a wrong conclusion in
[[#077]]. The MFV build and the A/B proceed as planned; this probe only says the
half-time ratings cannot carry a model on their own, which was the cheap question.

And it says **nothing at all about the 1H markets** (`1x2_1h`,
`team_total_1h_*`, `corners_1h_*`), where we currently price nothing. A model
that is mediocre at full-time totals may still be the only model in a market we
do not contest. That needs its own test against those markets' own prices.

---

# CORRECTION (2026-09-23) — the first probe tested a weaker construction than the one shipped

The probe above was re-run after the MFV population exposed a divergence.

| | first probe (frozen fit) | corrected (walk-forward) |
|---|---|---|
| α, de-vig — **DECIDES** | 0.0050 | **0.0100** |
| model AUC | 0.5425 | **0.5553** |
| residual AUC | 0.4086 | 0.4107 |
| α, Shin (robustness) | 0.0600 | 0.0650 |
| **verdict** | **FAIL** | **FAIL** |

**What went wrong.** The probe fitted the ratings once at the cutoff and
predicted every later match from that frozen state. `populate_half_time_features`
does not: it updates after each match, so a fixture is scored by a rating holding
everything up to the day before. Both imported the same `HalfRatings` class —
**sharing a class is not sharing a construction.**

Measured on identical post-cutoff fixtures: frozen **r = +0.1117**, walk-forward
**r = +0.2378**, against the market's **+0.2371** on the same window.

**How it surfaced, and it was luck.** The leak audit printed r = 0.2580 against
realised totals — suspiciously close to the market's own figure. Checking it for
the wrong reason (suspected leak) found a different bug. Had walk-forward been
slightly *worse* than frozen, nothing would have flagged it.

**The verdict survived the correction. It did not have to.** Guard added:
smoke `HALF-TIME-ONE-CONSTRUCTION`; pattern recorded as RELIABILITY_LEDGER #23.

---

# A/B RESULT (2026-09-23) — FAIL, and the two arms of the harness disagree about the sign

Two bundles trained in the same minute on the same 175k rows, differing by exactly
the five migration-378 columns. n = 9,127 out-of-sample fixtures.

| harness arm | | model log-loss | model AUC | residual AUC | α |
|---|---|---|---|---|---|
| OPTIMISTIC | A (52 feat) | 0.6791 | 0.5822 | 0.4536 | **0.0000** |
| OPTIMISTIC | B (57 feat) | **0.6783** | **0.5873** | **0.4552** | **0.0000** |
| **REALISTIC — DECIDES** | **A (52 feat)** | **0.6805** | **0.5785** | **0.4472** | **0.0000** |
| **REALISTIC — DECIDES** | **B (57 feat)** | **0.6809** | **0.5758** | **0.4423** | **0.0000** |

*market: log-loss 0.6737, AUC 0.6038*

## The verdict: FAIL, and the honest reading is NO EFFECT

**α = 0.0000 on all four cells.** The optimiser, free to choose any weight in
[0,1], puts nothing on either model whether or not it has half-time features.
That is the pre-registered criterion and it is unambiguous.

⚠️ **And the two harness arms disagree about the SIGN.** On the optimistic arm B
looks better (log-loss −0.0008, AUC +0.005); on the deciding arm B is **worse**
(log-loss +0.0004, AUC −0.003). Same bundles, same fixtures, opposite direction.

**That disagreement is the finding, not a puzzle to resolve in B's favour.** A
difference that flips sign between two arms of the same harness is smaller than
the harness's own methodological variation — the identical logic that made the
probe's Shin-arm "PASS" inadmissible. The effect is inside noise, and the correct
summary is *no effect*, not *a small improvement*.

**Recorded because I got this wrong in the moment:** on seeing the optimistic arm
alone I reported "Arm B is a genuinely better model." The deciding arm says the
opposite. The pre-registration names the realistic arm as the decider precisely
so this cannot be chosen after the fact.

## What this closes, and what it does not

**Closes:** half-time features do not help the existing 52-feature vector on
O/U 2.5. The probe said they cannot carry a model alone; the A/B says they do not
help as one input among many either. Fifth consecutive α = 0.

**Does NOT close:**
* **The 1H MARKETS themselves** — `1x2_1h` (3,439 Pinnacle matches),
  `team_total_1h_*` (~6,800 each), `corners_1h_*`. We price none of them. A model
  mediocre at full-time totals may still be the only model in an uncontested
  market, and nothing here has tested that.
* **A correctly SIZED vector.** Both arms carry 52+ features of which 15 are
  under 20% filled. The literature's sharpest finding — a two-number rating
  beating a 40-feature set 0.2085 to 0.2416 RPS — says the informative experiment
  is a SMALL market-shaped vector, which is [[#089]] arms C-H and is explicitly
  not what was tested here.
