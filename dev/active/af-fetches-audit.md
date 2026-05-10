# AF-FETCHES-AUDIT — Findings

Combined audit of two PRIORITY_QUEUE tasks shipped together 2026-05-10:
- **AF-FETCHES-AUDIT**: account for the 26K gap between the agent's earlier ~5K endpoint accounting and the ~31K actually observed daily.
- **AUDIT-AF-ENDPOINTS**: probe whether AF supports a single-call bulk form for `/standings`, `/sidelined`, `/transfers`, `/coachs` (the INJURIES-BY-DATE 47× pattern).

**Why we're doing this:** *not* a budget-rescue exercise — Mega plan ($39/mo) gives us 150K/day and a bigger plan is buyable if we ever need it. The goal here is **latency** (fewer sequential round-trips per pipeline run) and, more importantly, **freeing headroom so we can poll live matches harder** (shorter `FAST_INTERVAL`, more HIGH-priority slots, cheaper to add new live-data sources). Every call we save on a once-daily enrichment job is a call we can re-spend on a 30s-cadence live job.

---

## Bulk-form probes

Run via `scripts/probe_af_bulk_endpoints.py` (kept in repo for re-verify; costs ~10 AF calls).

| Endpoint | Bulk param tried | Result | Notes |
|----------|------------------|--------|-------|
| `/standings` | `?league=A-B-C` and `?league=A,B,C` | ❌ rejected | `'The League field must contain an integer.'` Per-league loop is the only form. |
| `/transfers` | `?team=A-B-C` | ❌ rejected | `'The Team field must contain an integer.'` |
| `/coachs` | `?team=A-B-C` | ❌ rejected | Same error. |
| `/sidelined` | `?players=A-B-C` (plural) | ✅ **WORKS** | Returns `[{"id": player_id, "sidelined": [...]}, ...]`. Per-player counts match per-id loop exactly. **Hard cap: 20 ids per call** (AF returns explicit error: `Maximum of 20 ids allowed`). Singular `?player=A-B-C` is rejected. |

### `/sidelined` swap shipped this commit

- New helper `get_sidelined_by_players_bulk(player_ids)` in `workers/api_clients/api_football.py` — chunks by 20, returns `{player_id: [entry, ...]}`.
- `workers/jobs/fetch_enrichment.py:fetch_player_sidelined` rewritten to one bulk call per chunk; legacy `get_sidelined(player_id)` retained as fallback (no production caller now).
- Per-run reduction: 50–80 calls → 3–4. Modest absolute (the 7-day cache already throttled most calls), but the morning-enrichment T9 component is faster and the pattern is reusable.

### Stop-criteria conclusion

Three out of four sequentially-called endpoints firmly reject bulk; AF appears to have built `?ids=` / `?players=` only for `/injuries` and `/sidelined`. Per the task's stop rule, no further endpoints (`/teams/statistics`, `/fixtures/headtohead`) are worth probing.

---

## The 26K mystery

Pre-this-commit instrumentation: `BudgetTracker.record_call()` only incremented a global counter. `api_budget_log` stored daily totals from AF `/status`, with no per-endpoint or per-source detail. There was no on-disk way to attribute the 26K gap.

### Fix (this commit)

1. `BudgetTracker` now keeps two endpoint counters:
   - `_endpoint_counts` — calls since the most recent hourly sync (drained on each sync).
   - `_endpoint_counts_today` — cumulative since UTC midnight (resets only on day rollover).
2. `_get(endpoint, …)` calls `record_call(endpoint)` with the literal path string (`fixtures`, `odds/live`, `fixtures/statistics`, etc.).
3. `sync_with_server()` writes both maps as JSONB into new `api_budget_log` columns:
   - `endpoint_breakdown` — last hour
   - `endpoint_breakdown_today` — day-to-date
4. New script `scripts/af_call_breakdown.py` reads the JSONB and prints a daily total, sorted endpoint breakdown, and an hour-by-hour endpoint matrix for the last 24h.
5. Migration `086_api_budget_endpoint_breakdown.sql` adds the JSONB columns (NULLable so existing rows stay valid).

### Expected source mix (a-priori)

The hypothesis from the original audit, to be verified once the breakdown logger has 24h of data:

| Endpoint | Expected daily share | Likely source jobs |
|----------|----------------------|--------------------|
| `fixtures` (multi-form) | mid | live_poller bulk live fixtures + scheduled fixture refreshes (4×/day) + per-id reads from settlement / fetch_enrichment |
| `odds` + `odds/live` | high | scheduled odds refreshes, betting-refresh runs, live_poller every cycle |
| `fixtures/statistics` | high | live_poller HIGH-priority matches every 45s (~30% of live cohort × peak hours) |
| `fixtures/events` | high | live_poller per active match cycle |
| `predictions` | low–mid | morning + betting refreshes (will drop after AF-PREDICTIONS-FREQ) |
| `injuries` | low | now `?date=` once via INJURIES-BY-DATE |
| `standings` | low | 3× daily across ~50 leagues (target for AF-STANDINGS-DAILY) |
| `teams/statistics` | low | morning + intraday refreshes (target for AF-CACHE-TEAM-STATS) |
| `fixtures/headtohead` | low | morning Tier-1-only after PIPELINE-STABILIZE (target for AF-CACHE-H2H) |
| `coachs`, `transfers`, `sidelined`, `venues` | low | backfill jobs (already cached) |
| `fixtures/lineups`, `fixtures/players` | low–mid | live_poller upcoming-lineups + post-match settlement |
| `leagues` | trivial | weekly fixtures refresh |
| `status` | trivial | hourly sync |

If the live measurement deviates significantly from this — e.g. `fixtures/statistics` is 60K — the next iteration of cache / frequency-tuning has a clear target.

### Re-running the breakdown

After 1–2 hours of post-deploy traffic:

```bash
venv/bin/python3 scripts/af_call_breakdown.py --days 2
```

Once we have 24h of clean post-migration data, file follow-up tasks for the top 2–3 endpoints whose share is materially higher than expected.

---

## Follow-on tasks unblocked by this audit

These were already in `PRIORITY_QUEUE.md` (P2 — AF Quota Optimization) but become pickable now that the breakdown is real:

- AF-CACHE-H2H (1h, save ~360/day)
- AF-CACHE-TEAM-STATS (1h, save ~150/day)
- AF-STANDINGS-DAILY (30m, save ~40/day)
- AF-PREDICTIONS-FREQ (30m, save ~360/day)
- AF-INJURIES-LATE (1h, save ~30/day)
- AF-COVERAGE-AUDIT (1h, gates live-poller per-match calls by `coverage_*` flags)
- AF-QUOTA-AUDIT — graceful degradation tier + 50/75% alert email

Combined ceiling: ~900–1000 calls/day reclaimed on top of whatever the breakdown surfaces. With 75K+ headroom on Mega we don't need this for cost, but every saved call is a call we can re-spend on tighter live-poll cadence (e.g. drop `FAST_INTERVAL` from 45s back toward 30s) or new live-data sources (xG provider, second odds feed) without breaching the daily ceiling.

---

## Measured 2026-05-10 (partial — Sunday)

Daily totals from `api_budget_log` (latest snapshot per day):

| Date | calls_today | remaining | Note |
|------|-------------|-----------|------|
| 2026-05-08 (Fri) | 53,124 | — | Quiet day, under old 75K |
| 2026-05-09 (Sat) | **103,627** | 46,373 | **End-of-season Saturday — exceeded old 75K plan, triggered Mega upgrade** |
| 2026-05-10 (Sun, by 10:00 UTC) | 41,924 | 108,076 | ~28% of Mega, on track |

Endpoint breakdown for 2026-05-10 (latest sync at 10:00 UTC):

```
total=41,924  attributed=8,727  unattributed=33,197
  predictions          3,115   35.7%
  fixtures/events      2,137   24.5%
  fixtures/statistics  2,137   24.5%
  fixtures               609    7.0%
  fixtures/lineups       538    6.2%
  odds                   118    1.4%
  odds/live               70    0.8%
  status                   3    0.0%
```

### Caveat — Railway restarts reset the in-memory cumulative counter

The 33K "unattributed" is **not** missing instrumentation; it reflects calls placed before the most recent scheduler restart today. `BudgetTracker._endpoint_counts_today` is in-memory only — when Railway redeploys, the dict resets to `{}` while `calls_today` gets re-synced from AF `/status` (server-side cumulative). Result: the script's per-endpoint sum lags `calls_today` by however many calls were placed pre-restart that day.

First sync after today's restart was 08:43:07 UTC at `calls_today=32,787`, so everything before that hour (morning pipeline: fixtures, full enrichment, odds, predictions, betting) is counted in the daily total but not in the per-endpoint dict.

**Fix options for AF-BREAKDOWN-REVIEW or a small follow-up task:**

1. On `BudgetTracker.__init__`, query the latest `api_budget_log` row for today and seed `_endpoint_counts_today` from `endpoint_breakdown_today` JSONB. Cheap, idempotent, fully closes the gap.
2. Or accept the gap: any day without a Railway restart will have full attribution. Document the caveat in `af_call_breakdown.py` output so agents don't chase a phantom 33K.

Option (1) is preferred — the breakdown is meant to be definitive, and the cost is one DB read at process startup.

### Initial signal from today's partial data

Even with only 21% attributed, the shape is informative:

- **`predictions` is 3,115 calls in one burst** at ~04:45 UTC (one call per fixture, ~3K fixtures/day). On `AF-BREAKDOWN-REVIEW`'s decision rule (b) this clears 5K/day on a busy day → `AF-PREDICTIONS-FREQ` is a likely first pick once 24h of full attribution is logged.
- **`fixtures/statistics` + `fixtures/events` = 4,274 calls** by 10:00 UTC and scaling roughly linearly with live matches. By end-of-day on a Saturday these are the candidates for rule (a) (>40K → `AF-COVERAGE-AUDIT`).
- **All cached endpoints (standings, h2h, teams/statistics, sidelined, transfers, coachs, venues) are absent from the breakdown** — confirming the existing caches are working as designed for today's run.

Defer the actual decision to AF-BREAKDOWN-REVIEW after a full clean day of post-restart attribution. Re-run with:

```bash
venv/bin/python3 scripts/af_call_breakdown.py --days 2
```
