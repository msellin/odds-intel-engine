# #191 OWN research idea 3: local lag vs a LIVE anchor (2026-09-26)

Parent row: `PRIORITY_QUEUE.md` #191 (OWN bot line-up). Direction: 🤖 OWN, meaning a stake rule at our
own Estonian books. Read-only; nothing in production changed.
Script and pre-registration: `scripts/analysis/own_local_lag_live.py`. The docstring was written before
the first run. Amendment 1 is post-hoc and is labelled that way.

## Answer in one paragraph

**Dead on arrival. The signal never fires.** In 2.4 days (456 finished matches with an exchange quote,
09-24 10:05 → 09-26 18:45) there were **0** lag legs whose stale price beat the live fair by 2% or more,
in either 1x2 or O/U 2.5. All eight pre-registered family cells are "too few" (n = 0). The reason is
structural, not a lack of data. On a leg whose price has not moved, an Estonian book sits a median
**−7.8% (1x2) / −6.8% (O/U)** below the live de-vigged fair. That gap is simply its margin. The live
market does move against a book that has not updated yet, but the move between two consecutive scrapes
(30–60 min) is almost never large enough to eat through a 7% margin. Among the 35 legs where the live
market did move by 2 points or more while the book stood still, the best edge was **+0.9%**.

## Pre-registered result

| market | X | judge | holdout n | verdict |
|---|---|---|---|---|
| 1x2 | 0.02 / 0.05 | exchange close / consensus close | 0 | too few |
| O/U 2.5 | 0.02 / 0.05 | exchange close / consensus close | 0 | too few |

- Fires: **0 per day** at X = 0.02 and at X = 0.05. The discovery half is also 0, so there is no ROI,
  no lag duration and no per-book figure.
- My expectation of "tens of fires per day" was wrong. I had under-estimated how wide the local margin
  is against a de-vigged live price.

## Amendment 1 (post-hoc, descriptive): does the lag carry any information?

These are every UNCHANGED local leg with the same live source at both scrapes. "Lag" means the live fair
rose by 2 points or more between the book's two scrapes. "Control" means it moved by less than 0.5
points. The first leg per (match, book, side, group) is counted.

| market | group | n | edge vs live fair (p50 / p90 / max) | CLV vs exchange close | CLV vs consensus close | flat ROI |
|---|---|---|---|---|---|---|
| 1x2 | lag | 22 | −5.1 / −0.3 / +0.9% | −1.7% [−5.1, +3.8] (n 3) | −9.0% [−12.6, −5.9] | −5.3% [−45, +33] |
| 1x2 | control | 2,583 | −7.8 / −3.4% | −7.4% [−8.1, −6.8] (n 946) | −8.8% [−9.2, −8.4] | −12.3% [−17, −7] |
| O/U 2.5 | lag | 13 | −5.1 / −4.3 / −4.1% | −3.1% [−4.2, −0.9] (n 3) | −8.0% [−11.1, −5.7] | +24% [−21, +65] |
| O/U 2.5 | control | 1,426 | −6.8 / −3.9% | −6.0% [−6.3, −5.7] (n 420) | −6.9% [−7.0, −6.7] | −4.7% [−6.6, −2.7] |

- The direction is as hypothesised. A lagging leg is **less bad** than a quiet one, by about 2–3 points
  of edge and about 4–6 points of exchange-close CLV. On all six per-book pairs the lag leg's exchange
  CLV beat the control's. But the lag leg's exchange CLV has n = 3 per market, and it stays negative:
  the lag shrinks the margin but never erases it.
- Lag events are rare because the live market moves by 2 points or more between two consecutive local
  scrapes only about 35 times in 2.4 days, across all four books and both markets.
- The consensus-close CLV is about −8% to −9% whatever the group. As pre-stated, those AF books carry
  the ~2 h refresh latency described in §88, so they cannot "see" the live move. That close is
  independent of the bet book, but it is not a live judge.

## Update rhythm (part 3)

An exchange move is a rise of 3 points or more in 20–40 min on a liquid market that persists to the
next capture. There were 19 such moves (match × market) and 56 book × move pairs.

| book | n | median follow (min after move) | led | ≤30 min | ≤60 min | ≤120 min | never | median scrape interval |
|---|---|---|---|---|---|---|---|---|
| Coolbet | 17 | +16 | 6% | 59% | 65% | 76% | 24% | 60 min |
| Epicbet | 18 | +13 | 22% | 89% | 94% | 94% | 6% | 30 min |
| Unibet-Site | 10 | +5 | 40% | 50% | 80% | 80% | 20% | 59 min |
| Tonybet | 11 | +7 | 27% | 55% | 64% | 64% | 36% | 30 min |

- Every book follows within its **first scrape** after the move (median +5 to +16 min). "Led" means the
  book was already there before the exchange moved; that happened on 6–40% of moves. So the Estonian
  books are **not slow** relative to the exchange. The follow time we can see is bounded by OUR scrape
  cadence of 30–60 min, not by the book.
- Epicbet is the most reliable follower. Coolbet is the laggard: it is last to lead (6%) and never
  follows on 24% of moves. It is also the book scraped least often, at 60 min, so part of that is our
  resolution. n is tiny, and this is descriptive only.

## Is the exchange history long enough?

**No, and it will not fix this idea.**

1. **Length.** There are 2.4 days of exchange data. Only **21%** of exchange 1x2 captures in the last
   30 min before KO are liquid (169/798), and 15% for 1x2 at 30–120 min. That drops to 8% more than
   12 h out, and to 1% for O/U. A liquid exchange close existed for about 100 of 286 matches (the
   control group's exchange-CLV n is 946 of 2,583 legs). At that rate, 4–6 weeks of history would give
   a usable exchange close on about 1,000–1,500 matches, which is enough for a CLV judge.
2. **But the signal is margin-bound, not sample-bound.** The best stale edge seen was +0.9%, against a
   ~7% margin. More weeks will add rare big-move cases, such as lineup news in small leagues (the EBK v
   GrIFK type). Expect well under 1 fire per day at X ≥ 2%.
3. **Retention caps the look-back at about 7 days.** `odds_snapshots` keeps the full local price path
   for only about 7 days (§59). This study therefore can never see more than about a week of local
   scrape pairs at once, however long the exchange table grows. A re-test needs either a rolling run
   whose results are appended, or longer path retention for the four local books.

## Verdict for the OWN line-up

- **Do not build a LOCAL-LAG bot.** At X ≥ 2% against a live fair, it produced zero bets in 2.4 days.
  This is not "can't tell yet": the local margin structurally exceeds the lag we can observe.
- The useful by-product is the Amendment 1 direction. A price that has not yet followed a live move is
  **less overpriced** than usual. That is a tie-breaker (which Estonian book to place an OWN 1X2·SHARP
  pick at), not a signal. It is already implied by "take the best Estonian price".
- The rhythm table confirms #186. The Estonian books move with the live market within one of our
  scrapes, and it is AF-Pinnacle that is slow.
- Revisit only if (a) scrape cadence for a book drops to ≤ 10 min, so that a real book-side lag becomes
  visible, or (b) someone wants the lineup-news case specifically. That would be a different, narrower
  hypothesis, and it would need first-seen lineup timestamps (a #191 gap already listed).
