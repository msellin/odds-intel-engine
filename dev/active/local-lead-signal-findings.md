# #186 LOCAL-BOOKS-LEAD-SIGNAL: findings (2026-09-26)

Parent row: `PRIORITY_QUEUE.md` #186. Direction: 👥 PICKS (Telegram rule at global books), with a
🤖 OWN side angle (the lagging Estonian book). Read-only research; nothing in production changed.
Script and pre-registration: `scripts/analysis/local_lead_signal.py`. The docstring was written
before the first run. Amendments 1 and 2 are post-hoc and are labelled that way.

## Answer in one paragraph

The Estonian books do not lead the global market. They lead **API-Football's copy** of it. AF's
pre-match `/odds` refreshes about every two hours. Its Pinnacle "close" last changed value a median
**120 min before kick-off** on all matches, and it changed in the last hour on only 14%. Our local
scrapes are a median 13 min old at kick-off. On the matches where the locals and AF-Pinnacle split, the
**live Betfair Exchange** (our own reader, not routed through AF) sits with the locals. On 17 of 17
matches with a gap of 0.05 or more, the exchange was nearer the local line. Across 131 matches the
regression slope of (exchange − AF-Pin) on (local − AF-Pin) is 1.12, with a correlation of 0.90. The
+18% ROI "at the global close" is therefore priced at quotes that were most likely gone from the real
global books by kick-off. **Do not build a Telegram PICKS rule on this.**

## Data

- 3,406 finished matches had at least 2 Estonian books quoting 1x2 within 3 h of KO, and 2,852 of them
  had a global line. After the data-fault guards and the common-window rule, **2,624** matches were
  usable. Their global line was the Pinnacle close (at most 60 min old) on 2,538 and a non-local
  consensus of 5 or more books on 86.
- In practice the window is **2026-08-27 → 09-25**. A second local book (Epicbet) starts on 08-27, and
  every signal match falls in September. That is about one month of data.
- Retention was verified as §59 describes. At 14 days or more, AF books keep about 6 rows per series:
  the open, the is_closing rows from the last 15 min, and the latest. Coolbet and Epicbet keep 1 row per
  series at 30 days. So the 6-hour path, and therefore liveness, is only visible in about the last 7–10
  days.
- The guards dropped 225 (match, book) pairs. 200 fell on the 1.5625× leg ratio against the global
  fair: Epicbet 88, Coolbet 55, Unibet-Site 45, Tonybet 12. A further 20 were dropped for DQ findings
  and 5 for an O/U 2.5 board mismatch.

## How often it fires (of 2,624 usable matches)

| G | signal (locals together, ≥ G shorter) | Pinnacle-anchored | OWN lag fires (of 820 with ≥ 3 locals) |
|---|---|---|---|
| 0.05 | **181 (6.9%)** | 170 | 31 |
| 0.08 | **34 (1.3%)** | 32 | 9 |
| 0.12 | **4 (0.15%)** | 3 | 1 |

The top signal leagues are EFL Trophy (11), Premier League 2 (9), Liga Alef, Paraguay Intermedia,
Liga de Expansión, USL Championship and the Professional Development League. That is youth, reserve
and cup football, where the late information is the lineup. Only 21 of 181 signals were
Nordic/Baltic.

## Pre-registered family (Holm over 15; bootstrap over matches, 10k, seed 186)

a = LL_global − LL_local (> 0 means the local line was better). b1 = flat ROI backing the locals'
side at the best global close. b2 = the same at Pinnacle's close. c = Pinnacle's open→close move
toward the locals. d = OWN: the lagging Estonian book at its own price.

| G | test | n | mean | 95% CI | p | Holm | verdict |
|---|---|---|---|---|---|---|---|
| 0.05 | a | 181 | +0.025 | [+0.003, +0.048] | 0.030 | 0.45 | undetermined |
| 0.05 | b1 | 181 | +18.1% | [+0.7, +36.0] | 0.042 | 0.59 | undetermined |
| 0.05 | b2 | 170 | +17.4% | [−0.1, +35.0] | 0.051 | 0.62 | undetermined |
| 0.05 | c | 156 | −0.003 | [−0.008, +0.003] | 0.32 | 1 | undetermined |
| 0.05 | d | 31 | −19.5% | [−57.6, +21.4] | 0.33 | 1 | undetermined |
| 0.08 | a | 34 | +0.065 | [+0.001, +0.126] | 0.047 | 0.62 | undetermined |
| 0.08 | b1 | 34 | +13.4% | [−25.7, +53.9] | 0.52 | 1 | undetermined |
| 0.08 | b2 | 32 | +11.0% | [−29.9, +52.2] | 0.62 | 1 | undetermined |
| 0.08 | c, d | 28, 9 | — | — | — | 1 | too few |
| 0.12 | all | ≤ 4 | (b: +131%, fewer than 2 losses, §66) | — | — | 1 | too few |

**Nothing survives Holm.** The base rate across all 2,624 matches is also slightly in the locals'
favour: a = +0.003 [+0.000, +0.005]. That is what you would expect if the local line is simply
**fresher** than AF's close. The local line is not better informed at a given moment.

My pre-stated expectation was ROI ≈ −margin and a ≤ 0. That was wrong in sign, and the post-hoc
checks below explain why.

## Live vs frozen: the owner's question

**Pre-registered split.** "Did it change in [KO−6h, KO]", measured on unpruned series only:

| Pinnacle in the 6 h before KO | n | a | b1 ROI |
|---|---|---|---|
| LIVE | 62 | +0.034 [−0.006, +0.071] | +24.1% [−4.4, +53.0] |
| FROZEN | 20 | +0.039 | +16.3% [−33, +67] |
| UNKNOWN (pruned) | 88 | +0.025 | +17.2% [−7.7, +43.0] |

The 6-hour window turned out to be the wrong resolution. A quote that last moved 3 h before kick-off
counts as "LIVE" here, yet it is still stale at the close.

**Post-hoc (Amendment 1).** Did AF-Pinnacle change value in the **last 60 min**?

| AF-Pinnacle changed in the last 60 min | n | a | b1 ROI |
|---|---|---|---|
| yes | 19 | +0.023 [−0.035, +0.079] | **−3.4%** [−47, +42] |
| no (frozen in our feed at the close) | 142 | +0.035 [+0.010, +0.060] | **+25.1%** [+5.5, +45.3] |

- The apparent edge sits almost entirely on quotes that AF had not refreshed in the final hour. Where
  AF did refresh, it vanishes (tiny n).
- AF-Pinnacle at the locals' own timestamp and AF-Pinnacle at the close are identical: the move is
  0.000 on 140 matches. The gap already exists at the locals' time (+0.070) and simply persists,
  because AF never updates.
- Test (c) (c = −0.003) is therefore uninformative. AF-Pinnacle cannot "follow" anyone within its
  2-hour refresh window.

**Post-hoc (Amendment 2).** Betfair Exchange as an independent live global price, 2026-09-24 → 25,
last capture at most 30 min before KO. 131 matches, 98 of them liquid:

| local − AF-Pin gap | n | local − AF-Pin | exchange − AF-Pin | exchange nearer the locals |
|---|---|---|---|---|
| ≥ 0.03 | 36 | +0.051 | +0.052 | 35/36 |
| ≥ 0.05 | 17 | +0.064 | +0.068 | **17/17** |

Across all 131 matches: corr 0.90, slope 1.12. The real global market moved with the locals. AF's
Pinnacle and Bet365 are **frozen in our feed**; they are not executable at those prices. The EBK v
GrIFK case fits this pattern. Only two days of exchange data exist, but the effect is not subtle.

## OWN angle (d)

The lagging Estonian book against the other locals: n = 31 at G = 0.05, ROI −19.5% [−58, +21], with
EV against the Pinnacle close of +4.5% [−0.8, +10.5]. That EV is judged against the same stale AF
close, so it is not trustworthy either. With about 820 matches carrying 3 or more locals (Tonybet only
since 09-23), this is **too thin to call**. There is no evidence for it.

## Recommendation

1. **Do not build a Telegram PICKS rule** that backs the locals' side at global prices. The "value" is
   API-Football latency. A reader clicking through at kick-off would find Bet365 and Pinnacle already
   moved, as the exchange shows.
2. **Keep #182's `market_split` guard, and read it the other way round.** When the locals split from
   the anchor, the usual cause is that the **anchor** is stale, not that the locals are wrong. The
   right response is "no price" (what #182 does), or re-anchor on the live exchange where it is
   liquid. It is never "bet against the locals" (the phantom +6..+24% draw).
3. **Wider implication, flagged for the queue and not acted on here.** `PIN_MAX_AGE_MIN = 60` and the
   "Pinnacle close" used by CLV, the sharp triggers and the anchor all age a quote by **our fetch
   time**. AF's own refresh is about 2 h. So the "fresh, ≤ 60 min" Pinnacle close is typically a
   quote that last moved about 2 h before kick-off. That deserves its own row: measure AF-Pinnacle's
   value-change age against the exchange across all matches, and decide whether the sharp close
   should prefer the exchange (`ANCHOR_USE_EXCHANGE`) near kick-off. It is also an ANALYSIS_GOTCHAS
   candidate.
4. **What to collect if anyone wants to revisit the PICKS idea.** Take a live global price at the
   moment the locals move: exchange captures (already running, so let it accumulate 4–6 weeks), or a
   direct Pinnacle or Bet365 scrape for the signal leagues. Then redo test (b) at the price available
   **at signal time** against the exchange close. Only a gap that the live exchange has not closed is
   a candidate edge. From today's data such gaps look rare, since the exchange was nearer the locals
   on 35 of 36 matches.
