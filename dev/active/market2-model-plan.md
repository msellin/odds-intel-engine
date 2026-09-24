Parent: [[#149]] SECOND-MARKET-MODEL-2026-09-24 — 🔴 P0 (owner, 2026-09-24)

# #149 — second market: O/U totals, the NEW+ recipe (plan + pre-registrations)

Owner brief (2026-09-24): pick the next market after 1X2, audit, research, use all our data (backfill if needed),
deliver a new model + bot that beats the market, backtest it against old bots, then iterate twice more with new
layers each time. Everything here is paper; no real-money path.

## 1. Market choice — O/U totals (2.5, with 1.5 and 3.5 on the same machinery)

Three independent research tracks (2026-09-24):
* **Data audit** — O/U 2.5 has the widest bettable coverage outside 1X2: 17 books, all four of our direct sweepers
  (Coolbet 59%, Unibet-Site 51%, Epicbet 60%, Tonybet 60% of matches last week), Pinnacle on 74%, ≥ 5 books on one
  line for 79%, settlement 100%; multi-book O/U prices in volume since 2026-05 (~25k matches May–Aug). 1.5 / 3.5:
  Pinnacle 74%, ≥ 5 books 70–75%, Unibet-Site only 25% on those lines.
* **Literature** — the only peer-reviewed non-1X2 result that beat the market is on totals (Wheatcroft, IJF 2020:
  +0.8%/bet, shots + corners ratings) and it held ONLY at best odds across books — i.e. the edge is price-shopping,
  which is exactly what a consensus-outlier bot does. Kaunitz et al. (2017) is model-free and ports to any market with
  enough books. Hubáček et al. (IJF 2019): profit comes from DECORRELATING from the book, not from accuracy.
  Asian handicap is the most efficient market (Whelan & Hegarty 2024) → second choice, gated on new data.
  Corners/cards: no peer-reviewed evidence; Pinnacle's corners close is thin (a weak CLV benchmark).
* **Empirical consensus-outlier scan** (all markets, Holm-corrected) — see §4 when it lands.

**Why not a seventh O/U stats model.** Six model-only O/U tests ended at α = 0 against Pinnacle (per-market design
doc, #089/#118/#090). What worked for 1X2 was not a better stats model: it was a COMBINED probability built mostly
from the market (18-book de-vigged consensus + Pinnacle) with the rating on top, used to find single books whose
price sits well above it (backtest B2: CLV +2.0% at EV ≥ 5%, +3.1% at EV ≥ 8%, 4/4 pre-registered arms passed).

## 2. Research before training (CLAUDE.md rule)

1. **Structural shape: SUM.** Totals depend on λ_home + λ_away (Karlis & Ntzoufras: the Skellam difference model
   integrates the sum out, so a difference-shaped feature set is silent about totals). Dixon-Coles ρ does nothing at
   the 2.5 line (all four low scores are Under). Rating input = P(total > L) under Poisson(λ_h + λ_a) from the
   walk-forward dynamic-Poisson ratings (`dp_lh`, `dp_la`, `workers/model/ratings_1x2.py`).
2. **Size: DOWN.** The combiner has ≤ 4 inputs per availability group (rating logit, consensus logit, log n_books,
   Pinnacle logit), exactly as COMB-HYB.
3. **Known negative results:** six α = 0 model tests; `bot_v10_ou` −3.85% CLV (n=181); Kaunitz pooled 1X2+O/U negative
   ROI by band; own-book margin-corrected CLV negative for every O/U bot so far; Epicbet O/U outliers score negative.
   Positive on CLV: `bot_sweep_ou25` +1.81% (n=454), `bot_sweep_ou35` +1.92% (n=373), sharp triggers at
   Coolbet/Unibet +5.5%/+5.4% (n=71/55). So the MATCHED CONTROL is the existing sharp/consensus O/U bots — a new bot
   must beat them, not just zero.
4. **De-vig:** 2-way power method per book (ANALYSIS_GOTCHAS #78: never proportional).
5. **Time decay:** totals want longer memory than 1X2 (~300 d in Wheatcroft & Sienkiewicz, with odds as a regressor);
   the ratings' tuned decay is the 1X2 one — an iteration-2 candidate, not round 1.
6. **Family-wise:** three lines × arms → Holm within each round's pre-registered family.

## 3. Pre-registration — ROUND O1: combined O/U model (2026-09-24, BEFORE the first run)

**Model `ou_comb_v1`, per line L ∈ {1.5, 2.5, 3.5}:** one binary logit per availability group (P&C, P, C, none), inputs
as log-odds of P(over): rating (Poisson on λ_h + λ_a), consensus (mean power-de-vigged log-odds over the non-Pinnacle
books quoting that exact line, outlier guard |p − median| > 0.20 with ≥ 3 books; + log n_books), Pinnacle (power
de-vigged). Fit on kickoffs 2026-05-01..2026-08-30; score ONCE on 2026-08-31..2026-09-24, OPEN prices (what a pre-match
bet sees) and CLOSE prices separately.
**Comparators:** rating alone, consensus alone, Pinnacle alone (where it prices), rule (Pinnacle > consensus > rating).
**PASS (per line)** = combined log-loss < the best single comparator on the same rows, paired bootstrap, Holm m = 3
(lines), adj p < 0.05, at OPEN. **Expected:** combined ≈ Pinnacle on Pinnacle-priced rows (tiny gain), clear gain
over the rating alone; the value is coverage (C group) and a probability to find outliers against.

## 4. Pre-registration — ROUND O1 BOT backtest (fixed before results)

Bots `bot_ou_comb_ev{5,8}_v1` (paper): edge = p × odds − 1 ≥ 5% / 8% flat, Pinnacle price required, odds 1.30–6.00,
one pick per match per line (best EV), lines 1.5/2.5/3.5, all books; window 2026-08-31..09-24, OPEN prices bounded
`timestamp < kickoff`, CLV vs Pinnacle de-vigged close on the same line. **Controls on the same window and rules of
measurement:** `bot_sharp_ou_v1`, `bot_sweep_ou25`, `bot_sweep_ou35` (their real stored picks) and the null "every
selection at best open price, same odds range". **PASS** = mean CLV > 0, one-sided bootstrap, Holm m = 2 (EV5, EV8),
adj p < 0.05 — AND reported side-by-side with the controls. Same-window caveat stands; the forward run decides.

## RESULTS — ROUND O1 (2026-09-24 evening)
**Model** (`scripts/ab_ou_combined.py`, n = 12,640 per line, OPEN): PASS 2.5 (Δ −0.0023 vs rule, Holm .004) and 3.5 (Δ −0.0038,
Holm < .001); FAIL 1.5 (Δ −0.0010, Holm .16). CLOSE the same. **But on Pinnacle-priced rows the combiner is WORSE than
Pinnacle alone** (1.5: 0.5661 vs 0.5620; 2.5: 0.6764 vs 0.6758; 3.5: 0.6451 vs 0.6449) — the whole gain is on rows Pinnacle
does not price (consensus + rating beat consensus alone there). Group 'P' (Pinnacle without consensus) is empty.
**Bots** (`scripts/backtest_ou_comb_bots.py --tag o1`, same window, OPEN quotes, CLV vs Pinnacle de-vigged close):
EV5 n=2,361 CLV +1.74% [+1.05, +2.45] PASS; EV8 n=1,358 +4.11% [+2.96, +5.30] PASS (Holm < 1e-4). Null −4.36%.
By line (EV8): 1.5 −5.8% (n=606), 2.5 +10.6% (467), 3.5 +14.6% (285). By source: AF books −2.2% (824), our direct
sweepers +13.8% (534). Old bots, same window + same CLV: sweep_ou25 +4.5% (274), sweep_ou35 +4.4% (230), coolbet/unibet
trigger_sharp +5.2%/+5.7% (72/57), model/trigger bots −7..−9%, bot_v10_ou −6.4% (96).
**Diagnosis:** split by the book's quote vs Pinnacle's fair OPEN price — picks where the book is NOT ≥ 3pp better than
Pinnacle have CLV −5.8% (AF) / −3.5% (direct); the positive CLV sits entirely in picks where the book beats Pinnacle's fair
price (3–6pp: +6/+8%; 6–10pp: +7/+15%; 10–15pp: +30%; > 15pp: +69% ≈ misposted lines, palpable-error void risk). So the
model's disagreement with Pinnacle is noise; the edge is soft-book vs sharp. Direct-book quotes posted > 24 h before kickoff:
+19% CLV (n=183). Direct books are NOT mislabelled on average (mean de-vig gap to Pinnacle ≈ 0, sd 0.02–0.035).

## Pre-registration — ROUND O2: sharp-anchored O/U outlier bot (written BEFORE its run)
Change from O1 (and why): fair = **Pinnacle's power-de-vigged OPEN price** wherever Pinnacle prices the line (O1 showed the
combiner loses to Pinnacle there); the combined model is used only as a FILTER/fallback, not as the fair price.
Rule: EV_pin = odds × p_pin_open − 1; one pick per (match, line), best EV; odds 1.30–6.00; **palpable-error cap:
EV_pin ≤ 15%** (larger gaps are near-certainly misposted lines a book may void). Lines 1.5 / 2.5 / 3.5.
**Confirmation window = 2026-05-01..2026-08-30** (kickoffs) — NOT seen by any O1/O2 rule choice; these arms fit nothing
(Pinnacle is the anchor), so the window is clean for them. OPEN = each book's opening/earliest pre-KO quote.
CLV vs Pinnacle de-vigged close, same line and side. Family (Holm m = 4):
| arm | books | EV_pin |
|---|---|---|
| S1 | all | ≥ 3% |
| S2 | all | ≥ 5% |
| S3 | direct sweepers only (Coolbet, Unibet-Site, Epicbet, Tonybet) | ≥ 3% |
| S4 | direct sweepers only | ≥ 5% |
PASS = mean CLV > 0, one-sided bootstrap, Holm adj p < 0.05. Also reported (not tested): by line, by side, by hours
before kickoff, ROI with CI, and the same arms on the O1 window (08-31..09-24) for continuity. **Expected:** all four
positive on CLV; direct > all; ROI inside noise. The production bot is the best-supported arm, forward-judged.

## RESULTS — ROUND O2 (2026-09-24 evening)
Pre-registered window 05-01..08-30: S1 CLV +4.66% / ROI −2.7%, S2 +5.93% / −5.1% [−9.0, −1.0], S3/S4 (only Coolbet swept
then) CLV −1.8% / −1.1% — formally S1/S2 PASS, S3/S4 FAIL. **The S1/S2 pass is not trusted:** a +6% CLV cannot coexist
with a −5% ROI at n = 2,938; cause found — before mid-July there is no Pinnacle O/U CLOSE (ANALYSIS_GOTCHAS #83), so that
"CLV" is circular. With real closes: **Aug S1 +1.22% [+0.9, +1.6], S2 +1.80% [+1.3, +2.3]**, ROI −0.5% / +0.6% (noise);
Coolbet (the only direct book in Aug) negative. Sep (O1 window): S1 +1.62%, S2 +2.63%, S3 +1.70%, S4 PASS; Epicbet
+4.8/+6.3%, Unibet-Site +2.0/+3.1%, Unibet(AF) +4.7/+5.8%, Coolbet ≈ 0/+1%, BetVictor & Betfair negative.
**Honest O/U edge:** a soft book beating Pinnacle's fair price by ≥ 5% ≈ **+2% CLV** — the same order as the 1X2 NEW+ EV5.

## Pre-registration — ROUND O3: filters on top of S2 (written BEFORE its run)
Web research between rounds: soft books lag sharp moves; value taken > 24 h out shows higher CLV than last-hour value
(practitioner sources); O1's split agreed (direct-book quotes > 24 h: +19%). And O2's failure mode was a STALE anchor.
Base = S2 (EV vs Pinnacle open ≥ 5%, ≤ 15% cap, all books, one pick per (match, line), odds 1.30–6.00). Arms (Holm m = 4):
| arm | extra filter |
|---|---|
| T1 | anchor fresh: the Pinnacle quote is within 3 h of the book's quote (`abs(ts_book − ts_pin) ≤ 3 h`) |
| T2 | two anchors agree: the book also beats the leave-one-out consensus of the OTHER books (EV_cons ≥ 2%) |
| T3 | early: the book's quote is ≥ 12 h before kickoff |
| T4 | T1 + T2 |
Primary window **2026-08-01..08-30** (real closes; these filters never examined there), secondary 08-31..09-24 (reported,
already seen for S arms). PASS = CLV > S2's CLV on the same window, paired by pick where the filter keeps it (one-sided
bootstrap of the difference in means, Holm m = 4, adj p < 0.05). A filter that passes both windows goes into the live bot.

## RESULTS — ROUND O3 (2026-09-24 evening) — all four filters PASS both windows (Holm < .001)
| arm | Aug (primary): n / CLV / ROI [CI] | Sep (secondary): n / CLV / ROI [CI] |
|---|---|---|
| S2 base | 1,107 / +1.80% / +0.6% [−5.8, +7.2] | 1,013 / +2.63% / +3.1% [−4.1, +10.3] |
| T1 anchor ≤ 3 h | 165 / +6.22% / +5.2% | 133 / +6.88% / +4.3% |
| T2 two anchors | 420 / +6.55% / +14.8% [+4.6, +24.7] | 480 / +4.23% / −2.3% |
| **T3 early ≥ 12 h** | **383 / +7.47% / +10.4% [−0.4, +21.5]** | **459 / +6.93% / +10.8% [−0.1, +21.5]** |
| T4 T1+T2 | 93 / +7.40% / +26.2% | 68 / +8.48% / −18.0% |
**Adopted for the live bot: T3** (strongest, simplest, consistent in both windows, ROI agrees with CLV — the first O/U rule
where outcomes and closing line tell the same story). **T2** runs beside it as a second bot. T1/T4: too few picks/week.

**Robustness check after O3 (not a new test; the two caveats track 3's scan raised):** on the FULL-intraday-history slice
(kickoffs 09-17..09-24) T3 early = n 176, CLV +7.12% [+6.3, +7.9], ROI +16.9% [+0.1, +33.8]; without Bet365 (the AF feed
that dominates outliers elsewhere) n 128, CLV +7.39%. Before 09-17: n 283, CLV +6.81%. So T3 is neither a stale-history
artefact nor a Bet365 artefact. Track 3 (all markets, consensus-outlier, Holm): the mechanism is broad on AF-fed books;
O/U 2.5 / 3.5 / 4.5, AH 0 / ±0.5 and double chance hold after 09-17; takeability of AF opening quotes is still unproven.

## Track 3 — consensus-outlier scan (landed 2026-09-24 evening, `scripts/scan_consensus_outlier_markets.py`)
EV ≥ 5% (Shin): Holm passes 17/21 non-1X2 markets; O/U FT 2.5 +6.4% (n 67), 3.5 +6.8% (50), 4.5 +11.7% (30); AH 0.0 +10.1%;
DNB +10.2%; corners O/U +8.7%. 1X2 calibration row +3.1% (≈ B2). Caveats: shrinks after 09-17 (pre-09-17 consensus built
partly from stale opening rows), Bet365 ≈ 36% of picks, proportional de-vig unusable, whole AH lines conditional on no push.
Next-market candidates for #149 round 2 (later): AH 0 / ±0.5 and DNB (need the push-aware CLV), corners (settlement 67%).
