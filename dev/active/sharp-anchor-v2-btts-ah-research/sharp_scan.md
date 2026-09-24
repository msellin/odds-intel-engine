# Sharp-anchor feasibility scan: BTTS and Asian handicap at Coolbet / Epicbet / Unibet-Site / Tonybet (2026-09-24)

This is a feasibility scan. It is not a result. It is read-only, and every number comes from local CSV dumps of `odds_snapshots` and `exchange_quotes`.
The pre-registration (family, thresholds and expected outcome) was written before any outcome was computed: `prereg.md`.
Scripts: `scan.py` (leg builder), `analyze.py` (DQ flags + raw cells), `family.py` (gated cells + Holm), `exchange.py`.
Raw outputs: `analyze_out.txt`, `family_out.txt`, `data/*.pkl`.

## Method (and the traps it avoids)
* **Pre-match only.** Rows are restricted to `timestamp <= matches.date`. `is_live` is ignored because it is not a pre-match filter (§37). Every leg is joined by `handicap_line` (§25). AH uses full and half lines only.
* **AH sign convention verified (§53).** Lines are home-perspective at every book. On fixtures where Pinnacle's home probability exceeds 0.65, the book's most balanced line is negative in 99.0-99.8% of cases at all 13 books (median -1.5).
* **Anchors.**
  * AF-Pinnacle, de-vigged with Shin (§78), using complete markets at one timestamp (§62).
  * **Consensus:** the mean of per-book Shin probabilities over at least 5 AF books. Pinnacle and the four target books are excluded.
  * **Betfair Exchange** from `exchange_quotes`, using the back/lay mid with liquidity = spread ≤5% and at least €1k matched.
  * AF "Betfair" is the **sportsbook**, not the exchange: its 1x2 overround median is 10.6%. "Betfair Exchange" in `odds_snapshots` holds football-data CSV closes that end on 2026-05-19 and contain no BTTS.
* **Retention (§59/§64).** Kickoffs before 2026-09-17 keep only about 3 rows per series. The decision regime is therefore scored only on the intact window, which is 8 days.
* **The two regimes.**
  * **DECISION:** at T−2h, the latest soft quote and the latest complete anchor market, both within the preceding 60 minutes. CLV is measured against the same anchor's close, which must be a strictly later snapshot in the final 60 minutes.
  * **CLOSE:** 60 days, last pre-KO quote against the anchor's close. Here edge equals CLV by construction, so ROI is the only check.
* **Gates.**
  * Dedup to one leg per (fixture, book, market), keeping the highest edge (§5).
  * Outlier guard: edge ≤25%, which is the §9 1.25 ratio.
  * The publisher's own gates: odds ≤4.0, and AF-Pinnacle overround ≤4%.
* **Family F** is 51 cells: BTTS × 4 books × {consensus, exchange} × {2,3,5%} plus AH × 3 books × {Pinnacle, consensus, exchange} × {2,3,5%}. It uses one-sided CLV t-tests with Holm, and untestable cells enter with p=1. Unibet-Site AH is not collected (#010).
* **Benchmark family B** is 24 cells of 1x2 under the same design, with its own Holm correction.

## 1. Coverage: fixtures where the soft book and the anchor price the same selection

| market / anchor | Coolbet | Epicbet | Unibet-Site | Tonybet |
|---|---|---|---|---|
| BTTS / consensus, CLOSE (60 d) | 2,830 | 2,204 (23 d) | 806 (9 d) | 47 (2 d) |
| BTTS / consensus, T−2h (8 d) | 467 | 815 | 448 | 24 |
| AH / Pinnacle, CLOSE | 2,048 | 2,462 | not collected | 56 |
| AH / consensus, CLOSE | 684 | 817 | — | 19 |
| AH / Pinnacle, T−2h | **28** | **69** | — | 11 |
| AH / consensus, T−2h | **2** | **7** | — | 6 |
| 1x2 / consensus, T−2h (benchmark) | 575 | 992 | 575 | 43 |

* **BTTS has no Pinnacle price** (§45). It also has no exchange history.
* **AH has no pre-close anchor at all.** AF writes the AH ladder almost only in the final pre-KO fetch. For Pinnacle, 43k rows fall in the last 30 minutes, against about 2k per hour earlier; Marathonbet and 1xBet show the same pattern.
* **AF-Pinnacle AH is not sharp by our own gate.** Its overround at close has a median of 5.5% (p10 3.3%). Roughly 80% of lines fail the publisher's ≤4% test. This is consistent with #015.
* **Tonybet** has data only since 2026-09-23. **The exchange** has data only since 2026-09-24 07:34 UTC.

## 2. Edge distribution: share of legs with edge = soft × P − 1 above 2 / 3 / 5%

**T−2h, consensus anchor.**
* 1x2: Coolbet 4.7/4.2/2.4%, Epicbet 4.6/3.7/2.7%, Unibet-Site 4.2/3.4/2.4%.
* BTTS: Coolbet 1.4/1.1/0.5%, Epicbet 1.2/0.9/0.3%, Unibet-Site 1.1/0.7/0.2%. That is about **4× rarer than 1x2**.
* Deduped and gated, legs above 3% come to 6.5 / 8.4 / 5.75 per day for 1x2 against 1.25 / 1.75 / 0.75 per day for BTTS.

**Close, Pinnacle anchor, ungated.**
* AH: Coolbet 5.9/5.1/3.7%, Epicbet 8.2/6.5/4.5%.
* 1x2 for comparison: Coolbet 6.2/5.0/3.3%, Epicbet 7.7/6.4/4.2%.
* So AH is no softer than 1x2. Its median edge is -6.3 to -7.3%, which is the book margin.

## 3. Does the edge hold? CLV vs the later anchor close (T−2h, clean legs)

| cell (above 3%) | n clean | mean edge | CLV | t |
|---|---|---|---|---|
| 1x2 cons Coolbet | 49 | 6.9% | +1.8% | 1.74 |
| 1x2 cons Epicbet | 60 | 8.3% | +1.7% | 1.71 |
| 1x2 cons Unibet-Site | 45 | 7.7% | +0.5% | 0.40 |
| BTTS cons Coolbet | 6 | 6.8% | −1.6% | – |
| BTTS cons Epicbet | 13 | 5.7% | −1.3% | −0.80 |
| BTTS cons Unibet-Site | 6 | 4.9% | −1.6% | – |
| AH (any anchor) | ≤3 | – | – | untestable |

* **Holm, family F (m=51):** 0 of 51 cells survive, and every adjusted p is 1.0. 33 of the 51 cells are untestable: 18 exchange cells, the AH cells and the Tonybet cells.
* **Holm, benchmark (m=24):** the minimum adjusted p is 0.915. **The 1x2 benchmark itself does not pass.** This matches #024, where the scraped books measured +0.58% with a CI straddling 0.
* **ROI (secondary).**
  * The BTTS T−2h ROIs look positive but rest on n = 6-18 with a standard error of 0.28-0.55. They are noise.
  * AH at CLOSE, Epicbet vs Pinnacle (gated), above 3%: **ROI −10.3% ± 10.3 (n=90)**, against a claimed mean edge of +7.1%.
  * AH at CLOSE, Epicbet vs consensus: +3.9% ± 9.0 (n=142), against a claimed +8.3%.
  * 1x2 at CLOSE, post-fix, where both anchors agree above 3%: −12.4% (n=181).
  * **Edges measured at the close do not turn into returns.** At close the two anchors agree (r = 0.91 for AH, 0.93 for 1x2), so the gap is on the soft side. At close the soft quote is a median 18 minutes older than the anchor, which sits at KO−5 minutes.
* **Power.** Per-leg CLV has an sd of about 5.3%, so +2% CLV needs about 44 legs as a single test, or about 110 under Holm with m=51. At 1-2 BTTS flags per book per day, that is 2-4 months per book.

## 4. How much of the tail is data error rather than opportunity

* **Coolbet BTTS and AH before 2026-09-18 (the COOLBET-MARKET-COLLISION era):**
  * Every pre-fix leg in the Coolbet CLOSE >3% tail falls inside that era. Those legs are 87-89% of the whole Coolbet BTTS tail and 60-85% of the whole Coolbet AH tail.
  * Outlier legs (edge above 25%) went from 6.2% to 0.36% of all Coolbet AH legs after the fix. For BTTS they went from 2.6% to 0.12%.
* **Epicbet BTTS >3% tail at close:** 32-48% flagged. The flags are board mismatches plus inverted favourites, i.e. the book's favourite is opposite the anchor's while the anchor gap is at least 0.10 (§67 ordering test). After the fix the share is 25%. **Unibet-Site BTTS:** 19-27% flagged.
* **Board check (§79).** 309 of 12,982 (fixture, soft book) pairs have a 1x2 board off the ≥4-book median by more than 0.12: Epicbet 140, Coolbet 129, Unibet-Site 39. `data_quality_findings` is empty, so the board-audit history cannot be joined yet.
* **Staleness.**
  * **At T−2h:** a soft price unchanged for at least 1h while the anchor moved at least 1.5pp is rare, at 0-2 legs per cell.
  * **At close:** staleness is the dominant effect. The soft quote comes before late anchor moves, and those "edges" have negative realised ROI, as shown in §3.
  * **Rough estimate:** at T−2h, after the collision era, 0-10% of the >3% tail carries a DQ flag. At close, most of the tail is not bettable value.

## 5. Exchange anchor (live since 2026-09-24 07:34 UTC)

* **Snapshot size:** 28 fixtures with BTTS and AH (only events with at least €1k matched on MATCH_ODDS).
* **Liquidity:**
  * BTTS is thin. The median market matched is €56 and only 22.5% of rows are liquid.
  * AH is 60% liquid, with a median of €3.7k matched on the market (this may be shared across lines).
* **Mid overround:** about 0.0%.
* **Current overlaps with soft books** (Coolbet, Tonybet): 144 legs, of which 0 have an edge above 2%.
* **No outcomes and no history**, so all 18 exchange cells are untestable.

## Verdict — which cells deserve a pre-registered forward test

* **BTTS: no.** It has about 4× fewer flags than 1x2, at 0.75-1.75 per day per book. CLV is negative in all three measurable cells. There is no sharp anchor, only consensus plus a thin exchange. It would take 2-4 months per book just to power a +2% CLV test.
* **AH: not yet testable, which is different from negative.**
  * No pre-close anchor exists: AF anchors land only at the final fetch, and AF-Pinnacle AH fails the ≤4% sharpness gate.
  * At close, the edges are as frequent as in 1x2 but do not convert into ROI.
  * **The only cell with volume is Epicbet AH.** It has about 7.6 legs per day above 3% vs consensus at close, and would be about 4 per day vs Pinnacle.
  * The prerequisite is a pre-close anchor: the exchange AH ladder, or AF polling the ladder at T−2h. If that anchor exists, Epicbet AH could reach about 110 CLV legs in about 3-4 weeks. Only then does a shadow-only pre-registered test make sense.
* **Coolbet and Tonybet AH:** too thin. Coolbet has about 3 per day at close with the collision era excluded; Tonybet has 1 day of data.
* **Benchmark caveat:** at T−2h, 1x2 itself shows only +0.5 to +1.8% CLV and no Holm survivor. "1x2 works" is not established at our scraped books either.
