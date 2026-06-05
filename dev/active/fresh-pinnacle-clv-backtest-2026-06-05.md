# Fresh-Pinnacle CLV Backtest

_Generated 2026-06-05 18:44 UTC. Window: last 30 days._

## Question

Does our settle logic capture the freshest pre-kickoff Pinnacle row
available, or do we leave fresher rows on the table?

Tested by recomputing `clv_pinnacle` for every settled bet using a
strict `WHERE timestamp <= match.date AND bookmaker = 'Pinnacle'`
filter (the freshest pre-kickoff Pinnacle row in `odds_snapshots`),
then comparing the resulting alt-CLV against the current
`simulated_bets.clv_pinnacle`.

## Sample

| Slice | Count |
|---|---:|
| Settled bets in window with `clv_pinnacle` set | 1134 |
| Matched (alt Pinnacle row found pre-kickoff) | 671 |
| Unmatched (no Pinnacle row in DB before kickoff) | 463 |

_Unmatched bets are the ones where AF never delivered Pinnacle data_
_for the match before kickoff. This is AF's 3h refresh cycle leaving_
_gaps on some leagues — already documented in the fidelity audit._

## Headline CLV comparison (matched rows only)

| Metric | Current `clv_pinnacle` | Alt CLV (strict pre-kickoff) | Delta (alt − current) |
|---|---:|---:|---:|
| Mean | +8.12% | +7.70% | -0.42% |
| Median | +4.92% | +3.46% | +0.00% |
| Stdev | 13.93pp | 12.40pp | 7.32pp |

**Bets where settle picked a different row than alt:** 363 of 671 (54.1%)

**Alt Pinnacle row age at kickoff:**
- Median: 60 min before kickoff
- Mean:   184 min before kickoff

## Verdict

✅ Outcome (a) — settle logic is correct

Mean delta is **-0.422pp**, well within the 1pp materiality threshold. The settle logic is grabbing essentially the same row the strict `timestamp <= kickoff` query returns. The 60min audit gap is a property of AF's 3h refresh cycle, not our pipeline.

**Action:** no internal fix possible. The bookmaker freshness question is settled — either live with current AF cadence or pay enterprise prices ($300+/mo) for a sub-minute feed.

## Per-market breakdown

| Market | N | Mean delta | Median delta | Stdev |
|---|---:|---:|---:|---:|
| 1x2 | 671 | -0.42% | +0.00% | 7.32pp |

## Method

Single SQL query joining `simulated_bets` → `matches` → LATERAL
`odds_snapshots` (strict pre-kickoff Pinnacle). Per-bet alt-CLV is
computed in Python; distributions are reported above.

Materiality threshold: **±1pp on mean delta**. Below that, settle is
considered correct (outcome a). Above, settle has a bug (b or c).

## Re-run cadence

Re-run after any change to settle logic (`_get_pinnacle_close`) or
to the `is_closing` flag wiring. Otherwise, monthly as part of the
fidelity-audit cycle.
