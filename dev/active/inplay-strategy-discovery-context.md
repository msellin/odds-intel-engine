# In-play strategy discovery — CONTEXT

**Last updated: 2026-09-14 ~20:35 UTC. Owner asleep. Agent autonomous.**

## Running right now

**Epicbet in-play collector — DETACHED, ~9h, started 20:34 UTC.**
```
workers/jobs/inplay_epicbet_collector.py
  --jsonl <scratch>/epicbet_inplay.jsonl --cadence 30 --rediscover 300
  --max-fixtures 30 --hours 9
```
Detached via `<scratch>/daemonize.py` (double fork — macOS has no `setsid`, and a
plain `nohup ... &` was killed with the tool call twice).
Check: `pgrep -f inplay_epicbet_collector`, log at `<scratch>/collector.log`.

Row shape (verified live): fixture + league + teams + `af_fixture_id` + `minute` +
`seconds` + `score` + **`af_age_s`** + ~29 markets over 13 families (1x2, ou, ah2,
btts, next_goal, corners_*, team_total_*, correct_score…).

`<scratch>/epicbet_inplay_nostate.jsonl` is the first 240 rows, collected before the
state bug below was fixed — odds are good, `minute`/`score` are null. Backfillable by
joining on team names + `captured_at` if it is ever worth it.

## Key files built

| path | what |
|---|---|
| `workers/jobs/inplay_epicbet_collector.py` | the collector (repo module, committed-ready) |
| `<scratch>/extract.py` | 2.2M snapshots → `matches.pkl`, one row per match |
| `<scratch>/matches.pkl` | **35,442 matches**: score at every 5-min bucket, final, HT, pre-match ov25/1x2 |
| `<scratch>/strategies.py` | the 14 candidate strategies + backtest harness |
| `<scratch>/edge.py` | hit rate vs the price the market showed (AF live, quiet states only) |

## Bugs already found and fixed (do not reintroduce)

1. **AF name lookup collapsed home/away.** `SELECT ht.name, at2.name` returns two
   columns both called `name` in a dict row, so away overwrote home and every fuzzy
   match scored against the wrong pair → `minute`/`score` were null on every row.
   Columns are now aliased. This is the same class as the `100% filled` SQL comment
   that psycopg2 read as a placeholder.
2. **requests pool exhaustion** — one thread per fixture against a default pool of 10.
   Adapter now sized to 40.
3. **`nohup &` does not survive the tool call**; `setsid` does not exist on macOS.

## The load-bearing insight

**Staleness only bites when the state is changing.** AF live odds are ~40s stale, which
is fatal around a goal but nearly harmless at a quiet 0-0. So the 7.5% of snapshots
carrying AF live prices ARE usable to estimate edge — but only on quiet triggers
(goalless / level), never on post-goal ones. `edge.py` enforces that.

## Next steps

1. Read `<scratch>/edge.out` — empirical hit rate vs offered price, the first real
   edge estimate.
2. Write `docs/INPLAY_STRATEGY_CANDIDATES_2026_09_14.md` (14 strategies + evidence).
3. In the morning: re-run the same edge test against the collected **Epicbet** prices
   (placeable, fresh) instead of AF's stale aggregate.
4. Update `PRIORITY_QUEUE.md` and commit everything together.

## Not done, deliberately

* Nothing deployed to the production scheduler. No migration pushed. No bot placing.

## UPDATE — B1 withdrawn (same night)

The "+9.0% ROI at minute 40" was a **narrow-bucket artefact**. Widening the entry to
38'–47' — the window a bot would actually use — gives **+2.3%, CI [−2.8,+6.9]** on a
*larger* sample, and the neighbouring windows are a spike, not a plateau
(−4.9 / −1.1 / **+2.3** / −1.0 / −5.0). It fails every other split too.

**No positive in-play strategy currently survives.** The negatives do, and they are the
deliverable: they are monotone across many buckets on large samples.

**The procedural rule to carry forward:** widen a trigger to the realistic betting
window BEFORE believing a cell. ~50 cells were tested; one significant cell is what
multiple testing produces on its own. `<scratch>/robust.py` is the check — run it on
every future candidate before it gets a line in a doc.
