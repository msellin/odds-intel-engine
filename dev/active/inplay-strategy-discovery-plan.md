# In-play strategy discovery — PLAN

**Started 2026-09-14 ~20:15 UTC. Owner asleep; agent running autonomously.**

## The ask

1. Produce **10+ candidate in-play betting strategies**.
2. **Validate them on data we already hold.**
3. **Collect Epicbet in-play odds** so the ideas that need a live price can be validated later.

Owner's own seed idea: *a game pre-match priced for goals that is still goalless at
minute X should be a good moment to back OVER — you are waiting for a better price
on the same outcome.* Treated as strategy #1, not as the whole programme.

## The key decomposition (why any of this is testable tonight)

An in-play bet has two halves, and **we already own one of them outright**:

| half | data | status |
|---|---|---|
| **Did the event happen?** | `live_match_snapshots.minute` + `score_home/away` (fully populated, 2.2M rows, 35,493 matches, 45s cadence) + `matches.score_*` final | ✅ **clean, usable now** |
| **What price would we have got?** | in-play odds | ❌ `live_1x2_home` only 7.5% filled, and sourced from AF `/odds/live`, measured ~40s stale |

So **hit rates and break-even odds are computable tonight on clean data**; only the
question "does the market actually offer better than break-even?" needs new collection.
That is exactly what the Epicbet collector is for.

**No sharp anchor is required.** In-play bets settle on the final score, which we have.
Realized ROI against settlement is ground truth; an anchor would only reduce variance.

## Method

For each strategy: define an entry **trigger** expressible purely in (minute, score,
pre-match price), compute the empirical hit rate of the outcome, convert to a
**break-even price**, then judge feasibility against the live prices we collect.

Guard rails, learned the hard way earlier today:
* Strategies are written down **before** looking at their numbers (no p-hacking).
* Every number gets an `n` and is treated as directional below n≈300.
* Pre-match price comes from **non-live** `odds_snapshots` rows only.
* The AF live price is **never** used as a price — only its score/minute, which are sound.

## Deliverables

* `docs/INPLAY_STRATEGY_CANDIDATES_2026_09_14.md` — the 10+ strategies with evidence.
* `workers/jobs/inplay_epicbet_collector.py` — the collector.
* Overnight Epicbet capture in the scratchpad (JSONL).
* Updated `PRIORITY_QUEUE.md`.

## Deliberate scope limits

* **Nothing is deployed to the production scheduler while the owner is asleep.** The
  collector runs locally against files tonight; the migration + scheduler wiring are
  prepared for review, not pushed live.
* No bot places anything. This is discovery only.
