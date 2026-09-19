# Request audit — Coolbet and API-Football (2026-09-19)

Written after an Incapsula flag took Coolbet down ~12.5 h. The runbook's standing
diagnosis of recurring §2 blocks is *"our own request volume from one IP"*, and
nobody had ever counted the requests. This counts them.

**Every number here is measured** — from the sweep's own logs, `api_budget_log`,
and the database. A cron interval is not a request count.

---

## 1. COOLBET — ~102,000 requests/day from one residential IP

### Where they go

`coolbet_explorer --board --horizon-hours 24`, launchd at :03/:33.
Per pass (measured 2026-09-18 19:34, a typical evening slate):

| Stage | Requests | Source of the multiplier |
|---|---|---|
| `fo-tree` (board index) | 1 | one call, retried up to 3× on failure |
| category event lists | **191** | 237 categories − 46 skipped by `BOARD-SWEEP-NEARTERM-SKIP` |
| per near-term event | **1,940** | **485 events × 4** |
| **total** | **≈ 2,132** | |

The **4 requests per event** are not obvious from the call site and are the whole
story:

```
fetch_match_markets()     → POST fo-match        (1)
                          → GET  sidebets        (1)
fetch_odds_for_markets()  → POST odds (simple)   (1)
                          → POST odds/fo-line    (1)
```

**91% of the pass is per-event odds fetching.** Categories are noise by
comparison, which means `BOARD-SWEEP-NEARTERM-SKIP` — the last volume fix — went
after the small half.

At :03/:33 that is up to **~102,000 requests/day at one bookmaker from one
residential IP**. And passes do not fit their slot: observed 80/237 categories at
18:39, 200/237 at 19:09 — **a pass takes 30+ minutes**, so passes overlap and the
sweep is effectively a continuous request stream, exactly as the runbook says.

### What that volume buys — measured churn

How often does a consecutive poll of Coolbet 1x2 return a *different* price
(5 days, 74,548 polls)?

| time to kickoff | polls | changed | unchanged |
|---|---|---|---|
| <1h | 4,011 | 22.9% | 77.1% |
| 1–3h | 7,716 | 17.6% | 82.4% |
| 3–6h | 13,216 | 17.8% | 82.2% |
| 6–12h | 15,094 | 18.2% | 81.8% |
| **>12h** | **34,511** | **8.5%** | **91.5%** |

**The >12h band is 46% of all polls and has the lowest churn of any band.** Note
what this does NOT say: prices 3–12h out move on ~18% of polls, so that band is
doing real work and must not be cut to nothing. The waste is concentrated, not
uniform — which is why "poll everything less" is the wrong fix.

### The cheapest ~50% cut

Tier the poll interval by time to kickoff instead of polling the whole 24h
horizon every 30 minutes:

| band | today | proposed | effect |
|---|---|---|---|
| >12h | 30 min | **3 h** | −39% of all polls |
| 6–12h | 30 min | **1 h** | −10% of all polls |
| <6h | 30 min | unchanged | keeps the band that actually moves |

**≈ 49% fewer Coolbet requests**, and the information lost is a price that was
unchanged 91.5% of the time and is picked up on the next poll regardless.

### Things that are NOT the problem (checked, so nobody re-litigates them)

- **`limit=1000` on sidebets costs nothing.** It changes payload size, not
  request count — still one GET. The 2026-09-18 truncation fix was free.
- **The 330 non-near-term events discarded per pass** cost no extra requests;
  they arrive inside category responses already paid for.
- **The in-play collector is not in these numbers** — it is parked. It was adding
  a further ~30k/day when the flag landed.

---

## 2. API-FOOTBALL — 9% of quota. Not a constraint.

> **⚠️ `api_budget_log` is a FLOOR, not a true count.** `api_football._get` makes
> up to `AF_MAX_ATTEMPTS=3` attempts, and **retried attempts are not recorded**.
> Every AF figure below undercounts by however often we retry. The conclusion
> (comfortably inside quota) survives a 3× worst case; the precision does not.

| date | calls |
|---|---|
| 2026-09-15 | 10,636 |
| 2026-09-16 | 12,686 |
| 2026-09-17 | 13,076 |
| 2026-09-18 | 13,548 |
| peak (09-13) | 25,173 |

Against a **150,000/day** quota. Even the peak is 17%.

**So for AF the question is not "how do we make fewer requests".** Nothing is at
risk and nothing is being rationed. The useful question is what we fetch and then
never read — a storage and clarity problem, not a quota one.

### Breakdown (2026-09-18, the last day with good attribution)

| endpoint | calls | share |
|---|---|---|
| odds | 3,229 | 33.5% |
| fixtures | 2,754 | 28.6% |
| fixtures/lineups | 1,312 | 13.6% |
| fixtures/statistics | 688 | 7.1% |
| predictions | 423 | 4.4% |
| fixtures/headtohead | 344 | 3.6% |
| standings | 340 | 3.5% |
| fixtures/events | 194 | 2.0% |
| transfers | 132 | 1.4% |
| teams/statistics | 94 | 1.0% |
| coachs | 84 | 0.9% |
| status / players / sidelined / injuries | 46 | 0.5% |

### Fetched twice — both found by a code inventory, neither visible at the call site

1. **The full day-odds sweep ran TWICE at 04:00 UTC.** `job_odds_refresh` is
   registered for every hour × {00,30}, and 04:00 is also `morning_pipeline`'s
   slot, whose step 4/7 runs `run_odds(today)` — the same ~56–77-page paginated
   sweep, in the same minute. A comment in `scheduler.py` had claimed since
   OPENING-LINE-MOVE-CAPTURE that the redundant 02:00 + 04:00 slots were
   *"removed"*; the loop went on re-adding them because its only skip was 20:00.
   **A comment is not a control.** Fixed: the skip is now in the loop. 02:00 is
   deliberately kept — the later WC-OVERNIGHT-COVERAGE decision made this refresh
   24/7 on purpose and 02:00 collides with nothing.

2. **`/odds/live` was fetched twice per in-play cycle** — `af_state()` (scores and
   clock) and `af_live_prices()` (control-arm prices) each called it, so every
   45 s cycle made two identical GETs: **~3,840 AF calls/day where 1,920 would
   do**. Fixed: the collector fetches once and passes the payload to both;
   verified to produce byte-identical output with one call instead of two.

### Fetched and never read

1. **`transfers` — 132 calls/day for a table with no reader.** `team_transfers`
   holds **1,437,485 rows / 882 MB** and was written as recently as today 04:50.
   Every reference in the engine is the writer (`fetch_enrichment.py`,
   `scripts/backfill_transfers.py`) or a smoke test *about the writer's
   efficiency*. Nothing in `workers/model/`, nothing in the web repo. The one
   "transfer" hit in the model is an unrelated `impact_type` string.

2. **`injuries` — calls made, `injuries` table holds 0 rows, all time.**

3. **Live `fixtures/statistics` returns nothing usable.** Over the last 7 days
   `live_match_snapshots` took **477,343 rows** with **0.00% `shots_on_target`
   and 0.00% `xg`**. A live probe of 6 in-play fixtures returned `raw_teams=0`
   for five and xG for none — AF does not serve statistics for the lower-tier
   slate that makes up most of our in-play board. **The post-match path is fine
   and must not be confused with this**: `match_player_stats` took 22,638 rows in
   the same 7 days and is read by `settlement.py`.

`live_match_snapshots` is **620 MB / 2,385,378 rows** whose state columns are
empty; its minute/score skeleton is the only part that works.

---

## 3. What to do, in order

1. **Tier the Coolbet poll by time-to-kickoff** — ~49% fewer requests, no
   meaningful information lost. This is the one that addresses the outage.
2. **Stop fetching `transfers`** — 132 calls/day and 882 MB for zero readers.
   Decide whether to drop the table or keep it as history.
3. **Stop the live `fixtures/statistics` fetch** (not the post-match one) — it
   has produced literally nothing for 7 days.
4. **A request budget that throttles itself.** The footprint control today is
   `daemons_paused`, a manual switch. A switch nobody is watching is not a
   control, and it is what failed here.

**Explicitly not recommended: more parallel sweepers.** Imperva gates on the IP
(proven: same VPS, same Linux Chromium, residential-EE tunnel → 170,237 bytes of
real `fo-tree`; the datacenter IP never solves the challenge). More readers
behind one IP is more volume from a flagged address. Multiple sweepers are only
the answer once each has its **own egress** — see `dev/active/VPS_FEATURE_MATRIX.md`.
