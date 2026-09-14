# 🤖 OWN — one paper bot per market? An attack on the market-expansion verdict

**Brief: attack `docs/OWN_MARKET_EXPANSION_2026_09_14.md`, then design what
survives.** Written 2026-09-14. Read-only research; nothing was implemented.

**Answer in three lines.**

1. **The market sweep has the same functional-form hole the config sweep had**
   (§42, now the *sixth* appearance). It swept a constant **expected-ROI** floor
   on every one of its 44 cells; the live gate is a constant
   **probability-difference** floor. Re-swept on the right form, **three of its
   decisive negatives soften to undecidable** — Asian handicap −12.6% → **−3.2%**,
   1st-half 1x2 −19.3% → **−1.7%**, cards from "−76%" to *no qualifying picks at
   all*. Its **conclusion survives**; three of its reasons do not.
2. **But the sweep asked the wrong question, and the answer to the right one was
   already in the database.** It never looked at the fleet. Four of the markets
   it discusses **already had a paper bot**, and on the metric it could not
   compute — margin-corrected **own-book CLV** — they are not "undecidable", they
   are **decisively negative**: team totals **−4.49% (t=−6.9)**, 1H 1x2
   **−5.69% (t=−7.0)**, double chance **−6.50% (t=−25.8, n=487)**. Three of
   those bots were retired earlier today on exactly this evidence.
3. **One instrument is worth building, and it is a REPLACEMENT, not a new
   market.** `bot_corners_paper_shadow_v1` is **live right now**, has written
   **639 settled picks** since 2026-09-07 — and prices every one of them at
   **Betano (591) and 'Unibet' (48)**. Neither is placeable; `'Unibet'` is on our
   own phantom-feed exclusion list. Its `clv` is **NULL on 639 of 639 picks**.
   The corners instrument the owner is asking for exists, measures a market we
   cannot bet, and has no working measuring device.

Reproduce:

```bash
python3 scripts/own_per_market_probedge_sweep.py --markets 1x2,over_under_25 --days 120 --verbose-all
python3 scripts/own_per_market_probedge_sweep.py --markets over_under_15,over_under_35,over_under_45,double_chance --days 120 --verbose-all
python3 scripts/own_per_market_probedge_sweep.py --markets corners_ou,corners_team,team_total,cards_ou,1x2_1h --days 20 --verbose-all
python3 scripts/own_per_market_probedge_sweep.py --markets asian_handicap --days 45 --verbose-all
python3 scripts/own_per_market_bot_ledger.py           # what the LIVE bots already say
```

Smoke test: `OWN-PER-MARKET-PROBEDGE`. **369 cells with n≥25 were evaluated**
across 12 markets, 2 functional forms, 4 floors, 3 odds bands and 4 book
groupings. Every cell prints its own date span, median alignment gap, folds,
held-out tail and a gate-matched junk-anchor control.

---

## 1. Claim-by-claim verdict on `OWN_MARKET_EXPANSION_2026_09_14.md`

| # | Claim | Verdict | The number |
|---|---|---|---|
| 1 | "44 cells tested … no market's CI excludes zero at the 2% floor in the positive direction" | **CONFIRMED as arithmetic, REFUTED as coverage** | All 44 cells gate on `p×odds−1 ≥ floor`. The live gate is `p − 1/odds ≥ floor`. Re-swept on the live form plus odds bands: 369 cells, and the *sign of three market verdicts changes* (rows below). Still no market's CI excludes zero positively. |
| 2 | "Asian handicap … **−12.6%**, negative at every floor below 8%" | **REFUTED as stated** | ROI-floor form reproduces. **Probability-floor form, pooled, ≥2%, odds ≤2.50: n=116, −3.19%, CI [−18.27, +11.90], 35d, gap 1.5m, junk −5.42%.** Epicbet alone **+4.34%**. AH is *undecidable*, not "decisively negative". |
| 3 | "1st-half 1x2 … **−19.3%**, decisively negative" | **REFUTED as stated** | ROI form at 1% reproduces (**−24.18%, n=111**). Probability form, same legs: **−1.72%, n=55, CI [−38.23, +34.79]**. The −19% was a longshot-selection artefact of the floor's shape. *(It is still a DO-NOT-BUILD — see claim 9.)* |
| 4 | "Cards … **−76.2%** on n=22, residual grading bias" | **CONFIRMED, and worse than stated** | Grading gap reproduces exactly: **−4.1pp, z=−3.0** on my independent pass. And under the live gate cards produces **no cell with n≥25 at any floor or band** — the market cannot even be instrumented. |
| 5 | "Corners settleability is 93%, not 16.7% — the denominator was wrong" | **CONFIRMED** | Independently: corners grading-vs-anchor on the bettable slate reads **−0.0pp (n=4,896)** over 20 days. The cleanest grading in the study. The correction stands. |
| 6 | "`match_stats.*_ht` holds FULL-MATCH values on 54% of rows" | **CONFIRMED** | Re-measured from scratch: n=1,972 rows with both, mean FT corners **9.74**, mean "HT" corners **7.39**, `corners_*_ht = corners_*` on **54.2%**. Corners-1H is unlabelable today. |
| 7 | "BTTS / double chance: **Pinnacle does not price it → GATE 1 FAIL**" | **REFUTED for double chance** | DC outcomes are *unions* of 1X2 outcomes, so de-vigged Pinnacle 1X2 gives `P(1X)=P(h)+P(d)` **exactly** — ANALYSIS_GOTCHAS §4 says so and `get_devigged_pinnacle_close_prob()` already implements it. Built here: DC yields **1,141 Coolbet + 1,591 Epicbet aligned candidates over 38 days**. Gate 1 conflated "Pinnacle quotes this market" with "a sharp anchor exists". **BTTS's gate-1 failure stands** — BTTS needs the joint, not the margin, and is not derivable. |
| 8 | "1st-half totals: **GATE 2** — Pinnacle quotes quarter lines, Epicbet only 0.5/1.5/2.5 … zero aligned co-priced fixtures" | **REFUTED — and it is a live outage** | Pinnacle quotes `over_under_1h_05` on **1,176 fixtures** and `_15` on **1,794** — the exact lines Epicbet prices. There is no line mismatch. **Pinnacle's `over_under_1h_*` collection stopped on 2026-09-04**; Epicbet's started **2026-09-05**. The overlap is zero because the anchor feed died one day before the book feed began — while `1x2_1h`, `corners_1h_*` and `team_total_1h_*` at Pinnacle all continue to 2026-09-14. |
| 9 | "Team totals (FT) … indistinguishable / decays" and "1H 1x2 … negative" | **CONFIRMED, on far better evidence than given** | Both had a live paper bot. **Margin-corrected own-book CLV: team totals −4.49%, CI [−5.77,−3.22], t=−6.90, n=126; 1H 1x2 −5.69%, CI [−7.28,−4.10], t=−7.00, n=71.** Both were retired today (migration 348, EV −4.98% / −6.25%) — reproduced here independently to within 0.5pp. These are **measured negative**, not "not yet measurable". |
| 10 | "Window: 17 days — the whole history there is, because every bolt-on market's first row is 2026-08-29" | **REFUTED as stated; the real constraint is worse** | False for four markets (`over_under_15/35/45` Coolbet from 2026-05-20, `asian_handicap` from 2026-05-28, `double_chance` from 2026-05-31, `btts` from 2026-05-20). **But extending the window to 120 days buys only ~38 days**, because retention has already deleted the price path: Coolbet's 1x2 rows are 27 in May, 42 in June, 15 in July, **13,614 in August**. So the sweep under-used the data by ~2.2×, not ~7×. n roughly doubles (O/U 3.5: 92 → 150; O/U 1.5: 27 → 48) and **no verdict changes**. |
| 11 | "Corners O/U (match) … the cleanest market in the study; **GATE 4**, no measurable edge" | **CANNOT-DETERMINE, and the sweep over-stated its own window** | Corners' *alignable* history is **8 days (2026-09-05..09-13)**, not 17. Best cell: pooled, prob ≥1%, n=44, **+10.86%, CI [−35.10, +56.82]**. That is not "no edge", it is **no measurement**. |
| — | **"Add nothing. Build no new market bot on this evidence."** | **CONFIRMED for every market as a STRATEGY. REFUTED as a complete answer.** | Two markets are worth an **instrument**, one of them is a *repair* of something already running, and four markets are **measured negative** — which is a different fact with a different consequence. |

### The functional-form hole, in one line

`roi_edge = prob_edge × odds`. A constant **ROI** floor therefore demands
**2.0pp** of probability at odds 1.00 and **0.5pp** at odds 4.00 — it
systematically **buys longshots**. A constant **probability** floor does the
opposite. The sweep also applied `max(legs, key=edge)` *within a ladder*, which
compounds it: on corners and cards the selection rule takes the longest price on
the board. That is why cards read −76% on 22 legs and reads *nothing at all*
under the gate we actually run.

---

## 2. Per-market recommendation table

Coverage = books with a **full, aligned complement** against a de-viggable
Pinnacle line. "CLV path" = whether
`settlement._market_complement_selections` can produce the own-book closing
margin the stopping rule needs. Edge cells are **pooled, probability floor 2%,
odds ≤2.50** unless the market produced no cell with n≥25 there, in which case
the widest cell is shown and marked.

| market | Coolbet | Epicbet | Unibet-Site | settleable (bettable slate) | same-quantity | CLV path | best cell — n, ROI, CI, span, gap | verdict |
|---|---|---|---|---|---|---|---|---|
| **1x2** | ✅ 38d | ✅ 13d | ✅ 6d | 100% | n/a | ✅ | 113, **+16.73%**, [−4.3,+37.8], 36d, 2.3m | **already instrumented** (`bot_trigger_1x2_sharp_tight_v1`) — no second bot |
| **O/U 2.5** | ✅ | ✅ | ✅ | 100% | PASS | ✅ | 54, **+24.96%**, [−2.2,+52.1], 36d, 5.7m | **already instrumented** |
| **O/U 3.5** | ✅ 39d | ✅ 13d | ✅ 6d | 100% (grading gap **−0.0pp**) | PASS | ✅ | 46, +7.86%, [−24.0,+39.7], 35d, 6.0m *(widest: 150, +8.05%, [−10.5,+26.6], 39d)* | **BUILD-AS-INSTRUMENT** (second priority) |
| **O/U 1.5** | ✅ | ✅ | trace | 100% | PASS | ✅ | *(widest)* 48, −6.14%, [−41.3,+29.0], 33d | **DO-NOT-BUILD** — no volume at any floor, on 2× the sweep's data |
| **O/U 4.5** | ✅ | ✅ | trace | 100% | PASS | ✅ | *(widest)* 45, −9.51%, [−45.3,+26.3], 35d | **DO-NOT-BUILD** — no volume |
| **Corners O/U (match)** | ✅ 7d | ✅ 9d | ❌ | **93%** (grading **−0.0pp**) | 10 of 24 lines | ✅ in code, **broken in practice** | *(widest)* 44, +10.86%, [−35.1,+56.8], **8d**, 6.1m | **BUILD-AS-INSTRUMENT — as a REPLACEMENT.** See §3. |
| **Corners O/U (team)** | ✅ 8d | ✅ 9d | ❌ | 94% (grading −0.3pp) | 8 of 42 lines | ✅ | *(prob 1%, ≤2.50)* 57, +12.45%, [−21.2,+46.1], **8d** | **BUILD-AS-INSTRUMENT** — same bot, second market |
| **Team totals (FT)** | ✅ | ✅ | ✅ | 100% | 7 of 26 lines | ✅ | 110, +6.59%, [−12.5,+25.7], 9d | **DO-NOT-BUILD — MEASURED NEGATIVE.** own-book CLV **−4.49%, t=−6.90** |
| **1st-half 1x2** | ✅ 4d | ✅ 10d | ❌ | 99.9% | n/a | ✅ | *(widest)* 55, −1.72%, [−38.2,+34.8], 8d | **DO-NOT-BUILD — MEASURED NEGATIVE.** own-book CLV **−5.69%, t=−7.00** |
| **Double chance** | ✅ 38d | ✅ 10d | ❌ | 100% | n/a | ⚠️ 3-line extension | 81, +2.05%, [−14.6,+18.7], 36d, 4.4m | **DO-NOT-BUILD — MEASURED NEGATIVE.** own-book CLV **−6.50%, t=−25.76, n=487** |
| **Asian handicap** | ✅ 38d | ✅ 12d | ❌ | 100% | 22 of 47 lines | ❌ (§16 — a fixed rung is not a close) | 116, −3.19%, [−18.3,+11.9], 35d, 1.5m | **DO-NOT-BUILD** — undecidable *and* unmeasurable: no stopping rule can be computed |
| **Cards O/U** | ✅ 8d | ✅ 9d | ❌ | 97.6% present, **grading −4.1pp (z=−3.0)** | 7 of 18 lines | ❌ (no complement defined) | **no cell with n≥25 under the live gate** | **DO-NOT-BUILD** — grading bias, no CLV path, no volume |
| **Corners O/U (1H)** | ✅ | ❌ | ❌ | **label is wrong on 54% of rows** | — | ✅ | not tested | **DO-NOT-BUILD** until `HT-STATS-FULL-MATCH-FALLBACK` is fixed |
| **1st-half totals** | ❌ | ✅ | ❌ | — | — | ✅ | **anchor feed dead since 2026-09-04** | **CANNOT-DETERMINE.** Fix the Pinnacle `over_under_1h_*` collection, then re-ask |
| **BTTS** | ✅ | ✅ | ❌ | 100% | n/a | ✅ | no sharp anchor exists | **DO-NOT-BUILD** — not derivable from 1X2 (needs the joint) |
| **Team totals (1H)** | ❌ | ❌ | ❌ | — | — | ✅ | no executable price | **DO-NOT-BUILD** |
| **Team cards, corners handicap** | C ✅ / E ✅ | E ✅ | ❌ | — | — | ❌ | **Pinnacle prices neither** | **DO-NOT-BUILD** — markets the sweep's catalogue missed; both fail gate 1 |
| **Draw no bet** | ❌ | ❌ | ❌ | — | — | — | no price at any of our books | **DO-NOT-BUILD** |

### Measured negative vs not yet measurable — the distinction the owner asked for

| | markets | what it means |
|---|---|---|
| **MEASURED NEGATIVE** (own-book CLV, t < −6) | double chance, team totals FT, 1H 1x2 | Close the question. Do not re-open without a *changed book*, not a changed gate. |
| **NOT YET MEASURABLE** (8–9 days, CI ±45pp) | corners match, corners team | An instrument is the only way to resolve them. This is the owner's actual request. |
| **UNMEASURABLE TODAY** (no stopping-rule metric exists) | Asian handicap, cards | Building an instrument here buys a ledger that can never be read. Build the metric first or not at all. |
| **BLOCKED BY A DEFECT** | 1H totals (dead anchor feed), corners 1H (broken label) | Not a market verdict. A repair. |
| **NO ANCHOR** | BTTS, team cards, corners handicap, DNB | Structural. Will not change. |

---

## 3. The finding the sweep could not have made: the corners instrument already exists and is mismeasuring

`python3 scripts/own_per_market_bot_ledger.py`:

```
bot_corners_paper_shadow_v1  ACTIVE  n=639  2026-09-07..2026-09-14
    priced at: Betano 591, Unibet 48
    ROI                             n=639  +8.53%  CI[-3.16,+20.22]
    own-book CLV, RAW               none — no own-book close on any pick
    own-book CLV, MARGIN-CORRECTED  NOT COMPUTABLE
```

Three separate problems, each independently disqualifying:

1. **It prices a market we cannot bet.** `PLACEMENT_BOOKS = ("Betano", "Unibet")`
   in `workers/jobs/corners_paper_bot.py`. Neither is in
   `ACCESSIBLE_BOOKMAKERS`; **`'Unibet'` is the dead AF feed** (33.1%
   phantom-high, excluded from every analysis in this repo since 2026-09-06, and
   dead since 2026-09-12) yet 48 of its picks are priced on it. Its **+8.53%** is
   a reference-price number in the exact shape §52/§55 exist to reject.
2. **Its CLV is NULL by design.** `settle_picks()`'s own docstring: *"clv columns
   stay NULL — no corners closing anchor is wired"*. So `PAPER-BOT-CLV-UNBLOCK-2026-09-13`,
   which taught `_market_complement_selections` about `corners_`, unblocked
   `clv_pinnacle` for the *generic* settler and did nothing for corners, because
   corners settles through its **own** settler. This is the RELIABILITY_LEDGER's
   "a second code path inheriting no gates", in the measurement layer.
3. **It has no odds floor and no band** (`EDGE_FLOOR=0.0`, `odds_floor=None` in
   the registry) and takes the best price across a 13-line ladder — the
   longest-price selection the ROI-floor analysis above shows is the wrong
   direction.

**Net: the one market where an instrument is genuinely warranted has an
instrument that cannot answer the question, and has spent a week accruing 639
rows of an unbettable book's prices.** No sweep of `odds_snapshots` can find
this; you have to read the fleet.

---

## 4. Three constraints any per-market bot must be designed around

### (a) The stopping rule's denominator is not picks — it is picks with an own-book close, and that is 8–50%

Measured over the last 7 days, share of (fixture, market) series where the book
has a **full closing complement** of its own:

| book | 1x2 | O/U 2.5 | corners (match) | corners (team) | O/U 3.5 |
|---|---|---|---|---|---|
| **Coolbet** | 8.3% | 8.0% | **5.2%** | 6.0% | 7.8% |
| **Epicbet** | 36.0% | 35.6% | 31.9% | 33.9% | 33.5% |
| **Unibet-Site** | 53.2% | 49.2% | — | — | 47.5% |
| *Pinnacle, for scale* | — | — | 84.0% | 83.9% | 84.9% |

It is a **book property, not a market property** — corners is not disadvantaged.
But it means **n≥300 CLV-eligible rows needs roughly 1,000 picks** at the current
book mix, and **a Coolbet-only instrument would need ~6,000**. Any
pre-registration that says "n≥300" without saying *n≥300 of what* will never
trigger. This applies to `bot_trigger_1x2_sharp_tight_v1` as written, too.

### (b) Retention deletes the past, so the only instrument is forward time

Coolbet `1x2` rows by month: **May 27, June 42, July 15, August 13,614**. The
120-day window buys ~38 days. **No market can ever accumulate a longer backtest
than ~5 weeks at our books.** That is the strongest argument in this document
for instruments over sweeps, and it applies to every future version of this
question.

### (c) The same-quantity gate is doubling as a volume filter on short-history markets

`gate_same_quantity` requires **30 paired fixtures per line** before a line is
testable. Over an 8-day corners window that rejects **14 of 24** match lines, **34
of 42** team lines and **19 of 26** team-total lines — not because they
disagree, but because they are thin. So gate 4's `n` on those markets is not the
market's `n`; it is the n of its densest rungs. Any instrument should **fire on
the full ladder and record the line**, and let the pairing test run later on the
accumulated data.

---

## 5. A caveat that lands on an already-shipped instrument

Not in scope, stated because it was measured on the way past and it changes a
prior that is currently locked.

`dev/active/own-sharp-tight-preregistration.md` rests on *"the matched junk
control reads ≈0 on the identical band (−1.48%)"*. At the tighter alignment used
here (≤15 min, one bet per fixture+book), it does not:

| 1x2, pooled, prob floor 2% | REAL | JUNK (identical gate) |
|---|---|---|
| odds 1.01–2.50 | n=113 **+16.73%** | n=773 **+4.35%** |
| odds 1.01–99 | n=243 −1.10% | n=3,703 −15.57% |

The junk arm gains **+19.9pp** by moving into the short band; the real arm gains
**+17.8pp**. **Real-minus-junk is ~13pp in both bands.** So the anchor's
contribution is roughly *flat in odds*, and the `odds ≤ 2.50` cap is not where
the anchor's information lives — it is where **this sample's favourites happened
to win**. On O/U 2.5 the junk arm stays at −4% to −5% (the vig) in every band, so
this is a **1x2-specific** property of the window, not a harness artefact.

This does not change the instrument's stopping rule — it strengthens its own
stated prior that the ROI is luck — but the pre-registration's negative-control
sentence should be restated when someone next touches that file.

---

## 6. Pre-registrations for the two BUILDs

### 6a. `bot_corners_sharp_exec_v1` — corners, at books we can actually bet

**This replaces `bot_corners_paper_shadow_v1`. Do not run both**: they would
share fixtures and the old one's Betano/`'Unibet'` prices would pollute any
pooled read. Retire the old bot in the same change (and per
`RETIRED-BOTS-KEPT-GENERATING`, retire it in the **DB**, not only the registry).

```
anchor   Shin de-vig of the Pinnacle corners_ou_<line> two-way book
gate     P_shin − 1/book_odds  ≥  2%        (PROBABILITY difference, NOT P×odds−1)
odds     ≤ 4.00                              (a CAP; no floor beyond 1.01)
lines    every line the anchor and the book both quote — record the line,
         do NOT pre-filter the ladder (see §4c)
books    Coolbet, Epicbet. One pick per (fixture, book); the LINE is chosen
         within a book, never across books (§52, §55).
markets  corners_ou_* (match) and corners_{home,away}_ou_* (team), tagged apart
league   keep the existing dynamic settleability gate (≥80% of finished games
         carry AF corner stats over a trailing 30 days) — it is the reason the
         bettable-slate settle rate is 93% rather than 16.5%
staking  PAPER. Not in PLACEABLE_BOTS. No placer toggle.
```

**Honest prior — and why to disbelieve it.**

* Best measured cell: pooled, prob ≥1%, **n=44, +10.86%, CI [−35.10, +56.82]**,
  **8 days**, median anchor gap 6.1 min. Team corners: **n=57, +12.45%,
  CI [−21.22, +46.12]**, 8 days.
* **Reasons to disbelieve it, all of them:**
  1. **Eight days.** Every corners number in this repo is 8 days old. The
     market-expansion sweep's "17 days" is not available for corners.
  2. **The CI is ±45 percentage points.** Detecting the +10.86% point estimate
     itself needs **n≈2,234**; detecting a true +3% needs **~15,600**.
  3. The live bot's **+8.53%** agrees in sign — and is measured at **Betano and a
     phantom feed**, so it is not independent evidence, it is a different market.
  4. Only **10 of 24** lines cleared the paired same-quantity test, and the rest
     failed for thinness. The ladder is not yet verified end to end.
  5. Corners is the market where a **longest-price-on-the-ladder** selection bug
     does maximum damage; the gate above is specifically shaped against it and
     has never been run live.
* **What it does have:** the best anchor-vs-grading agreement in the entire
  study (**−0.0pp**, n=4,896), 93% settleability on the slate it can bet, ~5.5
  qualifying picks/day pooled, and a CLV path that already exists in
  `_market_complement_selections`.

**Stopping rule (LOCKED).** Evaluated on this bot's own settled picks,
`closing_bookmaker IS NOT NULL` only, margin-corrected per row via
`closing_book_margin()`.

| checkpoint | criterion | action |
|---|---|---|
| **n ≥ 300 CLV-eligible rows** (≈1,000 picks at the current book mix — §4a) | margin-corrected own-book CLV **> 0**, CI excluding 0 | **PROMOTE** to a real-money candidate — owner decision, still gated on the OWN kill criterion |
| n ≥ 300 CLV-eligible | margin-corrected own-book CLV **< −2%** | **RETIRE** |
| n ≥ 300 CLV-eligible | anything between | **KEEP OBSERVING. Do not re-cut the rule.** |

**ROI may never promote this bot, at any value.** Per-bet return sd ≈ 1.3, so
confirming a true +3% ROI at 80% power needs **≈15,600 settled bets** — years at
5.5/day. A +30% ROI at n=300 is not grounds for promotion and must not be
presented as such.

**Blocking prerequisite.** `corners_paper_bot.settle_picks()` writes
`clv = NULL` by design. **Until an own-book closing anchor is wired into the
corners settler, this bot accrues an unreadable ledger** — exactly what the last
week produced. Wire the CLV first; the bot is worthless without it.

**What invalidates the test:** any change to the gate, the odds cap, the book
set, the anchor or the league gate. Each starts a new instrument with a new name
and a new start date.

**Negative control:** a junk-anchor arm at the **identical** gate, floor, band
and one-pick-per-fixture collapse. On the harness here that arm reads **−9.3%**
on corners (the vig) against the real arm's +10.9%. Do not compare against a
nominally-equal floor that passes 10× the legs — that mismatch is what made
"junk beats real" look true this morning.

---

### 6b. `bot_sharp_ou35_v1` — the only other market with volume, a CLV path and no verdict against it

```
anchor   Shin de-vig of the Pinnacle over_under_35 two-way book
gate     P_shin − 1/book_odds  ≥  2%
odds     ≤ 4.00 (CAP)
books    Coolbet, Epicbet, Unibet-Site. One pick per (fixture, book).
staking  PAPER. Never placeable.
```

**Honest prior.** Pooled, prob ≥2%, ≤2.50: **n=46, +7.86%, CI [−23.98, +39.69]**,
35 days, gap 6.0m. Widest cell: **n=150, +8.05%, CI [−10.45, +26.56]**, 39 days.
Grading-vs-anchor **−0.0pp (n=13,626)** — as clean as O/U 2.5. Junk control
−10.1%.

**Reasons to disbelieve it:**
1. **Every O/U ladder rung except 2.5 has come up empty in two independent
   sweeps.** 1.5 and 4.5 have no volume; 3.5 is the only one with any, and its
   CI is ±25pp.
2. It is **highly correlated with the O/U 2.5 bots already running** — the same
   fixtures, the same total-goals line one rung out. It is closer to a second
   reading of an existing instrument than a new market.
3. At ~1.5 qualifying picks/day pooled, **n≥300 CLV-eligible rows is ~18
   months**. On its own that is close to disqualifying; it is listed second for
   exactly this reason.
4. `bot_ou35_model_v1` already exists on the **model** anchor at Coolbet
   (+7.8%, "not robust"). A sharp-anchored twin is only worth having as the
   model-vs-sharp comparison, which is the strongest argument for it.

**Stopping rule:** identical to 6a, same n≥300 CLV-eligible threshold, same
absolute bar on ROI promoting anything.

---

## 7. Method, and what it does not do

* **Assemble, then align** (§63) — imported verbatim from
  `own_market_expansion_sweep.assemble`; no cross-book timestamp join anywhere.
* **Time alignment ≤15 min, median gap printed on every cell** — 1.1 to 7.9
  minutes on every cell quoted above.
* **`shadow_bets_unique`** only (§5). **Own-book rows only** for every CLV
  figure; `closing_bookmaker IS NULL` rows are dropped.
* **`clv` is a raw price ratio** — every CLV number here is corrected by that
  book's own closing overround on that fixture, computed per row.
* **Phantom books excluded**: `'Unibet'`, `'Unibet-Kambi'`, `Max`, `Avg`,
  `Betfair Exchange`, `BetWin`, `Betfred`.
* **Production outlier guard** (Pinnacle ×1.35 / ×1.30) on every leg.
* **Cluster-robust SEs on `match_id`** everywhere, ROI and CLV alike.
* **Single-book evaluation** — a book is chosen *within*, never *between*
  (§52, §55). POOLED rows are reported because the three books are all
  EMTA-legal and self-scraped, and are labelled as a pooled read.
* **369 cells with n≥25**, each with a time-ordered 3-fold split and a held-out
  final 25%.

**What it cannot do.** This is a near-closing-price backtest for most of its
span (§59 retention), so any sharp-anchor edge that is largest *early* is
invisible to it. It cannot separate the 2026-09-02 regime change (Epicbet
joining, retention not yet pruning, the 2026-09-11 `is_closing` widening) from a
real effect — **only forward time can**, which is the argument for instruments.
And 8 days of corners is 8 days of corners however it is analysed.

---

## 8. Recommended rows for `PRIORITY_QUEUE.md`

Not filed here — another agent owns that file this session.

* **CORNERS-BOT-UNBETTABLE-BOOKS** 🤖 OWN, ~2h — `bot_corners_paper_shadow_v1`
  is ACTIVE and prices 639 settled picks at Betano (591) and the **phantom
  `'Unibet'` feed** (48). Neither is placeable. Retire it **in the DB** and
  replace with the §6a instrument at Coolbet/Epicbet. *Highest-value row here:
  a live instrument is measuring a market we cannot bet.*
* **CORNERS-SETTLER-NO-CLV** 🤖 OWN, ~2h — `corners_paper_bot.settle_picks()`
  writes `clv = NULL` by design (its own docstring says so), so
  `PAPER-BOT-CLV-UNBLOCK-2026-09-13` never reached corners. 639 picks have no
  readable metric. Blocking prerequisite for §6a.
* **PINNACLE-1H-TOTALS-FEED-DEAD** 🤖👥 BOTH, ~1h — Pinnacle
  `over_under_1h_*` last wrote **2026-09-04** while every other Pinnacle 1H
  market still writes daily. The "1st-half totals has zero co-priced fixtures"
  verdict is this outage, not a market property. *Also a PICKS row: we publish
  nothing on a market whose sharp anchor silently died.*
* **HT-STATS-FULL-MATCH-FALLBACK** 🤖👥 BOTH, ~1h — independently confirmed
  here (n=1,972; 54.2% of `_ht` rows equal their full-match twin). Already
  recommended by `OWN_MARKET_EXPANSION_2026_09_14.md`; re-stated because it is
  the sole blocker on corners-1H.
* **CLV-COMPLEMENT-DOUBLE-CHANCE** 🤖 OWN, ~30m — `double_chance` is excluded
  from `_market_complement_selections` on the grounds that its outcomes "do not
  form a partition". True, and irrelevant to a **margin**: the three DC prices
  cover every outcome exactly twice, so `sum(1/o)/2 − 1` is the book's own DC
  overround. Adding it is what made the **−6.50% (t=−25.8)** verdict above
  computable. *Closing note: it confirms the market is dead, so this is a
  measurement fix, not a route to a DC bot.*
* **PREREG-N300-DENOMINATOR** 🤖 OWN, ~30m — every pre-registration in
  `dev/active/` says "n ≥ 300" without saying *n≥300 of what*. Only 8% (Coolbet)
  to 53% (Unibet-Site) of picks carry an own-book close, so the real requirement
  is ~1,000 picks. Restate the denominator in
  `own-sharp-tight-preregistration.md` and `picks-forward-test-preregistration.md`.
* **SHARP-TIGHT-JUNK-CONTROL-RESTATE** 🤖 OWN, ~30m — see §5. The
  pre-registration's "matched junk control reads ≈0" is **+4.35%** at ≤15 min
  alignment, and real-minus-junk is ~13pp in *both* odds bands. The `≤2.50` cap
  is not where the anchor's information is.

## 9. Docs this makes stale

* `docs/OWN_MARKET_EXPANSION_2026_09_14.md` — claims 2, 3, 7, 8 and 10 of §1
  above. Its **recommendation** stands; five of its stated reasons do not.
* `docs/MARKET_DATA_MAP.md` — if it records "Pinnacle does not price double
  chance" as a reason not to evaluate DC, the reason is wrong (§4/ this doc's
  claim 7); the conclusion is now right for a better reason (CLV −6.50%).
* `dev/active/own-sharp-tight-preregistration.md` — its negative-control
  sentence and its `n≥300` denominator (§5, §4a).
* `workers/jobs/settlement.py` line ~992 comment — it lists
  `bot_corners_paper_shadow_v1` among the bots whose CLV was unblocked on
  2026-09-13. Corners was **not** unblocked; it settles through its own settler.
