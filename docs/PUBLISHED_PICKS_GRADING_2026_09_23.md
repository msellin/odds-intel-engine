# Grading the published picks — pattern audit, 2026-09-23

Owner's question: *"we already have 18 picks from the consensus bot today… I'm afraid they are
not profitable and pollute the Telegram channel. Can we grade them, mark some more reliable than
others, so users can choose the better ones?"*

Parent row: [[#094]] in `PRIORITY_QUEUE.md`.

## 1. Consensus arm (`bot_consensus_anchor_v1`)

> **CHANGED 2026-09-23 ([[#095]], migration 380):** the arm is now **two bots** —
> `bot_consensus_b_v1` (grade B, `beta`) and `bot_consensus_c_v1` (grade C, `testing`);
> `bot_consensus_anchor_v1` is retired. Split in the views, not the ledger: the picks keep
> `arm='consensus_anchor'`, so a leg whose grade flips between runs is still de-duped.
> Each grade has its own record on /performance and a badge on /picks and Telegram.

### Volume — there is no "last Wednesday"

The arm started **2026-09-22 12:15 UTC** ([[#068]]). It published **26 picks on its first
(half) day and 18 on 2026-09-23**. It did not exist a week ago. For comparison, the sharp
arm (`arm='live'`) published 10 on Wednesday 2026-09-16 and nothing before 2026-09-14.

### Live record: 1 day, far too small to judge

2026-09-22: 26 published, **3 void** (postponed non-league fixtures, published before the
`status='scheduled'` fix), 23 settled: **8 won, −3.29u (−14% ROI), mean CLV −1.9%**.
The standard error on 23 bets at ~2.5 odds is about ±30pp ROI. This is not evidence either way.

### Replay: 677 picks over 56 days

`scripts/consensus_arm_replay.py` re-runs the arm's exact rule (it imports the publisher's
constants) over the stored odds history: 30-minute cadence, now+45 min to now+14 h window,
latest quote per book within 6 h, first qualifying side per (match, market), with the 8% ceiling.
**Fidelity:** it reproduces the live 2026-09-22 legs with the same book, odds and edge
(e.g. Three Bridges home 3.00 Unibet-Site, edge 0.0728). It finds 40 that day against the
live 26 because the live arm only started at 12:15.

Volume grew sharply in week 38. That is when the Epicbet and Unibet-Site feeds came online
(more books → more ≥5-book consensuses), so the replay is weighted toward recent weeks.

| | n | ROI (1u flat) | closing edge* |
|---|---|---|---|
| All | 677 | **−4.6% ± 4.6** | +0.3% ± 0.4 |
| Discovery (first ⅔) | 451 | −1.1% ± 5.6 | +0.5% ± 0.5 |
| Holdout (last ⅓) | 226 | −11.7% ± 8.0 | +0.0% ± 0.6 |

\* closing edge = `p_close × odds − 1`, with `p_close` taken from the same ≥5-book de-vigged
consensus at the last snapshot before kickoff. It is much less noisy than P&L.

**Headline: the rule has no demonstrable edge.** Published picks average ~+5% "edge" against
the consensus when posted. By kickoff the same consensus puts them at **+0.3%**, which is
indistinguishable from zero. The market closes most of the gap, so most of the posted edge is
movement we are early to, not mispricing we are right about.

### First pass — splits that held in BOTH halves (superseded by the shipped grade below)

Every split below was checked in both chronological halves. Splits that flipped sign between
halves were discarded; about 12 features were examined, so single splits at 1–2 SE are expected
by chance.

| Split | n | ROI | closing edge | disc / hold ROI | Mechanism |
|---|---|---|---|---|---|
| League `tier = 0` (unclassified: youth, women, reserves, regional cups) | 77 | **−34.0% ± 12.2** | −1.5% | −31 / −39 | the consensus of soft books is thin and wrong there |
| **Pinnacle disagrees** (edge vs Pinnacle's own de-vig < 0) | 75 | −12.5% ± 13.5 | **−1.5%** | −17 / +8 (ce −1.0 / −3.6) | the one sharp-ish book says there is no edge |
| Best price at an Estonian book (Epicbet / Coolbet / Unibet-Site) | 444 | −5.3% | **−1.9%** | ce −2.6 / −1.1 | the market moves AGAINST these picks by close — the soft book is lagging a move, not mispriced |
| Best price at Coolbet | 82 | −25.3% ± 13.2 | −1.4% | −19 / −36 | subset of the above |
| No Pinnacle quote at all | 179 | −10.1% ± 8.6 | 0.0% | −0 / −30 | no confirmation available |

**Combined keep-filter:** `tier ≠ 0` AND Pinnacle agrees (edge vs Pinnacle ≥ 0):

| | n | ROI | closing edge |
|---|---|---|---|
| **Keep** | 377 | **+2.2% ± 6.3** (disc +5.7 / hold −4.1) | +0.9% |
| **Drop** | 300 | **−13.2% ± 6.6** (disc −8.9 / hold −23.0) | −0.4% |

The kept group is still not *proven* profitable: its holdout ROI is negative and its CI spans zero.
The dropped group is consistently worse in both halves, with a ~15pp gap (≈1.7 combined SE).
**This is an honest "C-grade" filter, not a "these win" filter.**

**Kept AND best price at a non-Estonian book** (Bet365, 10Bet, Superbet, Betfair…): n=119,
ROI +11.9% ± 12.1, closing edge **+6.2%** (disc +5.9 / hold +7.5). ⚠️ **Not trusted.**
AF's Bet365 feed is known to run well above takeable prices (ODDS-OUTLIER-FILTER), so a price
that "keeps" its edge to the close may never have been available. The holdout n is only 21.
It also conflicts with 🤖 OWN, which can only bet the Estonian books.

### Is there a sharper book than Pinnacle to check against? (owner: "Marathonbet?")

No book in our feed is measurably sharper than any other. We scored each book's own de-vigged
closing 1x2 odds against results over 45 days, paired against Pinnacle on the same matches:

| Book | n | log-loss − Pinnacle's | t | median margin |
|---|---|---|---|---|
| Marathonbet | 10,767 | −0.00065 | −1.63 | 11.2% |
| 1xBet | 10,689 | −0.00036 | −0.83 | 10.1% |
| Betfair | 8,574 | +0.00027 | +0.42 | 10.6% |
| SBO | 5,251 | +0.00121 | +1.32 | 14.9% |
| Bet365 | 10,525 | +0.00038 | +0.63 | 11.1% |
| **Coolbet** | 6,227 | **+0.00579** | **+2.94** | 7.7% |
| Pinnacle (reference) | 10,928 | — | — | 9.1% |

Marathonbet is marginally better than Pinnacle, but not significantly. **Coolbet is the only
book that is measurably worse.** So no single book can be "the" sharp second opinion. The grade
therefore asks a **panel** of five (Pinnacle, Marathonbet, Betfair, 1xBet, SBO) whether any of
them disagrees.

### Gates, floors and ceilings swept (owner: "did you try different odds gates, floors, ceilings?")

These were measured on the same 677 replayed picks, applied on top of the live rule. ROI is
1u flat; "disc / hold" are the two chronological halves.

| Variant | n | ROI | disc / hold |
|---|---|---|---|
| Live rule (3–8%) | 677 | −4.6% | −1.1 / −11.7 |
| Edge 3–6% (ceiling 6%) | 539 | −1.6% | +2.2 / −9.2 |
| Edge 4–6% | 265 | +2.0% | +5.5 / −5.7 |
| Edge 6–8% | 138 | **−16.6%** | −14.2 / −21.1 |
| Odds ≤ 1.6 | 48 | +17.8% | +26.2 / −0.5 |
| Odds 3.0–4.0 | 173 | **−14.3%** | −12.0 / −19.0 |
| Odds ≤ 2.5 | 386 | −1.6% | +7.7 / −18.9 |

**No floor, ceiling or odds band is profitable in both halves.** Two bands are consistently bad:
edge above 6% (the biggest edges are the wrong prices, [[#007]] again) and odds above 3.0.

A second book disagreeing is the strongest single signal. Here "disagreeing" means another
panel book, not the one offering the price, sees no edge at the published price:

| | n | ROI | disc / hold |
|---|---|---|---|
| Another panel book disagrees | 122 | **−36.6% ± 10.0** | −41.5 / −22.4 |
| No other panel book disagrees | 555 | +2.4% ± 5.1 | +9.1 / −10.0 |

⚠️ **Why the grade uses realised ROI, not closing edge.** For picks priced at API-Football-fed
books (Bet365, 10Bet, Superbet…), the two measures disagree. Closing edge is +3.9% while ROI is
−1.3%. Where a panel book disagrees, closing edge is +4–6% while ROI is −15% to −38%. That is the
stale-close signature (`ANALYSIS_GOTCHAS` §70): the closing snapshot for those books is not a
real close. For this arm, closing edge is therefore not a safe ranking metric.

### Shipped grades (a label only; the rule and selection are unchanged)

| Grade | Definition | Replay n | ROI | disc / hold |
|---|---|---|---|---|
| **C — weaker** | league tier 0, OR another panel book sees no edge, OR edge > 6% | 285 | **−25.6% ± 6.6** | −26.0 / −24.7 |
| **B — standard** | everything else | 392 | +10.6% ± 6.2 | +18.3 / **−3.4** |

B–C gap: permutation p ≈ 0.001. **Caveat on that p:** the three conditions were chosen after
looking at both halves, out of about 15 features examined. The C side is robust: it is similar in
both halves and each condition has a mechanism. B is **not** proven profitable, since its holdout
is negative. There is no "A", because nothing earns one yet.

Implemented in `scripts/publish_picks_forward_test.py` (`grade_consensus_pick`, `_grade_line`).
The grade is stored in `picks_forward_test.grade` / `grade_reasons` (migration 379) and shown on
every consensus Telegram message. Earlier picks are backfilled by
`scripts/backfill_consensus_grades.py`.
**Backfill result (44 picks):** 2026-09-22: 14 B / 12 C; 2026-09-23: 14 B / 4 C. On the settled
day, **B went 5/14 for −3.27u and C went 3/9 for −0.02u**. That is the opposite of the replay,
at an n where ±30pp is noise; recorded so nobody reads the replay as a promise.
**Next:** per-grade record on /performance and /picks ([[#095]]), so the arm can be split in two
and C retired on its own evidence.

## 2. Sharp arm (`bot_sharp_forward_test_v1`, `arm='live'`) — audited, NOT graded

Done by a separate sub-agent with the same method (its scripts were scratch, not committed).

**Volume:** 8 / 4 / **10 (Wed 09-16)** / 3 / 7 / 42 / 17 / 1 picks from 09-14 to 09-21, then
**0** on 09-22 and 09-23. The ≤4% anchor-overround gate shuts it midweek, which is why the
consensus arm exists.

**Record (v4, n=84):** ROI +1.9% ± 28pp. Closing edge vs Pinnacle close +1.90% ± 0.94. The junk
control (shuffled anchor, n=397 deduped) comes in at −2.47% ± 0.38. So on closing edge **the live
arm beats its negative control by ~4.4pp**; on realised P&L the two cannot be told apart at this n.

**Replay:** the exact rule reproduces 73 of 84 ledger picks. Only 115 picks exist over 56 days,
because older odds history is pruned. Patterns were mined on a relaxed pool (gate lifted,
n=1,097) with a chronological holdout.

| Split | Consensus-close edge, disc / hold | Verdict |
|---|---|---|
| Best price at Epicbet / Coolbet / Unibet-Site | −0.77 / −2.44 | bad in both halves (same as the consensus arm) |
| Best price at an API-Football-fed book | +0.99 / +2.01 | good, but see caveat |
| Edge 3–4% | −0.69 / −3.11 | bad in both halves |
| Edge ≥ 6% | +1.92 / +1.60 | good in both halves (opposite of the consensus arm) |
| Anchor overround ≤ 4% | +2.36 / −0.01 | did not replicate |

**Proposed** A/B/C: C = Estonian scraped book OR edge < 4%; A = other book with edge ≥ 6%.
Holdout: A +4.6%, B +2.6%, C −2.2% closing edge. Of the 84 live picks, **67 would have been C**.

**Not shipped, for two reasons** ([[#095]]):
1. The grade rests on closing edge for API-Football-fed prices, which on the consensus arm
   disagrees with realised ROI (see §1). A/B needs a price-takeability check first.
2. Its C is precisely the books 🤖 OWN can bet. Grading those down is right for 👥 PICKS readers
   and hostile to own-betting, which is the owner's call.

**Common to both arms:** best price at an Estonian scraped book goes against us by kickoff. The
likely mechanism is that the scraped quote is older than the anchor (~22 min on the sharp arm),
so the apparent edge is a timing gap that reverts.

---

## 3. Pre-registration — grade A, and a re-test of B/C on UNSEEN data (2026-09-23, written BEFORE the run)

Owner: *"we need profitable bets to be grade A… we have thousands of games and odds from
starting May"*. Correct: the §1 replay used the script's default `--days 56`, not a data
limit. 1x2 matches with ≥5 books: May 7,888 · Jun 2,743 · Jul 3,557 · Aug 8,723 · Sep 6,687.

**Why this is a clean test.** The B/C conditions were chosen by looking at the last 56 days
(≈ 2026-07-29 onward). Everything **before 2026-07-29 was never looked at**, so it is
out-of-sample for every rule below. The run is `consensus_arm_replay.py --days 146`; only
rows with kickoff < 2026-07-29 are the test ("UNSEEN").

**Grade A candidates — a fixed family of four, Holm-corrected (m = 4):**

| id | rule (all on top of grade B) | why it is a candidate |
|---|---|---|
| A1 | best price at a NON-Estonian book | only split positive in both §1 halves (+5.9 / +7.5) |
| A2 | edge 4–6% | positive early, negative late in §1 |
| A3 | odds ≤ 1.6 | positive early, flat late in §1 |
| A4 | FULL panel coverage: ≥4 panel books other than the offering book priced the market, and ALL see an edge | the strongest single signal in §1, made stricter. *Clarified before any result: "every available book agrees" is already implied by grade B (a dissenting available book makes it C), so A4 must require coverage to be a distinct rule.* |

**PASS for an A candidate** (all three, on UNSEEN rows only):
1. ROI > 0 with Holm-adjusted one-sided p < 0.05 (m = 4), 1u flat;
2. ROI > 0 in BOTH halves of the unseen window (split at the median kickoff date);
3. beats grade B on the same rows.

**B/C re-test:** C must again be worse than B on UNSEEN rows — the claim the channel was
told on 2026-09-23 (message 395). If it is not, the explainer was wrong and gets corrected.

**Known confounds, stated before the result:** fewer books early (Epicbet / Unibet-Site came
online in week 38), so early consensuses are built from a different panel; and A1 rides on
API-Football-fed prices that may not have been takeable (ANALYSIS_GOTCHAS §70, [[#096]]) —
**A1 passing is necessary, not sufficient**: it still needs the price check before it is
published as A.

**Expected result:** no candidate passes (the §1 holdouts faded); C stays worse than B.

### RESULT (2026-09-23) — no grade A; C < B holds out of sample, but the gap shrinks ~5x

`consensus_arm_replay.py --days 146` → 1,212 replayed picks; 535 UNSEEN (kickoff before
2026-07-29; mostly May — 374 — because summer has few ≥5-book matches). Fidelity: the seen
window reproduces §1 exactly (B +10.6% n=392, C −25.6% n=285).

| | UNSEEN n | ROI | half 1 / half 2 |
|---|---|---|---|
| **B** | 179 | **+4.8% ± 8.0** | −2.3 / +6.8 |
| **C** | 356 | **−2.8% ± 5.7** | −8.1 / +5.9 |

**C is worse than B in both unseen halves — the channel claim (msg 395) stands — but the gap
is ~7.6pp out of sample vs ~36pp in sample.** Most of §1's gap was fit to the window that
chose the rules. B is still not proven (+4.8% ± 8.0).

**Drift by month (B − C gap):** May +4.6pp · Jun +19.9 · Jul* −17.0 · Aug* +44.2 · Sep* +34.6.
Positive in 4 of 5 usable months; the one reversal (July, n≈75) is inside noise.

**Each C condition alone, UNSEEN:** tier 0 −6.9% (n=80) · a panel book disagrees −1.4%
(n=267) · **edge > 6% +1.2% (n=149)**. Tier 0 is the most robust (−34% in sample too); the
edge > 6% condition does NOT hold out of sample. Not changed on this alone — noted for when
the live C record is big enough to decide it.

**Grade A — all four candidates FAIL (Holm m=4):**

| candidate | UNSEEN n | ROI | Holm p | halves |
|---|---|---|---|---|
| A1 non-Estonian best price | 179 | +4.8% | 0.824 | uninformative: before week 38 there WERE no Estonian feeds, so A1 = B |
| A2 edge 4–6% | 98 | −3.2% | 1.000 | −38.0 / +7.5 |
| **A3 odds ≤ 1.6** | **29** | **+14.7%** | 0.379 | **+11.2 / +15.3** |
| A4 full panel agrees | 71 | −5.6% | 1.000 | +1.2 / −7.9 |

**A3 is the only lead:** positive in both unseen halves AND in the seen window (+17.8%,
n=48) — but 29 picks cannot pass a correction, and at odds ≤1.6 a single upset swings it.
**Decision: no grade A is published.** A3 needs no new code to track: it is
`picks_forward_test WHERE arm='consensus_anchor' AND grade='B' AND odds <= 1.6`, both columns
already stored. Re-test it on LIVE picks only (published after 2026-09-23) at n ≥ 150.

## 4. Pre-registration — can grade B be made better? Walk-forward, all books (2026-09-23, BEFORE the run)

Owner: *"can we make grade B better? we can use any odds that we have, not just Estonian
ones, as our customers are not Estonians"* (👥 PICKS — any book's price is fair game).

**Why walk-forward.** All 146 days are now spent: §1 chose the grades on the last 56, §3
tested them on May–July. Searching the same rows again for a "better B" would be the
overfit §3 just caught. So the SEARCH is re-run inside every fold on past months only, and
scored on the next month: May→Jun, May–Jun→Jul, May–Jul→Aug, May–Aug→Sep. Every scored pick
is out of sample for the config that selected it. This measures whether *refining B* works,
not whether one rule fits history.

**Grid (all mechanistic — aimed at stale/phantom prices, the known loss source):**
confirmation `n_books_pos_edge` ≥ 1/2/3 · `gap_to_second` ≤ none/5%/3% · market both/1x2/O-U ·
max odds 4.0/2.5 · **min odds none/1.8/2.0** (owner, before the run: *"add some odds floor for grade B?"* — 324 configs total) · lead time any/≤6h. Selection in each fold: highest train ROI with train
n ≥ 40.

**PASS:** pooled out-of-sample ROI of the selected configs beats plain grade B on the same
test months, one-sided bootstrap p < 0.05, AND keeps ≥ 40% of B's volume. Otherwise B stays
as it is.

**Expected:** fails. Confirmation ≥2 is the most plausible single winner, given §1's stale-
price evidence.

### RESULT (2026-09-23) — FAIL: no refinement of B survives walk-forward

| test month | config chosen on earlier months | train | test, refined | test, plain B |
|---|---|---|---|---|
| Jun | confirm≥1, both, max 2.5 | +2.7% n=67 | +25.8% n=31 | −4.6% n=44 |
| Jul | confirm≥1, 1x2, max 2.5 | +12.3% n=55 | +14.1% n=27 | +23.2% n=40 |
| Aug | confirm≥1, 1x2, max 2.5 | +12.9% n=82 | +12.3% n=32 | +15.5% n=70 |
| Sep | confirm≥2, both, max 4.0 | +15.6% n=45 | −5.8% n=68 | +10.4% n=318 |

**Pooled out of sample: refined +7.5% (n=158) vs plain B +10.8% (n=472), −3.4pp, bootstrap
p=0.68, 33% of volume kept. FAIL on all three conditions.** The config that looked best on
past months was worse than plain B in 3 of 4 test months — the search was fitting noise.

Descriptive, all grade-B rows (not a test): the owner's **odds floor makes B worse** —
odds ≥1.8 +8.6%, ≥2.0 +5.6%, vs <1.8 +9.6%; confirmation ≥2 books +2.7%, ≥3 −11.3%;
1x2 +10.1% vs O/U +4.9%. **Plain grade B stays as it is.**

## 5. Pre-registration — grade B / C / A on the Beat the Bookie time series ([[#098]], BEFORE the run)

Owner: *"can we even test more back? like 2026 start, 2025?"* Our own odds before 2026-04 hold
1–2 snapshots per match — the rule cannot be replayed on them. `data/raw/beat_the_bookie/`
(Kaunitz, Zhong & Kreiner 2017) holds `odds_series.csv` + `odds_series_b.csv`: **~128k
matches, 32 anonymous books × 72 hourly 1x2 prices** (index 71 = the hour before kickoff —
checked: 4,280 prices at idx 71 vs 569 at idx 0 over 300 rows, books open late).

**Replay (mirrors `consensus_arm_replay.py`):** hourly runs from 14 h to 1 h before kickoff;
per book the latest price within 6 h; consensus = mean of each complete book's own de-vigged
1x2, **≥5 books**; best price per outcome across books; the publisher's MAX_ODDS 4.0,
MAX_RATIO 0.20, edge 3–8%; the FIRST qualifying outcome per match (claim-once). Close = the
consensus at index 71. 1x2 only — the series has no O/U.

**Grade (the live rule, translated because books are anonymous):**
* **panel** = the 5 books with the lowest log-loss of their own de-vigged index-71 prices
  against results, measured on the EARLIEST 20% of matches by date. Those matches are then
  EXCLUDED from every result below. (Same reasoning as the live panel: sharpness measured,
  not assumed.)
* **tier 0** = league name matches women / U17–U23 / youth / reserve / amateur / junior /
  primavera (the live tier-0 bucket: youth, women, reserves, regional).
* **C** = tier 0 OR a panel book (not the offering one) sees no edge at the price OR
  edge > 6%. **B** = the rest.

**Questions and bars (on the remaining 80%, split into two halves by date):**
1. **Is B profitable?** ROI > 0 with one-sided p < 0.05 AND > 0 in both halves.
2. **Is C worse than B?** B − C > 0 in both halves.
3. **Grade A**, Holm m = 3, same bar as §3 (ROI > 0 Holm p < 0.05, both halves, beats B):
   A2 edge 4–6% · **A3 odds ≤ 1.6** (the only §3 lead) · A4 full panel (≥4 panel books other
   than the offering one priced it, all see an edge). A1 (non-Estonian book) is undefined here.

Also reported, not tested: CLV vs the index-71 consensus, per-half and per-league-group ROI.

**Expected:** B's ROI slightly negative to flat; C worse than B; no A. Kaunitz et al.
reported this family of strategy profitable at the MAX price across many books — our rule
takes the best of ~32, so a positive B would not be a surprise either. Stated so neither
outcome is read as vindication after the fact.

### RESULT (2026-09-23) — the method works on 17,810 external picks; B passes; A3 (odds ≤ 1.6) passes

91,572 matches extracted (the time-series files cover **2015-09 → 2016-11**, not 2005–15 —
that span is closing-odds only). Calibration: earliest 18,314 matches, excluded. Panel =
b2, b14, b9, b1, b13. Evaluated: 73,258 matches → **17,810 picks**; halves split 2016-06-11.

| | n | ROI | p | half 1 / half 2 | CLV vs close |
|---|---|---|---|---|---|
| all consensus picks | 17,810 | **+3.76% ± 1.01** | 0.0001 | +4.01 / +3.51 | +4.27% |
| **grade B** | 7,076 | **+3.98% ± 1.54** | 0.005 | +7.37 / +0.57 | +3.64% |
| grade C | 10,734 | +3.62% ± 1.32 | 0.003 | +1.78 / +5.43 | +4.69% |

* **Q1 B profitable: PASS.** **Q2 C worse than B: FAIL** — C is also profitable here (+5.6pp
  gap in half 1, −4.9pp in half 2). The C rule, translated to anonymous books, does not
  separate on this data; on our own data it does (in sample strongly, unseen weakly).
* **Not one broken book:** profit is spread across books, CLV positive for nearly all; B
  without its top contributor stays +3.2 to +3.5%. The largest-volume book (b10, 22% of picks)
  LOSES −4.6% — the phantom-price book of this dataset.

**Grade A (Holm m=3, on grade-B picks):**

| candidate | n | ROI | Holm p | halves | verdict |
|---|---|---|---|---|---|
| A2 edge 4–6% | 3,557 | +6.09% | 0.006 | +8.75 / +3.40 | PASS here — but our own unseen data: −3.2% (n=98) |
| **A3 odds ≤ 1.6** | **696** | **+9.77%** | **<0.0001** | **+8.69 / +10.79** | **PASS — and positive in BOTH of our own windows** |
| A4 full panel agrees | 3,902 | +1.89% | 0.18 | +2.24 / +1.59 | FAIL |

**A3 is the one rule that has now held in three independent samples:** our last 56 days
(+17.8%, n=48), our unseen May–July (+14.7%, n=29), and 2015–16 external (+9.8%, n=696, win
rate 0.764 at mean odds 1.44 vs 0.697 implied). It has a published mechanism — the
favourite-longshot bias (favourites are under-priced) — and it is spread across books.

**Caveats that stand:** 2015–16 had ~32 books and softer markets; our live consensus uses
~7–12. Bookmakers limited Kaunitz et al.'s accounts (irrelevant for 👥 PICKS, decisive for
🤖 OWN). Grade B by odds band here: ≤1.6 +10.6%, 1.6–2.0 +6.1%, 2.0–2.5 +7.2%, 2.5–3.0
−3.0%, 3.0–4.0 +2.7% — the owner's instinct that B has a weak zone is right, but it is the
HIGH-odds end, not the low.

### Are three tiers distinct? + grade A odds floor (2026-09-23, owner: "if we add 3 tiers, they must be really distinct"; "maybe better if we don't bet at 1.1 odds")

A = grade B at odds ≤ 1.6; B below = grade B without A.

| sample | A | B | C | A − B | B − C |
|---|---|---|---|---|---|
| ours, last 56 d (rules built here) | +27.1% (32) | +9.2% (360) | −25.6% (285) | +17.9pp p=0.057 | **+34.8pp p<0.001** |
| ours, May–Jul unseen | +14.7% (29) | +2.9% (150) | −2.8% (356) | +11.9pp p=0.21 | +5.7pp p=0.31 |
| external 2015–16 | +9.8% (696) | +3.3% (6,380) | +3.6% (10,734) | **+6.4pp p=0.011** | **−0.3pp p=0.56** |

**A > B in every sample. B > C only on our own data**, and mostly on the window that built
the rules — out of sample the gap is small or none.

**Odds floor for A:** below 1.20 is almost empty (external 9 picks, −10.4%; ours 2). With a
1.20 floor external A is +10.0% (n=687) vs +9.8% without; 1.30 gives +10.1% (n=629). A
floor at **1.20** costs nothing measurable and removes the picks readers find pointless.
Bands externally: 1.20–1.30 +9.4% (58) · 1.30–1.40 +8.0% (143) · 1.40–1.50 +12.2% (169) ·
1.50–1.60 +9.9% (317).

Owner decision pending: three tiers (A 1.20–1.60 · B · C, C retired if it does not separate
live) or two (A · everything else — the only split distinct in every sample).
