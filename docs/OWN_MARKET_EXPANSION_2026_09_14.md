# 🤖 OWN market expansion — which additional markets can carry a sharp edge?

**Answer: none of them today — but two of the three reasons the last attempt
gave were wrong, and one of them was hiding a live data defect.**

Reproduce: `python3 scripts/own_market_expansion_sweep.py --quantity-table`
(read-only; writes nothing). Window: 17 days to 2026-09-14 — which is the whole
history there is, because every bolt-on market's first row is 2026-08-29.

---

## The gate table

Four gates, tested in order, stopping on the first failure — plus **3b**, a
gate this run added and which turned out to be the decisive one. "Settleable" is
measured on the population that matters (anchor ∩ executable book), never on all
fixtures. Edge cells are the single-book executable sharp rule
`edge = P_Shin(de-vigged Pinnacle) × book price − 1` at a **2% floor**, with
cluster-robust CIs on `match_id`.

| Market | ① anchor overround | ② exec fx/day | ③ settleable | ③b grading vs anchor | ④ same-quantity | ⑤ edge @2% (n, ROI, CI, gap) | verdict |
|---|---|---|---|---|---|---|---|
| **1x2** *(live control)* | 9.04% | 274.9 | 100.0% | +0.5pp (z +0.8) | n/a | 479, **−14.1%**, [−28.4,+0.1], 3.5m | control — negative, as OWN_PATH_VERDICT found |
| **O/U 2.5** *(live control)* | 6.27% | 256.6 | 100.0% | +1.5pp (z +2.2) | PASS | 101, **+6.1%**, [−16.8,+28.9], 5.7m | control — reproduces the known ≈+5.6% |
| O/U 1.5 | 5.44% | 153.0 | 100.0% | +1.1pp | PASS | 27, +3.2%, [−50.1,+56.5], 4.8m | ❌ no volume at any floor |
| O/U 3.5 | 5.83% | 199.4 | 100.0% | +0.9pp | PASS | 92, +3.0%, [−21.6,+27.5], 5.6m | ❌ indistinguishable from zero |
| O/U 4.5 | 5.15% | 83.5 | 100.0% | −1.0pp | PASS | 25, +0.8%, [−55.5,+57.1], 5.4m | ❌ n too small; −78% at a 5% floor |
| **BTTS** | — *Pinnacle does not price it* | — | — | — | — | not tested | ❌ **GATE 1** |
| **Double chance** | — *Pinnacle does not price it* | — | — | — | — | not tested | ❌ **GATE 1** |
| **Draw no bet** | — *no Pinnacle, and no Coolbet / Epicbet / Unibet-Site either* | 0 | — | — | — | not tested | ❌ **GATE 1 + 2** |
| **Asian handicap** | 6.35% | 194.6 | 100.0% | +0.4pp (z +1.6) | PASS (22 of 44 lines) | 173, **−12.6%**, [−28.8,+3.6], 1.3m | ❌ negative at every floor below 8% |
| **1st-half 1x2** | **5.52%** *(tightest anchor we have)* | 72.8 | 99.9% | +0.3pp | n/a | 81, **−19.3%**, [−49.0,+10.4], 5.9m | ❌ negative; and the books charge MORE here (Coolbet 9.83%) |
| **1st-half totals** | 6.77% | **0.0** | — | — | — | not tested | ❌ **GATE 2** — Pinnacle quotes quarter lines (0.75/1.0/1.25/1.75), Epicbet only 0.5/1.5/2.5, Coolbet none; zero aligned co-priced fixtures |
| **Team totals (FT)** | 6.22% | 111.6 | 100.0% | +0.5pp (z +1.1) | PASS (7 lines: 0.5/1.5/2.5 both sides + home 3.5) | 209, +5.0%, [−10.4,+20.4], 4.0m | ❌ decays to −14.6% at an 8% floor; folds −7.7/+28.1/−5.3 |
| **Team totals (1H)** | 6.71% | **0.0** | — | — | — | not tested | ❌ **GATE 2** — Pinnacle only; no executable book prices it |
| **Corners O/U (match)** | 6.80% | 33.5 | **93.3%** | **−0.0pp (z −0.0)** | PASS (7.5–12.0) | 51, +4.0%, [−38.8,+46.9], 5.9m | ❌ **GATE 4** — cleanest market in the study, no measurable edge, needs 16,930 bets ≈ **15 years** at 3/day |
| **Corners O/U (team)** | 7.64% | 32.6 | 94.2% | −0.3pp | PASS (8 lines) | 67, +13.9%, [−17.3,+45.0], 5.3m | ❌ **GATE 4** — CI spans zero, folds −27/+56/+12 |
| **Corners O/U (1H)** | 8.05% | 9.1 | 93.6% *(present)* | **+28.6pp (z +41.0)** | PASS | not tested | ❌ **GATE 3b — the data is present and WRONG.** See "the defect" below |
| **Cards O/U** | 6.54% | 10.1 | 97.6% *(present)* | **−4.1pp (z −3.0)** | PASS (2.5–5.5) | 22, **−76.2%**, [−97.0,−55.5], 4.8m | ❌ **GATE 3b + 4** — residual grading bias, and almost no volume |

**44 cells tested** (market × floor), 4 floors each (2/3/5/8%) on 11 markets that
reached gate 4. Folds are time-ordered thirds and are printed for every cell.
No market's CI excludes zero at the 2% floor in the positive direction.

---

## Recommendation

**Add nothing. Build no new market bot on this evidence.**

Ranked by how close each came, so the next look starts in the right place:

| rank | market | what it has | what it lacks |
|---|---|---|---|
| 1 | **Corners O/U (match)** | the best anchor–grading agreement in the entire study (−0.0pp), 93% settleability on the bettable slate, 10 clean lines, 33 fixtures/day | **volume and effect.** 51 bets in 17 days at a 2% floor. 16,930 bets to confirm its own point estimate ≈ 15 years. |
| 2 | **Team totals (FT)** | 112 fixtures/day, perfect settlement, 7 verified lines, three executable books | edge decays monotonically as the floor rises (+5.0 → +2.0 → +0.8 → −14.6%), which is the signature of no signal |
| 3 | **Corners O/U (team)** | +13.9% point estimate, the largest in the study | n=67, CI [−17,+45], folds −27/+56/+12, and it shares corners' volume ceiling |
| — | everything else | — | fails a gate outright |

**Explicitly do NOT build:**

* **Cards.** Not for the reason previously given. See below — the real reason is
  worse and the previously-given one was partly a measurement artifact.
* **Corners 1H.** The settlement column is contaminated (below). Even fixed, it
  is 9 fixtures/day against a book that charges 8.00%.
* **1st-half totals, 1st-half team totals.** We have the sharp anchor and no
  executable price. Zero co-priced fixtures, not "few".
* **BTTS, double chance, draw-no-bet.** No Pinnacle complement exists, so there
  is no sharp edge to compute — this confirms MARKET_DATA_MAP's verdict by a
  different route. Draw-no-bet additionally has **no price at any of our three
  books**; it exists in `odds_snapshots` only via Bet365/BetVictor/Betano etc.
* **Asian handicap, 1st-half 1x2.** Both clear gates 1–3b cleanly and both are
  decisively negative. 1H 1x2 is the sharpest anchor we own (5.52%) sold through
  the widest book margin we own (Coolbet 9.83%) — the worst possible combination.

---

## Three corrections to the record

### 1. Corners settleability is 93%, not 16.7% — the denominator was wrong

`docs/OWN_PATH_VERDICT_2026_09_14.md` and `docs/PLAN_AFTER_AUDITS_2026_09_14.md`
both say *"corners are settleable on only 16.7% of finished fixtures — a corners
bot cannot be evaluated on 5 of every 6 bets it places"*. The 16.7% is real and
reproduces exactly; it is measured over the **wrong population**.

| population | finished fixtures | with corner stats | % |
|---|---|---|---|
| all finished fixtures, all time | 171,064 | 53,676 | 31.4% |
| all finished fixtures, last 17 days | 10,685 | 1,762 | **16.5%** ← the quoted figure |
| …that **Pinnacle** priced corners on | 2,181 | 1,450 | 66.5% |
| …that **Coolbet or Epicbet** priced corners on | 600 | 490 | 81.7% |
| …that **both** priced — i.e. the bettable slate | 523 | 482 | **92.2%** |

A corners bot cannot place a bet on a fixture nobody quotes corners on. The
books quote corners on exactly the higher-tier fixtures where API-Football has a
statistics object, so conditioning on "we could have bet it" conditions on "we
can grade it" almost for free. Measured inside the harness on (fixture, line)
cells of the anchor ∩ executable population: **93.3%**.

This is ANALYSIS_GOTCHAS §10 in a new costume. §10 says an unpaired cross-book
comparison measures coverage rather than price. The sibling: **a coverage
percentage measured over a population you cannot bet measures your slate, not
your ability to settle.** Always state the denominator, and make it the
population the bot would actually act on.

**It does not change the verdict**, because corners then fails gate 4 anyway —
but it failed for a stated reason that was not true, and the next person to ask
"why not corners?" would have been told something false.

### 2. The cards trap is real, but the headline numbers were unpaired

The cited mismatch (implied P(over) on `cards_ou_65`: Coolbet 0.176, Epicbet
0.270, Pinnacle 0.346, Bet365 0.482) reproduces almost exactly as an
**unpaired** median — this run got 0.182 / 0.253 / 0.332 / 0.461. But each book
quotes cards 6.5 on a different set of fixtures, and Pinnacle quotes that line on
**21 fixtures in 17 days**. Paired on shared fixtures, our three books and the
anchor agree on every cards line dense enough to pair:

| line | worst paired bias vs Pinnacle | paired n |
|---|---|---|
| 2.5 | +0.011 | 48 / 73 |
| 3.5 | +0.001 | 79 / 155 |
| 4.5 | +0.003 | 83 / 162 |
| 5.5 | −0.005 | 36 / 62 |

Lines 6.0 and up cannot be paired at all — the anchor is too thin. So the
"0.176 vs 0.346" gap is mostly slate plus a 21-fixture anchor, not a
demonstrated units mismatch *at the books we bet*.

**What IS demonstrated, and is decisive, is the flat-ladder pathology at Bet365
and 1xBet.** Measured within a single fixture's own ladder, every book we bet
prices the cards ladder properly — Coolbet 90.6% monotone, Epicbet 99.7%,
Pinnacle 99.2%, P(over) falling 0.82 → 0.14. Bet365 and 1xBet sit at 0.46–0.58
at every cards line from 2.5 to 7.5 and their ladder cannot be tested at all
(fewer than 20 comparable steps). Their line label does not correspond to the
quantity. **We do not bet them, so this never had to be a cards verdict — it is
a parser bug at two feeds, and it would equally poison corners, where Bet365 is
66.7% monotone with P(over) moving only 0.539 → 0.469 across the whole ladder.**

**The right reason to kill cards is settlement, and it is in gate 3b:** our card
count realises **−4.1pp fewer overs (z = −3.0)** than the sharp book's de-vigged
probabilities say it should, and the flat negative control loses 8.72% where the
vig alone is 7.41%. The bets the rule selects are overs at 5.0–5.5 at odds
3.4–4.3, which is exactly where a −4pp count bias does maximum damage: **−76%
ROI on n=22.** §51's "residual settlement bias even under the best definition"
is now a number.

### 3. ⚠️ A live data defect: `match_stats.*_ht` holds FULL-MATCH values on 54% of rows

Gate 3b flagged first-half corners at **+28.6pp, z = +41.0** — the realised
"first-half over" rate is 79% where the sharp book prices 50%. That is not a
market inefficiency, it is a broken column.

```
match_stats rows with both HT and FT corners:         1,972
mean FT corners  9.74   mean "HT" corners  7.39   (a true 1H total is ~4.7)
corners_home_ht = corners_home AND away likewise:     54.2%
shots  identical: 53.4%      yellow cards identical: 58.1%
```

**Root cause,** `workers/api_clients/api_football.py::parse_fixture_stats_halftime`:

```python
half_stats = team_data.get("statistics_1h")
if half_stats is None:
    half_stats = team_data.get("statistics", [])   # <-- FULL-MATCH fallback
```

When API-Football returns no `statistics_1h` split, the parser falls back to the
**full-match** `statistics` array and writes it into every `_ht` column. The
comment says the fallback is for "a caller passing a genuinely half-scoped
payload", but the only production caller passes the `half=true` response, where
absence of `statistics_1h` means *AF has no half split for this fixture* — so
the fallback is always wrong in production.

**Nothing bets on this today.** No `*_1h` stat market is in the settlement
registry and no bot places corners-1H, so no money has been lost. But we are
collecting 303,120 `corners_1h_ou_45` snapshots against a label that is
full-match on half our rows, and any future 1H stat model would train on it.
Recommended rows for `PRIORITY_QUEUE.md` (not filed here — another agent owns
that file this session):

* **HT-STATS-FULL-MATCH-FALLBACK** 🤖👥 BOTH — remove the fallback so an absent ✅ **FIXED 2026-09-24 in [[#111]]** — fallback removed from `parse_fixture_stats_halftime` (never reads the full-match block now), 1,002 contaminated rows since 2026-08-17 overwritten with AF's real first-half split (`scripts/repair_ht_stats_111.py`), smoke `XG-LATE-FILL` guards it. It sat here for ten days with no `PRIORITY_QUEUE.md` row.
  `statistics_1h` writes NULL rather than the full-match value (*honestly absent
  beats confidently wrong*, §61), and NULL the 54% of `_ht` rows that equal
  their full-match twin. ~1h.
* **SETTLEMENT-1H-TOTALS-GRADED-ON-FULL-TIME** 🤖 OWN — `_r_ou_goals` matches on
  the substring `"over_under"`, so `over_under_1h_15` would be graded against
  the **full-time** score. Latent (no bot bets it) but it is the same shape as
  the bug `_r_1x2_1h` was written to prevent. ~30m.

---

## The negative control

The harness is driven a second time with a **junk anchor**: each leg keeps its
own book price, outcome, line and timing, but its de-vigged anchor
probabilities come from a **different fixture** at the same market and line.

The useful form is the **flat** control — every leg, no floor, no selection —
because its expected value is known in closed form. If a book's prices were
proportional to the true probabilities, flat-betting every leg returns exactly
**−v/(1+v)**, where v is the book's overround.

| market | flat control ROI | −v/(1+v) | deviation |
|---|---|---|---|
| O/U 2.5 | −6.34% | −6.70% | **+0.36pp** |
| O/U 3.5 | −7.04% | −6.78% | −0.25pp |
| Team totals | −6.98% | −6.62% | −0.36pp |
| Corners (match) | −6.55% | −6.99% | +0.43pp |
| Corners (team) | −6.71% | −7.37% | +0.66pp |
| Asian handicap | −5.36% | −6.99% | +1.63pp |
| 1st-half 1x2 | −6.77% | −8.19% | +1.42pp |
| **Cards** | **−8.72%** | −7.41% | **−1.31pp** |
| 1x2 | −9.87% | −7.43% | −2.45pp |
| O/U 1.5 | −9.66% | −6.94% | −2.73pp |
| O/U 4.5 | −11.06% | −6.55% | −4.50pp |

**The harness pays the vig and nothing else on every near-symmetric market.**
The three markets that lose more than the vig are the three asymmetric ones
(1x2's draw/away-dog legs, O/U 1.5 at P(over) 0.73, O/U 4.5 at 0.22) — that is
the favourite–longshot bias, present with perfect grading, and O/U 4.5 is the
proof: its grading is literally `goals > 4.5`. So this line answers *"is the
harness paying the vig"*, and **gate 3b** answers *"is the grading right"*,
because comparing the anchor's de-vigged probability to the realised rate is
free of both biases. On that measure every market lands inside ±1.5pp except
cards (−4.1pp) and corners-1H (+28.6pp).

The **floored** control loses more than the vig on every laddered market
(corners −7.4%, cards −28%), because taking the max-edge leg across a ladder's
many lines systematically selects the longest price on offer. That is a property
of the selection rule, not of the grading — which is why the flat form is the
one to read.

---

## Method, and the three traps that shaped it

1. **Assemble, then align** (§63). `odds_snapshots` stamps each ROW; Coolbet's
   triple spans ~100ms. Every book's complement is assembled from a ±2 min
   window before any cross-book comparison — the same algorithm as
   `own_path_kill_criterion.assemble`, generalised over the selection list.
2. **Time alignment, reported per cell.** An anchor quote must be within 15 min
   of the executable quote or the leg is dropped. **Median gap on selected legs
   is 1.3–6.0 minutes in every cell in the gate table** — against the 360 min
   that made the same publish rule read +8.47% unaligned and +5.54% aligned.
3. **PAIR THE QUANTITY CHECK.** This one cost a full rerun. Comparing two books'
   median P(over) at a line compares their **slates**, not their prices (§10) —
   run unpaired, the check rejected O/U 1.5, 3.5 and 4.5, three markets where
   the books agree to within **0.009, 0.003 and 0.004** once paired on shared
   fixtures. Every level test here is a median of per-fixture differences
   against the anchor; every monotonicity test is measured inside one fixture's
   own ladder. The generalisation: **any cross-book statistic that is not paired
   on fixtures is a statement about coverage.**

Also applied: the production outlier guard (a book price above Pinnacle × 1.35
for 3-way / × 1.30 for 2-way on the same aligned anchor is dropped — §9; it cut
the O/U 2.5 control from a fake +16.3% to +6.1%, which is the known live
figure); single-book evaluation, never best-of-books (§52, §55); cluster-robust
CIs on `match_id`; the phantom-book exclusion list; home-perspective AH grading
(§53); and `power_n` printed on every cell (§60).

## What this cannot do

`prune_old_simple` keeps at most three rows per series after 7 days (§59), and
every bolt-on market's first row is **2026-08-29**. So there are 17 days of
history, of which ~7 are at full resolution. No cell in this study has the n for
a meaningful held-out test, and the honest output for every one of them is
**"indistinguishable from zero"**, not the sign of its point estimate. The
markets are ranked above by how cheaply they could *become* testable, not by
their point estimates.

## Docs this makes stale

* `docs/OWN_PATH_VERDICT_2026_09_14.md` line 105 and
  `docs/PLAN_AFTER_AUDITS_2026_09_14.md` line 108 — the "corners settle on only
  16.7% of finished fixtures" claim. Correct statement: *16.5% of ALL finished
  fixtures in the window, but 92–93% of the fixtures a sharp anchor and an
  executable book both price — which is the only population a bot can act on.
  Corners still fails, at gate 4 rather than gate 3.* Banners added.
* `docs/MARKET_DATA_MAP.md` — the corners/cards rows say "AF CEILING, ~32%
  stuck". Still true of the whole fixture universe (31.4% all-time measured
  here) and still the right reason not to TRAIN a corner model on everything,
  but it is not the reason a corners BOT cannot be evaluated. Banner added.
