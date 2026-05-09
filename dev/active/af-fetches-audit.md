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
