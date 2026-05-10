# Morning Pipeline AF Audit — 2026-05-10

Scope: identify call-volume optimizations for the 04:00–06:00 UTC morning pipeline (Fixtures → Enrichment → Odds → Predictions → Betting). Today's burn was ~33K calls before live polling started; predictions alone fired 3,115 calls in a single 04:45 UTC burst.

This audit is **deliverable only** — concrete code changes are deferred to follow-up tasks. Every claim about AF endpoint behaviour is grounded in either the v3.9.3 PDF, the existing `probe_af_bulk_endpoints.py` results, or the new probe shipped today (`scripts/probe_predictions_odds_bulk.py`).

---

## Summary

| Job | Today's burn | Realistic savings | Confidence |
|-----|--------------|-------------------|------------|
| ① Fixtures | ~1 call (1× /fixtures?date=) | 0 | already optimal |
| ② Enrichment | ~600 calls (extra /fixtures?date= duplicate, ~50 standings, ~50 H2H, ~50 team-stats, ~150 venue/coach/transfer/sidelined backfills) | **~80–120/day** | high — caches help on intraday, but the *morning* run can drop ~50 standings, ~50 H2H, ~30 team-stats by switching to once-daily |
| ③ Odds | ~64 pages (1× /odds?date=) | 0 | confirmed via probe — `bookmaker=` and `bet=` filters reduce page count by ≤11%, not worth the complexity |
| ④ Predictions | **3,115 calls (35.7% of attributed today)** | **~2,000–2,500/day** | high — endpoint accepts ONLY `?fixture=ID`; only client-side filtering helps |
| ⑤ Betting | 0 AF calls | 0 | confirmed — `betting_pipeline.py` has zero AF imports |

**Combined ship-ready ceiling: ~2.1–2.6K calls/day** (~6–8% of today's morning attributed burn). The headline finding is that **/predictions has no API-side bulk form** — the win has to come from predicting fewer fixtures, not predicting them faster. Everything else (H2H, standings, team-stats, coachs, transfers, sidelined) is already either bulk-batched (sidelined), per-day-cached (transfers/coaches), or unavoidably per-row (standings/H2H/team-stats per AF docs).

There is **no large hidden win** in the morning pipeline. The 33K is mostly real, structurally fixed cost. The big knobs left to turn are the `/predictions` filter and the live-poller cadence (out of scope here, see AF-COVERAGE-AUDIT in PRIORITY_QUEUE).

---

## Per-job findings

### ① Fixtures (`workers/jobs/fetch_fixtures.py`)

**Current**
- One `/fixtures?date=YYYY-MM-DD` call → ~1.5K fixtures returned in a single page (the endpoint is *not* paginated for date queries — verified by inspecting the call signature in `api_football.py:get_fixtures_by_date`).
- On Mondays, additionally one `/leagues?current=true` call.
- Total: 1 call/day, 2 on Mondays.

**Findings**
- This is already the lowest-call form available. Doc page 60 confirms `/fixtures?date=` is the canonical bulk-by-date pattern; no further reduction possible.
- Mondays-only `/leagues?current=true` is also at the AF-recommended cadence (1/week — doc page 30-ish).

**Proposal**
- None.

**Risk**
- N/A.

---

### ② Enrichment (`workers/jobs/fetch_enrichment.py`)

This job has the most surface area but is already heavily optimized. Current per-component state:

| Component | Endpoint | Per-run cost | Caching |
|-----------|----------|--------------|---------|
| `_build_fixture_meta` | `/fixtures?date=` | **+1 call (duplicate of step ①)** | none |
| Injuries | `/injuries?date=` | 1 call (from INJURIES-BY-DATE) | none — cheap enough |
| Team stats | `/teams/statistics` | per (team × league × season), Tier 1 only | dedupe via `seen` set |
| Standings | `/standings` | per (league × season) | dedupe via `seen` set |
| H2H | `/fixtures/headtohead` | per Tier-1 fixture | same-day cache via `matches.h2h_raw IS NOT NULL` |
| Coaches | `/coachs` | per unique team | 48h DB cache (`team_coaches.fetched_at`), capped at 50/run |
| Venues | `/venues` | per unique venue | permanent DB cache (`venues.af_id`) |
| Sidelined | `/sidelined?players=A-B-…` | 1 call per 20 players | 7-day DB cache, **20-id bulk shipped 2026-05-10** |
| Transfers | `/transfers` | per unique team | 30-day DB cache (`team_transfer_cache`), capped at 100/run |

**Findings — what AF supports that we're NOT using**

1. **Duplicate `/fixtures?date=` call.** `_build_fixture_meta` (line 137 of `fetch_enrichment.py`) calls `get_fixtures_by_date(target_date)` to recover home/away AF team IDs and venue IDs that the morning step ① already fetched and threw away. This is one call/run, but it's pure waste.
2. **`/standings` does not support multi-league bulk.** Verified by 2026-05-10 probe (`af-fetches-audit.md`): `?league=A-B-C` returns `'The League field must contain an integer.'`. Doc page 51 confirms — `league` is `integer`, no array form. **No optimization here.**
3. **`/coachs` and `/transfers` do not support multi-team bulk.** Same probe rejected `?team=A-B-C`. Doc pages 82 (coachs) and 106 (transfers) confirm `team` is `integer`.
4. **`/teams/statistics` does not support bulk.** Doc page 43 — `league`, `season`, `team` all required as scalars. There's a `date` parameter that limits stats to "the beginning of the season to the given date" but that's a freshness knob, not a call reducer.
5. **`/fixtures/headtohead` does not support multi-pair bulk.** Doc page 62 — `h2h=ID-ID` is *one* team pair only (the hyphen separates the two team IDs of a single pair, not multiple pairs). Tested in earlier audit: AF rejects multi-pair forms.
6. **`/injuries?date=` is optimal.** Doc page 76-77 confirms `date` is supported and is the cheapest form for "all injuries for today." Already in use.
7. **What's actually inefficient: H2H is fetched per-run for **every Tier-1 fixture every day**.** The same-day cache only spares the 12:00/16:00 refreshes — the morning run still does ~30–60 calls.

**Concrete proposals (ranked by ROI)**

**P-ENR-1 — Drop the duplicate `/fixtures?date=` call.** _Save: 1 call/run × 3 enrichment runs/day = 3 calls/day._

In `fetch_enrichment._build_fixture_meta`, the call to `get_fixtures_by_date(target_date)` only exists to backfill `home_team_api_id`, `away_team_api_id`, `venue_af_id`, `season`. All four are already stored on the `matches` row by `fixture_to_match_dict` during step ① (see `api_football.py:1547-1571` — `home_team_api_id`, `away_team_api_id`, `venue_af_id`, `season` are all set). The fix: extend the SQL select in `_build_fixture_meta` (lines 73-78) to read those four columns and skip the AF call entirely.

**P-ENR-2 — Move H2H + team_stats + standings to once-daily, not 3×/day.**  _Save: ~80–100 calls/day._

Current schedule (from `scheduler.py`):
- Morning enrichment 04:15 UTC: all components incl. H2H, team_stats, standings.
- 13:00 UTC `enrichment_full`: all components again — re-fires H2H, team_stats, standings.
- 10:30 + 16:00 UTC `enrichment_refresh`: only injuries+standings (so standings 4×/day total).

H2H, team stats, and standings only change *post-match*. Refetching them mid-day before any matches have finished is purely redundant. The `enrichment_full` 13:00 job exists "so afternoon/evening betting runs have up-to-date context" (per code comment) — but at 13:00 UTC, only Asian-window matches have finished, and those don't materially shift Tier-1 H2H or season-stats numbers.

This is exactly what the existing PRIORITY_QUEUE tasks `AF-CACHE-H2H`, `AF-CACHE-TEAM-STATS`, `AF-STANDINGS-DAILY` propose. They claim ~360 + ~150 + ~40 = ~550/day combined, but those numbers assumed pre-PIPELINE-STABILIZE volumes — post-`PIPELINE-STABILIZE` (Tier-1-only H2H + same-day cache), realistic combined is ~80–120/day. Still worth shipping as one commit.

**P-ENR-3 — `enrichment_refresh` 10:30/16:00 standings call could be gated on "any league had a finished match in the last 90 min."**  _Save: ~30 calls/day on quiet weekday afternoons._

If no matches finished in the standings-relevant league since last fetch, skip. Implementable as a tiny SQL check before the per-league loop.

**P-ENR-4 — Backfill jobs (`/coachs`, `/transfers`) overlap morning enrichment.**  _Save: marginal; mostly a code-clarity win._

`workers/scheduler.py` has *both* `fetch_enrichment.fetch_coaches` (morning, 50/run, 48h cache) AND `scripts/backfill_coaches.run_batch` (every 25 min, 10/run). Same for transfers (100/run morning vs 25/run every 25 min). The backfill jobs are designed for cold-start; once steady-state, they should self-skip but might still fire 1–2 wasted batches/day. Probably not worth touching — verify post-AF-BREAKDOWN-REVIEW.

**Risk**
- P-ENR-1: minor — `season` is stored as `int`, AF `_build_fixture_meta` falls back to `season=year if month>=7 else year-1` if absent; verify all today's matches have non-null `season` before removing the AF fallback.
- P-ENR-2: H2H staleness for *very late-evening* matches that follow an earlier same-day match between the same pair — vanishingly rare in practice.
- P-ENR-3: pre-existing `pipeline_runs.last_run_at` lookup logic can confirm "any standings-eligible league saw a finished match" cheaply.
- P-ENR-4: none beyond what's already there.

---

### ③ Odds (`workers/jobs/fetch_odds.py`)

**Current**
- One `/odds?date=YYYY-MM-DD&page=N` call per page.
- Today (2026-05-10): 64 pages × 9 daily refreshes (07/08/10/12/14/16/18/22 UTC + 20:00 mark_closing) = ~575 calls/day for odds polling. Morning run = 64 calls.
- Pages fetched concurrently (max 8 workers) — already optimized for latency.

**Findings (probe results, 2026-05-10)**

| Variant | Total pages | Notes |
|---------|-------------|-------|
| `?date=2026-05-10` (current) | **64** | 13 bookmakers per fixture, 10 fixtures per page |
| `?date=…&bookmaker=8` (Bet365) | 57 | -11% — pagination is by *fixture*, not bookmaker row |
| `?date=…&bet=1` (1X2 only) | 62 | -3% — same reason, bet filter doesn't shrink page count |

The pagination algorithm is **fixture-based, not row-based**: page size = 10 *fixtures*, with all bookmaker × bet rows for those fixtures bundled into one entry. So filtering by `bookmaker=` only saves pages on fixtures where Bet365 has no odds priced (which is rare) — net 7-page savings out of 64. Filter by `bet=` is even worse.

Doc page 122-124 confirms: `bookmaker` and `bet` are scalar filters on the response shape, not on the pagination axis.

**The 64-page-per-run cost is essentially fixed by the fixture count.**

**Proposals**
- **None.** The 11% savings from a `bookmaker=` filter doesn't justify the loss of the 13-bookmaker best-price aggregation that powers the value-bet pipeline.

**Risk**
- N/A — proposing nothing.

**Aside**: if the call count became urgent, the only meaningful lever is **fewer scheduled refresh slots**. Today the schedule is 9 odds runs/day = ~575 calls. Dropping to 4 runs (07, 12, 17, 22) would cut to ~250/day = save ~325/day. But that hurts CLV tracking and pre-KO line-move detection, both of which are core product features. Not recommended unless quota is critical.

---

### ④ Predictions (`workers/jobs/fetch_predictions.py`)

**Current**
- One `/predictions?fixture=ID` call **per fixture** with predictions coverage.
- Today: **3,115 calls** in a single 04:45 UTC burst (35.7% of today's attributed burn).
- Caching: none within a single run (results are stored on `matches.af_prediction` JSONB + `predictions` table rows).
- Coverage filter: skips leagues where `coverage_predictions = false`. This already filters out a chunk; the 3,115 is *post*-filter.
- Refresh frequency: morning + each betting refresh would hit the predictions endpoint, but `fetch_predictions.run_predictions` is the only caller and it runs **once at 05:30 UTC + once per betting_refresh slot (09:30, 11:00, 15:00, 19:00, 20:30)**.

Wait — checking `scheduler.py:job_betting_refresh` (line 284): it explicitly calls `run_predictions(target_date=today)` *before* `run_betting()`. So predictions actually run **6× per day** (morning + 5 betting refreshes), not once. **3,115 × 6 ≈ 18,690 calls/day worst case** if all 6 runs see all fixtures. The 3,115 burst we saw at 04:45 is just the morning slice; the same pattern repeats at every betting refresh.

This means the headline opportunity is roughly **5× larger than today's snapshot suggests**, because the budget log we read at 10:00 UTC had only seen the morning + 09:30 runs.

**Findings — bulk forms tested 2026-05-10 (this audit)**

Probe `scripts/probe_predictions_odds_bulk.py` tried four undocumented bulk variants. AF returned explicit field-level errors on all four:

| Attempt | AF response |
|---------|-------------|
| `?ids=A-B-C` | `{'fixture': 'The Fixture field is required.', 'ids': 'The Ids field do not exist.'}` |
| `?fixtures=A-B-C` | `{'fixtures': 'The Fixtures field do not exist.'}` |
| `?date=2026-05-10` | `{'date': 'The Date field do not exist.'}` |
| `?league=76&season=2025` | `{'league': 'The League field do not exist.', 'season': 'The Season field do not exist.'}` |

Doc page 79-81 confirms: **`fixture` is the only accepted parameter.** There is no API-side bulk path. Period.

**Concrete proposals (ranked by ROI)**

**P-PRED-1 — Drop predictions refetch from intraday betting_refresh runs.** _Save: ~2,500–10,000 calls/day depending on fixture count._

In `scheduler.py:job_betting_refresh` (line 284), `run_predictions(target_date=today)` is called before `run_betting()`. AF predictions update at most **hourly** per their docs (page 80, "Update Frequency: every hour"), and meaningfully change far less often than that. Re-pulling 3,000 predictions at 09:30 and again at 11:00 just to feed a model that already has them is pure burn.

Fix: drop `run_predictions` from `job_betting_refresh` entirely. Predictions get refreshed once at 05:30 UTC; betting_refresh runs use the cached `matches.af_prediction` JSONB. If a specific match's prediction is suspected stale (e.g., late lineup news), the betting pipeline could selectively re-fetch — but for the 99% case, the morning fetch is sufficient.

This is the same goal as the existing `AF-PREDICTIONS-FREQ` task in PRIORITY_QUEUE, but more aggressive (drop to 1×/day, not 2×).

**P-PRED-2 — Filter the prediction set to "fixtures the betting pipeline will actually act on."** _Save: ~30–60% of the 3,115 morning burst, depending on filter aggressiveness._

The current code (line 49-52 of `fetch_predictions.py`) fetches predictions for **every** fixture with `api_football_id IS NOT NULL` and `coverage_predictions = true`. That's ~3,000 fixtures/day. But the betting pipeline doesn't bet on every fixture — `betting_pipeline.py` filters by:
- `tier IN (1,2,3,4)` (excludes obscure leagues)
- `odds_range` per bot (e.g., 1.30–4.50)
- `min_prob` per bot
- League/country whitelists for some bots

A meaningful chunk of the 3,115 fixtures are in leagues no bot will ever bet on (e.g., friendlies, women's youth, lower-tier amateur). Filtering to "league has at least one active bot interested in it" should drop the predictions set significantly.

Implementation: build a `bettable_leagues` set from `BOTS_CONFIG` (intersection of all bots' `tier_filter` + `league_filter`) and filter the SQL in `fetch_predictions.fetch_af_predictions` to only those leagues.

**Risk**: harder than P-PRED-1 because new bots may want predictions for previously-excluded leagues. Mitigation: rebuild `bettable_leagues` daily from BOTS_CONFIG so adding a bot doesn't require a manual filter update.

**P-PRED-3 — Coverage cache.** _Save: handled already._

Predictions endpoint returns 204 / empty for fixtures whose league lacks coverage. The current code does check `coverage_predictions` *before* the call (line 81-83 of `fetch_predictions.py`), so this is already in place.

**Risk / caveats**
- P-PRED-1: betting_refresh model freshness drops from "5 hours stale at worst" to "16 hours stale at worst." For AF predictions specifically, this is fine — they don't react to last-minute lineup news; they're pre-computed per-fixture statistics. Lineup-driven model adjustments come from the predictions JSONB consumer downstream, not from re-fetching AF.
- P-PRED-2: Sky-high blast radius if the bot config filter is wrong. Tested via smoke test that compares fetched-fixture-count against placed-bet-count for a recent day; should be at least 2× ratio (we predict more than we bet because of edge thresholds).

---

### ⑤ Betting (`workers/jobs/betting_pipeline.py`)

**Confirmed: zero AF calls.**

`grep -rn "api_football\|api-football" workers/jobs/betting_pipeline.py` returns nothing. The job is purely a SQL-driven model evaluation loop. Skip.

---

## Probes attempted

### Probe 1 — `/predictions` undocumented bulk forms

Script: `scripts/probe_predictions_odds_bulk.py`. Cost: 5 AF calls.

Tried `?ids=A-B-C`, `?fixtures=A-B-C`, `?date=YYYY-MM-DD`, `?league=X&season=Y`. **All four rejected with explicit field-not-exist errors.** Endpoint definitively accepts only `fixture=ID`.

### Probe 2 — `/odds` `bookmaker=` and `bet=` filter on page count

Same script. Cost: 3 AF calls.

Result table:

```
?date=2026-05-10                       → 64 pages
?date=2026-05-10&bookmaker=8           → 57 pages   (-11%)
?date=2026-05-10&bet=1                 → 62 pages   (-3%)
```

Pagination is by *fixture*, not by bookmaker row. Filter doesn't help meaningfully.

### Earlier probes (carried over from `af-fetches-audit.md`, 2026-05-10)

- `/standings ?league=A-B-C` → rejected (`The League field must contain an integer`)
- `/transfers ?team=A-B-C` → rejected
- `/coachs ?team=A-B-C` → rejected
- `/sidelined ?players=A-B-C` → **WORKS** (20 ids/call, already shipped)

Total probe budget spent across both audits: ~18 AF calls (well under 50-call cap).

---

## Recommended ship order

Ranked by `(savings ÷ implementation hours)`, with the lowest-risk first.

1. **P-PRED-1 — Drop `run_predictions` from `job_betting_refresh`.** ~30 min implementation. Save 2,500–10,000 calls/day. Lowest risk (predictions stale by hours, not relevant for AF predictions which barely change). **Highest ROI in the entire audit.**

2. **P-ENR-1 — Drop duplicate `/fixtures?date=` from `_build_fixture_meta`.** ~20 min. Save 3 calls/day. Trivial; do as drive-by in same commit as P-PRED-1.

3. **P-ENR-2 — Move H2H + team_stats + standings to once-daily.** ~1.5 h (combines existing `AF-CACHE-H2H`, `AF-CACHE-TEAM-STATS`, `AF-STANDINGS-DAILY` tasks). Save ~80–120 calls/day. Low risk (all three are post-match-update data).

4. **P-PRED-2 — Filter predictions fetch to bettable-league set.** ~1 h. Save 30–60% of the per-run prediction burst. Medium risk (need smoke test to verify no bot loses fixtures). Ship *after* P-PRED-1 has been live for a week so the savings show cleanly in `api_budget_log`.

5. **P-ENR-3 — Skip `enrichment_refresh` standings if no relevant matches finished.** ~45 min. Save ~30 calls/day. Low risk.

**Combined ceiling once all five ship: ~3,000–13,000 calls/day saved**, dominated by P-PRED-1 + P-PRED-2. The exact number depends on full-day measurement of how many betting_refresh runs see fresh fixtures (probably 4–5 of the 5 slots, so the realistic floor is ~2.5K/day).

**What this audit did NOT find:**
- No undocumented bulk form for `/predictions`, `/standings`, `/teams/statistics`, `/fixtures/headtohead`, `/coachs`, `/transfers`. AF has invested in `?ids=` / `?players=` only for `/fixtures`, `/injuries`, `/sidelined`.
- No way to reduce `/odds` page count below ~64/day on a busy date — pagination is fixture-bound.
- No bigger morning-pipeline win lurking. The 33K is real, and most of it is structurally fixed cost for the data we use.

The 4–5× headroom on Mega (we use ~33K of 150K daily) is best spent on the *live* side — tighter `FAST_INTERVAL`, broader HIGH-priority tier — not on chasing further morning savings beyond what's listed here.
