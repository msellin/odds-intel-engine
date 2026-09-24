# Asian handicap: can it be beaten? Research report (2026-09-24, read-only)

Tags: [V] = primary text read · [A] = abstract/secondary only · [D] = derivation · [M] = measured here on our DB.
Scripts + intermediate data: this scratchpad (`ah1.py`…`ah8.py`, `ah_close.csv`, `pin_1x2_ou.csv`, `ah_pit.csv`).

## 0. Expected outcome, stated BEFORE the tests (per CLAUDE.md "Research before you train")

* Model route: α = 0. Already measured on 74k observations (MARKET_DATA_MAP: market AUC 0.70–0.75 against our model's 0.55; nested-logistic model coefficient 0.028). The literature calls AH the most efficient football market.
* Sharp-anchor route: flat betting returns about −v/(1+v) at each soft book. Any EV-selected tail is indistinguishable from 0 once time-aligned. Nothing survives a family-wise correction.
* Structural (Skellam) route: a price derived from 1x2 + O/U should roughly equal Pinnacle's own AH price. It adds little beyond it.

## 1. Known negative results (do not re-derive)

| result | source |
|---|---|
| `bot_ah_home_fav`, −13.6% ROI, n=132. Model p(win) was mapped onto ±1/±1.5 lines | dev/active/ah-bot-postmortem-2026-06-25.md |
| AH model-edge sweep negative at every floor (−9.6% to −10.9% on the test split). The early "+142%" was the §53 sign bug | docs/MARKET_DATA_MAP.md |
| Market AUC 0.70–0.75 against the model's 0.55. The model adds zero information out of sample | MARKET_DATA_MAP |
| No fold-robust cell at any line or floor, n=2,059 / 1,573 | docs/BETTING_GATE_DECISIONS.md |
| OWN expansion: −12.6%, later corrected to −3.19% (n=116, CI [−18,+12]) under the live probability gate. Undecidable. "UNMEASURABLE" (§16) | docs/OWN_PER_MARKET_BOTS_2026_09_14.md |
| AH line-movement gates: mc-CLV −4.4% to −13.3%. 0 of 194 cells above 0 | docs/OWN_LINE_MOVEMENT_2026_09_14.md |
| Flat control ROI −5.36% against the −6.99% the overround implies (+1.63pp). Soft AH is priced slightly better than its overround suggests, not beatable | OWN_MARKET_EXPANSION |
| **Shadow ledgers (`shadow_bets_unique`) [M]:** bot_high_alignment n=1,347 ROI −5.95%; bot_ah_home_fav n=309 −5.89%; bot_ah_away_dog n=204 −6.24%. All retired. `clv_pinnacle` is NULL on **all** AH rows (§47: `odds_at_pick_live` never backfilled for AH). real_bets: 74 paper Coolbet AH bets, May–Jun, +3.2%, no CLV | DB |

## 2. Literature (beyond the existing note)

* **Hegarty & Whelan, Rev. Behav. Finance 16(5) 2024 "Returns on complex bets" [V]** (primary text read). Football-Data average odds give these loss rates: integer lines 3.24%, .25 3.61%, half 4.16%, .75 3.57%. On Pinnacle (2016–22, 43,235 and 24,138 matches with simultaneous ladders) the realised loss rates are **integer 2.97%, .25 3.84%, half 4.82%, .75 4.00%**. The paper's second Pinnacle sample covers exactly the 0.75/1/1.25/1.5 rungs. The pattern is fully predicted by the ex-ante expected loss once refund probability is modelled. So it is a *price-complexity* effect, not an exploitable mispricing. The average odds are ~1.92 on every line type, so bettors pay the overround and forget the refund. Section 7: no favourite-longshot bias in AH.
* **Hegarty & Whelan, IJF 41(2) 2025 "A tale of two markets" [V abstract + conclusions]**. 1x2 shows strong favourite-longshot bias. **AH implied probabilities are unbiased, and ex-ante expected losses predict realised losses.** Their explanation: AH is dominated by low-margin, "winners welcome" books with syndicate money, while 1x2 is the soft/retail market. This is the key structural reason AH is the WORSE market for us: the people setting it are the sharpest in football.
* **Constantinou, J. Sports Analytics 8(3) 2022 [V method]**. EPL only, 13 seasons, ratings + Bayesian network. It concludes AH "shares the inefficiencies" of 1x2. **However,** the betting threshold θ is optimised per season on the same data (look-ahead), and AH generated "considerably lower profit and ROI" than 1x2. That is not evidence of an exploitable AH edge.
* **Grant, Oikonomidis, Bruce & Johnson, Eur. J. Finance 24(18) 2018 [A]**. Using AH together with 1x2 odds, they found cross-market arbitrages across fixed-odds books and exchanges. The "position-taker" books that create them *restrict informed bettors*. This is the only documented AH/1x2 inconsistency: an arbitrage between books, not a model edge. It is closed in practice by account limits.
* **Kaunitz, Zhong & Kreiner 2017 [A]** (1x2 only). A consensus-vs-soft-book strategy was profitable in backtest and in 5 months of real money, then accounts were limited to about $1.25. This is the generic fate of the sharp-anchor route at soft books.
* **Home bias, big-club/sentiment bias, late-season motivation specific to AH:** **no AH-specific literature found.** Recorded as the answer. For 1x2 the sentiment-bias evidence is mixed [A]. H&W find no favourite-longshot bias on AH lines [V].
* **Is an AH edge just a 1x2 edge re-expressed? [D]** For 0 and ±0.5, yes exactly: −0.5 = P(H), 0 = P(H)/(P(H)+P(A)). For ±0.25 and ±0.75 the price is a mix of those plus the P(win by exactly 1) mass. Only |L| ≥ 1 needs the margin distribution, whose Skellam variance is λh+λa, so the total matters.
* **Pricing AH from 1x2 + O/U (Poisson/Skellam/Dixon-Coles):** a standard practitioner method (penaltyblog `goal_expectancy_extended`, "1x2 → AH" converters) [A]. **No published accuracy study found.** Measured here instead (§4).
* **Betfair AH efficiency/liquidity:** secondary sources only [A]. About £0.25 is matched on AH for every £1 on match odds. AH is "very liquid in major leagues". Betfair white-label exchanges carry 10–30× the liquidity of betfair.com. Exchanges are generally more informationally efficient than dealer books (Smith, Paton & Vaughan Williams 2006, cited in the Reading EPL paper) [A]. **No paper measures Betfair AH against Pinnacle AH.** Recorded as the answer.

## 3. Our data [M]

### Coverage, last 90 days, `odds_snapshots`, market='asian_handicap', pre-match

| book | matches | whole / half / quarter rows | note |
|---|---|---|---|
| Pinnacle | 18,476 | 342k / 338k / 690k | full ladder, 7–10+ rungs |
| Coolbet | 9,649 | 160k / 95k / **0** | since 2026-07-03 |
| Epicbet | 5,901 | 468k / 552k / 766k | since 2026-08-27; **quotes quarter lines** |
| Tonybet | 248 | 10k / 14k / 24k | **started 2026-09-23** (one day) |
| Unibet-Site | **0** | — | no AH at all. ("Unibet", AF, 4,007 matches, half-lines only, ended 09-11, not placeable) |

**Coolbet quotes NO 0 or ±0.5 lines.** One row at −0.5 in 30 days. It quotes only |L| ≥ 1 in whole and half steps (±1, ±1.5, ±2, …). Coolbet's AH is therefore exactly the "new information" set. It is also Hegarty-Whelan's most expensive line type (half) and their cheapest (integer).

Last-30-day line counts per book: `ah1.py`/`ah2.py` output in the scratchpad. Pinnacle quotes every line Coolbet quotes on most fixtures, so overlap is not the bottleneck. **Time alignment is.** Median latest pre-kickoff quote is Pinnacle T−5 min, Coolbet T−112 min, Epicbet T−114 min.

### Sign convention: VERIFIED
* `handicap_line` is the home-perspective line for both selections at all four books (§53). The home de-vigged probability at L agrees with Pinnacle at the same L: MAD 2.3–2.8pp, corr 0.96–0.97. Against −L the MAD is 22–53pp.
* Betfair Exchange (`exchange_quotes`) was checked the same way: corr **0.996** at the same line, MAD 23pp flipped. `line_of()` negates the away runner's own handicap correctly.
* The `shadow_bets`/`real_bets` selection strings ("away −1", "home +0.5") are NOT checked against this convention here. Verify before any grading reuse.

### Data defect found: Coolbet quotes near kickoff
On 6.8% of Coolbet quotes within 30 minutes of kickoff, the de-vigged probability is more than 15pp away from Pinnacle at the same line. Example: home −1 at 5.40/1.13 against Pinnacle's 2.37. The mismatch rate is 1.5–2.6% at more than 30 minutes, so it concentrates at kickoff. Team swap does NOT explain it (3%). It looks like in-play or wrong-kickoff rows (§21/§37). Unguarded, it produces a fake "+37% EV / +15% ROI" tail. This is §67: an anchor-anchored rule searches for data faults. **Any AH anchor rule needs a |p_soft − p_pin| ≤ 10pp guard.**

## 4. Structural test: price AH from Pinnacle 1x2 + O/U [M]

λh and λa were fitted, with and without Dixon-Coles ρ (mean ρ = −0.055), per match. Inputs were de-vigged Pinnacle 1x2 and O/U 1.5/2.5/3.5 within 60 minutes of kickoff: 13,554 matches. The fitted λs price every Pinnacle rung, including quarter lines through the effective-probability formula. Scoring is on legs that resolve binarily: half lines, and whole lines excluding pushes.

* Derived price minus Pinnacle AH (proportional de-vig): the MAD grows with the line. It is 0.8pp at 0/.25, 1.5 at .5/.75, 2.3 at 1/1.25, 2.7 at 1.5/1.75 and 3.9 at 2+. The derived price sits about 1pp lower on the home side.
* **Out-of-sample log-loss** (train before 2026-09-04, test after): the derived price and a derived+direct blend beat raw proportional-de-vig Pinnacle AH at |L| = 1–1.75. The margin is **tiny**: 0.5986 vs 0.5994 recalibrated at 1/1.25, and 0.6221 vs 0.6232 at 1.5/1.75. In sample, the (derived − direct) term is "significant" (bootstrap z 4.6–6.8). Out of sample it is worth about 0.001 log-loss.
* **What that gain actually is: a DE-VIG artefact, not market inefficiency.** Calibration at |L| ≥ 1, clustered on match, n = 12,859 home-favourite legs:
  * proportional de-vig: y − p = **−1.78pp, z = −2.97**, stable across both halves of the window;
  * power de-vig: **−0.52pp, z = −0.86**;
  * DC-derived: +0.67pp on these legs, but −2.02pp (z = −2.44) on away-favourite legs.
  **Power de-vig of Pinnacle's own AH is calibrated on both sides.** Proportional de-vig overstates "home favourite covers −1/−1.5" by about 1.8pp. That is exactly the leg on which `bot_ah_home_fav` lost money.
* Verdict: deriving AH from 1x2+O/U is a known method and does work, in that it reproduces Pinnacle AH to about 2–3pp. It does not beat Pinnacle's own AH once that is power-de-vigged. Its one use is to fill rungs Pinnacle does not quote (rare, since Pinnacle quotes more rungs than any soft book).

## 5. Sharp-anchor tests [M]

### (a) Close vs close, time-aligned (both quotes ≤30 min pre-KO, |Δt| ≤ 20 min), guarded, 90 days
Flat, all legs: Coolbet ROI −6.96% (CI −9.0/−4.9, n=2,156) against a predicted −6.33%. Epicbet −5.75% (−6.4/−5.1, n=22,226) against −5.64%. Tonybet −8.1% (n=452). **Pinnacle's close predicts flat soft-book returns to within about 0.6pp.**

**Pre-registered family**, |L| ≥ 1, EV ≥ 2%, 3 anchors × 2 books, Holm m=6:

| book | anchor | n | pred EV | ROI | 95% CI | Holm p |
|---|---|---|---|---|---|---|
| Epicbet | blend | 755 | +6.4 | +9.8 | [−4.2, +23.9] | 0.50 |
| Coolbet | proportional | 185 | +11.0 | +6.9 | [−16.3, +30.2] | 1.0 |
| Epicbet | proportional | 742 | +6.5 | +4.2 | [−10.0, +18.4] | 1.0 |
| Epicbet | power | 632 | +5.8 | +3.2 | [−10.4, +16.8] | 1.0 |
| Coolbet | blend | 168 | +11.0 | +1.5 | [−21.3, +24.2] | 1.0 |
| Coolbet | power | 147 | +9.5 | −6.2 | [−27.3, +14.9] | 1.0 |

**None passes.** The point estimates lean positive but all CIs straddle zero. Coolbet produces only about 2 aligned qualifying legs a day over 90 days.

### (b) Point-in-time, intact 7-day window (the honest ex-ante test)
Soft quote taken 30–360 minutes pre-KO. Pinnacle anchor is the latest same-line quote ≤15 minutes before, power de-vigged. Metric is CLV against the de-vigged Pinnacle close. One bet per match, line and side.
* All legs: Epicbet CLV −7.2% (core lines) and −7.6% (wide lines). Coolbet −8.0% (wide).
* **Decision EV ≥ 2%:** Epicbet wide legs **decision EV +5.6% → CLV +0.14% [−2.4, +2.6], n=29**. Core legs +6.7% → −0.47%, n=30. Coolbet +9.7% → +5.9% [−2.9, +14.8], n=11.
* **The decision-time "edge" decays to about zero by the close.** Pinnacle moves toward the soft price, so the overlay was anchor error or timing, not soft-book staleness we could harvest. This is the same mechanism SYSTEM_MAP §1 records for 1x2 (the stale-anchor half of the overlay). Qualifying volume is small (about 4 Epicbet legs a day).

### Power
At a true +3% ROI (per-bet sd ≈ 1 at odds ~2), about 8,700 bets are needed. Epicbet produces about 3,000 aligned qualifying legs a year, so the ROI test takes about 3 years. On CLV (sd ~9%), a 2pp effect needs about 160 bets. **The PIT CLV route is the only measurable one, and it currently reads about 0.**

## 6. Betfair Exchange as a second anchor (coordinator request) [M]

* Table `exchange_quotes` (migration 395). Feed started **2026-09-24 07:34 UTC**, **AH from 08:04 UTC**: **28 matches, 2,954 AH rows, 0 settled**, a single matchday (UEFA Nations League, friendlies, AFCON qualifiers). **No history exists to test anything.**
* Gating in code: AH is read only for events with at least €1k matched on MATCH_ODDS. A line is stored only when both sides are two-sided within 20% spread. So coverage will be restricted to liquid events by construction.
* Liquidity: median AH market matched about **€3.7k** (whole market, all rungs). Back-side overround **1.9%** against Pinnacle's median 6.6% on our slate. **Runner spread (lay/back − 1) is 2.7–3.3% at 0/±0.5 but 5.7–8.2% at ±1…±1.5.** On exactly the lines Coolbet quotes, the exchange mid is uncertain by ±3–4%, as wide as the edge being hunted.
* Overlap (same match and line, these 28 matches): Coolbet 103/197 lines (52%; Coolbet's ±2.5+ rungs are dropped by the two-sided filter). Epicbet 254/273 (93%). Tonybet 209/231 (90%). Pinnacle 147 lines on only 16 of the 28 matches: on a national-team day Betfair covered fixtures Pinnacle did not.
* Agreement with Pinnacle at the same line: **corr 0.996, MAD 1.6pp** (not simultaneous). Sign convention correct.
* Retention: rows more than 6 hours before kickoff are thinned to hourly after 2 days. Near-KO rows are kept in full, which suits close studies.
* Literature: no study of Betfair AH against Pinnacle AH. Betfair AH turnover is about 25% of match-odds turnover [A]. Implication: the exchange mid is a good anchor at core lines on liquid leagues. It is a weak anchor at ±1+, where spreads are about 7%. Use back/lay as bounds, not the mid, and require spread ≤ 5% (`is_liquid`).

## 7. Verdict

| route | verdict | expected α |
|---|---|---|
| **Model** (margin/Skellam head) | **Do not build.** Best-documented efficient market [V]. Our model adds 0 information over a market AUC of ~0.72 [M, prior]. Even a perfect DC fit to Pinnacle's own 1x2+O/U only reproduces Pinnacle AH. A model built without prices would have to beat that | **0** |
| **Sharp anchor, ±0/±0.5/±0.25/±0.75** | Adds nothing over the 1x2 sharp anchor (identical information). Coolbet doesn't quote these lines anyway | same as 1x2 |
| **Sharp anchor, ±1, ±1.5, ±2 (Coolbet/Epicbet)** | **Undecidable, leaning zero.** Aligned close-vs-close tail +3–10% with CIs straddling 0, nothing survives Holm. The ex-ante PIT test shows the decision-time EV decays to **CLV ≈ 0** | **~0** (at best +1–2% before limits) |

**If the owner still wants an AH instrument, the only defensible design:**
1. Books: Epicbet first (all lines incl. quarters, 7.1% margin, most volume), Coolbet second (only |L| ≥ 1). Tonybet: 1 day of data, 9% margin, skip for now.
2. Anchor: Pinnacle AH at the **same line**, **power de-vig (not proportional)**, quote ≤ 15 min older than the soft quote. Guards: |p_soft − p_pin| ≤ 10pp, no soft quotes inside the last 30 minutes before kickoff, overround sanity. Betfair back/lay as a second anchor only where spread ≤ 5%.
3. Metric: **CLV against the power-de-vigged Pinnacle close at the same line**, one row per (match, line, side), first decision only. Stop at n≈160–300 on CLV. The ROI test is 3 years out. Do not fix a rung and call its last quote a close (§16). Pinnacle rungs are now quoted to T−5, which makes the same-line close usable on recent data.
4. Family: pre-register ≤ 4 cells (book × {|L|=1–1.25, ≥1.5}) with Holm.
5. Stated expected outcome: CLV ≈ 0 ± 2pp. That would not clear Coolbet's ~8% or Epicbet's ~7% two-way margin. Even a positive result must survive account limiting (Grant 2018; Kaunitz 2017).

## 8. Data gaps
* `clv_pinnacle` is NULL on every AH shadow and real bet. `odds_at_pick_live` is never backfilled for AH (§47).
* The Coolbet near-KO row contamination (6.8% of ≤30-minute quotes) needs a root cause: in-play or wrong-kickoff rows (§21/§37).
* Retention leaves only 7 days of intraday AH paths. The PIT test has about 7 days: Epicbet n≈60 qualifying, Coolbet n≈11.
* Betfair Exchange: 1 day, 0 settled.
* Tonybet: 1 day.
* Unibet-Site: no AH at all.
* Pinnacle quarter-line O/U is not stored for FT, so the Skellam fit uses half-line totals only.
* The selection-string convention in `shadow_bets`/`real_bets` AH rows is unverified.
