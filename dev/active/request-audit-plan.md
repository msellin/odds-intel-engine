# Request audit — what we fetch, what it costs, what we can stop

**Started 2026-09-19.** Trigger: an Incapsula flag took Coolbet down ~12.5 h, and
the runbook's own diagnosis of recurring §2 blocks is *"our own request volume
from one IP"*. Volume is now a first-class constraint, not an afterthought.

## The three questions, in order

1. **What do we request today?** Every outbound consumer, its schedule, and its
   real request count — measured, not inferred from the cron line.
2. **Same data, fewer requests?** Where the payload is bigger than the need:
   discarded responses, re-fetching unchanged data, N calls where 1 would do.
3. **What do we request that we do not need at all?** Markets we cannot bet,
   books we cannot place at, fields nothing reads, fixtures out of horizon.

## Budgets these compete for

| Source | Constraint | Consequence of exceeding |
|---|---|---|
| Coolbet | Imperva, per-IP volume | full block, hours-to-days, recurring |
| Epicbet | anonymous REST, no known cap | (watch, none observed) |
| Unibet-Site | logged-in Chrome tab on the Mac | session loss |
| API-Football | 150k calls/day hard quota | pipeline stops |
| Pinnacle guest | unauthenticated, easy to flag | block |

## Rules for this audit

- **Measure, never estimate.** A cron schedule is not a request count; the
  Coolbet sweep's "every 30 min" was really ~192 category requests per pass with
  ~73% discarded, and passes overlapped into a continuous stream.
- **A row count is not a request count** — one request can write hundreds of rows
  (37 rows per match per sweep on `odds_snapshots`).
- **Separate "wasteful" from "unneeded".** Fetching a market we cannot bet is
  unneeded; fetching a market we CAN bet 48×/day when it moves twice is wasteful.
  The fixes differ.
