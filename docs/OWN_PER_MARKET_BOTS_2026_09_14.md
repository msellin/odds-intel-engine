# 🤖 OWN — one paper bot per market? An attack on the market-expansion verdict

**Brief: attack `docs/OWN_MARKET_EXPANSION_2026_09_14.md`, then design what
survives.** Written 2026-09-14. Read-only research; nothing was implemented.

---

## The answer in four lines

1. **The market sweep has the same functional-form hole the config sweep had**
   (ANALYSIS_GOTCHAS §42, now its **sixth** appearance). All 44 of its cells gate
   on a constant **expected-ROI** floor; the live gate is a constant
   **probability-difference** floor. Re-swept on the right form over 369 cells,
   **three of its decisive negatives soften to undecidable** — Asian handicap
   −12.6% → **−3.2%**, 1st-half 1x2 −19.3% → **−1.7%**, cards from "−76% on n=22"
   to *no qualifying picks at all*.
2. **It also asked the wrong question.** It is a pure backtest and never reads the
   fleet. **Five** of the markets it discusses already had a paper bot, and on the
   metric a backtest cannot produce — margin-corrected **own-book CLV** — they are
   not undecidable, they are **decisively negative**.
3. **Including the one I was going to recommend building.**
   `bot_corners_paper_shadow_v1` has 639 settled picks and `clv = NULL` on every
   one, because its private settler never wrote a close. The close was sitting in
   `odds_snapshots` the whole time. Recomputed: **margin-corrected own-book CLV
   −4.86%, CI [−5.59, −4.12], t = −12.93, n = 467**, and at the exact 2%
   probability gate I had drafted a pre-registration for: **−4.45%, t = −7.19,
   n = 179**, with **no dose-response** (−4.85 → −4.64 → −4.45 → −3.50 as the
   floor goes 0 → 1 → 2 → 3%).
4. **So nothing survives as a BUILD — and the reason is the same in every
   market.** Measured across eight independent bots spanning seven markets,
   margin-corrected own-book CLV clusters in a band of **−3.5% to −7.2%** against
   a random leg's ≈ **−7%**. The Shin-de-vigged Pinnacle anchor reliably buys
   **~2 percentage points** of closing-line value, everywhere, and every book we
   can reach charges **7–11%**. **That is a book-margin problem, not a
   market-selection problem, and no per-market bot can fix it.**

Reproduce:

```bash
python3 scripts/own_per_market_probedge_sweep.py --markets 1x2,over_under_25 --days 120 --verbose-all
python3 scripts/own_per_market_probedge_sweep.py --markets over_under_15,over_under_35,over_under_45,double_chance --days 120 --verbose-all
python3 scripts/own_per_market_probedge_sweep.py --markets corners_ou,corners_team,team_total,cards_ou,1x2_1h --days 20 --verbose-all
python3 scripts/own_per_market_probedge_sweep.py --markets asian_handicap --days 45 --verbose-all
python3 scripts/own_per_market_bot_ledger.py --recompute     # what the LIVE bots already say
```

Smoke test: `OWN-PER-MARKET-PROBEDGE`. **369 cells with n≥25** across 12 markets,
2 functional forms, 4 floors, 3 odds bands, 4 book groupings. Every cell prints
its date span, median alignment gap, time-ordered folds, held-out tail and a
**gate-matched** junk-anchor control.

---

## 1. The measurement that decides it: margin-corrected own-book CLV, per market

`clv` is a raw price ratio; break-even is **the closing book's own margin**, so
every row here is corrected per row by that book's own closing overround on that
fixture (`m = sum(1/o)/fair − 1`, the definition in
`settlement.closing_book_margin`). Own-book rows only — `closing_bookmaker IS
NULL` rows came through the arbitrary-book fallback retired today. Cluster-robust
SEs on `match_id`. A randomly chosen leg scores ≈ `−m/(1+m)` ≈ **−7%**.

| market | bot | book(s) | n | **margin-corrected own-book CLV** | t |
|---|---|---|---|---|---|
| **Corners O/U (match)** | `bot_corners_paper_shadow_v1` **(ACTIVE)** | Betano 447, Unibet 20 | **467** | **−4.86%** [−5.59, −4.12] | **−12.93** |
| **Double chance** | `bot_dc_specialist` / `bot_dc_value` | mixed | **487** | **−6.50%** [−7.00, −6.01] | **−25.76** |
| **Double chance** | `bot_dc_strong_fav` | mixed | 267 | **−6.85%** [−7.50, −6.20] | −20.56 |
| **Team totals (FT)** | `bot_team_total_paper_shadow_v1` | Epicbet only | 126 | **−4.49%** [−5.77, −3.22] | −6.90 |
| **1st-half 1x2** | `bot_1h_1x2_paper_shadow_v1` | Epicbet/Betano | 71 | **−5.69%** [−7.28, −4.10] | −7.00 |
| **O/U 3.5** | `bot_sweep_ou35_v1` | 8 books incl. phantoms | 72 | **−4.30%** [−5.71, −2.90] | −5.99 |
| **1x2** (model anchor) | `bot_trigger_1x2_model_v1` | Coolbet/Unibet-Site | 215 | −5.03% [−7.04, −3.01] | −4.89 |
| **O/U 2.5** (model anchor) | `bot_unibet_trigger_ou_v1` | Unibet-Site | 124 | −5.71% [−6.19, −5.23] | −23.27 |
| **BTTS** | `bot_btts_all` | mixed | 13 | −6.83% [−9.96, −3.70] | −4.28 |
| *reference — a random leg* | — | — | — | **≈ −7%** | — |

**Every market, every anchor, every book: −3.5% to −7.2%.** The sharp anchor is
genuinely informative — it lifts CLV ~2pp off the random-leg baseline, which
matches its measured calibration — and it is **uniformly nowhere near the
margin**. `bot_trigger_1x2_sharp_tight_v1`'s own pre-registration reached the
same number (+1.9pp of closing-line value) from the 1x2 side and called it "not
enough". **This document's contribution is that it is not enough in *any*
market we can price.**

*(Two ACTIVE sharp-anchor trigger bots read positive —
`bot_unibet_trigger_sharp_1x2_v1` **+5.35%** [+0.17,+10.53] at n=47 and
`bot_coolbet_trigger_sharp_1x2_v1` +6.18% at n=8, both six days old, both partly
on CLV recomputed here rather than stored. They are noted, not relied on, and
they are 1x2 — a market that already has its instrument.)*

---

## 2. Claim-by-claim verdict on `OWN_MARKET_EXPANSION_2026_09_14.md`

| # | Claim | Verdict | The number |
|---|---|---|---|
| 1 | "44 cells tested … no market's CI excludes zero at the 2% floor in the positive direction" | **CONFIRMED as arithmetic, REFUTED as coverage** | All 44 cells gate on `p×odds−1 ≥ floor`; the live gate is `p − 1/odds ≥ floor`. Re-swept: 369 cells, three market verdicts change sign-strength. Still no market's CI excludes zero positively. |
| 2 | Asian handicap **−12.6%**, "negative at every floor below 8%" | **REFUTED as stated** | Probability floor, pooled, ≥2%, odds ≤2.50: **n=116, −3.19%, CI [−18.27,+11.90], 35d, gap 1.5m**, junk −5.42%. Epicbet alone **+4.34%**. Undecidable, not decisively negative. |
| 3 | 1st-half 1x2 **−19.3%**, "decisively negative" | **REFUTED as stated** | ROI form reproduces (−24.18%, n=111). Probability form, same legs: **−1.72%, n=55, CI [−38.23,+34.79]**. The −19% was the ROI floor's longshot selection. *(Still DO-NOT-BUILD — claim 9.)* |
| 4 | Cards **−76.2%** on n=22, residual grading bias | **CONFIRMED, and worse** | Grading gap reproduces exactly on an independent pass: **−4.1pp, z=−3.0**. And under the live gate cards yields **no cell with n≥25 at any floor or band** — it cannot even be instrumented. |
| 5 | "Corners settleability is 93%, not 16.7% — the denominator was wrong" | **CONFIRMED** | Corners grading-vs-anchor on the bettable slate: **−0.0pp, n=4,896**. The cleanest grading in the study. The correction stands. |
| 6 | "`match_stats.*_ht` holds FULL-MATCH values on 54% of rows" | **CONFIRMED** | Re-measured from scratch: n=1,972, mean FT corners **9.74**, mean "HT" corners **7.39**, identical on **54.2%**. |
| 7 | BTTS / double chance: "Pinnacle does not price it → **GATE 1 FAIL**" | **REFUTED for double chance** | DC outcomes are unions of 1X2 outcomes, so de-vigged Pinnacle 1X2 gives `P(1X)=P(h)+P(d)` exactly — §4 says so and `get_devigged_pinnacle_close_prob()` implements it. Built here: **1,141 aligned Coolbet candidates over 38 days and 1,591 Epicbet over 10 days**. Gate 1 conflated "Pinnacle quotes it" with "a sharp anchor exists". **BTTS's failure stands** — it needs the joint, not the margin. |
| 8 | 1st-half totals: "**GATE 2** — Pinnacle quotes quarter lines, Epicbet only 0.5/1.5/2.5 … zero co-priced fixtures" | **REFUTED — it is a live outage** | Pinnacle quotes `over_under_1h_05` on **1,176** fixtures and `_15` on **1,794** — exactly Epicbet's lines. **Pinnacle's `over_under_1h_*` collection stopped 2026-09-04**; Epicbet's started **2026-09-05**. Zero overlap because the anchor feed died one day before the book feed began, while `1x2_1h`, `corners_1h_*` and `team_total_1h_*` at Pinnacle all still write daily. |
| 9 | Team totals "indistinguishable / decays"; 1H 1x2 "negative" | **CONFIRMED, on far better evidence than given** | Both had a live bot. Own-book CLV **−4.49% (t=−6.90)** and **−5.69% (t=−7.00)**. Both retired today (migration 348, EV −4.98% / −6.25%) — reproduced here independently to within 0.5pp. **Measured negative**, not "not yet measurable". |
| 10 | "Window: 17 days — the whole history there is; every bolt-on market's first row is 2026-08-29" | **REFUTED as stated; the real constraint is worse** | False for four markets (`over_under_15/35/45` Coolbet from 2026-05-20, `asian_handicap` 2026-05-28, `double_chance` 2026-05-31, `btts` 2026-05-20). **But 120 days buys only ~38**, because retention already deleted the path: Coolbet 1x2 rows are **27 in May, 42 in June, 15 in July, 13,614 in August**. n roughly doubles (O/U 3.5: 92 → 150; O/U 1.5: 27 → 48) and **no verdict changes**. |
| 11 | Corners O/U (match): "the cleanest market in the study; **GATE 4**, no measurable edge, needs 15 years" | **CONFIRMED — and it took eight days, not fifteen years** | Corners' *alignable* backtest history is **8 days**, so the backtest genuinely cannot decide it (best cell **n=44, +10.86%, CI [−35.10,+56.82]**). But the live bot's own-book CLV, recomputed, is **−4.86%, t=−12.93, n=467**. The sweep's "15 years" figure is the **ROI** requirement; the **CLV** requirement was already met and unread. |
| — | **"Add nothing. Build no new market bot on this evidence."** | **CONFIRMED — and it is now a stronger statement than the sweep could make.** | Not "no market showed an edge in 17 days". **Seven markets measured, all negative on the fastest-converging metric available, in a band 2pp wide.** |

### The functional-form hole, in one line

`roi_edge = prob_edge × odds`. A constant **ROI** floor demands **2.0pp** of
probability at odds 1.00 and **0.5pp** at 4.00 — it systematically buys
longshots. A constant **probability** floor does the opposite. The sweep
compounds it with `max(legs, key=edge)` *within a ladder*, so on corners and
cards its selection rule takes the longest price on the board. That is why cards
read −76% on 22 legs and reads *nothing at all* under the gate we run.

---

## 3. Per-market recommendation table

Coverage = books with a full, aligned complement against a de-viggable Pinnacle
line, with the span each book's usable history actually covers. "CLV path" =
whether `settlement._market_complement_selections` can produce the own-book
closing margin a stopping rule needs. Edge cells are **pooled, probability floor
2%, odds ≤ 2.50**, or the widest available cell where that produced n<25 (marked
*w*).

| market | Coolbet | Epicbet | Unibet-Site | settleable on the BETTABLE slate | same-quantity | CLV path | best cell — n, ROI, CI, span, gap | **verdict** | gate if built |
|---|---|---|---|---|---|---|---|---|---|
| **1x2** | ✅ 38d | ✅ 13d | ✅ 6d | 100% | n/a | ✅ | 113, +16.73%, [−4.3,+37.8], 36d, 2.3m | **ALREADY INSTRUMENTED** — `bot_trigger_1x2_sharp_tight_v1` | — |
| **O/U 2.5** | ✅ 38d | ✅ 13d | ✅ 6d | 100% | PASS | ✅ | 54, +24.96%, [−2.2,+52.1], 36d, 5.7m | **ALREADY INSTRUMENTED** | — |
| **Corners O/U (match)** | ✅ 7d | ✅ 9d | ❌ | **93%** (grading **−0.0pp**) | 10 of 24 lines | ✅ in code, **NULL in practice** | *w* 44, +10.86%, [−35.1,+56.8], **8d** | **DO-NOT-BUILD — MEASURED NEGATIVE.** CLV **−4.86%, t=−12.9, n=467**; **−4.45%, t=−7.2** at the 2% gate | — |
| **Double chance** | ✅ 38d | ✅ 10d | ❌ | 100% | n/a | ⚠️ 3-line extension | 81, +2.05%, [−14.6,+18.7], 36d, 4.4m | **DO-NOT-BUILD — MEASURED NEGATIVE.** CLV **−6.50%, t=−25.8, n=487** | — |
| **Team totals (FT)** | ✅ 4d | ✅ 10d | ✅ 4d | 100% | 7 of 26 lines | ✅ | 110, +6.59%, [−12.5,+25.7], 9d | **DO-NOT-BUILD — MEASURED NEGATIVE.** CLV **−4.49%, t=−6.9** | — |
| **1st-half 1x2** | ✅ 4d | ✅ 10d | ❌ | 99.9% | n/a | ✅ | *w* 55, −1.72%, [−38.2,+34.8], 8d | **DO-NOT-BUILD — MEASURED NEGATIVE.** CLV **−5.69%, t=−7.0** | — |
| **O/U 3.5** | ✅ 39d | ✅ 13d | ✅ 6d | 100% (grading **−0.0pp**) | PASS | ✅ | 46, +7.86%, [−24.0,+39.7], 35d, 6.0m *(w: 150, +8.05%, [−10.5,+26.6], 39d)* | **BUILD-AS-INSTRUMENT — the only candidate left, and a weak one** | `P_shin − 1/odds ≥ 2%`, odds ≤ 4.00 cap, Coolbet/Epicbet/Unibet-Site, one pick per (fixture, book) |
| **Corners O/U (team)** | ✅ 8d | ✅ 9d | ❌ | 94% (grading −0.3pp) | 8 of 42 lines | ✅ | *(prob 1%, ≤2.50)* 57, +12.45%, [−21.2,+46.1], **8d** | **DO-NOT-BUILD** — no bot of its own, but same anchor, same books and same ladder as match corners, which is decisively negative | — |
| **Asian handicap** | ✅ 38d | ✅ 12d | ❌ | 100% | 22 of 47 lines | ❌ (§16 — a fixed rung is not a close) | 116, −3.19%, [−18.3,+11.9], 35d, 1.5m | **DO-NOT-BUILD** — undecidable *and* unmeasurable: no stopping rule can be computed | — |
| **Cards O/U** | ✅ 8d | ✅ 9d | ❌ | 97.6% present, **grading −4.1pp (z=−3.0)** | 7 of 18 lines | ❌ (no complement defined) | **no cell with n≥25 under the live gate** | **DO-NOT-BUILD** — grading bias, no CLV path, no volume | — |
| **O/U 1.5** | ✅ 38d | ✅ | trace | 100% | PASS | ✅ | *w* 48, −6.14%, [−41.3,+29.0], 33d | **DO-NOT-BUILD** — no volume at any floor, on 2× the sweep's data | — |
| **O/U 4.5** | ✅ 38d | ✅ | trace | 100% | PASS | ✅ | *w* 45, −9.51%, [−45.3,+26.3], 35d | **DO-NOT-BUILD** — no volume | — |
| **Corners O/U (1H)** | ✅ | ❌ | ❌ | **label wrong on 54% of rows** | — | ✅ | not tested | **DO-NOT-BUILD** until `HT-STATS-FULL-MATCH-FALLBACK` is fixed | — |
| **1st-half totals** | ❌ | ✅ | ❌ | — | — | ✅ | **anchor feed dead since 2026-09-04** | **CANNOT-DETERMINE** — repair the Pinnacle feed, then re-ask | — |
| **BTTS** | ✅ 38d | ✅ | ❌ | 100% | n/a | ✅ | no sharp anchor exists | **DO-NOT-BUILD** — not derivable from 1X2 (needs the joint) | — |
| **Team totals (1H)** | ❌ | ❌ | ❌ | — | — | ✅ | no executable price | **DO-NOT-BUILD** | — |
| **Team cards; corners handicap** | ✅ / ❌ | ✅ / ✅ | ❌ | — | — | ❌ | **Pinnacle prices neither** | **DO-NOT-BUILD** — two markets the sweep's catalogue missed entirely; both fail gate 1 | — |
| **Draw no bet** | ❌ | ❌ | ❌ | — | — | — | no price at any of our books | **DO-NOT-BUILD** | — |

### Measured negative vs not yet measurable — the distinction the brief asked for

| | markets | consequence |
|---|---|---|
| **MEASURED NEGATIVE** on own-book CLV, t < −6 | **corners (match)**, double chance, team totals FT, 1H 1x2 | Closed. Do not re-open on a changed gate — only on a changed **book**. |
| **NOT YET MEASURABLE** | **O/U 3.5** (39d of backtest, no bot of its own), corners (team) | An instrument is the only resolver. O/U 3.5 is the single BUILD-AS-INSTRUMENT below; team corners is not, because match corners already answered it at the same anchor and books. |
| **UNMEASURABLE TODAY** — no stopping-rule metric exists | Asian handicap, cards | An instrument here accrues a ledger nobody can read. Build the metric first, or not at all. |
| **BLOCKED BY A DEFECT, not a verdict** | 1H totals (dead anchor feed), corners 1H (broken label) | Repairs, not research. |
| **NO ANCHOR — structural** | BTTS, team cards, corners handicap, draw-no-bet | Will not change. |

---

## 4. The finding the sweep could not have made: the corners instrument was already running, and already answerable

```
bot_corners_paper_shadow_v1  ACTIVE  n=639  2026-09-07..2026-09-14
    priced at: Betano 591, Unibet 48
    ROI                                    n=639  +8.53%  CI[-3.16,+20.22]
    own-book CLV, RAW (as stored)          none — clv is NULL on 639 of 639
    own-book CLV, MARGIN-CORRECTED
      (recomputed from odds_snapshots)     n=467  -4.86%  CI[-5.59,-4.12]  t=-12.93
```

Four separate problems, and the fourth is the one that mattered:

1. **It prices books outside the brief.** `PLACEMENT_BOOKS = ("Betano", "Unibet")`
   in `workers/jobs/corners_paper_bot.py`. Betano *is* EMTA-licensed and in
   `ACCESSIBLE_BOOKMAKERS` (95.1% fixture coverage) — **but it is an
   API-Football feed we do not self-scrape and cannot place at**, and the
   `OWN_BOOK_UNIVERSE` rule is that a book enters the placeable set on a
   **verified** feed or not at all.
   *Fairness check, because the reflex here is to assume phantom prices:* Betano
   is **not** phantom-high. Paired on shared fixtures against Pinnacle's close,
   median price ratio **−0.69%** on 1x2 (n=4,317) and **−0.50%** on corners
   (n=3,170) — tighter than Coolbet or Epicbet. The 'Unibet' AF feed, by
   contrast, is the known 33.1%-phantom one, and **48 picks are priced on it**.
2. **Its gate is the wrong shape.** `EDGE_FLOOR = 0.0`, no odds band, best price
   across a 13-line ladder — the longest-price selection §2 shows is backwards.
3. **Its CLV is NULL by design.** `settle_picks()`'s own docstring: *"clv columns
   stay NULL — no corners closing anchor is wired"*. So
   `PAPER-BOT-CLV-UNBLOCK-2026-09-13`, which taught
   `_market_complement_selections` about `corners_`, unblocked the **generic**
   settler and never reached corners, because corners settles through its **own**
   settler. RELIABILITY_LEDGER's "a second code path inheriting no gates",
   appearing in the measurement layer.
4. **And the close it needed was there all along.** Betano carries a full
   own-book closing complement on **75.6%** of its corners series (against
   Coolbet 5.2% and Epicbet 31.9%). Recomputing CLV from `odds_snapshots` at the
   bot's **own** recommended book resolves **467 of 639** picks and answers the
   corners question outright:

| gate applied to the existing corners ledger | n | margin-corrected own-book CLV | t |
|---|---|---|---|
| prob-edge ≥ 0% (the bot's actual rule) | 466 | **−4.85%** [−5.59, −4.12] | −12.90 |
| prob-edge ≥ 1% | 313 | **−4.64%** [−5.65, −3.62] | −8.96 |
| **prob-edge ≥ 2%** (the gate I had drafted) | **179** | **−4.45%** [−5.66, −3.24] | **−7.19** |
| prob-edge ≥ 3% | 81 | −3.50% [−5.96, −1.04] | −2.79 |
| odds ≤ 2.00 / 2.00–4.00 | 164 / 303 | −4.88% / −4.84% | — |

**No dose-response, no odds-band structure, every cell decisively negative.**
A sharper gate on corners does not find edge; it finds the same −4.5% on fewer
bets. **I set out to recommend this bot and the data killed it.**

---

## 5. Three constraints any future per-market bot must be designed around

### (a) The stopping rule's denominator is picks **with an own-book close** — 5% to 53% of them

Share of (fixture, market) series where the book itself has a full closing
complement, last 7 days:

| book | 1x2 | O/U 2.5 | O/U 3.5 | corners (match) | corners (team) |
|---|---|---|---|---|---|
| **Coolbet** | 8.3% | 8.0% | 7.8% | **5.2%** | 6.0% |
| **Epicbet** | 36.0% | 35.6% | 33.5% | 31.9% | 33.9% |
| **Unibet-Site** | 53.2% | 49.2% | 47.5% | — | — |
| *Betano, for scale* | 75.3% | — | — | **75.6%** | — |
| *Pinnacle, for scale* | — | — | 84.9% | 84.0% | 83.9% |

It is a **book property, not a market property**. Consequence: **n ≥ 300
CLV-eligible rows needs ~1,000 picks** at the current mix, and a **Coolbet-only**
instrument would need ~6,000. This is visible in the live fleet right now —
`bot_coolbet_trigger_sharp_ou_v1` has 32 picks and **3** CLV-eligible rows. Every
pre-registration in `dev/active/` that says "n ≥ 300" without saying *n ≥ 300 of
what* will not trigger on the schedule its author expects.

### (b) Retention deletes the past, so forward time is the only instrument

Coolbet `1x2` rows by month: **May 27, June 42, July 15, August 13,614.** A
120-day window buys ~38 days. **No market will ever have a backtest longer than
~5 weeks at our books.** This is the strongest structural argument for
instruments over sweeps — and it applies to every future version of this
question.

### (c) On short-history markets the same-quantity gate doubles as a volume filter

`gate_same_quantity` needs **30 paired fixtures per line**. Over an 8-day corners
window that rejects **14 of 24** match lines, **34 of 42** team lines and **19 of
26** team-total lines — for thinness, not disagreement. Gate 4's `n` on those
markets is the n of the densest rungs, not of the market. An instrument should
fire on the full ladder and **record the line**, letting the pairing test run
later on accumulated data.

---

## 6. Pre-registration — `bot_sharp_ou35_v1`, the only BUILD-AS-INSTRUMENT left

**Register it only if the owner wants the question resolved. The prior is that it
loses, and §1 says why.**

```
anchor   Shin de-vig of the Pinnacle over_under_35 two-way book
gate     P_shin − 1/book_odds  ≥  2%          (PROBABILITY difference, NOT P×odds−1)
odds     ≤ 4.00                                (a CAP; no floor beyond 1.01)
books    Coolbet, Epicbet, Unibet-Site. One pick per (fixture, book);
         a book is chosen WITHIN, never BETWEEN (§52, §55).
align    anchor quote within 15 min of the book quote
guard    production outlier filter (book price ≤ Pinnacle × 1.30, two-way)
staking  PAPER. Not in PLACEABLE_BOTS, no placer toggle, cannot stake.
```

**Honest prior.** Pooled, prob ≥2%, ≤2.50: **n=46, +7.86%, CI [−23.98, +39.69]**,
35 days, median gap 6.0 min. Widest cell **n=150, +8.05%, CI [−10.45, +26.56]**,
39 days. Grading-vs-anchor **−0.0pp (n=13,626)** — as clean as O/U 2.5. Matched
junk control **−10.1%**.

**Every reason to disbelieve it, stated now so it cannot be quietly dropped
later:**

1. **Its own market family already reads negative.** `bot_sweep_ou35_v1`
   (n=407, best-of-8-books, retired) has margin-corrected own-book CLV
   **−4.30%, t=−5.99, n=72**; `bot_ou35_model_v1` (ACTIVE, Coolbet, model
   anchor) is **−15.01% ROI** on n=223. Neither is the sharp rule, but neither
   points the other way.
2. **Every other rung of the O/U ladder has come up empty twice.** 1.5 and 4.5
   have no volume at any floor in either sweep; 3.5 is the only rung with any,
   and its CI is ±25pp.
3. **It is highly correlated with the O/U 2.5 bots already running** — same
   fixtures, one rung out. It is closer to a second reading of an existing
   instrument than a new market.
4. **Volume makes it slow.** ~1.3 qualifying picks/day pooled at the 2% gate
   (3.8/day at 1%). With §5a's denominator, **n ≥ 300 CLV-eligible rows is
   roughly 12–18 months.** If that is unacceptable, do not start it.
5. **The unifying result in §1 predicts its answer.** Seven markets, −3.5% to
   −7.2%. There is no reason O/U 3.5 sits outside that band, and the honest
   expected value of this instrument is another point inside it.

**Stopping rules (LOCKED).** Evaluated on this bot's own settled picks,
**own-book** close only (`closing_bookmaker IS NOT NULL`), margin-corrected per
row via `closing_book_margin()`.

| checkpoint | criterion | action |
|---|---|---|
| **n ≥ 300 CLV-eligible rows** (≈ 1,000 picks — §5a) | margin-corrected own-book CLV **> 0**, CI excluding 0 | **PROMOTE** to a real-money candidate — owner decision, still gated on the OWN kill criterion |
| n ≥ 300 CLV-eligible | margin-corrected own-book CLV **< −2%** | **RETIRE** |
| n ≥ 300 CLV-eligible | anything between | **KEEP OBSERVING. Do not re-cut the rule.** |
| n ≥ 100 CLV-eligible | CLV **< −4%**, CI excluding −2% | **EARLY RETIRE** — that is the band all seven measured markets sit in, and a further 900 picks will not move it |

**ROI may never promote this bot, at any value.** Per-bet return sd ≈ 1.3, so
confirming a true +3% ROI at 80% power needs **≈ 15,600 settled bets** — decades
at 1.3/day. A +30% ROI at n=300 is not grounds for promotion and must not be
presented as such.

**What would invalidate the test:** any change to the gate, the odds cap, the
book set, the anchor or the alignment window. Each starts a new instrument with a
new name and a new start date.

**Negative control:** a junk-anchor arm at the **identical** gate, floor, band and
one-pick-per-fixture collapse — never at a nominally-equal floor that passes 10×
the legs, which is the mismatch that made "junk beats real" look true this
morning. On this harness that arm reads **−10.1%** on O/U 3.5 against the real
arm's +7.9%.

**What was NOT pre-registered, and why.** A corners instrument. I drafted one; §4
killed it with n=179 at its own gate. If corners is ever revisited it must be on
a **different book**, not a different floor.

---

## 7. Method, and what it cannot do

* **Assemble, then align** (§63) — `assemble` imported verbatim from
  `own_market_expansion_sweep`; no cross-book timestamp join anywhere.
* **Alignment ≤ 15 min, median gap printed per cell** — 1.1 to 7.9 minutes on
  every cell quoted here.
* **Reuse vs re-implementation.** Leg construction, de-vig, grading, the outlier
  guard and the clustered CI are imported from the sweep — two independent
  implementations already agreed to the digit on six dipstick figures, so a third
  buys nothing. **The selection rule, which is where the hole is, is written from
  scratch.** Dipstick: at the sweep's own settings this harness returns O/U 2.5
  **n=101, +6.06%** against its published **101, +6.1%**, and 1x2 **n=483,
  −14.84%** against **479, −14.1%** (the 4-leg gap is the moving `now()` window).
* **`shadow_bets_unique`** everywhere (§5). **Own-book rows only** for every CLV
  figure. **`clv` treated as a raw ratio**, corrected per row.
* **Phantom books excluded**: `'Unibet'`, `'Unibet-Kambi'`, `Max`, `Avg`,
  `Betfair Exchange`, `BetWin`, `Betfred`.
* **Cluster-robust SEs on `match_id`**, ROI and CLV alike. **369 cells with
  n≥25**, each with time-ordered folds and a held-out final 25%.

**What it cannot do.** It is a near-closing-price backtest for most of its span
(§59), so any sharp-anchor edge largest *early* is invisible to it. It cannot
separate the 2026-09-02 regime change (Epicbet joining, retention not yet
pruning, the 2026-09-11 `is_closing` widening) from a real effect. And the
recomputed corners CLV is measured at **Betano**, not at Coolbet or Epicbet — it
is evidence about the rule against a book whose close tracks Pinnacle to within
0.5%, which is the strongest available proxy but is not the same thing as
measuring it at our own two books.

---

## 8. A caveat that lands on an already-shipped instrument

Stated because it was measured in passing and it changes a locked prior.

`dev/active/own-sharp-tight-preregistration.md` rests partly on *"the matched
junk control reads ≈0 on the identical band (−1.48%)"*. At the tighter alignment
used here (≤15 min, one pick per fixture+book) it does not:

| 1x2, pooled, prob floor 2% | REAL | JUNK (identical gate) |
|---|---|---|
| odds 1.01–2.50 | n=113 **+16.73%** | n=773 **+4.35%** |
| odds 1.01–99 | n=243 −1.10% | n=3,703 −15.57% |

The junk arm gains **+19.9pp** by moving into the short band; the real arm gains
**+17.8pp**. **Real-minus-junk is ~13pp in both bands.** The anchor's
contribution is therefore roughly *flat in odds*, and the `odds ≤ 2.50` cap is
not where the anchor's information lives — it is where **this window's
favourites happened to win**. On O/U 2.5 the junk arm stays at −4% to −5% (the
vig) in every band, so this is a **1x2-specific** property of the sample, not a
harness artefact.

It does not change the instrument's stopping rule — it reinforces its own stated
prior that the ROI is luck — but the negative-control sentence and the `n≥300`
denominator should be restated when someone next opens that file.

---

## 9. Recommended rows for `PRIORITY_QUEUE.md`

Not filed here — another agent owns that file this session. Ordered by value.

* **CORNERS-BOT-RETIRE-ON-CLV** 🤖 OWN, ~1h — `bot_corners_paper_shadow_v1` is
  ACTIVE and its margin-corrected own-book CLV is **−4.86% (t=−12.9, n=467)**,
  and **−4.45% (t=−7.2)** at a 2% probability gate with no dose-response. Retire
  it **in the DB** (per `RETIRED-BOTS-KEPT-GENERATING`, a registry edit alone
  does not stop generation) and record the number. *Do not replace it.*
* **CORNERS-SETTLER-NO-CLV** 🤖 OWN, ~2h — `corners_paper_bot.settle_picks()`
  writes `clv = NULL` by design, so 639 picks looked unreadable for a week while
  Betano carried a full own-book close on 75.6% of them. Even if the bot is
  retired, wire the close: the next market-specific settler will repeat this.
  `scripts/own_per_market_bot_ledger.py --recompute` is the working reference
  implementation.
* **PINNACLE-1H-TOTALS-FEED-DEAD** 🤖👥 BOTH, ~1h — Pinnacle
  `over_under_1h_*` last wrote **2026-09-04** while every other Pinnacle 1H
  market still writes daily. The "1st-half totals has zero co-priced fixtures"
  verdict is this outage, not a market property. 👥 too: we publish nothing on a
  market whose sharp anchor silently died.
* **HT-STATS-FULL-MATCH-FALLBACK** 🤖👥 BOTH, ~1h — independently confirmed
  here (n=1,972; **54.2%** of `_ht` rows equal their full-match twin). Already
  recommended by `OWN_MARKET_EXPANSION_2026_09_14.md`; restated because it is
  the sole blocker on corners-1H and on any future 1H stat model.
* **PREREG-N300-DENOMINATOR** 🤖 OWN, ~30m — every `dev/active/`
  pre-registration says "n ≥ 300" without saying *of what*. Only 5–53% of picks
  carry an own-book close (§5a), so the real requirement is ~1,000 picks and a
  Coolbet-only bot would need ~6,000. Restate the denominator in
  `own-sharp-tight-preregistration.md` and `picks-forward-test-preregistration.md`.
* **SHARP-TIGHT-JUNK-CONTROL-RESTATE** 🤖 OWN, ~30m — §8. The matched junk
  control is **+4.35%**, not ≈0, and real-minus-junk is ~13pp in *both* bands.
* **CLV-COMPLEMENT-DOUBLE-CHANCE** 🤖 OWN, ~30m — `double_chance` is excluded
  from `_market_complement_selections` because its outcomes "do not form a
  partition". True, and irrelevant to a **margin**: the three DC prices cover
  every outcome exactly twice, so `sum(1/o)/2 − 1` is the book's own DC
  overround. Adding it is what made the **−6.50% (t=−25.8)** verdict computable.
  A measurement fix that **closes** DC, not a route back into it.
* **MARKET-SWEEP-FUNCTIONAL-FORM** 👥 PICKS, ~1h — add the probability-floor
  form to `own_market_expansion_sweep.py` (or link this doc from it), so the
  next person who reads "AH is −12.6%" learns it is −3.2% under the gate we
  actually run. §42's sixth appearance; the recurrence is the problem.

## 10. Docs this makes stale

* `docs/OWN_MARKET_EXPANSION_2026_09_14.md` — claims 2, 3, 7, 8, 10 and 11 of
  §2. Its **recommendation stands and is now better supported**; six of its
  stated reasons do not.
* `docs/MARKET_DATA_MAP.md` — if it records "Pinnacle does not price double
  chance" as a reason DC cannot be evaluated, the reason is wrong (§4 of
  ANALYSIS_GOTCHAS); the conclusion is now right for a better reason.
* `dev/active/own-sharp-tight-preregistration.md` — its negative-control
  sentence and its `n ≥ 300` denominator (§8, §5a).
* `workers/jobs/settlement.py` (~line 992) — the comment lists
  `bot_corners_paper_shadow_v1` among the bots whose CLV was unblocked on
  2026-09-13. **Corners was not unblocked**; it settles through its own settler
  and carried `clv = NULL` on 639 of 639 picks.
* `workers/registry/bot_registry.py` — `bot_corners_paper_shadow_v1`'s
  description ("Forward paper test on executable corners books") should record
  that the forward test **concluded**, and its answer.
