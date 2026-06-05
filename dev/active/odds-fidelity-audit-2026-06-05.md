# Odds Fidelity Audit

_Generated 2026-06-05 18:24 UTC. Window: last 30 days._

The strategic question: are our existing odds feeds (API-Football + 
Coolbet scraper) reliable enough, or do we need a complementary
aggregator (e.g. The Odds API at +$59/mo) to tighten our CLV story
and broaden detected value bets?

This audit measures five things across our ingested books, and the
final section translates the numbers into a Phase 1 recommendation.

**Books in scope:**
- *Accessible* (used for value-bet edge math): 10Bet, 888Sport, Bet365, Betano, Coolbet, Marathonbet, Pinnacle, Unibet
- *Inspect-only* (ingested for comparison, currently excluded): 1xBet, BetVictor, Betfair, Bwin, Dafabet, SBO, Superbet, William Hill
- *Synthetic / excluded from scoring*: Avg, Betfair Exchange, Max, api-football, api-football-live

---

## 1. Placement freshness

How stale was the latest available odds row for the recommended
bookmaker when the bot decided to bet? `pick_time` minus latest
`odds_snapshots.timestamp` for the same match/market/selection/book.

Window: last 30 days. Sample: 942 bet decisions.

| Book | N decisions | Median staleness | P95 staleness | Verdict |
|---|---:|---:|---:|---|
| 10Bet | 126 | 25.6m | 39.4m | ✅ fresh |
| 888Sport | 19 | 13.1m | 44.6m | ✅ fresh |
| Bet365 | 196 | 14.1m | 41.7m | ✅ fresh |
| Betano | 195 | 20.4m | 44.2m | ✅ fresh |
| Coolbet | 6 | 3.8h | 6.7h | 🔴 very stale |
| Marathonbet | 142 | 12.5m | 42.2m | ✅ fresh |
| Pinnacle | 121 | 15.4m | 42.5m | ✅ fresh |
| Unibet | 137 | 13.3m | 42.4m | ✅ fresh |

Thresholds — `✅ fresh` = median < 30min, `⚠️ ~stale` < 2h, `🔴 very stale` ≥ 2h.

## 2. Pinnacle close-capture staleness

For every settled bet with a clv_pinnacle value, how recent was the
latest Pinnacle snapshot we had for the match-market-selection before
kickoff? Smaller is better — the smaller this gap, the closer our
captured `closing_odds` is to the actual market close.

Window: last 30 days. Settled-bet sample: 704.
Bets with NO Pinnacle row before kickoff: **233**

| Percentile | Gap (kickoff − latest Pinnacle row) |
|---|---:|
| P25 | 44.9m |
| Median | 60.0m |
| P75 | 2.0h |
| P95 | 13.6h |

⚠️ Median gap is 60.0m — workable but loose.
Lines move in the final hour before kickoff; our close capture
is missing some of that motion. Marginal case for fresher source.

## 3. Implied-sum sanity (1X2 markets)

For each book's most-recent 1X2 quote per match (last 30 days), the
implied-probability sum should land in the typical overround band
of **1.02-1.15**. Sub-1.0 means a book is offering free money (data
bug). Above 1.20 means the book is structurally non-competitive.

| Book | Matches | Avg sum | Min | Max | % in 1.02-1.15 band | Verdict |
|---|---:|---:|---:|---:|---:|---|
| 1xBet | 7297 | 1.0908 | 1.0129 | 1.1771 | 97.4% | ✅ healthy |
| Marathonbet | 7287 | 1.1022 | 1.0220 | 1.1177 | 100.0% | ✅ healthy |
| Dafabet | 7150 | 1.0757 | 1.0337 | 1.1243 | 100.0% | ✅ healthy |
| Betano | 7033 | 1.0894 | 1.0201 | 1.1953 | 99.0% | ✅ healthy |
| William Hill | 6877 | 1.1168 | 0.9855 | 1.1584 | 99.7% | ✅ healthy |
| Pinnacle | 6775 | 1.0842 | 0.9893 | 1.2137 | 99.3% | ✅ healthy |
| Bet365 | 6629 | 1.1019 | 1.0345 | 1.2746 | 99.8% | ✅ healthy |
| 10Bet | 6616 | 1.1005 | 1.0373 | 1.1334 | 100.0% | ✅ healthy |
| Betfair | 6338 | 1.1043 | 1.0258 | 1.1583 | 99.9% | ✅ healthy |
| BetVictor | 5950 | 1.1166 | 1.0458 | 1.2115 | 74.7% | 🔴 broken / non-competitive |
| Unibet | 5870 | 1.1060 | 1.0371 | 1.1578 | 99.2% | ✅ healthy |
| SBO | 3861 | 1.1437 | 1.0636 | 1.2284 | 59.8% | 🔴 broken / non-competitive |
| 888Sport | 2411 | 1.1069 | 1.0459 | 1.1576 | 99.8% | ✅ healthy |
| Superbet | 1694 | 1.0932 | 1.0374 | 1.1244 | 100.0% | ✅ healthy |
| Coolbet | 270 | 1.0742 | 1.0298 | 1.1995 | 99.6% | ✅ healthy |
| BetWin | 201 | 1.0650 | 1.0357 | 1.1093 | 100.0% | ✅ healthy |

## 4. Cross-book consistency on home-win probability

For each match, when 2+ accessible books quote 1X2, what's the
spread on implied home-win probability? A real market has books
disagree by 1-4 percentage points on home-win across them. >10pp
spread = at least one book has stale or broken data.

| # books quoting | N matches | Avg range (max-min p_home) | Avg stddev |
|---:|---:|---:|---:|
| 2 | 113 | 0.013 | 0.009 |
| 3 | 202 | 0.025 | 0.013 |
| 4 | 538 | 0.032 | 0.015 |
| 5 | 1252 | 0.042 | 0.017 |
| 6 | 2918 | 0.045 | 0.017 |
| 7 | 2124 | 0.041 | 0.014 |
| 8 | 154 | 0.048 | 0.016 |

Interpretation — avg range below 0.05 across many books = healthy
market agreement. Above 0.10 = systematic divergence; investigate.

## 5. Per-book refresh cadence

How often does each book's data actually update in odds_snapshots?
Measures the gap between consecutive snapshots per `(match, market,
selection, bookmaker)`. Tighter median = more responsive feed.

| Book | Sample gaps | Median refresh interval | P95 |
|---|---:|---:|---:|
| 1xBet | 252831 | 1.0h | 6.2h |
| Marathonbet | 251703 | 1.0h | 6.2h |
| Dafabet | 247938 | 1.0h | 6.2h |
| Betano | 242244 | 1.0h | 6.2h |
| Bet365 | 234000 | 1.0h | 6.2h |
| William Hill | 227838 | 1.0h | 6.2h |
| Pinnacle | 220545 | 1.0h | 6.2h |
| 10Bet | 218184 | 1.0h | 3.5h |
| Betfair | 218136 | 1.0h | 6.2h |
| Unibet | 201510 | 1.0h | 6.2h |
| BetVictor | 198221 | 1.0h | 4.2h |
| SBO | 121677 | 1.0h | 6.3h |
| Superbet | 80214 | 1.0h | 2.0h |
| 888Sport | 68337 | 1.0h | 12.0h |
| Coolbet | 2976 | 1.0h | 9.5h |
| BetWin | 291 | 7.0d | 7.0d |

---

## Recommendation framework

Read the verdicts above against these thresholds:

**If Section 2 (Pinnacle close-capture staleness) median > 1h**: the
case for a complementary aggregator is strong. Our CLV benchmark is
materially stale. Action: subscribe to The Odds API 100K plan ($59/mo)
for fresher Pinnacle scraping; route only the Pinnacle stream of TOA
into `odds_snapshots` (don't double-ingest other books we already get
from AF). Re-run this audit after 2 weeks of TOA Pinnacle to confirm
median gap dropped < 10min.

**If Section 2 median 5-60min**: marginal case. Continue with AF for
now; pursue **official Pinnacle API access** via `api@pinnacle.com`
as zero-cost optionality.

**If Section 2 median < 5min**: AF is fine. **Do not pay for a
complementary aggregator** based on freshness alone. Look elsewhere
for bookmaker-expansion ROI (e.g. un-excluding Bwin in Section 3).

**If any book in Section 3 shows < 80% in 1.02-1.15 band**: that book
is broken or non-competitive on 1X2. Recommend dropping from edge
math (similar to William Hill OU blacklist) until fixed upstream.

**If Section 4 avg range > 0.10 with 3+ books**: book divergence is
wide enough to suggest one or more sources is glitchy. Cross-check the
worst outliers per match (`scripts/diag_book_outlier.py` would be a
natural follow-up if this audit shows the problem).

**Re-run cadence:** monthly until N (settled bets in window) ≥ 500,
then quarterly. Save outputs to `dev/active/odds-fidelity-audit-
YYYY-MM-DD.md` so we can track per-book fidelity over time.
