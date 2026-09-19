# Coolbet — how to run 24/7 on a fraction of the requests

**2026-09-19.** Coolbet is our main betting site and its sweep must run 24/7, but
at **~2,132 requests/pass × 48 passes/day ≈ 102,000/day from one residential IP**
it gets the IP flagged by Imperva. Measurements in
`docs/REQUEST_AUDIT_2026_09_19.md`. This is the menu of cuts, ranked by
(saving × confidence), with the two that need live validation marked.

**The shape of one pass:** 1 `fo-tree` + 191 category lists + **485 events × 4**
(`fo-match`, `sidebets`, `odds`, `odds/fo-line`). **91% is per-event.** Any fix
that does not attack the per-event multiplier is rounding error.

---

## 1. Batch the odds calls across events — biggest single win ⚠️ validate

**Today:** 2 odds requests **per event** — 970 of the 2,132.

**Why it can be batched:** neither odds endpoint is scoped to a match. They take
lists of globally-unique market ids:

```python
POST /s/sb-odds/odds/current/fo        {"where": {"market_id": {"in": [...]}}}
POST /s/sb-odds/odds/current/fo-line/  {"marketIds": [[...]]}
```

So: collect market ids across **all** events in the pass, chunk, and fetch odds
in bulk. Epicbet's equivalent already chunks at 250.

**Saving:** ~87,300 ids/pass at 500/chunk ≈ **175 calls instead of 970 (−37% of
the whole pass)**. With §3 it drops to ~18 calls.

**Cost:** restructures the sweep loop — markets for all events first, then bulk
odds, then store. Today it interleaves per event.

**⚠️ Validate when the block lifts:** that a single call accepts ids spanning
multiple matches, and the real chunk ceiling.

## 2. Poll the >12h band rarely — do NOT truncate it

Your 24h→12h idea is worth more than the fixture count suggests: only 7.7% of
fixtures are >12h out at any instant, but events spend as long in that band as
under it, so it is **47.9% of all polls**.

| horizon cut | polls removed |
|---|---|
| 24h → 12h | **47.9%** |
| 24h → 8h | 58.1% |
| 24h → 6h | 67.7% |

**But truncating has a real cost: 15.4% of Coolbet-priced picks are made more
than 12h before kickoff**, and we hold 113,751 Coolbet `is_opening` rows that
`model/train.py` reads. Cutting the horizon throws both away.

**Better: tier it.** Keep the 24h horizon, poll the >12h band **every 6h instead
of every 30 min** (÷12). Saves **~44%** — almost all of the truncation's benefit
— while keeping the opening snapshot and the early picks.

Also relevant: `near_kickoff_capture` already covers T−15 min every 300 s, so the
main sweep does not have to carry precision near kickoff.

## 3. Only fetch odds for markets we actually bet

**We store 168 distinct Coolbet markets. Seven have ever been picked.**

| picked (90d) | rows stored (30d) |
|---|---|
| `double_chance` 6,829 · `1x2` 5,381 · `over_under_25` 2,634 · `over_under_35` 1,487 · `btts` 747 · `asian_handicap` 282 · `over_under_15` 23 | `asian_handicap` 187,059 · `1x2` 124,136 · `over_under_25` 78,280 · `over_under_35` 77,907 · `btts` 68,337 · … 163 more |

Filtering market ids before the odds call **does not on its own reduce the
request count** (still 2 calls/event, just smaller) — **but it multiplies with
§1**, cutting the batched id list by ~90%: ~18 bulk calls instead of ~175.

**Decide deliberately:** breadth has a purpose (cross-book comparison, future
markets, `lineshop_new_markets`). The cheap compromise is to fetch odds for the
7 live families every pass and sweep the long tail once or twice a day.

## 4. Drop `fo-match` if `sidebets` already covers it ⚠️ validate

`fetch_match_markets` calls both. Evidence they overlap: the in-play collector
produced **433 duplicated `(fam, line)` market entries** because the headline
markets (1x2, O/U 2.5, AH) came back from **both** sources — that is what forced
the de-dup in `inplay_coolbet_collector`.

If `sidebets` at the non-binding limit is a superset pre-match too, dropping
`fo-match` removes **1 of the 4 per-event requests — ~23% of the pass**, with no
restructuring.

**⚠️ Validate:** diff the two responses for a few fixtures before removing.

## 5. Things that do NOT help (measured, so they are not tried again)

- **A larger `limit` saves nothing.** It changes payload size, not request count
  — still one GET. It is already effectively unbounded at 1000, and raising it
  from 13 on 2026-09-18 was free.
- **More parallel sweepers make it worse.** Imperva gates on the IP (proven: same
  VPS, same Linux Chromium, residential-EE tunnel → 170,237 bytes of real
  `fo-tree`; the datacenter IP never solves the challenge). More readers behind
  one address is more volume from a flagged address.
- **Discarding the 330 non-near-term events per pass** costs nothing — they
  arrive inside category responses already paid for.

---

## Suggested order

| # | change | saving | risk |
|---|---|---|---|
| 2 | tier the >12h band to 6-hourly | ~44% | low — no API change, reversible |
| 4 | drop `fo-match` | ~23% | low once diffed |
| 1 | batch odds across events | ~37% (→ more with 3) | medium — restructures the loop |
| 3 | odds only for live market families | multiplies §1 | low, but a product decision |

**2 + 4 alone take ~102k/day to roughly 35k** with no API risk and no loop
rewrite, and both can ship before Coolbet is even reachable again. Adding 1 + 3
takes it under 10k.

**None of this is a substitute for a request budget that throttles itself.** The
footprint control today is `daemons_paused`, a manual switch — which is what
failed on 2026-09-18.
