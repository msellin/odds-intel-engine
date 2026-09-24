# Handover — Telegram alert triage, 2026-09-11

Context for a fresh session. Self-contained: you do not need the originating
conversation. Three Telegram alerts were triaged against the live DB, the VPS
journal and CI. Two were real, one was already fixed. Two extra problems
surfaced that the alerts did not mention.

State captured at **18:46 UTC 2026-09-11**. Re-verify before acting — the
Coolbet and Epicbet numbers move.

---

## CLOSED — do not redo

| Item | Why closed |
|---|---|
| **`1x2_1h` unsettleable alert** | Stale alert. Resolver `_r_1x2_1h` AND the `void_ungradeable_1h_bets` sweep both shipped 2026-09-11 in `e38f38e`; VPS is on current HEAD. Proof it works: 15 won / 10 lost already graded. Of 58 pending, 57 are live/scheduled (correctly pending). |
| **ODDS-FRESHNESS-DEDUP-COLUMN** | Fixed + pushed, `052e8d3`. `odds_freshness._set_dedup_row` wrote `last_reason`; migration 258 created `last_alert_reason` and no migration ever created `last_reason`. Every call raised UndefinedColumn, swallowed by the broad `except`, so the DB dedup layer was dead from day one (`pipeline_health_state` held zero `epicbet_odds` rows). Smoke `ODDS-FRESHNESS-DEDUP-COLUMN`. Ledger pattern 13. |
| **SYSTEM-MAP-REGISTRY-NOT-DRIFTED (CI red)** | Closed by a parallel session while this triage ran. Migration 331 had created the 4 merged trigger bots in the DB with `bot_registry.py` still listing only the old 8. Registry now lists them; the drift test passes locally. |

**Do NOT treat the 12 active trigger bots as a bug.** Migration 331 documents
the deliberate temporary 16 → 18 overlap: the 8 per-book bots keep running until
`scripts/trigger_calibrator_check.py` returns its verdict, because retiring them
now would make the comparison span a bot change *and* a calibrator change.

---

## OPEN 1 — EPICBET-FS-500: Epicbet feed dead ~3h (P1 — 👥 PICKS)

**Status: still down.** Last Epicbet row **15:46 UTC**, age **179 min**, while
every other book wrote at 18:25.

```
18:32 failed | 500 Server Error: Internal Server Error for url: http://localhost:8191/v1
18:02 failed | 500 Server Error ... (same)
17:32 failed | killed — scheduler restarted   <- the 16:02-17:32 runs died on deploys, not FS
```

**Diagnosis.** `oi_local_flaresolverr` reports `Up 6 weeks (healthy)` and
`sessions.list` returns `ok` with exactly one session (`coolbet_prod`) — so the
container is alive but failing on **new session/tab creation**. It sits at
**707 MiB of a 1 GiB limit**, so tab-creation OOM is the leading candidate. This
is the known per-session Chrome-tab crash pattern (`docs/RELIABILITY_LEDGER.md`
§1 "we blame the loud thing").

**Why nothing was done.** The normal remedy is destroying the crashed session,
but the only session present is `coolbet_prod`, and an FS restart kills it while
the Coolbet placer is **already locked out** (OPEN 2). That trades a paper-price
feed for the real-money session — the owner's call, not an implementation detail.

**Two options, (b) is the durable one:**
- **(a)** raise the FS container memory above 1 GiB, restart FS *after* Coolbet
  is re-logged in.
- **(b)** give Epicbet its own FS session name so its crashes clear without
  touching `coolbet_prod`.

**Why it matters for PICKS:** on the O/U mirror's own gate (O/U 2.5, odds ≥1.80)
Epicbet holds the best price on 65% of 1,429 series (see BOOK-PRICE-DIMENSIONS in
`PRIORITY_QUEUE.md`). A dead feed silently narrows line-shopping to Coolbet.

Check:
```bash
ssh root@204.168.199.8 'curl -s -m 15 -X POST http://localhost:8191/v1 \
  -H "Content-Type: application/json" -d "{\"cmd\":\"sessions.list\"}"; \
  docker stats --no-stream --format "{{.Name}} {{.MemUsage}}" | grep -i flare'
```

---

## OPEN 2 — COOLBET-IMPERVA-COOKIES-STALE: placer locked out (P1 — 🤖 OWN)

**Needs the operator.** Completing SMS/Imperva in the foreground CDP-Chrome
(:9222) is a credential/challenge step an agent must not perform.

### ⚠️ Diagnosis corrected — it is NOT the JWT

The alert said "likely an Imperva challenge or expired JWT", and the first read
of this was *expired JWT*. That was wrong, and acting on it would waste time.
Measured at 18:45:48 UTC:

```
now                18:45:48
jwt_exp_at         19:00:13  -> JWT VALID (14 min left)
jwt_current_set_at 18:45:00  (refreshed 48s earlier, set_by=adopt_manual_jwt)
last_heartbeat_at  18:45:03  -> ok = False     <- fails WITH a valid JWT
imperva cookies    07:00:06  -> 11.8 HOURS OLD  (source: watchdog_cdp)
```

**The JWT auto-refresh is working** (~15 min cadence, `last_auto_login_outcome
= success`). The heartbeat fails anyway, so the blocker is the **Imperva cookie
set, 11.8h stale**. The fix is re-harvesting those cookies via the foreground
CDP login — which is what the alert's remediation says, for a different reason
than the alert gives.

`/s/casino/fo/maintenance` is the *endpoint name* of the authenticated probe in
`scripts/coolbet/health_ping.py`. Non-200 means auth failed — **Coolbet is not in
maintenance.** Do not go looking for a Coolbet outage.

`coolbet_health_ping` has failed every 5 min since ~16:10 (alerted
`consecutive_failed>=3` at 16:23). `placement_paused=false` and
`daemons_paused=false`, so the placer fails **closed** correctly and recovers on
its next run once the session is restored — no code change needed to resume.

**The 2 blocked picks were on Newells Old Boys v Velez Sarsfield, kickoff 20:00
UTC 2026-09-11.** If you are reading this after that, they are moot — check for
current ones rather than chasing those two.

### Minor, separate, worth a guard (not the outage cause)

At the 18:30 boundary the session re-adopted a JWT decoding to
`iat 17:59:49 / exp 18:30:09` — i.e. **9 seconds of life left** — instead of
fetching a fresh one. At 18:45 it re-adopted correctly with 15 min of margin, so
this is intermittent, not the outage. Still: a refresh that can land a 9-second
token has no margin. Worth a "reject a token with < N minutes left" guard in the
adopt path. Low priority; log it rather than chase it.

---

## OPEN 3 — verify the 1H void sweep actually fired (P3, 5 minutes)

Exactly **1** finished match carries an ungradeable 1H bet (no HT score from AF).
`void_ungradeable_1h_bets` should void it with `void_reason='no_ht_score'` in the
**21:00 UTC** settlement pass. Nothing to fix — just confirm the safety net ran,
because if it did not, that row re-alerts "Unsettleable market" indefinitely and
the alert you just triaged comes straight back.

```sql
SELECT result, void_reason, count(*) FROM shadow_bets
 WHERE market LIKE '%_1h' GROUP BY 1,2;
-- expect at least one row: void / no_ht_score
```

The void is reversible by design: `resettle_wrongly_voided_bets` re-grades it if
AF ever backfills the half-time score.

---

## One more thing

**A parallel Claude session was editing this repo throughout the triage** —
it rewrote `coolbet_model_{1x2,ou}_shadow.py`, `pick_triggers.py`, `WORKFLOWS.md`,
`SYSTEM_MAP.md`, `api_football.py`, `unibet_odds_feed.py` and added tests to
`smoke_test.py`, then committed `8c45bb1` and `e9edc82`. Commit `052e8d3` was
staged as exact blobs rather than `git add` to avoid absorbing their in-flight
work. **Check `git status` for another session's uncommitted changes before you
stage anything.**
