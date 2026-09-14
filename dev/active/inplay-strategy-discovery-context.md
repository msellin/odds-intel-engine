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

## UPDATE — the structural finding (Parts 3 & 4)

**Where the in-play vig sits** (n=7,539 level-score snapshots, minute 30–84,
book overround 7.8%):

| outcome | vig charged | relative cost |
|---|---|---|
| favourite | +1.8pp | **3.8%** |
| draw | +2.3pp | **6.7%** |
| underdog | +3.7pp | **14.3%** |

This explains every negative result: the losing strategies were not wrong about
football, they were **shopping on the long side**. "Back the trailing favourite" buys
at 2.9–7.5; "lay the dog" at 3.6–10.8.

**Confirmed by direct test (Part 4).** Restricted to prices 1.05–2.30 the house edge
nearly vanishes: 0-0 → under 2.5 at 1.63 costs 0.4%; a 2-goal leader at 1.11 is
indistinguishable from break-even across three windows. Still nothing positive —
every interval containing a gain contains zero.

**The operational consequence:** a genuine 2–3% signal would SURVIVE short-side and be
WIPED OUT long-side. So the search is constrained, not hopeless. **Screen every future
candidate for "does this back a price under ~2.20?" before testing it.**

## Morning checklist

1. `pgrep -f inplay_epicbet_collector` — the 9h run should have ended on its own.
2. Re-run `edge2.py` / `shortside.py` logic against `epicbet_inplay.jsonl` (real
   placeable prices) instead of AF's aggregate. Expect the *shape* to hold and the
   absolute costs to be lower — Epicbet's O/U margin measured 6.4–6.6% vs AF's 7.8%.
3. Always run `robust.py`-style wide-window + split checks BEFORE a candidate is
   written down as a candidate.

## UPDATE — fidelity (Part 5) and what is still running

**AF's live 1x2 de-vigs to essentially Epicbet's** (n=307, 7 fixtures, concurrent
threads): median gap −0.01 / +0.42 / −0.04pp on home/draw/away. Margins differ,
AF 6.51% vs Epicbet 5.56%, so every Part 2–4 number is **conservative by ~+0.8pp ROI**.
No conclusion flips. **The 2.2M-row history is therefore usable for 1x2 discovery.**

⚠️ **1x2 only.** Most candidates are O/U and that fidelity is NOT established.

### Two detached jobs running overnight

| job | output | purpose |
|---|---|---|
| `inplay_epicbet_collector.py` (~9h from 20:34 UTC) | `<scratch>/epicbet_inplay.jsonl` | the placeable in-play board |
| `<scratch>/oufid.py` (900 cycles / 30s) | `<scratch>/oufid.jsonl` | **O/U fidelity AF vs Epicbet** — the open question |

Both detached with `<scratch>/daemonize.py`. Check with
`pgrep -f inplay_epicbet_collector` and `pgrep -f oufid.py`.

**Gotcha already hit and fixed in `oufid.py`:** AF puts the O/U line in the
`handicap` field, *not* inside `value` (which is just "Over"/"Under"). Parsing the
line out of the string yields zero rows and looks like "no overlap" rather than an error.

### Morning: analyse `oufid.jsonl`

Per line: `{t, home, af:{line:{over,under}}, eb:{line:{over,under}}}` on common lines.
Compute the de-vigged P(over) gap per line, exactly as Part 5 did for 1x2. If the
median gap is near zero, margin-adjust the O/U numbers and the history is usable for
O/U discovery too. If not, O/U pricing work waits on accumulated Epicbet data.
