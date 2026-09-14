# Line movement on the books we can bet — **DEAD**

**2026-09-14.** The last untested own-path idea in our own data: everything before
this compared a *snapshot* of a sharp line against a *snapshot* of a book we can
bet. Nobody had tested the **delta**. This did.

**Verdict: DEAD.** 194 gated configurations across 3 books × 4 markets × 3
lookback windows. **Zero** have margin-corrected own-book CLV above 0. The best
cell in the entire sweep is **−4.16%**, and it is not distinguishable from its own
gate-matched placebo. The mechanism is measured, not inferred: our books' prices
**do not follow** the market's moves (follow-through β ≈ 0.00–0.14 where
exploitation needs ≈1), and the total predictable component of a book's move to
its own close is **R² = 0.0004–0.028**, which is **10–89× too small** to pay the
vig.

One incidental defect found and worth fixing: **7.87% of Coolbet BTTS quotes**
move more than 65% before that book's own close. That is not a market moving.

**Independently re-derived.** A second agent recomputed the baselines, the
closing margins and the follow-through slopes from the frozen parquet with its own
code, without reading these scripts. Baselines agreed within 0.64pp on every cell,
closing margins to three decimals, and the βs to ±0.02. It also found the
repricing-frequency mechanism in §3a and the output-parsing error corrected in §4.

---

## 0. The bar, and why almost everything lands near −7%

```
margin-corrected own-book CLV  =  (1 + clv) / (1 + m) − 1
   clv = quote_taken / own-book closing quote − 1        (RAW price ratio, no de-vig)
   m   = that book's own closing overround on that fixture+market, PER ROW
         (settlement.closing_book_margin(); NULL when uncomputable, never averaged)
```

Betting *at* the close gives clv = 0, hence mc = −m/(1+m) ≈ **−7%**. So the bar is
not "beat the close". It is **beat the close by more than the book's own vig** —
clv > m ≈ 7–9pp. Measured own-book closing margins, this window:

| book | 1x2 | O/U 2.5 | O/U 3.5 | BTTS | AH |
|---|---|---|---|---|---|
| Coolbet | 7.85% | 7.99% | 7.97% | 6.98% | 7.96% |
| Epicbet | 8.02% | 6.98% | 6.95% | 7.90% | 6.95% |
| Unibet-Site | 9.08% | 7.18% | 7.28% | — | — |

---

## 1. The constraint nobody had hit yet: the price path lives for SEVEN DAYS

`prune_old_simple` keeps three rows per `(match, book, market, selection, line)`
series after 7 days — `is_opening`, `is_closing`, latest pre-kickoff
(`ANALYSIS_GOTCHAS` 59a). Rows per price series, by match date, measured today:

| match date | Coolbet | Epicbet | Unibet-Site | Pinnacle |
|---|---|---|---|---|
| 2026-09-12 | 2.6 | 10.4 | 5.6 | 11.8 |
| 2026-09-11 | 6.2 | 16.8 | 12.7 | 12.4 |
| 2026-09-09 | 7.8 | 16.7 | 3.3 | 11.3 |
| 2026-09-07 | 5.0 | 14.6 | — | 13.0 |
| **2026-09-06** | **1.02** | **1.20** | — | 2.74 |
| **2026-09-05** | **1.01** | **1.29** | — | 2.97 |
| 2026-08-30 | 1.02 | — | — | 1.90 |

**A line-movement study is only possible on ~8 days of data, and that window is
being destroyed as it ages.** `scripts/own_movement_snapshot.py` freezes it:
5,363,972 pre-kickoff quotes, 4,721 fixtures, 12 books, 5 markets,
2026-09-05..2026-09-14. Everything below runs off that frozen file, so it is
reproducible after the source rows are gone. **Any future re-run needs a fresh
snapshot taken within 7 days of the matches it wants to study.**

This single fact is the honest answer to "why has nobody tested movement" — before
2026-09-11 our three bettable books had **one** surviving row per series and **no
opening price at all**. The data to do this did not exist until three days ago.

---

## 2. Scrape cadence — Coolbet's continuous daemon is NOT per-fixture density

Brief hypothesis #3 was that Coolbet's near-continuous Mac daemon lets us see it
move before the others. **It is the opposite.** The daemon's write-minutes are
spread thin across the whole fixture book:

| book | write-minutes/day | **median obs per price series** | median distinct prices | median span |
|---|---|---|---|---|
| **Coolbet** | **261.5** (most of any book) | **3** (fewest of any book) | 1 | **4.5 h** |
| Unibet-Site | 137.5 | 6 | 2 | 19.0 h |
| BetVictor | 128.0 | 13 | 2 | 15.9 h |
| Pinnacle | 109.0 | 11 | 2 | 20.4 h |
| Bet365 | 106.0 | 16 | 2 | 20.9 h |
| **Epicbet** | **49.0** (fewest) | **22** (most) | 2 | **22.3 h** |

Coolbet writes on the most distinct minutes of any book and still gives us the
**worst-observed** price path — 3 observations over 4.5 hours, median **one**
distinct price. Epicbet writes on the fewest minutes and gives the best path.
**Write frequency is a property of the sweep, not of per-fixture resolution.**
A companion to `ANALYSIS_GOTCHAS` §63: the 309-write-minutes figure describes
breadth, and reading it as "we see Coolbet move first" is wrong.

---

## 3. The steam / lag measurement itself

Anchor is a **multi-book consensus** (median log-odds over ≥4 of Pinnacle, Bet365,
1xBet, Marathonbet, Betfair, William Hill, Betano, BetVictor, SBO, in 10-min
buckets) rather than Pinnacle alone — `ANCHOR_IS_NOT_SHARP_2026_09_14` showed
Pinnacle in this feed carries a 9.18% median 1x2 overround and is not sharp.
Own-book quotes are matched to the newest consensus point within 40 min; **median
alignment gap 10.5 min**, reported per cell. Books are never joined on timestamp
equality (§63).

### 3a. Follow-through: do our books move when the market moves?

β from OLS of *the own book's remaining move to its own close* on *the consensus'
move over the previous W minutes*. Exploiting a lag needs β ≈ 1 (the book
eventually converges to where the market went). n = 16k–70k per row.

| book | W=60m | W=120m | W=240m | **gap mean-reversion β** |
|---|---|---|---|---|
| Coolbet | +0.082 | +0.084 | +0.086 | −0.110 |
| Epicbet | −0.014 | −0.026 | +0.003 | −0.035 |
| Unibet-Site | +0.115 | +0.140 | +0.106 | −0.114 |

**Read this as: when the market moves 10%, our books subsequently move 0.0–1.4%.**
Gap mean-reversion says the same thing from the other side — a book sitting 10%
off consensus closes ~1% of that gap by its own close, where full convergence is
−1.00. Our books are not laggards that catch up. They hold their own line.

Two honest qualifications, both from an independent re-derivation of this table:

1. **These βs are statistically distinguishable from zero** (t = +5.1, −2.9, +5.4
   at n = 13k–25k). The claim is not "no relationship"; it is that the
   relationship is **economically an order of magnitude too small**, needing ≈1.0
   and delivering ≈0.1.
2. **Part of the near-zero β is that these books barely move at all.** Own-book
   1x2 prices are *exactly unchanged* across a 120-minute window **70–80% of the
   time** (Coolbet 72.2%, Epicbet 70.0%, Unibet-Site 79.9%) against **19.6%** for
   the consensus. They reprice in infrequent jumps, not continuously. So the
   measurement is "they do not chase the market" *and* "there is little movement
   to chase with" — which matters, because it means a longer sample would raise
   precision but is unlikely to raise β.

A second, more direct test agrees: regressing the own book's move-to-close on its
**current** gap to consensus (staleness measured directly, rather than the
market's recent move as a proxy) gives convergence coefficients of 0.109 / 0.060 /
0.184 at n = 19k–43k. Those are **upper bounds** — the own price enters both sides,
so idiosyncratic pricing noise mean-reverts by construction — and they are still
5–17× short of 1.0.

### 3b. The corroborating oddity

Contemporaneous correlation of 30-minute log-odds increments, 1x2:

| pair | corr (all bins) | corr (bins where BOTH moved) |
|---|---|---|
| 1xBet ↔ Marathonbet | +0.924 | +0.972 |
| Pinnacle ↔ Marathonbet | +0.781 | +0.910 |
| Bet365 ↔ Pinnacle | +0.594 | +0.758 |
| **Coolbet ↔ Epicbet** | **+0.295** | **+0.676** |
| **Epicbet ↔ Pinnacle** | **−0.005** | **−0.022** |
| **Coolbet ↔ Pinnacle** | **+0.010** | **+0.069** |

The AF-fed books move as one block. Our two direct scrapes move with **each other**
and with **nothing in that block**, at any lag from −120 to +240 min. Not a
mapping bug: closing-level log-odds correlate 0.956–0.981 with Pinnacle at a
median price ratio of 0.99–1.00. The two Estonian books look like they share a
regional price supplier that is not the one behind the AF feed.

This is the *only* real correlation in the own-book data, so it was tested
directly as a strategy — every cross-own-book lead-lag arm, 1x2, gate "leader
dropped ≥3%":

| leader → follower | W=60m | W=120m | W=240m |
|---|---|---|---|
| Coolbet → Epicbet | −6.72% | −7.39% | −6.80% |
| Epicbet → Coolbet | −6.16% | −6.21% | −6.13% |
| Epicbet → Unibet-Site | −7.02% | −8.30% | −8.51% |
| Unibet-Site → Coolbet | −4.18% | −3.47% | **−2.78%** |
| Unibet-Site → Epicbet | −7.70% | −7.55% | −7.20% |
| Coolbet → Unibet-Site | −4.44% | −5.94% | −6.21% |

Best arm −2.78% [CI −4.15, −1.40] on 922 legs from only **87 fixtures**. Negative,
and the CI excludes zero on the wrong side.

---

## 4. Every configuration tested

Full table: `python3 scripts/own_line_movement.py --parquet <snapshot> --mode cells`.
Margin-corrected own-book CLV, SEs **clustered on fixture**. "junk" is the
**gate-matched placebo** — the signal column permuted within (book, market,
selection), so it preserves the signal's marginal distribution exactly and passes
the same gate at the same rate.

### Baselines — a random own-book quote, no gate

| market \| book | n | mc-CLV | CI |
|---|---|---|---|
| btts \| Coolbet | 11,942 | **−4.94%** | [−5.09, −4.79] |
| over_under_35 \| Epicbet | 14,428 | −6.02% | [−6.11, −5.92] |
| over_under_35 \| Coolbet | 10,658 | −6.13% | [−6.25, −6.01] |
| over_under_25 \| Unibet-Site | 6,720 | −6.38% | [−6.45, −6.32] |
| over_under_25 \| Epicbet | 17,624 | −6.40% | [−6.49, −6.31] |
| 1x2 \| Coolbet | 19,808 | −6.59% | [−6.79, −6.40] |
| over_under_25 \| Coolbet | 12,059 | −6.69% | [−6.77, −6.60] |
| btts \| Epicbet | 17,688 | −6.94% | [−7.00, −6.88] |
| 1x2 \| Epicbet | 29,594 | −7.80% | [−7.95, −7.65] |
| 1x2 \| Unibet-Site | 15,273 | −7.91% | [−8.06, −7.76] |

(The btts/Coolbet −4.94% is **not** a real edge — see §6, it is contaminated by a
scrape defect.)

### Best 12 of 194 gated cells

All spans within 2026-09-05..2026-09-14; alignment gap in minutes.

| cell | n | fx | gap | mc-CLV | CI | junk |
|---|---|---|---|---|---|---|
| 1x2 \| Coolbet \| consensus W=120m ≤ −10% | 216 | 70 | 9 | **−4.16%** | [−7.31, −1.01] | −6.35% |
| btts \| Coolbet \| consensus W=120m ≥ +5% | 81 | 41 | 9 | −4.21% | [−7.05, −1.36] | −6.55% |
| over_under_35 \| Coolbet \| consensus W=120m ≤ −5% | 139 | 65 | 9 | −4.22% | [−5.87, −2.58] | −5.52% |
| over_under_35 \| Epicbet \| own price ≥ consensus +5% | 1,524 | 447 | 3 | −4.26% | [−4.80, −3.73] | −6.08% |
| 1x2 \| Coolbet \| own price ≥ consensus +10% | 1,282 | 256 | 9 | −4.27% | [−5.17, −3.36] | −6.54% |
| over_under_35 \| Coolbet \| consensus W=60m ≤ −5% | 66 | 43 | 9 | −4.31% | [−6.38, −2.25] | −5.92% |
| over_under_35 \| Coolbet \| W=120m ≤ −5% AND ≤6h to KO | 109 | 49 | 9 | −4.31% | [−5.78, −2.85] | −5.89% |
| 1x2 \| Coolbet \| consensus W=120m ≤ −5% | 443 | 139 | 9 | −4.44% | [−5.98, −2.90] | −6.31% |
| over_under_35 \| Coolbet \| consensus W=60m ≤ −2% | 239 | 129 | 10 | −4.54% | [−5.42, −3.66] | −5.93% |
| over_under_35 \| Coolbet \| own price ≥ consensus +3% | 2,522 | 423 | 11 | −4.59% | [−4.97, −4.21] | −6.05% |
| 1x2 \| Coolbet \| consensus W=60m ≤ −2% | 607 | 228 | 9 | −4.65% | [−5.56, −3.74] | −6.59% |
| 1x2 \| Coolbet \| consensus W=240m ≤ −10% | 351 | 92 | 9 | −4.67% | [−6.80, −2.55] | −6.59% |

### Worst — the signal is actively *adverse* at Epicbet

| cell | n | fx | mc-CLV | CI | junk |
|---|---|---|---|---|---|
| 1x2 \| Epicbet \| consensus W=240m ≤ −10% | 377 | 105 | **−11.32%** | [−13.04, −9.60] | −6.97% |
| 1x2 \| Epicbet \| consensus W=120m ≤ −10% | 229 | 90 | −11.21% | [−13.31, −9.10] | −6.80% |
| 1x2 \| Epicbet \| consensus W=120m ≤ −5% | 537 | 200 | −10.91% | [−12.10, −9.71] | −7.55% |

Epicbet is **3.5pp worse than its own baseline** when the consensus has just
dropped: it has already over-moved and drifts back. Betting into an Epicbet steam
move is a way to lose faster.

### Asian handicap (Coolbet's largest market, 60k rows/week)

Complement assembled as {home, away} at the same `handicap_line`
(§53: the line is stored home-perspective for both selections).

| cell | n | fx | mc-CLV | CI |
|---|---|---|---|---|
| Coolbet ALL | 24,068 | 232 | −7.86% | [−8.33, −7.40] |
| Coolbet consensus W=240m ≤ −2% | 1,844 | 51 | −13.31% | [−17.17, −9.46] |
| Epicbet ALL | 42,310 | 856 | −5.85% | [−6.01, −5.69] |
| Epicbet own price ≥ consensus +5% | 8,568 | 541 | **−4.38%** | [−4.61, −4.14] |

### Headline counts

- **194 real cells + 192 gate-matched placebo cells.**
- **Cells with mc-CLV CI strictly above 0: 0.**
- **Cells whose CI even *touches* 0: 0.**
- Real vs its own placebo, over the 191 cells that pair: mean advantage
  **+0.36pp**, median **+0.42pp**; the real signal beat its own placebo in
  **111 of 191 cells (58.1%)**.

So the gates are not *quite* pure noise — a real movement signal beats its
permuted twin a bit more often than not, and by about **a third of a percentage
point**. That is the same order as the ~2pp the static sharp anchor was already
known to buy, and it is **20× short** of the 7–9pp of vig it has to clear. The
honest reading is not "no information"; it is **"information worth 0.4pp against
a 7.5pp toll"**.

> *Correction, same day:* an earlier draft of this line read "+0.05pp, 82 of 160
> cells, 51.3% — a coin flip". That came from re-parsing the script's printed
> table with a regex that silently dropped every cell whose name exceeded the
> 64-character column, i.e. exactly the longest (most-conditioned) gates. The
> figures above are computed inside `own_line_movement.py` from the dataframe.
> The verdict does not change, but "a coin flip" overstated it and is withdrawn.

---

## 5. Why it cannot work — the arithmetic, not the search

Let `y = log(own_book_close / quote_taken)`. CLV is positive iff y < 0, and
profitable iff `−y > log(1+m) ≈ 0.072`.

Regressing y on the movement features **only** (consensus moves at 60/120/240 min
and time-to-kickoff), excluding `gap` because it shares `log(p)` with y and is
therefore mechanically correlated with it:

| book | market | n | sd(y) | R² | \|r\| | need | 1-sd signal buys | shortfall |
|---|---|---|---|---|---|---|---|---|
| Coolbet | over_under_35 | 7,362 | 0.0466 | 0.0276 | 0.166 | 0.0767 | 0.0077 | **×10** |
| Coolbet | over_under_25 | 8,557 | 0.0420 | 0.0186 | 0.136 | 0.0767 | 0.0057 | ×13 |
| Unibet-Site | over_under_25 | 3,994 | 0.0388 | 0.0156 | 0.125 | 0.0688 | 0.0049 | ×14 |
| Unibet-Site | 1x2 | 9,302 | 0.0685 | 0.0042 | 0.065 | 0.0826 | 0.0045 | ×18 |
| Coolbet | 1x2 | 14,316 | 0.0668 | 0.0024 | 0.049 | 0.0764 | 0.0033 | ×23 |
| Epicbet | 1x2 | 16,478 | 0.0785 | 0.0006 | 0.025 | 0.0858 | 0.0020 | ×43 |
| Epicbet | btts | 9,436 | 0.0371 | 0.0005 | 0.023 | 0.0761 | 0.0009 | ×89 |

**The vig we must clear is about one full standard deviation of the thing we are
trying to predict, and the movement features explain 0.04%–2.8% of its variance.**
Even a *perfect* extractor of the explainable part, fired on a 1-sd signal
excursion, moves the conditional mean by 0.1–0.8pp against a 7–9pp requirement.

This is why the sweep has no near-misses and why widening it would not help. It is
not a power problem and it is not a tuning problem. **There is not enough
predictable variation in a soft book's move to its own close to pay that book's
margin.**

---

## 6. Incidental finding worth fixing: Coolbet BTTS price noise

Share of own-book pre-kickoff quotes whose price moves **more than 65%** before
that same book's own close:

| book | 1x2 | O/U 2.5 | O/U 3.5 | **BTTS** |
|---|---|---|---|---|
| Coolbet | 0.12% | 0.00% | 0.00% | **7.87%** |
| Epicbet | 0.39% | 0.01% | 0.02% | 0.02% |
| Unibet-Site | 0.05% | 0.00% | 0.00% | — |

p99 of |log move| is **0.80** for Coolbet BTTS against 0.13–0.34 everywhere else.
Counting whole series instead of rows (max/min > 1.65, ≥3 observations, verified
against the live table not the snapshot): **3.68% of Coolbet BTTS series vs 0.37%
of its 1x2 and 0.00% of its O/U 2.5.**

**It is not a numeric parse error, and that is the important part.** Fixture
`72dfd8e0…` sits at `yes 2.03 / no 1.73` all morning, reads `yes 5.00 / no 1.15`
on the 18:11 sweep, and is back at `yes 1.97 / no 1.78` at 19:46. The anomalous
pair is **internally coherent** — its overround is 7.0%, right on Coolbet's normal
BTTS margin — so the scraper did not garble a number: it attached **a different
fixture's BTTS market** to this match for one sweep. That is the same failure
family as `ANALYSIS_GOTCHAS` §21 (Coolbet rows attached to the wrong fixture),
surfacing in a market where nobody had looked.

A mis-attached but self-consistent price is the worst kind for us: every
plausibility guard we have (positive odds, sane overround, complete complement)
passes it.

Two consequences. It makes Coolbet BTTS the "best" baseline in §4 (−4.94%) purely
by adding variance. And regressing y on `gap` in that market returns **R² = 0.91**,
which is not prediction — `gap` and `y` both contain `log(p)`, so price noise
manufactures the fit. With `gap` removed the same cell reads **R² = 0.0004**.
**Anyone who runs a gap-style regression without that control will find a
spectacular fake signal here.** Logged for triage; not fixed in this pass because
the brief bars touching workers/.

---

## 7. Verdict

### DEAD

- **Betting into a sharp/consensus move at Coolbet, Epicbet or Unibet-Site.**
  Follow-through β ≈ 0.1 where ≈1.0 is needed. 194 cells, none above 0, none
  within 4pp of 0, and the gate buys +0.36pp over its own permuted placebo
  against a 7–9pp toll.
- **Early/opening own-book prices as a source of CLV** (brief question 4). The
  time-to-kickoff profile is flat: Coolbet 1x2 reads −5.66% at >24h and −7.11%
  inside 15 min; every book/market spans ≤1.5pp across the whole pre-match window.
  No bucket is close to 0.
- **Coolbet's scrape cadence as a timing asset** (brief question 3). It has the
  worst per-fixture path resolution of all 12 books.
- **Cross-soft-book lead-lag** (brief question 5). Real correlation exists between
  Coolbet and Epicbet but carries no CLV; best arm −2.78%.
- **Asian handicap movement**, same conclusion, added because it is Coolbet's
  largest market.

### INSTRUMENT

- **Coolbet BTTS price integrity** (§6) — 7.87% wild-move rate is a parser/feed
  defect, and it will fake a signal for the next person who looks. Worth a row.
- **The 7-day path window** — if own-book price-path research is ever wanted
  again, retention must be changed *first*, or a standing weekly snapshot must
  run. Today's study was only possible because the window was frozen on the day.

### BUILD

**Nothing.** No pre-registration is offered, because a pre-registration requires a
candidate whose stopping rule could plausibly fire, and there is no cell within
4pp of break-even.

---

## 8. What is decided, and what is merely not-yet-measurable

**Decided** (n in the tens of thousands, CIs far from 0, and a mechanism that
explains the sign):

- Our books do not follow market moves. β ≈ 0.00–0.14, n = 16k–70k.
- A random own-book quote is worth −4.9% to −7.9% margin-corrected, everywhere.
- No movement gate tested reaches break-even; the *arithmetic* in §5 says none can.
- Epicbet steam-following is adverse, not merely useless (−11.3% vs −7.8%).

**Not decided** — and honestly so:

- **Extremely rare, extremely large steam.** The tightest gates hold 66–216 legs
  from 41–70 fixtures. A true +2% there would read as −4% ± 3pp. The *point*
  estimates are all negative and the arithmetic in §5 makes a large true effect
  implausible, but 8 days cannot produce enough ≥10% consensus moves to rule out
  a tail strategy outright.
- **Anything outside 8 days.** Every number here comes from
  2026-09-05..2026-09-14, and rows-per-series is not constant even inside it
  (Coolbet 2.6 on 09-12 vs 6.2 on 09-11). Per-day folds on the best cells swing
  −15.9% to +15.0% at n=8–31/day. **Do not read the per-day series as a trend** —
  the 09-14 cohort carries 53–56% of the legs in the best cells purely because it
  is the least-pruned day. Every cell above prints its span for this reason.
- **In-play.** Out of scope, pre-match only, and CLV is meaningless there anyway
  (§14).
- **Markets with no clean complement** beyond AH (corners, team totals, 1H), where
  the margin correction cannot be computed per row and so the bar cannot be
  evaluated at all.

**Multiple comparisons.** 194 real cells were graded. At α=0.05 one expects ~10
false positives by chance; **0 were positive in either direction toward profit**.
The placebo arm is the control for exactly this and it lands on top of the real
arm. There is no multiplicity correction to argue about because there is nothing
to correct.

---

## 9. Reproducing

```bash
# 1. freeze the window (MUST run within 7 days of the matches studied)
python3 scripts/own_movement_snapshot.py --from 2026-09-05 \
        --out data/own_movement_2026_09_14.parquet

# 2. the three measurements
python3 scripts/own_line_movement.py --parquet data/own_movement_2026_09_14.parquet --mode cadence
python3 scripts/own_line_movement.py --parquet data/own_movement_2026_09_14.parquet --mode leadlag
python3 scripts/own_line_movement.py --parquet data/own_movement_2026_09_14.parquet --mode cells
```

Method guards are pinned by smoke test `OWN-LINE-MOVEMENT-METHOD-PINNED`.

The frozen window used for every number in this document sits at
`data/own_movement_2026_09_14.parquet` (13 MB, 5,363,972 rows). It is **not
committed** — it is a large binary, and re-running step 1 today would no longer
reproduce it because the source rows are being pruned as they age. If that file
is lost, the 2026-09-05..09-14 price paths are gone for good and the tables above
cannot be regenerated, only re-measured on a new window.

**One bug worth naming**, because it cost a full pass and returns silently wrong
answers rather than errors: `datetime64[us].astype("int64")` yields
**microseconds**. Dividing by `60e9` to get "epoch minutes" makes every lookback
window 1000× too long and every freshness filter a no-op — the study returned
**zero events**, which read as a query-scope problem rather than a unit error.
Both scripts now derive minutes from `.dt.total_seconds()` and the smoke test
bans the constant.
