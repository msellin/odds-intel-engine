# Coolbet in-play collection — plan

**Task:** INPLAY-SUSPENSION-LEAD-SIGNAL-2026-09-18 (🤖 OWN)
**Claimed:** 2026-09-18

## Why this, and what "done" means

Not "collect Coolbet in-play because more data is good". The rig exists to answer
ONE question:

> When Epicbet pulls its 1x2 market (a 19.06% goal predictor vs a 1.18% baseline,
> n=934), **is Coolbet still quoting a stale price?**

If yes, that gap is the only in-play edge available to us and the only OWN play
the 2026-09-14 verdict did not close. If no — if Coolbet suspends at the same
instant, or within our own round-trip latency — this closes as a negative and we
stop. **The measurement is the deliverable; the collector is just how we get it.**

## What is already measured (do not re-derive)

| Fact | Value | Source |
|---|---|---|
| Epicbet fetch cost | 0.13–0.8 s/fixture | measured 2026-09-18 |
| **Coolbet fetch cost** | **~6 s/fixture** (3.1 s markets + 2.8 s odds) | measured 2026-09-18 |
| Coolbet live sidebets `limit=13` | **8 groups / 12 markets** | measured 2026-09-18 |
| Coolbet live sidebets `limit=300` | **39 groups / 48 markets**, 1.4 s | measured 2026-09-18 |
| Live fixtures with a Coolbet mapping | 13 of 24 | `book_event_map` |
| Live fixtures with a Unibet-Site mapping | 21 of 24 | `book_event_map` |

**Coolbet is ~10–20× slower than Epicbet per fixture.** That single number drives
every design decision below.

## Design

1. **Separate process, not threads in the Epicbet loop.** Coolbet's shared
   FlareSolverr session silently returns ANOTHER match's markets under parallel
   reads (INPLAY-VIABILITY-GATE defect (b)) — plausible numbers in the right
   shape, which downstream name-matching happily accepts. So: **one serial
   reader, one dedicated FS session name, never `coolbet_prod`** (that session is
   real money; see `docs/COOLBET_RUNBOOK.md`).

2. **Reuse `inplay_book_quotes`.** It already carries a `book` column and is
   already pruned at 90 days. No migration needed. Epicbet rows keep their shape;
   Coolbet rows land beside them and join on `match_id` + `captured_at`.

3. **Fix the `limit=13` truncation.** Live goes to the same non-binding limit
   pre-match already uses. The comment defending 13 says "in-play is retired
   anyway" — that has been false since Phase 1b.

4. **Depth over breadth, deliberately.** At ~6 s/fixture serial, covering 13
   fixtures costs ~78 s — far worse than Epicbet's 45 s cadence, and the
   comparison needs the two books read CLOSE IN TIME on the SAME fixture. So the
   Coolbet arm tracks a **small intersection set** (AF-live ∩ Coolbet-mapped ∩
   on the Epicbet board), tightly sampled, rather than the whole board.
   Breadth is Epicbet's job; Coolbet's job is to be the second clock.

## Risks

| Risk | Mitigation |
|---|---|
| Parallel reads return another match's markets | single serial reader + dedicated FS session; assert returned match id |
| Touching `coolbet_prod` breaks real-money placement | hardcode a different session name; smoke test pins it |
| Imperva 403 / budget | serial + modest cadence; reuse runbook remedies, do NOT retry-storm |
| We conclude "edge" from a latency we cannot actually act on | pre-register the STOP below |

## Pre-registered stop

The cross-book lead must exceed our achievable round-trip. Measure the lead
first, our own placement latency second, and **STOP if lead ≤ latency** — that is
a negative result, not a tuning problem.

## Out of scope

No placement. No bot. No gate changes. `placement_paused` stays TRUE and this
work must not touch it.
