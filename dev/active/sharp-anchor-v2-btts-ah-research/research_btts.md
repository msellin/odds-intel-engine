# BTTS — literature, structure, our data, and a verdict (2026-09-24, READ-ONLY research)

Tags: [V] primary text read · [A] abstract / secondary summary only · [D] my derivation · [M] measured on our DB today.
Scripts (scratchpad, not committed): `btts_derive2.py` (derived-Pinnacle BTTS vs consensus vs books), `btts_probe.py` (contamination probe). Output: `btts_derive2.out`.

---

## 0. Expected outcome, stated before the measurements below (per the CLAUDE.md rule)

* Derived-Pinnacle BTTS (λh, λa fitted to de-vigged 1x2 + O/U 2.5) would sit within ~0.002 nats of the soft-book consensus, and add nothing to it in a blend.
* Independent Poisson would under-price BTTS-yes slightly. A negative Dixon-Coles ρ would fix the level.
* An "anchor vs Coolbet" rule at the close would show roughly zero ROI once faulty rows are removed. Any large positive ROI would be data faults (§67).
* A BTTS model without market inputs would reach α = 0, as every O/U arm did.

All four held. Section 3 has the numbers.

---

## 1. Literature

### 1a. Is BTTS efficient? What BTTS-specific edges are published?

* **da Costa, Marinho & Pires (2022), *IJF* 38(3):895-909, "…The case of 'both teams to score'"** — [A] (full text paywalled; 403 on every mirror). This is the only BTTS-specific paper found. Six seasons (2013-2019), nine top leagues, odds from 19 bookmakers, and ML classifiers on engineered team features. Its claim is hedged: the market is "hard to predict", but in "some scenarios" it can be beaten, and some leagues are more predictable than others. Ensembles did best.
  * The paper does NOT compare against a sharp closing line. From the snippets, the betting evaluation uses soft-book odds.
  * It is the same kind of result as Boshnakov et al.: a positive ROI against average odds, which our §55/§75 rules treat as line-shopping or soft-book evidence, not alpha.
* **No paper found on the favourite-longshot bias or margins specific to BTTS.** None found on BTTS versus a closing line either. That absence is itself the answer per the repo rule: BTTS efficiency against a sharp price is **unstudied**.
* **Exchange vs bookmaker accuracy.** Franck, Verbeek & Nüesch (2010, *IJF* 26(3)), 5,478 Big-5 matches [A]: the exchange forecasts more accurately than bookmakers, and a bookmaker+exchange combined bet was an arbitrage in 19.2% of matches. That result is for **1X2 only**. Nothing published covers Betfair BTTS efficiency or liquidity. Practitioner sources [A, low quality] rank BTTS liquidity below Match Odds and O/U 2.5 on the same event.

### 1b. Modelling approaches, and whether any beats the market out of sample

* **Dixon & Coles (1997)** — ρ adjusts the (0,0), (1,0), (0,1) and (1,1) cells. Practitioner fits put ρ at about −0.10 to −0.15 for top leagues [A].
  * Derivation [D]: with the DC τ, ΔP(BTTS yes) = −ρ·λh·λa·e^(−λh−λa). So **a negative ρ raises BTTS-yes**. At λ = (1.5, 1.1) and ρ = −0.13 that is about +1.2pp.
  * ρ is the one parameter that matters for BTTS but not for O/U 2.5, because all four cells are Under.
* **Karlis & Ntzoufras (2003)**, bivariate Poisson with λ3 ≥ 0 [A]: only positive covariance is possible, and it raises both BTTS-yes and draws.
* **Boshnakov, Kharrat & McHale (2017, *IJF* 33(2))** [V, PDF read]:
  * Model: Weibull-count marginals with a Frank copula, EPL 2006/07-2015/16.
  * κ = −0.456 (Kendall τ = −0.050): a **negative** overall dependence, the opposite sign of the low-score dependence that DC's ρ < 0 implies. The Weibull shapes are close to Poisson (c_H 1.05, c_A 0.98).
  * Out of sample: 1,020 matches, **average odds** (overround 5.5% on 1X2 and 6.0% on O/U), Kelly with EV > 3.5%: +21.2% on 1X2 and +15.5% on O/U 2.5.
  * **BTTS was not tested.** One league, soft average odds, n ≈ 600 bets per market: this is not evidence against a sharp price.
* **McHale & Scarf (2007, 2011)**, copulas [A]: negative dependence in shots-for vs shots-against, and in international goals.
* **Takeaway [D].** The sign and size of goal dependence are model-dependent and small (|τ| ≈ 0.05). BTTS is exactly the market where that small term matters. But **the market already prices it**: see 3b, where soft books imply ρ ≈ −0.1.
* **No study found in which any count model beats a market-implied BTTS baseline out of sample.** No study found on zero-inflation or Weibull for BTTS against odds either.

### 1c. Features the literature and practice point to

* The weaker attack, i.e. min(λh, λa). BTTS-no is dominated by a single team failing to score [D].
* Failed-to-score and opponent clean-sheet rates.
* League BTTS base rate.
* Low-score dependence, from a league-level ρ.
* Shots/xG as inputs, following Wheatcroft's shots > goals result for goal markets. That result has been replicated here, and it fades to zero against Pinnacle's close (#089, #118).

### 1d. Cross-market structure: how much of BTTS do 1x2 + O/U determine?

* I found no academic paper on BTTS-vs-totals consistency or arbitrage.
* Practitioner tooling (for example impliedscore.com [A]) does exactly this. It fits λh and λa to the de-vigged 1X2 + O/U, adds a DC correction, and reads BTTS off the score grid.
* Structurally [D]: P(BTTS yes) = P(H ≥ 1) + P(A ≥ 1) − 1 + P(0-0).
  * P(H ≥ 1) and P(A ≥ 1) are the **team-total 0.5 marginals**, which Pinnacle does quote (§45).
  * The two λs are pinned by 1x2 (difference) plus total (sum).
  * The only free part is the dependence term. **So BTTS is almost fully determined by the 1x2 + totals prices** — confirmed empirically in 3b.

---

## 2. Our data [M]

### 2a. Who quotes BTTS (`odds_snapshots.market = 'btts'`; also `btts_1h`, `btts_2h`), matches last 90 days

| book | matches | median margin (30d, latest pre-KO pair) |
|---|---|---|
| Marathonbet | 19,746 | 9.4% |
| 1xBet | 19,054 | 8.6% |
| William Hill | 17,820 | 8.4% |
| BetVictor | 16,281 | 8.7% |
| Betfair (sportsbook, via AF) | 12,172 | 7.2% |
| Betano | 11,650 | 8.2% |
| Superbet | 10,759 (stopped 09-11) | 8.0% |
| Bet365 | 10,303 | 7.6% |
| **Coolbet** | **10,043** | **7.0%, the lowest of all 16** |
| Unibet (AF) | 9,661 (stopped 09-12) | 9.2% |
| 10Bet | 8,128 | 9.6% |
| **Epicbet** | **5,941** (since 08-27) | 7.9% |
| 888Sport | 3,406 | 8.1% |
| **Unibet-Site** | **2,069** (since 09-16) | 8.3% |
| **Tonybet** | **231** (since 09-23) | 9.0% |

**Pinnacle: 0 BTTS rows, ever** (§4/§45 — AF does not carry it).

**Pinnacle correct score** would be an exact BTTS anchor, but it is not collected. `CAPTURE_EXACT_SCORE` is off (`api_football.py:402`), and there are **0 `correct_score` rows**.

**Pinnacle team totals 0.5** exist, but coverage is thin: home 2,865 and away 4,528 matches in 90 days, against 18.3k for 1x2 and 17.0k for O/U 2.5.

Coolbet's BTTS-yes probability averages **0.9pp below consensus**, i.e. more generous Yes odds. Epicbet's averages 0.5pp below. These gaps are far smaller than a ~7% margin.

### 2b. Betfair Exchange (per coordinator note)

* `exchange_quotes`, market `btts`: **28 matches, 218 rows, all captured 2026-09-24 08:04-08:49 UTC.** The feed started today.
* By design, BTTS is read only on events whose Match Odds have ≥ €1k matched (`EXTRA_MIN_MATCHED`).
* Of those 28 matches:
  * only **~9 have a BTTS market with ≥ €1k matched**;
  * the median matched on the market is **€54** (about 10 h pre-KO);
  * the median spread is 3.9%.
* The deepest quote was ~€7.5k (Nations League), back/lay 2.28/2.32.
* `anchor.py` has an exchange tier: `ANCHOR_USE_EXCHANGE`, OFF, with liquidity ≤ 5% spread and ≥ €1k matched.
* **Verdict on liquidity: at today's coverage it can anchor a handful of big-league fixtures per day. It cannot anchor a BTTS strategy on the leagues where Coolbet has the volume.** Its sharpness near the close for BTTS is unmeasured: gotcha 78's "as sharp as Pinnacle" was for 1x2/O/U on liquid main-league markets. Re-measure after 4-6 weeks of capture, against the derived-Pinnacle BTTS price from 3b.

### 2c. Existing BTTS bots — **all 6 retired**; none active

**`shadow_bets_unique`** (deduped, per §5):

| bot | n | ROI | raw clv | clv_margin_corrected | hit / implied |
|---|---|---|---|---|---|
| bot_btts_all (ret. 09-03) | 252 | −6.4% | +1.3% | **−6.35%** | 44.4 / 47.4 |
| bot_btts_conservative (ret. 05-27) | 83 | +7.1% | +2.0% | −5.77% | 56.6 / 52.7 |
| bot_sweep_btts_yes_v1 (ret. 09-03) | 55 | −2.7% | +7.6% | −0.55% | 44.4 / 46.1 |
| bot_acca_leg_shadow | 225 | −8.8% | — | — | 53.3 / 59.0 |
| bot_high_alignment | 13 | −26% | | −8.0% | |

**`simulated_bets`:** bot_btts_all n = 212 settled, ROI −2.2%, clv +1.46%.

* `clv_pinnacle` is permanently NULL for BTTS.
* The CLV column is against a soft close, so raw clv breaks even at the closing margin (§70). The margin-corrected figure (≈ −6%) is the honest one.
* **No BTTS bot has ever shown a positive margin-corrected CLV.**

### 2d. Feature fill (last 365 days, finished matches)

| feature | where | fill |
|---|---|---|
| `market_implied_btts_yes` | MFV | 43.7% |
| `ht_expected_total` / `ht_expected_diff` (#084 walk-forward ratings → λh, λa recoverable) | MFV | 71.4% |
| `elo_home/away` | MFV | 81.1% |
| `goals_for_avg_*` | MFV | 71.6% (and the literature says goals are the worst input) |
| `league_btts_pct`, `league_avg_goals` | match_signals | 37.2k matches (~75% per audit) |
| xG (`xg_overperf_*`) | MFV | 2.9% |
| **`clean_sheet_pct` / `failed_to_score_pct`** | `team_season_stats` | **96.8% ROW fill, but only 7.6% POINT-IN-TIME per match** |

**Correction to the design doc.** The 96.87% figure in `per-market-feature-sets-design.md` is misleading. `team_season_stats` holds AF season-aggregate snapshots fetched 2026-04 onwards (mostly one May sweep). Only 7.6% of the last 365 days' home teams have a snapshot fetched **before** kickoff with ≥ 3 games played; 53.3% have one at any time, which would leak. **Do not use it for training.** Derive walk-forward failed-to-score and clean-sheet rates from `matches` scores instead (175k scores, ~100% for teams with history).

---

## 3. Measurements [M] — last 120 days, finished, Pinnacle 1x2 + O/U 2.5 and ≥ 3 BTTS books

**3a. Setup.**
* n = 17,939 fixtures. BTTS-yes rate 53.6%.
* Pinnacle latest pre-KO complete pair: median 5 min before KO.
* Shin de-vig everywhere.
* λh and λa are fitted to (pH, pA, pOver). Median max residual is 0.8pp.

**3b. BTTS is ~97% determined by 1x2 + O/U.**

| forecast | log-loss | mean P(yes) | corr with consensus | mean abs diff vs consensus |
|---|---|---|---|---|
| base rate | 0.69055 | 0.536 | | |
| soft-book consensus (median of books) | **0.68027** | 0.5365 | | |
| derived Pinnacle, independent Poisson (ρ = 0) | 0.68102 | **0.522** (under-prices yes by 1.4pp) | 0.986 | 1.66pp |
| derived Pinnacle, DC ρ = −0.10 | 0.68049 | 0.534 | 0.987 | **0.89pp** |
| derived Pinnacle, DC ρ = −0.15 | 0.68048 | 0.540 | 0.987 | 0.95pp |

* **Soft books price BTTS as if ρ ≈ −0.1.** The derived price matches the consensus level exactly at ρ = −0.10 and lands on the realised rate.
* **Blend test**, trained on the first half and scored on 8,970 held-out fixtures: logit(consensus) + logit(derived-Pinnacle) improves on consensus alone by **ΔLL = +0.00006 nats, t = 1.0**. The sharp-derived price adds nothing measurable to the soft consensus.
* Both are equally calibrated. By quintile, both over-predict the 4th quintile by about 2pp.

**3c. Sharp-anchor rule at the close.**
* Rule: bet a book's latest pre-KO BTTS side when book_odds × p_anchor − 1 > thr.
* Anchor: the derived-Pinnacle price (ρ = −0.15) or the ex-book consensus.
* **The naive run is the §67 trap.** Coolbet showed +27% to +58% ROI at t = 3.4-4.7 (n = 159-342), and Epicbet +13% to +52%. The probe found the cause: **data faults**.
  * Coolbet BTTS-yes quoted at **11.0, 4.5 or 4.1 against a 47-56% consensus**, i.e. in-play or another match's board (the same family as #120 and §79).
  * 29 of 8,055 Coolbet fixtures and 40 of 4,814 Epicbet fixtures have |p_book − consensus| > 15pp.
  * ROI rises monotonically with the size of the gap and with quote age (>600 min: +72%). That is the signature of faults, not of edge.
* **Clean subset** (|gap| ≤ 8pp, quote ≤ 180 min old, anchor = consensus):

| book | thr | n | ROI | t |
|---|---|---|---|---|
| Coolbet | 0% | 362 | +0.4% | 0.06 |
| Coolbet | 3% | 147 | +1.7% | 0.18 |
| Coolbet | 5% | 72 | +5.2% | 0.35 |
| Epicbet | 0% | 149 | −13.0% | −1.42 |
| Epicbet | 5% | 31 | −13.2% | −0.56 |
| Unibet-Site | any | ≤ 76 | −5% to −19% | n.s. |

  * This is the best case: close vs close, with no stale-anchor penalty.
  * Of Coolbet's 8,055 BTTS boards, the rule fires on ~4.5%, at about 0 ROI.
  * **Median gap to consensus is 0.5pp against a 7-8% margin.** The book rarely strays far enough to clear its own vig, and when it does, the row is usually broken.
* Caveat: retention (§59/§64) keeps only the latest pre-KO row for most of this window, so a T-2h decision test is not possible on history. At T-2h the anchor is staler, which makes the result worse, not better.

---

## 4. Verdict

### Model route: do NOT build a BTTS model head.
* No published result beats a BTTS market out of sample against a sharp price. The one BTTS paper is soft-odds and hedged [A].
* Our own O/U heads never beat the market (0.005 nats short, α = 0 six times).
* Most directly: a **sharp-derived** BTTS price adds only +0.00006 nats to the soft consensus. A model built from inputs weaker than Pinnacle's own 1x2 + O/U has nothing left to find.
* The only structural BTTS-specific term, ρ, is already priced (consensus ≈ ρ −0.1).
* **Expected α = 0.0000**, for the same reason as O/U. If an α harness were built anyway, its anchor would have to be the derived-Pinnacle price (below), since there is no Pinnacle BTTS.

### Sharp-anchor route: the only candidate, and it measures ≈ 0 at the books we can bet.
* Coolbet has the lowest BTTS margin (7.0%) and slightly generous Yes odds (−0.9pp). Even so, the clean close-vs-close rule gives +0.4% ROI (n = 362, t = 0.06). Epicbet gives −13%.
* Not a 🤖 OWN money strategy on current evidence.

### What IS worth doing — cheap, and mainly 👥 PICKS / measurability
1. **Derived-Pinnacle BTTS as the BTTS anchor for `clv_pinnacle`.** λ fitted to de-vigged 1x2 + O/U 2.5 with DC ρ ≈ −0.10 (or a per-league walk-forward ρ). It is within 0.9pp of consensus on 18k fixtures and ends "BTTS is permanently unmeasurable" (§4/§62). Where Pinnacle team-total 0.5 lines exist (~3-4.5k/90d), use them to pin the marginals directly.
2. **Optional: turn on `CAPTURE_EXACT_SCORE` for Pinnacle, but only for fixtures Coolbet or Epicbet quote BTTS.** That gives an exact BTTS anchor with no ρ assumption. Storage cost is ~70 rows per fixture per sweep, so it needs capping (e.g. latest-only or T-2h plus the close).
3. **Exchange:** let `exchange_quotes` accumulate 4-6 weeks, then test exchange-BTTS against derived-Pinnacle-BTTS agreement on liquid markets. Expect agreement within about 1pp; if so, the exchange is a cross-check for top leagues, not a coverage source.
4. **Data fault to track (#120 family).** BTTS boards off-consensus by > 15pp: Coolbet 29, Epicbet 40 in 120 days, including quotes like yes 11.0 / no 1.04 pre-KO. `mirror_guard` ignores BTTS ("no home/away to swap"), but the #120 board-level check should cover BTTS too. Any future BTTS backtest must filter these, or it will "find" +30-50% ROI.

### Proposed feature set, if the owner still wants a model arm (≤ 8, market-free α variant)

| # | feature | source | fill |
|---|---|---|---|
| 1 | `lam_min` = min(λh, λa) | #084 walk-forward ratings (ht+h2 expected total/diff) | 71.4% |
| 2 | `lam_prod` = λh·λa (scales the DC term) | same | 71.4% |
| 3 | `p_home_scores_emp` — home team's walk-forward venue-specific scoring rate × opponent's walk-forward conceding rate | derived from `matches` scores | ~95%+ (derive; NOT team_season_stats) |
| 4 | `p_away_scores_emp` | same | same |
| 5 | `league_btts_wf` (decayed, same-day-blind) | `matches` (or `league_btts_pct` signal ~75%) | ~100% derived |
| 6 | `league_rho_wf` — walk-forward league low-score dependence (0-0 and 1-1 excess over Poisson) | `matches` | ~100% derived |
| 7 | `elo_absdiff` (mismatch → underdog blank) | MFV | 81.1% |
| 8 | shots-on-target λ_min (covered leagues only) | `match_stats` | ~31% (coverage-switch arm only) |

**Data gaps:**
* no Pinnacle BTTS or correct score;
* team-total 0.5 is thin;
* the exchange is 1 day old and €54 median matched;
* `team_season_stats` is not point-in-time;
* there is no pre-KO BTTS history before the retention change (§64), so no T-2h backtest.

### Known negative results to not re-derive
* Six O/U α = 0 results (#089 arms A-E, faithful Wheatcroft, #118 xG).
* All 6 BTTS bots retired, margin-corrected CLV ≈ −6%.
* §24: the BTTS head lost to a base-rate constant.
* §55: BTTS model AUC ≈ coin.
* §67 / this run: anchor-driven BTTS ROI is fault-driven.

---

Sources:
* [da Costa, Marinho & Pires 2022, IJF — RePEc](https://ideas.repec.org/a/eee/intfor/v38y2022i3p895-909.html) · [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0169207021001084)
* [Boshnakov, Kharrat & McHale 2017 (PDF)](https://pure.manchester.ac.uk/ws/files/49399144/ijfpaper.pdf)
* [Franck, Verbeek & Nüesch 2010 (PDF)](https://www.unifr.ch/tim/en/assets/public/uploads/Publication%20list/2010/IJoF_Franck_Verbeek_Nuesch.pdf)
* [McHale & Scarf 2007](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-9574.2007.00368.x)
* [Dixon-Coles ρ practitioner notes](https://dashee87.github.io/football/python/predicting-football-results-with-statistical-modelling-dixon-coles-and-time-weighting/) · [impliedscore methodology](https://impliedscore.com/methodology/)
* [Betfair liquidity (practitioner)](https://betfairsquare.com/sports/football)
