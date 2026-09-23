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
