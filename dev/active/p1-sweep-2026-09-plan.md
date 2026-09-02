# P1 Sweep — 2026-09-02

Six P1 tickets, accepted in this order. Ordering is not arbitrary: the
restatement changes the inputs every later ticket is measured on.

## Why this order

`STALE-BEST-ODDS` (fixed 2026-09-02) means every historical `odds_at_pick` is
a high-water mark rather than a price on offer. Anything computed from those
odds is inflated — including `edge_percent`, which is `odds × prob − 1`. So:

1. **STALE-ODDS-HISTORY-RESTATE** must go first. Until there is a live-priced
   column, `BET365-EXECUTION-AUDIT` measures execution against fictional
   prices and `SWEEP-HOME-BOTS-CALIBRATION` measures edges partly made of the
   bug.
2. The rest can then run against clean inputs.

Note the one hypothesis already tested and REJECTED: the stale odds do NOT
explain the sweep bots' 45% edges (72.6% → 72.0% of picks above 20% edge when
re-priced). That ticket is a real calibration fault. Do not re-test it.

## Phases

| # | Ticket | Est | Gate before starting |
|---|---|---|---|
| 1 | STALE-ODDS-HISTORY-RESTATE | 2-4h | — |
| 2 | AF-STALE steps 2-4 | ~2h | independent of 1 |
| 3 | BET365-EXECUTION-AUDIT | 2-4h | needs 1 (prices must be real) |
| 4 | SWEEP-HOME-BOTS-CALIBRATION | 4-6h | needs 1 (edges must be real) |
| 5 | BOT-GATE-OU-BTTS | 3-4h | needs 4's calibration read |
| 6 | AF-QUOTA-REALLOCATION | 2-4h | needs OPERATOR SIGN-OFF |

## Risks

- **Restatement blast radius.** `pnl` and `bankroll_after` are running totals
  and bot promotions were decided on them. Decision taken: add a PARALLEL
  `odds_at_pick_live` column, never overwrite `odds_at_pick` or `pnl`. The
  audit trail is the product; destroying it to look tidier is the one
  unacceptable outcome.
- **The honest number is smaller.** Landing cohort goes +13.96% → ~+10.27%,
  which puts us level with Betaminic (+10.60%) rather than ahead. Accepted by
  the operator; publishing the smaller true number is the point.
- **AF-QUOTA needs sign-off** — it changes live polling cadence. Do not ship
  without explicit approval.
- **Coverage is partial** (76% of the landing cohort is repriceable). Always
  publish n and coverage alongside any restated figure, per ANALYSIS_GOTCHAS
  #29.
