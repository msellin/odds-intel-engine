# OWN segment & signal search — where does the soft book misprice, and does anything we store predict it?

**Date:** 2026-09-14 · **Direction:** 🤖 OWN — every number here is about what we
should stake at Coolbet / Epicbet / Unibet-Site. Nothing in it is publishable to
customers, and nothing in it changes a published figure.

**Scripts (all committed):**
`scripts/own_book_clv_universe.py` · `scripts/own_segment_signal_search.py` ·
`scripts/own_anchor_placebo.py` · `scripts/own_anchor_gate_calibration.py` ·
`scripts/own_ledger_segment_check.py` · `scripts/own_signal_residual_test.py`
**Smoke:** `OWN-SEGMENT-SEARCH-METHOD-GUARDS`

---

## The one-paragraph answer

No segment of these three books is careless enough to be bet. Across **899
segment cells** at four decision lead times, **zero** reached break-even, and the
whole descriptive spread — women's football, youth and reserve sides, cup ties,
obscure leagues, 04:00 kickoffs, longshots, unfamiliar teams — fits inside
**3pp of a −7.4% baseline** that is **7.4pp** from placeable. Of **27 stored
signals** tested against the own-book price, **zero** add anything out of sample.
The one thing that *is* real is the sharp anchor: margin-corrected own-book CLV
rises monotonically in the Pinnacle prob-edge with slope **+1.31 (t=4.9)**, and a
gate-matched placebo built to reproduce every mechanical route from the same soft
price returns slope **+0.007 (t=0.22)** — so the ladder is information, not
arithmetic. But it crosses zero at a prob-edge of **+6.0%** once the top 1% of
price moves is trimmed, not the **+2%** the live instrument gates at, and that
gate admits ~3 legs/day. **INSTRUMENT, not BUILD.**

---

## 0. What is being measured, and the two things that bound it

Target throughout, per the brief and `dev/active/own-sharp-tight-preregistration.md`:

```
mc_clv = (1 + clv) / (1 + m) - 1
  clv = odds_at_decision / odds_at_close_AT_THE_SAME_BOOK - 1     (raw price ratio)
  m   = that book's OWN overround on that fixture+market, PER ROW
```

`m` is re-derived in `own_book_clv_universe.py` rather than imported, because
`settlement.closing_book_margin()` costs one SQL round trip per leg. The
definition is copied exactly and pinned by smoke test: close leg = the book's
last pre-kickoff non-live quote within `DIRECT_CLOSE_MAX_MIN` (60) minutes of
kickoff; margin legs = the book's last pre-kickoff quote per selection over the
full complement, no freshness bound; `m` rejected unless `0 ≤ m ≤ 0.5`.

### 0a. The binding constraint: own-book CLV is seven days deep, and that is structural

`prune_old_simple` keeps, after 7 days, at most `is_opening` + `is_closing` +
the latest pre-kickoff row per series — and until 2026-09-11 the direct-book
writers stamped almost no anchors (`ANALYSIS_GOTCHAS` 59: Epicbet 0.09%,
Coolbet 0.22%, Unibet-Site 0.00%). At our three books that leaves **exactly one
surviving pre-kickoff row per series** outside the window, and **that row is the
close**. Own-book CLV there is not noisy, it is **zero by construction**
(numerator = denominator). Measured rows-per-series confirms the cliff exactly:

| match day | Coolbet 1x2 rows/series | Epicbet | Unibet-Site |
|---|---|---|---|
| 2026-09-05 | **1.0** | 1.3 | — |
| 2026-09-06 | **1.0** | 1.3 | — |
| 2026-09-07 | 4.9 | 20.6 | — |
| 2026-09-11 | 8.2 | 23.0 | 13.6 |
| 2026-09-13 | 3.9 | 24.1 | 6.9 |

So the panel is **2026-09-07 … 2026-09-13, seven match days**, and every table
below prints that span. This is not a choice; it is the whole extent of the data.
It is also the single highest-value thing to change: see §6.

### 0b. The traps this search had to clear

| trap | how it is handled here |
|---|---|
| `clv` is a raw ratio, break-even is the book's margin | per-row `m`, never an average |
| `closing_bookmaker IS NULL` reads 4–10pp high | panel is own-book by construction; the ledger arm filters `closing_bookmaker IS NOT NULL` |
| never join books on exact timestamp (§63) | no cross-book join anywhere: every book's complement is assembled **within** the book |
| `is_live=false` is not pre-kickoff (§37) | every quote bounded `timestamp <= m.date`; Pinnacle additionally `minutes_to_kickoff > 0` |
| leakage | the target **never touches the outcome** — CLV is an outcome-free quantity, so a result cannot leak into it. The outcome-based arm (§4) carries an explicit noise control, a saturated positive control, and a post-hoc canary |
| phantom feeds | `Unibet` (AF), `Unibet-Kambi`, `Max`, `Avg`, `Betfair Exchange`, `BetWin`, `Betfred` never enter; books are the three self-scraped ones only |
| `shadow_bets` duplicates 8.43× | the ledger arm reads `shadow_bets_unique` only |
| date span beside every n | printed by every script, reproduced in every table |
| multiple comparisons | see §5 — cells counted, SEs clustered on fixture, time-ordered holdout, BH-FDR, per-day folds, power, and a gate-matched placebo |
| n<100 is not a finding | flagged inline everywhere it applies |

**Known residual caveat:** `ANALYSIS_GOTCHAS` 21 records that some Coolbet odds
rows are attached to the wrong fixture date. Every lead-time and freshness bound
here is computed from `matches.date`, so a mis-dated fixture mis-places its own
legs. It affects a minority of Coolbet rows and cannot produce the Epicbet
finding in §3, which is a *between-book* contrast on the same fixtures.

### 0c. The panel

46,547 leg-rows = (fixture × book × market × selection × lead-time arm), 4,869
book-market series, 1,627–1,817 fixtures per arm. Markets `1x2` and
`over_under_25` (the only two all three books quote at volume). Decision arms at
1h / 3h / 6h / 12h / 24h before kickoff. Pinnacle Shin de-vig attached **as of
the same decision moment**, coverage 72.4%.

**The unselected baseline — a randomly chosen leg:**

| book | market | n | margin | raw clv | **mc_clv** |
|---|---|---|---|---|---|
| Coolbet | 1x2 | 1,344 | 7.51% | +0.00% | **−6.96%** |
| Coolbet | over_under_25 | 864 | 7.26% | −0.11% | **−6.85%** |
| Epicbet | 1x2 | 2,814 | 8.13% | −0.67% | **−8.10%** |
| Epicbet | over_under_25 | 1,732 | 6.69% | −0.24% | **−6.48%** |
| Unibet-Site | 1x2 | 2,481 | 8.80% | −0.28% | **−8.32%** |
| Unibet-Site | over_under_25 | 1,068 | 7.02% | +0.08% | **−6.48%** |

*(6h arm; 2026-09-07…09-13.)* Raw CLV is ≈0 at every book: **our books' prices
do not systematically drift in any direction.** The whole −7% is the vig. Pooled
6h baseline **−7.46% [−7.60, −7.31]**, n=10,303, 1,627 fixtures, sd 9.19pp.
mc_clv sd of 6–12pp (against ROI's ~130pp) is why n≈1,000 legs resolves a 1pp
difference here and 15,600 bets would be needed for the same question on ROI.

---

## 1. SEGMENTS — 899 cells, zero placeable · **DEAD (measured negative)**

Dimensions scanned: book, market, selection, odds band, favourite/dog, league
tier, country, kickoff-hour bucket, day of week, cup vs league, women's, youth/
reserve, team familiarity, travel band, rest days, injury load, lineup confirmed,
ELO gap, data tier, sharp-edge band — **plus every one of them crossed with the
sharp-edge band**. Cells with n≥100 only.

| lead arm | cells tested | baseline | **cells with 95% CI above zero** |
|---|---|---|---|
| 1h | 243 | −7.42% | **0** |
| 3h | 234 | −7.46% | **0** |
| 6h | 220 | −7.46% | **0** |
| 12h | 202 | −7.41% | **0** |
| **total** | **899** | | **0** |

The descriptive spread (3h arm, 2026-09-07…09-13, cluster-robust CIs):

| dimension | level | n | fx | mc_clv | 95% CI |
|---|---|---|---|---|---|
| womens | mens | 10,918 | 1,720 | −7.45% | [−7.58, −7.31] |
| womens | **womens** | 221 | 43 | **−8.08%** | [−9.14, −7.01] |
| youth_reserve | senior | 10,794 | 1,685 | −7.44% | [−7.58, −7.31] |
| youth_reserve | **youth/reserve** | 345 | 78 | **−7.91%** | [−9.19, −6.63] |
| competition | league | 10,237 | 1,616 | −7.43% | [−7.57, −7.30] |
| competition | **cup** | 902 | 147 | **−7.76%** | [−8.39, −7.13] |
| league_tier | tier1 | 6,638 | 1,063 | −7.29% | [−7.47, −7.10] |
| league_tier | tier3 | 989 | 153 | −7.59% | [−8.10, −7.09] |
| league_tier | tier5 | 101 | 11 | −7.32% | [−7.95, −6.69] |
| ko_hour | 00–08 UTC | 788 | 108 | −7.27% | [−7.68, −6.87] |
| ko_hour | 16–20 UTC | 3,919 | 629 | −7.16% | [−7.39, −6.92] |
| familiarity | fam<10 prior fixtures | 3,200 | 537 | −7.98% | [−8.24, −7.71] |
| familiarity | fam 50–200 | 4,325 | 622 | −6.58% | [−6.79, −6.37] |
| selection | home | 2,403 | 1,739 | −7.15% | [−7.69, −6.61] |
| selection | **draw** | 2,403 | 1,739 | **−8.31%** | [−8.59, −8.03] |
| selection | away | 2,403 | 1,739 | −8.42% | [−8.93, −7.90] |
| odds_band | ≥4.00 | 1,583 | 788 | −8.11% | [−9.05, −7.18] |
| odds_band | [2.00, 2.75) | 2,765 | 1,316 | −6.94% | [−7.18, −6.69] |
| book | Coolbet | 2,364 | 479 | −6.92% | [−7.15, −6.68] |
| book | Unibet-Site | 4,204 | 986 | −7.78% | [−7.96, −7.61] |
| country | Italy (best of top-12) | 370 | 56 | −5.81% | [−6.70, −4.92] |
| country | Czech-Republic (worst) | 256 | 53 | −8.88% | [−9.71, −8.06] |

**The extremes of that whole table are −4.76% and −8.88%.** Every one of the
"careless corner" hypotheses is not merely unproven, it is **measured and
negative with a tight CI**: women's football is *worse* than men's, youth and
reserve football is *worse* than senior, cup ties are *worse* than league,
unfamiliar teams are *worse* than familiar, low tiers are indistinguishable from
tier 1, and no kickoff hour differs by more than 0.8pp. These are well-powered
negatives, not "too few rows to say".

**On the §57 draw question** — draws are the *worst* 1x2 selection at our books
(−8.31%, tightest CI in the table). At a matched sharp gate (edge ≥2%) draws
give −0.25% on **n=16**, which is nothing. The sharp draw edge that §57 records
against de-vigged Pinnacle **does not survive the transfer to a soft book's own
closing price** at any sample we can currently measure. Marked **DEAD on the
unconditional cut, NOT YET MEASURABLE on the gated cut.**

---

## 2. THE SHARP ANCHOR IS NOT UNIFORM — it is monotone, and it is real · **INSTRUMENT**

The established result is that the anchor buys ~2pp uniformly. It does not. It
buys an amount that rises linearly in the prob-edge (1x2, 3h arm, 2026-09-07…09-13):

| Pinnacle Shin prob-edge | n | fx | mc_clv | 95% CI | mean odds |
|---|---|---|---|---|---|
| ≤ −10% | 40 | 39 | −15.82% | [−20.94, −10.70] | 2.10 |
| −10 … −5% | 473 | 383 | −11.75% | [−12.55, −10.96] | 2.55 |
| −5 … −2% | 3,219 | 1,148 | −8.45% | [−8.67, −8.23] | 3.27 |
| −2 … 0% | 1,404 | 797 | −5.76% | [−6.23, −5.29] | 3.90 |
| 0 … +1% | 147 | 138 | −3.73% | [−5.23, −2.23] | 3.26 |
| +1 … +2% | 86 | 79 | −1.92% | [−4.41, +0.58] | 3.95 |
| +2 … +3% | 42 | 39 | −0.66% | [−4.20, +2.88] | 3.23 |
| +3 … +5% | 38 | 37 | −0.74% | [−3.48, +1.99] | 2.59 |
| ≥ +5% | 26 | 24 | +13.37% | [−3.14, +29.88] | — |

`mc_clv = −4.10% + 1.314 × edge`, cluster-robust **t = +4.86**, n=5,475, 1,268 fixtures.

### The placebo says this is information, not arithmetic

`prob_edge` and `mc_clv` share `odds_soft(T)`. A leg quoted unusually long at T
gets a high prob-edge *and* tends to shorten toward its own close — which would
manufacture the entire ladder with no information at all. So the placebo arm
replaces Pinnacle's de-vig with **a real Pinnacle de-vig from a different
fixture, drawn within the same (market, selection, odds-decile) stratum**. Every
mechanical route from the decision odds survives that shuffle; only Pinnacle's
fixture-specific content is destroyed.

| arm | slope | t | intercept | zero crossing |
|---|---|---|---|---|
| **REAL** — Pinnacle Shin de-vig | **+1.314** | **+4.86** | −4.10% | +3.12% |
| **PLACEBO** — other fixture, odds-decile matched | **+0.007** | **+0.22** | −7.63% | +1096% |
| **SOFT** — the other self-scraped books' de-vig | +0.793 | +5.32 | −5.59% | +7.04% |

The placebo is **flat to three decimal places**, every band sitting at −7.6% to
−7.8%. The ladder is real. A second soft book also predicts, but with a worse
intercept and a break-even gate more than twice as far out, so **Pinnacle is
doing work no cheaper substitute does.**

### But the gate the operator is using is in the wrong place

The crossing point — not the slope — is what a gate is set from, and it is
**tail-driven**. `ANALYSIS_GOTCHAS` 9 applies with full force: the target is a
price *ratio* with a long right tail.

| trim (each side) | slope | intercept | **break-even gate** | predicted mc_clv at the live 2% gate |
|---|---|---|---|---|
| none | +1.314 | −4.10% | **+3.12%** | −1.47% |
| 1.0% | +0.886 | −5.31% | **+5.99%** | **−3.54%** |
| 2.5% | +0.801 | −5.56% | **+6.94%** | −3.95% |

Removing the single most extreme percent of price moves nearly doubles the
required gate. Realised numbers at the live configuration (edge ≥2%, odds ≤2.50,
n=49, 7.0 legs/day) agree and are sign-unstable in exactly the same way:
**+1.66%** untrimmed → **+0.47%** at 1% trim → **−0.45%** at 2.5% trim, CI
straddling zero throughout.

**Verdict: the live `bot_trigger_1x2_sharp_tight_v1` gate of 2% sits roughly
3–4pp below where own-book CLV actually breaks even.** It is not wrong in kind
— the instrument measures a real thing — it is loose.

### The ledger agrees, independently

`shadow_bets_unique`, own-book rows only (`closing_bookmaker IS NOT NULL`),
margin-corrected per row, cluster-robust, last 45 days — **24 bot × market
cells**. The two sharp-anchored 1x2 bots are the top two of all 24:

| bot / market | n | span | mc_clv | 95% CI |
|---|---|---|---|---|
| **bot_unibet_trigger_sharp_1x2_v1 / 1x2** | 72 | 09-11…09-14 | **+2.15%** | [−1.49, +5.78] |
| **bot_coolbet_trigger_sharp_1x2_v1 / 1x2** | 70 | 09-11…09-14 | **−2.98%** | [−4.85, −1.10] |
| bot_coolbet_value_v1 / over_under_35 | 83 | 08-27…09-08 | −2.98% | [−5.06, −0.89] |
| bot_sweep_ou35_v1 / over_under_35 | 138 | 08-26…09-08 | −3.82% | [−5.28, −2.37] |
| bot_coolbet_value_v1 / 1x2 | 354 | 08-26…09-08 | −3.91% | [−4.91, −2.91] |
| … 19 further cells | | | −4.3% to −7.0% | |
| bot_coolbet_trigger_1x2_v1 / 1x2 | 297 | 09-11…09-13 | −6.97% | [−7.49, −6.44] |

Pooled by book: Coolbet −4.97% (n=1,963), Epicbet −5.58% (n=883), Unibet-Site
−5.03% (n=1,020). **Nothing in the live fleet is placeable on this criterion,
and the only two cells near zero are the sharp-anchored ones** — the same
conclusion the panel reaches from an entirely unselected population.

This also **reconciles the pre-registration's −5.36% … −7.56%** with the panel's
≈0% at the nominal gate: those two numbers are not in conflict, they are
different lead times and different book mixes on the same rule. The ledger's
picks land at a median **6.3h** before kickoff with a p05–p95 of **0.24h–17.9h** —
a spread, not a lead-time arm — and the panel's book-by-book intercepts differ by
4pp (§3), so a pooled ledger number is a weighted average over an axis the panel
holds fixed.

---

## 3. SOFT-BOOK LAZINESS IS REAL — but it is Epicbet's *responsiveness*, not a careless corner

The one conditional structure that replicates. Slope of mc_clv on the sharp
prob-edge, per book, cluster-robust, both in the full panel and restricted to the
window all three books cover (a book × era confound otherwise, since Unibet-Site
starts 09-09):

| window | lead | Coolbet | Epicbet | Unibet-Site |
|---|---|---|---|---|
| 09-07…09-13 | 3h | +1.145 ±0.137 | **+0.578 ±0.233** | +2.062 ±0.391 |
| 09-07…09-13 | 6h | +1.558 ±0.181 | **+0.571 ±0.296** | +1.898 ±0.132 |
| 09-10…09-13 | 3h | +1.564 ±0.172 | **+0.657 ±0.291** | +2.168 ±0.368 |
| 09-10…09-13 | 6h | +1.971 ±0.199 | **+0.753 ±0.335** | +1.898 ±0.132 |

| contrast | t (four windows) |
|---|---|
| Unibet-Site − Epicbet | **+3.26, +4.10, +3.22, +3.18** |
| Coolbet − Epicbet | **+2.09, +2.85, +2.68, +3.13** |
| Unibet-Site − Coolbet | +2.22, +1.52, +1.49, **−0.30** |

**Epicbet's line responds to sharp disagreement at roughly half the rate of the
other two, in every window and at every lead.** Coolbet and Unibet-Site are
indistinguishable once the window is matched — the apparent Unibet-Site advantage
is the same tail effect as §2, and it disappears under a 1% trim (crossing moves
+1.06% → +3.95%).

Per-book break-even gates, 1% trimmed:

| book | slope | intercept | **break-even gate** | predicted mc_clv at 2% gate |
|---|---|---|---|---|
| Coolbet | +1.058 | −4.39% | **+4.15%** | −2.27% |
| **Epicbet** | **+0.444** | −6.48% | **+14.59%** | **−5.59%** |
| Unibet-Site | +1.196 | −4.72% | **+3.95%** | −2.33% |

This is a genuine mis-specification in the live instrument, which **pools all
three books under one gate**. It is not "Epicbet is careless" — it is the
opposite: Epicbet's price *doesn't move*, so there is no closing-line value to
collect there however right the anchor is. A sharp signal is worth roughly
**2.5× less** at Epicbet than at the other two.

⚠️ **This rests on one week.** Slope is a within-week estimate and the three
books' feeds are of very different ages. It is the strongest conditional result
in this search and it is still a single-era measurement.

---

## 4. SIGNALS — 27 tested against the own-book price, zero add out of sample

Benchmark: the **Shin de-vig of the closing price at Coolbet** — the strictest
own-book benchmark available, and the one that goes back furthest (2026-05-20 …
2026-09-14, **8,002** 1x2 fixtures and **7,480** O/U fixtures, time-ordered 65/35
split). If a signal cannot beat the close it cannot beat the earlier price we
would trade against, since the close is the more informative of the two.

Method per signal, as specified: fit `logit(p) = logit(p_market) + c·x` on TRAIN
with only `c` free (so `c` is by construction what the signal adds *on top of*
the price), then on untouched later fixtures build `p_aug` and fit the blend
weight α of `α·p_aug + (1−α)·p_market` with a **profile likelihood CI**.
`dLL_fixed` pins α=1 with `c` from train, so **nothing is fitted on the test
fixtures** — that is the number the verdict reads.

### 1x2 → home win (test log-loss of the market alone: 0.61807)

| signal | coverage | ĉ | α OOS | profile 95% CI | dLL_fixed |
|---|---|---|---|---|---|
| elo_diff | 96% | +0.073 | +0.018 | [−1.00, +1.16] | **−0.00052** |
| form_ppg_diff | 91% | +0.028 | −0.728 | [−1.00, +2.00] | −0.00017 |
| form_momentum_diff | 67% | +0.043 | −1.000 | [−1.00, +0.82] | −0.00075 |
| rest_days_diff | 95% | −0.015 | −1.000 | [−1.00, +2.00] | −0.00002 |
| league_position_diff | 76% | +0.070 | +0.632 | [−0.68, +1.91] | +0.00012 |
| h2h_win_pct | 39% | +0.011 | −1.000 | [−1.00, +2.00] | −0.00012 |
| lineup_confirmed | 100% | −0.032 | +2.000 | [−0.52, +2.00] | +0.00031 |
| fixture_importance | 76% | −0.020 | +0.608 | [−1.00, +2.00] | +0.00001 |
| bookmaker_disagreement | 84% | +0.045 | +0.743 | [−0.78, +2.00] | +0.00014 |
| model_ensemble_home | 92% | +0.002 | −1.000 | [−1.00, +2.00] | −0.00002 |
| injury_count_diff | **4%** | −0.087 | +1.438 | [−1.00, +2.00] | +0.00171 |
| injury_severity_score_diff | **5%** | −0.096 | +0.605 | [−1.00, +2.00] | +0.00021 |
| player_rating_diff | **17%** | +0.101 | −0.615 | [−1.00, +1.04] | −0.00216 |
| injury_severity_diff / travel_km / xg_overperf_diff / news_impact | 0–11% | — | — | — | *coverage too thin to test* |
| **CANARY** pseudo_clv_home | 75% | +0.108 | −0.060 | [−0.79, +0.67] | −0.00167 |
| **CONTROL** noise_gauss | 100% | +0.003 | −1.000 | [−1.00, +2.00] | −0.00003 |
| **CONTROL** market_self | 100% | −0.105 | −0.640 | [−1.00, +0.29] | −0.00168 |

### over/under 2.5 → over (test log-loss of the market alone: 0.65969)

| signal | coverage | ĉ | α OOS | profile 95% CI | dLL_fixed |
|---|---|---|---|---|---|
| goals_for_sum | 83% | −0.001 | −1.000 | [−1.00, +2.00] | −0.00001 |
| goals_against_sum | 83% | −0.007 | +2.000 | [−1.00, +2.00] | +0.00001 |
| rest_days_min | 95% | −0.059 | −0.147 | [−1.00, +0.95] | −0.00080 |
| elo_gap_abs | 96% | +0.009 | +2.000 | [−1.00, +2.00] | +0.00004 |
| ou25_bookmaker_disagreement | 86% | +0.051 | −0.728 | [−1.00, +1.14] | −0.00053 |
| league_draw_rate_ytd | 69% | −0.087 | +0.087 | [−1.00, +1.17] | −0.00063 |
| lineup_confirmed | 100% | +0.024 | −1.000 | [−1.00, +1.47] | −0.00032 |
| model_ensemble_over25 | 75% | +0.010 | +0.480 | [−1.00, +2.00] | −0.00000 |
| weather_rain_mm | **14%** | +0.659 | +0.062 | [−0.46, +0.56] | **−0.03994** |
| weather_wind / temp / referee_over25 | 14–20% | — | −1.0 / +2.0 / −1.0 | wide | ≈0 |
| injury_count_total | **3%** | −0.044 | −1.000 | [−1.00, +2.00] | −0.00203 |
| **CONTROL** noise_gauss | 100% | −0.032 | −1.000 | [−1.00, +1.42] | −0.00037 |
| **CONTROL** market_self | 100% | −0.060 | +0.015 | [−1.00, +1.45] | −0.00035 |

**Signals adding OOS (α>0.02, profile CI excluding 0, and dLL_fixed>0): 0 of 27.**

Three readings worth keeping:

1. **The controls behave**, which is what licenses the negative. `noise_gauss`
   returns ĉ≈0.003 and a profile CI spanning the entire grid; `market_self`
   returns a *negative* ĉ with p=0.004 — the harness detects a real coefficient
   when one exists, and correctly reads the market as needing shrinkage rather
   than augmentation.
2. **The profile CIs are enormous — and that is the correct answer, not a
   defect.** α is not identified when `p_aug ≈ p_market`; a signal that adds
   nothing leaves the blend weight undetermined. Read `dLL_fixed`, not α.
3. **`weather_rain_mm` is the cautionary tale.** It has the largest ĉ in the
   whole scan (+0.659) and an in-sample p of 0.010 — and the *worst* honest
   out-of-sample loss on the page, **−0.03994**, an order of magnitude worse
   than anything else. On 14% coverage, in-sample significance bought a strictly
   worse forecast. The **CANARY** tells the same story in the other direction:
   `pseudo_clv_home` is post-hoc-derived and is in-sample significant at
   p=0.0030, right at the BH threshold — and adds exactly nothing out of sample
   (−0.00167). No evidence of feature-store contamination; strong evidence that
   in-sample significance here is worthless.

### The coverage finding, which is bigger than the α results

Measured on 25,610 finished fixtures in the last 60 days:

| feature | coverage |
|---|---|
| `lineup_confirmed` | 100.0% |
| `elo_diff` | 92.5% |
| `rest_days_home` | 89.7% |
| `h2h_win_pct` | 30.6% |
| `weather_rain_mm` | 8.9% |
| `referee_over25_pct` | 8.4% |
| `team_avg_player_rating_home` | 7.6% |
| `xg_overperf_home` | 4.6% |
| **`injury_count_home`** | **2.1%** |
| `news_impact_score` | 1.7% |
| **`teams.stadium_lat` / `stadium_lng`** | **0 of 12,128 rows** |

**Travel distance does not exist.** The brief assumed `teams.stadium_lat/lng`
were populated; they are NULL on every one of 12,128 team rows, so travel and
any geography-derived signal is **NOT MEASURABLE**, not negative. The same
applies to injuries (2.1%), xG differentials (4.6%) and news impact (1.7%): the
α results for those sit on 3–5% of fixtures and should be read as *untested*.

**This is the sharpest decision-relevant split in the whole report.** The
well-covered signals — ELO, form, rest, lineups, league position, book
disagreement, goals averages — are **measured negative on thousands of
fixtures**: stop looking there. The sparse ones are **not yet measurable**, and
the way to test them is to populate them, not to run another α fit on 4% of rows.

---

## 5. Multiple comparisons, power, and the placebo

**Cells tested, total: 899 segment cells** (243 + 234 + 220 + 202 across four
lead arms) **+ 27 signals × 2 markets + 3 anchor arms × 2 markets × 9 bands.**
All counted and printed by the scripts.

- **Cluster-robust SEs on `match_id`** everywhere. The three 1x2 legs of one
  fixture share an overround and one shortening is mechanically another's drift;
  treating them as independent understates the SE by up to √3.
- **Time-ordered holdout**, train < 2026-09-11 ≤ test, both printed for every
  cell — never one alone.
- **BH-FDR q=0.10** across each full scan (thresholds p≤0.0248 / 0.0048 / 0.0627
  / 0.0406 by arm).
- **Per-day fold signs** printed per cell.
- **Power.** `n = ((1.96+0.84)·sd/effect)²`:

| gate | n | legs/day | sd | n to resolve a 2pp departure from break-even | **days at current volume** |
|---|---|---|---|---|---|
| edge ≥ 2% | 106 | 15.1 | 23.1pp | 1,046 | **69** |
| edge ≥ 3% | 64 | 9.1 | 28.1pp | 1,544 | **169** |
| edge ≥ 5% | 26 | 3.7 | 41.9pp | 3,435 | **925** |

The gate that the trimmed regression actually puts break-even at (≈+6%) needs
**years** at current volume on 1x2 alone. That is the binding fact behind the
INSTRUMENT verdict, and it is a volume problem, not a statistics problem.

### Gate-matched placebo control

The whole 234-cell segment scan re-run with the target **shuffled within
(book, market, match-day)** — preserving every marginal, destroying only the
fixture-level link:

| arm | cells | CI above zero | surviving CI>0 + BH-FDR + positive OOS |
|---|---|---|---|
| REAL | 234 | **0** | **0** |
| PLACEBO | 234 | **0** | **0** |

The search machinery is not manufacturing findings — and equally, the real arm
has nothing the placebo lacks. Combined with the *anchor* placebo of §2 (which
**does** separate: +1.314 vs +0.007), the picture is consistent: there is exactly
one real signal in this data and it is the sharp anchor.

---

## 6. VERDICTS

| # | Finding | Verdict |
|---|---|---|
| 1 | No segment of the three books reaches break-even. 0/899 cells. Women's, youth/reserve, cup, obscure leagues, odd kickoff hours, unfamiliar teams all measured and *worse* than baseline. | **DEAD — measured negative, well powered** |
| 2 | The §57 draw edge does not transfer to a soft book's own close. Draws are the worst 1x2 selection (−8.31%). | **DEAD unconditionally; NOT YET MEASURABLE at a sharp gate (n=16)** |
| 3 | 27 stored signals vs the own-book price: 0 add OOS. Controls behave; canary clean. | **DEAD for the 13 well-covered signals** |
| 4 | Injuries (2.1%), xG (4.6%), news (1.7%), weather (8.9%), referee (8.4%), player ratings (7.6%) | **NOT MEASURABLE — coverage, not effect** |
| 5 | `teams.stadium_lat/lng` NULL on all 12,128 rows — travel/geography signals cannot be computed at all | **NOT MEASURABLE — fix the data first** |
| 6 | The sharp anchor's own-book CLV ladder is monotone, slope +1.31 (t=4.9), placebo-flat at +0.007 (t=0.22) | **REAL** |
| 7 | Break-even gate is **+6.0%** prob-edge (1% trimmed), not the live +2%; at 2% the predicted mc_clv is **−3.5%** | **INSTRUMENT — re-gate, do not stake** |
| 8 | Epicbet's line responds to sharp disagreement at ~half the rate of Coolbet/Unibet-Site (t = 2.1–4.1 across four windows); its break-even gate is +14.6% vs ~+4% | **INSTRUMENT — pre-registered in `dev/active/own-book-split-gate-preregistration.md`** |
| 9 | Own-book CLV is computable over **7 days only**, because retention deleted the rest | **BUILD — the cheapest thing on this page (§7)** |
| 10 | Nothing here supports placing real money on any configuration | **DEAD for staking today** |

**No BUILD for a betting strategy.** One BUILD, for measurement (§7). One
pre-registered INSTRUMENT (finding 8).

---

## 7. The one BUILD — and it is not a bet

Every "not yet measurable" on this page has the same root cause: **seven days of
own-book price path.** `prune_old_simple` keeps one pre-kickoff row per series
after 7 days, and at our three books that row *is* the close, so own-book CLV
older than the window is zero by construction. That single retention rule is why:

- the segment search cannot have a real out-of-sample era, only a 4-day/3-day
  split inside one week;
- the per-book slopes of §3 rest on a single week;
- the §2 gate calibration cannot be checked across a book-set change or a model
  version change, which `SHARP_ANCHOR_AUDIT_2026_09_14` §2 correctly identifies
  as the actual promotion constraint (regime, not n).

**Retain a decision-time snapshot per series at the three EMTA-legal books** —
one extra surviving row at a fixed lead (say the last quote ≥3h before kickoff),
alongside the existing latest-pre-kickoff row. That is ~2 rows per series instead
of 1, at our three books only, and it converts every question on this page from
"7 days" to "as long as we keep it". It is a retention change, not a collection
change: the rows are already being written and then deleted.

*(Filed as a finding here rather than implemented — this report's scope is
read-only analysis, and retention policy is `workers/` code this brief excludes.)*

---

## 8. What I did NOT test, and who owns it

- **Line movement as a predictor.** Deliberately excluded: the decision-time
  price level is used, never the path. `docs/OWN_LINE_MOVEMENT_2026_09_14.md`
  owns that angle.
- **Lineup-surprise timing** — whether a confirmed-lineup surprise moves the
  soft book more slowly than the sharp one — is movement-shaped and belongs with
  that work. What is settled here is the *level* question: `lineup_confirmed` is
  null both as a segment (−5.91% vs −7.58%, and that gap is a big-league proxy)
  and as a signal (dLL_fixed +0.00031 on 1x2, −0.00032 on O/U).
- **Markets beyond 1x2 and over_under_25.** The other books' markets do not have
  the volume at all three books to support a cross-book statement in this window.
- **Asian handicap.** Excluded: its margin needs the handicap line threaded
  through and a fixed line is not a close (§16).
