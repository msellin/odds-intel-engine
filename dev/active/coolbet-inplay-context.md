# Coolbet in-play — context / state

**As of 2026-09-18 17:37 UTC+3. Collector is SHIPPED and RUNNING. The measurement is not done.**

## Where things stand

`com.oddsintel.inplay-coolbet-collector` (Mac launchd, KeepAlive) is steady at
**8 targets → 7 rows, 1 empty, 0 errors, ~45-49 s per 90 s cycle**. Rows land in
`inplay_book_quotes` with `book='Coolbet'` beside the Epicbet arm.

Data quality verified post-fix: **0 duplicate (row, fam, line) groups, 0 pre-fix
rows remaining, suspension being captured** (16 suspended of 668 selections in
the current window).

## What the next session should do FIRST

**The measurement, which is the whole point.** Everything above is plumbing.

> When Epicbet's 1x2 goes OPEN → SUSPENDED, is Coolbet still quoting?

Both books now write `inplay_book_quotes`, so this is a self-join on
`(match_id, captured_at within N seconds)` comparing each book's 1x2 suspension
state. **The pre-registered STOP is in the plan: if the cross-book lead is at or
below our achievable round-trip latency, this closes as a NEGATIVE.** Do not
convert that into a tuning exercise.

Needs a few days of paired rows first — at 7 rows/cycle the sample builds slowly,
and the fixtures both books cover is the binding constraint (13 of 24 live
fixtures have a Coolbet mapping; Unibet-Site has 21 of 24 and is the obvious
third arm if Coolbet's coverage proves too thin).

## Traps already paid for — do not rediscover

1. **Suspension must never be stored as absence.** The first version dropped
   price-less selections, destroying the measured quantity. Pinned by
   `COOLBET-INPLAY-SUSPENSION-IS-DATA`.
2. **fo-match and sidebets both return the headline markets** — de-dup by
   (fam, line) or every count doubles.
3. **A separate FS session does NOT isolate the footprint.** `daemons_paused` is
   checked per cycle, failing closed. Never point this at `coolbet_prod`.
4. **`af_state()` returns only {minute, seconds, goals, af_age_s}** — league and
   team names come from the DB.
5. **The smoke runner is a ThreadPoolExecutor** and `os.environ` is shared —
   see `_ROUTER_ENV_LOCK` (RELIABILITY_LEDGER 16).

## Open, filed

- `COOLBET-INPLAY-ORPHAN-RESOLVES-FIRST-MATCH-2026-09-18` (P2) — the orphaned
  `coolbet_inplay.py` is the other `live=True` caller; a first-match resolver now
  picks among ~48 markets instead of ~12. Probably delete it.
