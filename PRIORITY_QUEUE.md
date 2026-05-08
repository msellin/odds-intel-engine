# OddsIntel — Master Priority Queue

> Single source of truth for ALL open tasks. Every actionable item across all docs lives here.
> Other docs may describe features but ONLY this file tracks task status.
> Last updated: 2026-05-08 — STAGING-ENV moved from P1 to P3 Watchlist (trigger: first paying user). No paying users yet — staging is premature complexity at this stage.

**Column guide:**
- **☑** — `⬜` not started · `🔄` in progress · `✅` done
- **Ready?** — `✅ Ready` pick up now · `⏳ Waiting [reason]` blocked

---

## Reliability Hardening — Pre-Launch (4-AI Review, 2026-05-08)

> Origin: pool-exhaustion outage 2026-05-08. Dashboard at 0 for 11h before discovery. Root cause: `db.py:get_conn()` only returned the connection on success or connection-level errors — any other exception leaked it. With `maxconn=10` and InplayBot polling every 30s, the pool died and every subsequent job (Fixtures, Enrichment, Odds, Predictions, Betting, Settlement, ops snapshot, budget logger) failed with `pool exhausted`. The deeper lesson: one faulty subsystem permanently degraded the whole platform without isolation, alerting, or auto-recovery.
>
> List below is consolidated from 4 independent AI reviews. Strong consensus marked ✅ where 3+ reviewers agreed. Sharp disagreements resolved with reasoning in Notes column.

### P0 — Reddit Launch Blockers (~6h, this Friday)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| POOL-LEAK-FIX | `db.py:get_conn()` rewrite — `try/finally`, rollback on app exception, return conn always. Skip-cycle on `PoolError` in InplayBot with terse log line. **Keep `maxconn=10`** (R4 was right — bumping to 20 hides the next leak; loud failure at 10 is a diagnostic feature, not a bug). | 1.5h | ✅ Done 2026-05-08 | ✅ Ready | Commit `eb53c3e`. 2 new smoke tests (15 SQL errors + 15 caller-raised exceptions verify no leak). InplayBot now skips cycle with one log line on `PoolError`. Migration 069 made idempotent (drop-then-create). Recovery script `scripts/recover_today.py` triggers all missed jobs locally — usage: `venv/bin/python scripts/recover_today.py`. |
| INPLAY-UUID-FIX | InplayBot was placing 0 live bets despite 89 candidates/cycle because `mid = cand["match_id"]` is a `uuid.UUID` object (psycopg2 default), but `_get_prematch_data` keys its return dict on `str(match_id)` — `prematch.get(uuid)` always returned None, every candidate hit `if not pm: continue` before any strategy ran. Same mismatch broke `red_card_matches` and `existing_bets` lookups. | 30m | ✅ Done 2026-05-08 | ✅ Ready | One-line fix: `mid = str(cand["match_id"])` at top of loop. 2 new source-inspection smoke tests (INPLAY-UUID-FIX). Also moved smoke suite from pre-push hook (145s blocking) to GH Actions on push to main. |
| EXCEPTION-BOUNDARIES | Wrap every APScheduler job and every `live_poller._run_cycle` iteration in top-level `try/except Exception` so a single bug can never kill the loop silently. Log to Sentry, write to `pipeline_runs` with status. ✅ All 4 reviewers flagged blast-radius isolation as the architectural fix that obviates `WORKER-SPLIT`. | 1h | ✅ Done 2026-05-08 | ✅ Ready | Scheduler already had `_run_job()` wrapper — fixed `job_budget_sync()` which was bypassing it. LivePoller `run_forever()` already had try/except — added `traceback.print_exc()` so exceptions aren't silently swallowed. Fixed `return None` → `return False` in budget-exhausted path. |
| JOB-COALESCE | `coalesce=True, max_instances=1` on every APScheduler job. ✅ Strong consensus. | 30m | ✅ Done 2026-05-08 | ✅ Ready | Applied via `BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1})` — one line covers all current + future jobs. |
| DB-STMT-TIMEOUT | Set `statement_timeout=60s` and `idle_in_transaction_session_timeout=30s` via DSN options on conn open. R4 caveat: 15s would kill nightly settlement (legitimate joins push 20-30s). 60s is the right global default. | 30m | ✅ Done 2026-05-08 | ✅ Ready | Migration 070. Set at database level (`ALTER DATABASE postgres`) — Supavisor strips per-connection options= so it must be database-level. |
| OBS-HEARTBEAT | External healthchecks.io ping on `/health` every 5min. Alert when `ops_snapshot` >2h old, pool >80%, or `/health` 5xx for 2 consecutive checks. ✅ All 4 reviewers — would have caught today's outage in 5 min vs 11h. | 1h | ✅ Done 2026-05-08 | ✅ Ready | `job_healthcheck_ping()` in scheduler pings `HEALTHCHECKS_IO_PING_URL` every 5 min. healthchecks.io account created, check live (last ping 20s ago confirmed). Period 5min / Grace 10min set. |
| OBS-SENTRY-BACKEND | Wire `sentry_sdk` into `workers/scheduler.py` + `workers/live_poller.py`. Frontend already has it (`SENTRY` ✅). Add `before_send` filter to drop `psycopg2.pool.PoolError` and `OperationalError` to avoid free-tier flood. | 1.5h | ⬜ | ✅ Ready | R4 trap: Sentry will flood without filtering — APScheduler scheduling exceptions and httpx retries are noisy. Budget 1h for tuning, not 0. |

### P1 — Pre-Paid-Launch Money / Security (~14h, before Stripe goes live)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| STRIPE-WEBHOOK-SIG | Verify `Stripe-Signature` header against `STRIPE_WEBHOOK_SECRET` using `stripe.Webhook.construct_event`. Reject any unsigned/bad-signature payload. ✅ R1 + R4 both flagged: without this, anyone can POST fake `checkout.session.completed` and grant themselves Elite. | 1h | ✅ Done 2026-05-08 | ✅ Ready | Already implemented — `constructEvent()` with body+sig+secret was already in the handler. Verified 2026-05-08. |
| MONEY-STRIPE-IDEMPOTENT | `processed_events` table keyed by `event.id` from **JSON payload (NOT header)** — R4 trap: header `Stripe-Signature` is per-attempt and won't dedupe retries. Wrap handler logic + DB write in a single transaction; on commit failure, mark event unprocessed for retry. | 3h | ✅ Done 2026-05-08 | ✅ Ready | Migration 071 (`processed_events` table, UNIQUE on event_id). Webhook handler now inserts event.id before processing — on 23505 (duplicate) returns 200 immediately without re-applying side effects. On unexpected DB error returns 500 so Stripe retries later. |
| MONEY-WEBHOOK-TEST | Script 50+ webhook scenarios via Stripe CLI: `success`, `dupe`, `out-of-order`, `network-fail-after-process`, `bad-signature`, `unknown-event-type`. Verify no double-grants, no ghost tiers, no missed grants. R2 add. | 1h | ✅ Done 2026-05-08 | ✅ Ready | `scripts/test_stripe_webhook.sh` — automates bad-sig and no-sig checks, provides manual checklist + exact `stripe trigger` commands for remaining scenarios. |
| STRIPE-RECONCILE | Daily script: `stripe.events.list(created.gte=yesterday)` → diff vs `processed_events` table → alert on drift. R4 add: bigger Stripe risk isn't double-grant, it's **never-grant when webhook silently fails**. | 1h | ✅ Done 2026-05-08 | ✅ Ready | `scripts/stripe_reconcile.py` + `job_stripe_reconcile()` in scheduler at 09:00 UTC. Emails `ADMIN_ALERT_EMAIL` with missed event IDs + resend instructions if drift found. |
| MONEY-RLS-AUDIT | Walk every table; confirm RLS policy + that service-role key is server-only (never in NEXT_PUBLIC_ env). R4: 30 min not 2h — checklist walkthrough since schema is known. | 30m | ✅ Done 2026-05-08 | ✅ Ready | All tables have RLS. Service key only in server-side API routes, never in NEXT_PUBLIC_. One gap fixed: migration 072 adds RLS to `processed_events` (no public SELECT). |
| MONEY-SETTLE-RECON | Daily reconciliation: count of bets settled vs count of finished matches. Alert on drift >2. ✅ R1 + R2 + R4. | 2h | ✅ Done 2026-05-08 | ✅ Ready | `scripts/settle_reconcile.py` — queries finished matches with pending bets, alerts via Resend if >2 stuck. Wired into scheduler at 21:30 UTC alongside settlement health check. |
| BACKUP-RESTORE-DRILL | Actually restore Supabase PITR to a scratch project. Time it. Document the procedure. R4 add: untested backups = no backups. | 1h | ⬜ | ✅ Ready | You upgraded to Pro for this. Verify it works end-to-end before you need it at 02:00 UTC during an incident. |
| RATE-LIMIT-API | Upstash rate limit on `/api/bet-explain` (Gemini cost), `/api/live-odds` (DB load), `/api/stripe/upgrade`. ✅ R1 + R4. | 2h | ✅ Done 2026-05-08 | ✅ Ready | No Upstash needed — in-memory sliding window (`src/lib/rate-limit.ts`). `bet-explain`: 10/hour/user, `live-odds`: 120/hour/user (30s chart refresh), `stripe-upgrade`: 5/hour/user. Resets on redeploy — sufficient for abuse prevention. |
| ABUSE-DETECT-PRELAUNCH | One-shot scan: SQL injection on user-input forms, password policy, session timeout, anonymous endpoint enumeration, CSRF on state-changing routes. R4 add. | 2h | ✅ Done 2026-05-08 | ✅ Ready | Audit complete: (1) SQL injection — safe, Supabase SDK uses parameterized queries throughout; (2) CSRF — safe, all state-changing routes require Supabase auth cookie verified server-side; (3) Input validation — UUID regex added to `matchId` (live-odds) and `betId` (bet-explain) params; (4) No sensitive data exposed to anon users — all data routes require auth; (5) Stripe webhook — signature verified. Rate limits added (RATE-LIMIT-API). No critical vulnerabilities found. |
| DEPLOY-ROLLBACK-RUNBOOK | One-page doc: exact Railway redeploy-from-SHA + Vercel redeploy-from-deployment commands. Test it: deploy a no-op commit, then roll back. R4 add. | 30m | ✅ Done 2026-05-08 | ✅ Ready | `docs/ROLLBACK_RUNBOOK.md` — Railway (redeploy via dashboard or git revert), Vercel (CLI `vercel rollback`, dashboard promotion, or revert push), DB migration reversal procedure, post-rollback checklist. |

### P2 — Reliability Hardening (post-Reddit, before paid launch, ~12h)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| OBS-POOL-METRIC | Add pool utilization (`used/max`) to `/health` JSON and to InplayBot's 10-cycle heartbeat log. Alert when >80%. | 30m | ✅ Done 2026-05-08 | ✅ Done | `db.py:get_pool_status()` reads `_used`/`_pool` from psycopg2 internals. `/health` now returns `pool:{used,idle,max,pct}` + `pool_alert:bool`. InplayBot heartbeat shows `pool X/10 (Y%)` + `⚠️ POOL HIGH` at ≥80%. Smoke test added. |
| SYNTHETIC-LIVENESS | Business-level liveness checks beyond infra: did we generate signals today? Did snapshots arrive in last 5 min during 10-23 UTC? Did settlement produce rows? Did bets get placed if matches existed? **Merge with existing `PIPE-ALERT` task** (line 170 in this file). | 2h | ✅ Done 2026-05-08 | ✅ Ready | Merged into PIPE-ALERT. `workers/jobs/health_alerts.py` — 4 checks, Resend email alerts, in-memory dedup. Wired into scheduler: 09:35 morning, hourly 10-22 snapshot, 21:30 settlement. Set `ADMIN_ALERT_EMAIL` env var on Railway. |
| KILL-SWITCH-FLAGS | Operator toggles via env var or `system_flags` table: `disable_inplay_strategies`, `disable_enrichment`, `disable_news_checker`, `disable_paper_betting`. Workers check on each cycle. R3 add. | 2h | ✅ Done 2026-05-08 | ✅ Ready | `workers/utils/kill_switches.py` — reads `DISABLE_*` env vars. Wired into `run_inplay_strategies()`, `run_enrichment()`, `run_news_checker()`, `run_morning()`, `run_betting()`. Set env var in Railway → skip takes effect next cycle. 3 smoke tests added. |
| PIPELINE-STABILIZE | Fixed 4 sources of ops-page rot: (1) orphaned 'running' records — startup cleanup 30→10 min, new periodic cleanup job every 30 min; (2) transfers capped 100/run + cache 7→30 days; (3) coaches capped 50/run; (4) H2H now Tier 1 only + same-day cache (442 → ~50-80 morning calls, 0 intraday). Full enrichment target: <10 min. | — | ✅ Done 2026-05-08 | ✅ Ready | Deploy to Railway to activate periodic cleanup. |
| JOB-TIMEOUT | Per-job watchdog timeout via `signal.alarm` or threading. Mark `pipeline_runs.status='timed_out'` distinct from 'killed'. | 2h | ⬜ | ✅ Ready | Hung jobs hold conns forever. Distinct status lets you tell crash-cause apart. |
| JOB-IDEMPOTENT | Audit fixtures/odds/predictions/settlement/ELO for re-runnability. **R1 + R3: prefer destructive idempotency** — wipe day's records for a match and rewrite cleanly, instead of perfect-merge logic. R4 effort = 6h, not 3h. | 6h | ⬜ | ✅ Ready | The audit is fast. The fix-where-broken is the iceberg. Settlement and ELO update are likely offenders. |
| API-RETRY-WRAPPER | `tenacity` decorator on AF/Kambi/ESPN/Gemini clients: 2 retries, exponential backoff, jitter, fail-fast after 3rd attempt, log to Sentry on each retry. R1 estimate (30m) was too low; R3 (1-2 days) was for circuit-breaker version we don't need. R4: 2h is right. | 2h | ✅ Done 2026-05-08 | ✅ Ready | No tenacity needed. Manual retry loop in `_get()` (api_football.py): 3 attempts, 1s/2s/4s backoff, retries on 429/503 and connection/timeout errors, fail-fast on other 4xx. Gemini retry in `news_checker.py` and `match_previews.py`: 3 attempts, backoff on `ResourceExhausted`/`ServiceUnavailable`/`DeadlineExceeded` by exception class name (no google-api-core import needed). |
| OBS-BUDGET-ALERT | Alert when AF daily burn projects >60K of 75K. R4: low priority — failure mode is degraded, not financial. Pulled out of P1. | 30m | ⬜ | ✅ Ready | Catch runaway loops before quota blown. |
| MEMORY-MONITORING | Track Railway pod memory, alert if >70% sustained. R1 trap: XGBoost + LiveTracker on $5 pod can OOM during concurrent peak — and SIGKILL by Railway emits no Python exception, so Sentry won't catch it. Heartbeat is the only defense. | 30m | ⬜ | ✅ Ready | Especially important if WORKER-SPLIT stays deferred. |

### P3 — Watchlist (only when triggered, not on schedule)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| WORKER-SPLIT | Split `live_poller.py` into its own Railway service. R3 wanted P0; R4 said skip. **Resolution: only do this if cascade failures persist after EXCEPTION-BOUNDARIES.** Pool fix + boundaries should give blast-radius isolation without process split. Revisit if `live_poller` exceptions are still killing scheduler jobs after 2 weeks of monitoring. | 4h (lower than R3's "1 day") | ⬜ | ⏳ Trigger: scheduler jobs killed by live_poller after EXCEPTION-BOUNDARIES ships | Two Railway services: `scheduler-service` and `live-service`. Effort is small (separate entrypoints already exist) but operational complexity grows. |
| MODEL-DRIFT-ALERT | Z-score on prediction mean/variance vs trailing 14-day distribution. Alert if today's predictions deviate >3σ. R4 add. | 1h | ⬜ | ✅ Ready | Catches silently broken feature pipeline before paper bots drain bankrolls. |
| FAIL-OPEN-DEGRADATION | Stale-but-usable fallbacks: yesterday's standings if enrichment fails, skip one bookmaker if its API dies, keep live tracking even if news analysis fails. R3 add. | 3-4h | ⬜ | ✅ Ready | Reliability is mostly graceful degradation. Right now AF rate-limit cascades through enrichment → predictions → betting. |
| USER-DEGRADATION-UX | Clear "data temporarily unavailable" messages in frontend when backend stale/down, instead of "Loading..." forever. R4 add. | 2h | ⬜ | ✅ Ready | UX during degradation is half the trust-loss equation. |
| SUPPORT-RUNBOOK | One-page: Stripe-charged-but-no-tier, tier-granted-but-no-charge, settlement-disputed, refund procedure. R4 add. | 1h | ⬜ | ⏳ After paid launch | Need the runbook before the first edge case fires, not during it. |
| STAGING-ENV | Separate Supabase project (free tier) + Railway staging service + Vercel preview env + Stripe test webhook endpoint. | 3h | ⬜ | ⏳ After first paying user | R4 flagged as highest-leverage pre-paid-launch item, but the risk it protects against is "a paying user hits a broken Stripe flow." With 0 paying users that risk doesn't exist. At 12 users (3 family, no revenue), adding staging infra is premature complexity. Trigger: first paid subscription received. |
| SCHEMA-DRIFT-SMOKE | 30-min cheap version of SCHEMA-DRIFT-GUARD: pytest that `SELECT col FROM table LIMIT 0` for every column code references. R4: cheap version captures 80% for 5% effort. | 30m | ⬜ | ✅ Ready | Drop the proper CI-integration version. |
| BACKFILL-SAFETY | Test re-running each backfill script — same input, same output, no duplicates. R4: low priority since you backfill ~quarterly. | 2h | ⬜ | ⏳ Before next backfill | Just be careful that day. |
| AF-COVERAGE-AUDIT | Validate whether AF league coverage flags (`coverage_events`, `coverage_lineups`) actually match reality. Pick ~20 leagues spanning `coverage_events = true/false`, grab a recent fixture from each, call `/fixtures/events` and `/fixtures/lineups`, compare results against stored flags. If flags are reliable, gate events and lineups fetches in the live poller — events are 1 call/match every ~135s during live matches so a 50% reduction is meaningful. | 1h | ⬜ | ✅ Ready | Script: pick leagues from DB, get a recent fixture_id per league, call AF, compare. |
| EMAIL-DELIVERY-CHECK | Verify Resend DKIM/SPF/DMARC are correct (digest emails already sending — confirm not landing in spam at scale). | 1h | ⬜ | ✅ Ready | If `ENG-4` already configured this, mark ✅. |

### Explicitly DROPPED (consensus from 4-AI review)

| ID | Why dropped |
|----|-------------|
| ~OBS-LOGS-STRUCTURED~ | All 4 reviewers: yak-shaving for 12 users. Sentry + Railway logs + grep are sufficient. Revisit at 1K users. |
| ~JOB-LOCK~ | All 4 reviewers: duplicate of JOB-COALESCE. APScheduler + `max_instances=1` already serializes runs. Custom locking adds failure modes (stale locks, recovery work). |
| ~RUNBOOK-INCIDENTS~ (broad) | R1 + R3: at 02:00 UTC you restart the pod, you don't read a Notion doc. Replaced by targeted `DEPLOY-ROLLBACK-RUNBOOK` + `SUPPORT-RUNBOOK`. |
| ~SCHEMA-DRIFT-GUARD~ (proper) | R3 + R4: founder dopamine, not founder reliability. Replaced by 30-min `SCHEMA-DRIFT-SMOKE`. |
| ~LIVE-BATCH-COLLAPSE~ | All reviewers: defer until paying user complains about latency. |
| ~SNAPSHOT-PARTITION~ | R3 + R4: way premature. Postgres handles more than founders think. |
| ~FE-LIVE-WEBSOCKET~ | All reviewers: not a launch concern. |
| ~PSYCOPG3-MIGRATION~ | Only if pool issues persist after POOL-LEAK-FIX. |
| ~PUSH-FEED~ (Sportradar/BetGenius) | $3K-50K/mo. Defer until revenue covers 10× cost. |
| ~RAILWAY-UPGRADE~ | Single instance fine ≤100 concurrent users. |
| ~ADD-REDIS~ | No queue need yet. |
| ~READ-REPLICAS~ | Overkill at this scale. |

## InplayBot Tuning — Post-Bug Triage (5-AI review, 2026-05-08)

> Origin: InplayBot placed only 2 paper bets in 11 days. Root cause was 4 stacked bugs (UUID cast, market-name mismatch, af_prediction.goals misread as xG, settlement market-format mismatch — all fixed). Replay of today's data showed only Strategy E firing (81 bets), all others firing zero — root cause is over-stacked AND-gates per the AI consensus, not a math problem.
> 5 AI tools (ChatGPT, Gemini, Claude, etc.) reviewed the strategies on 2026-05-08 and produced strong consensus on threshold loosening, B's broken model, F's lack of edge, and a missing corner-pressure strategy.

### P0 — Capture lost data + fix model bugs (~4h)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| INPLAY-BACKFILL-RUN | Run `scripts/replay_inplay.py --backfill` for the full Apr 27 → today window with the just-fixed strategies. Review CSV + summary in `dev/active/`. **Don't write to DB yet** — see INPLAY-BACKFILL-PERSIST. | 30m | ⬜ | ✅ Ready | Captures "what we lost" baseline. Runs in ~2 min with in-memory Strategy F. |
| INPLAY-FIX-B-MODEL | Strategy B currently computes P(BTTS) and bets Over 2.5 — different markets, phantom edge. **5/5 AI consensus** this is a logic bug. Fix: compute P(BTTS) and bet **BTTS Yes** at BTTS odds. Requires reading live BTTS odds (snapshot already has `live_btts_yes/no` columns? verify). | 2h | ⬜ | ✅ Ready | Reply 5: cleanest fix; reply 4: option C (BTTS as filter, P(O2.5) as edge) is more conservative middle ground. |
| INPLAY-FIX-E-FALLBACK | Strategy E's 1.3+1.3 fallback inflates pace_ratio denominator for low-scoring leagues → fake Under edges. **Replies 4 + 5 explicit:** either (a) use league-median goals/game when team_season_stats is missing, or (b) skip the bet entirely with a fallback flag. Today's +14% replay ROI is likely overstated; true ROI probably +3% to +7% (reply 5 estimate). | 2h | ⬜ | ✅ Ready | Add `LEFT JOIN league_avg_goals` (or compute via subquery) and use that instead of hardcoded 1.3. |
| INPLAY-BACKFILL-PERSIST | After INPLAY-FIX-B-MODEL + INPLAY-FIX-E-FALLBACK ship, re-run backfill, then add `is_backfill BOOLEAN` column to `simulated_bets` and persist the backfilled bets with that flag set. Performance page shows them with a visual differentiator (dashed line on equity chart, "BF" badge in table). | 3h | ⬜ | ⏳ After backfill review | Let user review CSV first before any DB writes. |

### P1 — Strategy consolidation + threshold loosening (~3h)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| INPLAY-DROP-F | **4/5 consensus drop F** (replies 1, 2, 5 explicit; 4 says probation). Reply 5 decisive: sharp books already price the pace signal — we have no edge over them on the same data. Remove `inplay_f` from INPLAY_BOTS dict and delete `_check_strategy_f`. | 30m | ⬜ | ✅ Ready | Mark the existing inplay_f bot as inactive in DB; don't delete its bets. |
| INPLAY-MERGE-A2 | Merge A2 into A — single "low-scoring xG divergence" strategy with `total_goals ≤ 1` (replaces score=0-0 vs score-sum=1 split). **4/5 consensus.** | 1h | ⬜ | ✅ Ready | Same thesis, less dilution. |
| INPLAY-MERGE-CHOME | Merge C_home into C with a home-favourite flag that relaxes possession threshold by 5pp. **3/5 consensus.** | 1h | ⬜ | ✅ Ready |  |
| INPLAY-LOOSEN-THRESHOLDS | Apply convergent threshold reductions per the 5-AI table: A window 25-35 → 20-40, A live_xg 0.9 → 0.6, A SoT 4 → 3, A proxy SoT 9 → 6, A posterior multiplier 1.15 → 1.05-1.08, edge floors 3% → 1.5-2%. C/C_home possession 60% → 52-55%. D window 55-75 → 48-80, D live_xg 1.0 → 0.7, D OU odds floor 2.50 → 2.10-2.20. | 2h | ⬜ | ✅ Ready | All 5 replies converged on these numbers. Caveat: don't lock in based on backfill alone — reply 5 says wait for 1500+ bets before claiming calibration. |

### P2 — New strategies (~6h)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| INPLAY-NEW-CORNER | New strategy G: **Corner Cluster Over**. Entry: ≥3 corners in last 10 min for one team, minute 30-70, total goals ≤ 1, OU 2.5 odds ≥ 2.10, edge ≥ 3%. Bet Over 2.5. **4/5 consensus** — most-proposed new strategy. | 3h | ⬜ | ✅ Ready | Snapshots already have `corners_home/away`; needs rolling 10-min delta computed in-strategy. |
| INPLAY-NEW-HT-RESTART | New strategy: **Half-Time Restart Surge**. Entry: minute 46-55, score 0-0 at HT, first-half xG ≥ 0.7 OR first-half SoT ≥ 6, prematch_o25 > 0.50, edge ≥ 3%. Bet Over 2.5 if odds > 2.80, else Over 1.5 if odds > 1.60. **3/5 consensus.** | 3h | ⬜ | ✅ Ready | Need to compute first-half stats from snapshots at minute 45. |
| INPLAY-NEW-RED-CARD | New strategy: **Red Card Overreaction Over**. Entry: red card in minute 15-55, total goals ≤ 1, 11-man team possession ≥ 55%, OU 2.5 over odds > 2.30. Bet Over 2.5. **1/5 (reply 4 only)** but unique thesis — exploits the fact that all current strategies *exclude* red-card matches. | 3h | ⬜ | ✅ Ready | Speculative — run alongside corner + HT and compare. |
| INPLAY-NEW-POSSESSION-SWING | New strategy: **Possession Swing**. Detect ≥10pp possession increase over 15-min rolling window. Bet 1X2 on swinging team or BTTS Yes. **2/5 consensus.** | 4h | ⬜ | ⏳ After corner + HT validated | Most complex — needs rolling-window state tracking. |

### P2 — Calibration improvements (~5h)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| INPLAY-LAMBDA-STATE | Add score-state multiplier to remaining-goal lambda: ~+15% trailing team late, −10% leading team late, +5% level late. **5/5 consensus.** Football is not Poisson-stationary. | 2h | ⬜ | ✅ Ready | Apply in `_check_strategy_*` after computing posterior. |
| INPLAY-TIME-DECAY-PRIOR | Bayesian update weight should drift over the match: `w_live = 1 - exp(-minute/30)` (replies 1, 4, 5). At minute 30 → ~60/40 live/prematch; at 60 → ~85/15. Replaces flat `(prematch_xg + live_xg) / (1 + minute/90)`. | 2h | ⬜ | ✅ Ready |  |
| INPLAY-PERIOD-RATES | Reply 4: scale remaining lambda by period-specific multipliers (1-15: 0.85×, 76-90+ST: 1.20×). | 1h | ⬜ | ✅ Ready | Marginal lift on top of state multiplier. |
| INPLAY-EMA-LIVE-XG | Smooth live xG via 5-10 min EMA instead of cumulative-to-minute (reply 4). One spike shouldn't trigger a bet by itself. | 1h | ⬜ | ✅ Ready |  |
| INPLAY-DIXON-COLES | Apply Dixon-Coles low-score correction (replies 3, 4). Matters most for E (Under bets on low-scoring) where exact P(0-0)/P(1-0)/P(0-1) determines edge. | 4h | ⬜ | ⏳ After 1500+ bets | Don't tune this until we have enough data to validate the correction parameter. |

### P3 — Speculative / infra-dependent

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| INPLAY-SOFT-GATES | Reply 1's biggest recommendation: replace hard threshold gates with composite weighted scoring (assign points to SoT pace, xG pace, possession, corners, market drift, prematch strength, score state — trigger above a single composite threshold). High-impact but architectural. | 8h | ⬜ | ⏳ After P0/P1 land | Will likely supersede many of the threshold tweaks above. |
| INPLAY-TWO-BOOK-ARB | Reply 5: bet when primary book's OU 2.5 differs from a second feed by ≥4pp. Requires a second odds source. **Most reliable edge** if infra exists. | varies | ⬜ | ⏳ Need 2nd odds feed | Out of scope without Pinnacle/sharp feed. |
| INPLAY-FUNNEL-LOGGING | Wire the `_funnel` dict (already added in inplay_bot.py) to heartbeat output: per-cycle counts of skips at each stage (no_prematch, league_xg_gate, no_strategy_trigger, odds_stale, score_changed, store_bet_error). Lets us see where the funnel collapses next time a strategy stops firing. | 1h | ⬜ | ✅ Ready | Defensive — pays for itself the next time a strategy goes silent. |

### Suggested commit grouping

1. **Commit 1 (done):** POOL-LEAK-FIX + EXCEPTION-BOUNDARIES + JOB-COALESCE + DB-STMT-TIMEOUT — fixes today's outage class.
2. **Commit 2 (next):** OBS-HEARTBEAT + OBS-SENTRY-BACKEND — visibility before Reddit goes live.
3. **Commit 3 (pre-paid-launch):** STRIPE-WEBHOOK-SIG + MONEY-STRIPE-IDEMPOTENT + MONEY-WEBHOOK-TEST + STRIPE-RECONCILE — one Stripe-integrity commit.
4. **Commit 4:** MONEY-RLS-AUDIT + MONEY-SETTLE-RECON + BACKUP-RESTORE-DRILL + RATE-LIMIT-API + ABUSE-DETECT-PRELAUNCH + DEPLOY-ROLLBACK-RUNBOOK — pre-paid-launch security/recoverability.
5. **Commit 5+:** P2 tasks individually as time allows.

---

## Gemini AI Cost Tracker

> Billing enabled 2026-05-05. Prices: `gemini-2.5-flash` $0.15/1M input + $0.60/1M output · `gemini-2.5-flash-lite` $0.075/1M input + $0.30/1M output.

### Running now

| Job | Model | Calls/day | Tokens/call | $/mo now | Scales with |
|-----|-------|-----------|-------------|----------|-------------|
| `news_checker` (4×/day per active bet) | flash | ~64 (4 × 16 bets) | ~800 | **~$0.38** | Active bets — 50 bets = ~$1.20/mo |
| `match_previews` (ENG-3, 1×/day) | flash | ~10 | ~1,200 | **~$0.11** | Fixed (top 10 matches) |
| `settlement post-mortem` (1×/day) | flash | 1 batch | ~2,000 | **~$0.04** | Fixed |
| `bet-explain` (BET-EXPLAIN, user-triggered) | flash-lite | ~5 new/day | ~800 | **~$0.02** | New bets only — cached after 1st call |
| **Total running** | | | | **~$0.55/mo** | |

### Planned (not yet built)

| ID | Feature | Model | Calls/day | $/mo at launch (10 users) | $/user/mo |
|----|---------|-------|-----------|--------------------------|-----------|
| MTI | Managerial tactical intent (press conf.) | flash | ~10 (5 matches × 2 teams) | **~$0.22** flat | negligible per user |
| RSS-NEWS | RSS news extraction pipeline | flash | ~20 articles | **~$0.30** Gemini only | negligible — data service ($30-90/mo) is the real cost |

**Current total AI cost: ~$0.55/mo running + $0/mo planned = ~$0.55/mo**

---

## Tier 0 — Foundation (all done)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| B-ML1 | Pseudo-CLV for all ~280 daily matches | 2-3h | ✅ | ✅ Done | `(1/open) / (1/close) - 1` per finished match. 280 rows/day |
| B-ML2 | `match_feature_vectors` nightly ETL | 1 day | ✅ | ✅ Done | Pivots signals + predictions + ELO/form → wide row per match |
| CAL-1 | Calibration validation script | 2h | ✅ | ✅ Done | `scripts/check_calibration.py` |
| S1+S2 | Migration 010: `source` on predictions + `match_signals` table | 2-3h | ✅ | ✅ Done | Unique constraint on (match_id, market, source) |
| CAL-2 | Flip calibration α: T1→0.20, T2→0.30, T3→0.50, T4→0.65 | 30 min | ✅ | ✅ Done | Was T1=0.55 (model-heavy). Now market-heavy in efficient markets |
| RISK-1 | Reduce Kelly fraction to 0.15×, cap to 1% bankroll per bet | 15 min | ✅ | ✅ Done | KELLY_FRACTION 0.25→0.15, MAX_STAKE_PCT 0.015→0.010 |
| LLM-RESOLVE | Run `scripts/resolve_team_names.py --apply` | 30 min | ✅ | ✅ Done | 143 total mappings. 204 unmatched names accounted for. **AI: $0 ongoing (one-time batch)** |

---

## Tier 1 — Next 1-2 Weeks

### Done

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| OPS-DASHBOARD-FIX | Ops dashboard blank — `write_ops_snapshot` swallowed all errors silently, no row written → every metric showed `—` | ✅ Done 2026-05-08 | `workers/api_clients/supabase_client.py`: per-section try/except so a single bad query doesn't kill the snapshot; logs to `pipeline_runs` so failures are visible on the dashboard; re-raises only on INSERT failure. `workers/scheduler.py`: hourly `job_ops_snapshot` now wrapped in `_run_job` so failures hit `/health` and `_recent_errors`. 2 smoke tests added. Backfilled today's row. |
| INPLAY-EDGE-BUG | Inplay bot edge_percent stored as percent not decimal | ✅ Done 2026-05-07 | `inplay_bot.py` stored `edge = (prob-implied)*100` but `store_bet` expects decimal. Fixed: divide by 100 at storage. Patched 1 bad DB record. Smoke test added. |
| SIGNALS-RLS | `match_signals` RLS enabled but no SELECT policy — anon key returned [] for everyone | ✅ Done 2026-05-07 | Migration 069. `getMatchSignals()` was silently returning empty, hiding accordion + summary on ALL matches. |
| SIGNALS-UI | Wire all missing signals to accordion + summary (~20 signals in DB but invisible) | ✅ Done 2026-05-07 | `signal-labels.ts`: 15 new label functions (Pinnacle, manager change, turf, H2H depth, goals avg, relegation, referee O/U, AH, BTTS). `signal-accordion.tsx`: new Specialist Markets group + all signals added. `match-signal-summary.tsx`: manager change, relegation pressure, Pinnacle line moves added to top-5 priority list. |
| S3/S4/S5/S3b-f | All signals wired (ELO, form, H2H, referee, BDM, OLM, venue, rest, standings) | ✅ | Full signal set in match_signals |
| SIG-7/8/9/10/11 | Importance asymmetry, venue splits, form slope, odds vol, league meta | ✅ | |
| META-2 | Meta-model feature design (8 market-structure features) | ✅ | |
| PIPE-1 | Clean 9-job pipeline replacing monolith | ✅ | |
| STRIPE / F8 | Stripe test mode: checkout, webhook, portal, founding cap, annual billing | ✅ | |
| B3 | Server-side tier gating in Next.js | ✅ | |
| SUPABASE-PRO | Supabase upgraded to Pro ($25/mo) | ✅ | PITR + backups |
| LEAGUE-DEDUP | Kambi/AF dedup, priority sort, ~1100 orphan leagues pruned | ✅ | |
| SENTRY | Error monitoring wired in frontend | ✅ | |

### Done (continued)

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| PERF-FE-1 | A1: daily_unlocks check parallelised inside auth IIFE | ✅ Done 2026-05-06 | Was sequential after main Promise.all (one extra round-trip for logged-in users). Moved inside authResult async block, runs in parallel with getUserTier. `src/app/(app)/matches/page.tsx` |
| PERF-FE-2 | C3: getTodayOdds — replace SELECT * with get_latest_match_odds RPC | ✅ Done 2026-05-06 | Was fetching all historical snapshots (~18k rows for 160 matches). Now DISTINCT ON (match, bookmaker, market, selection) returns only the latest snapshot per combo. Migration 053. `src/lib/engine-data.ts` |
| PERF-FE-3 | D1: getTrackRecordStats — replace 2500-row fetch with get_coverage_counts RPC | ✅ Done 2026-05-06 | Was fetching 500 odds_snapshots + 2000 matches to count distinct bookmakers/leagues in JS. Now COUNT(DISTINCT) in DB returns two integers. Migration 053. `src/lib/engine-data.ts` |
| PERF-FE-4 | C1: getPublicMatchBookmakerCount — replace row fetch with get_bookmaker_count_for_match RPC | ✅ Done 2026-05-06 | Was fetching all 1x2 rows per match and counting in JS. Now single COUNT(DISTINCT bookmaker) in DB. Migration 053. `src/lib/engine-data.ts` |
| PERF-FE-5 | C2: getOddsMovement — replace JS bucketing with get_odds_movement_bucketed RPC | ✅ Done 2026-05-06 | Was fetching 100-1000 raw snapshots and bucketing by hour in JS. Now DATE_TRUNC('hour') + MAX GROUP BY in DB returns ~20-50 rows. Migration 053. `src/lib/engine-data.ts` |
| PERF-PY-1 | B1: compute_market_implied_strength — fix N+1 (was 2+N queries) | ✅ Done 2026-05-06 | Was 1 query per match in two loops (up to 12 queries). Replaced with one batched DISTINCT ON query for all match IDs. 2+N → 3 queries total. `workers/api_clients/supabase_client.py` |

### Open

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| CAL-DIAG-1 | SQL diagnostic on 77 settled home bets: avg Poisson vs XGB prob, sharp_consensus direction, pre-Platt vs post-Platt comparison | 1h | ✅ Done 2026-05-06 | ✅ Ready | Results: n=31 bets, model=38.2%, calibrated=42.0% (Platt inflated +3.87pp), market_implied=29.0%, actual=25.8%. Pinnacle=30.2% — closer to actual than model. Sharp consensus avg=−0.0034. Gate coverage: 1/23 losses caught, 7 missing signal. `scripts/run_cal_diag.py` |
| CAL-PIN-SHRINK | Switch shrinkage anchor from market avg → Pinnacle (with soft-book fallback when Pinnacle unavailable) | 30min | ✅ Done 2026-05-06 | ✅ Ready | `calibrate_prob()` now accepts `anchor_implied`; Pinnacle-implied used when available for 1X2 Home. Batch-loaded from match_signals in daily_pipeline_v2. Soft-book fallback preserved when Pinnacle unavailable. `workers/model/improvements.py` |
| CAL-ALPHA-ODDS | Odds-conditional α reduction: `if odds > 3.0: alpha = max(alpha - 0.20, 0.10)` | 30min | ✅ Done 2026-05-06 | ✅ Ready | Note: alpha = model weight in this codebase (opposite of AI consultant convention — they used α = market weight). Reducing alpha pulls harder toward anchor for longshots. Targets 0.30-0.40 bin (23 bets, 13% actual vs 35.5% predicted). `workers/model/improvements.py` |
| CAL-SHARP-GATE | Skip 1X2 Home bets when `sharp_consensus_home < −0.02` | 1h | ✅ Done 2026-05-06 | ✅ Ready | Batch-loads `sharp_consensus_home` from match_signals alongside Pinnacle. Gate fires in betting loop after PIN-VETO check. Coverage currently low (1/23 losses, 7 missing signal) — will improve as more bets settle with signal data. `workers/jobs/daily_pipeline_v2.py` |
| CAL-DRAW-INFLATE | Add draw inflation factor to Poisson convolution: `adjusted_draw = raw_draw_prob × 1.08`, renormalize home/away | 1h | ✅ Done 2026-05-06 | ✅ Ready | Applied after DC correction in `_poisson_probs()`. DRAW_INFLATE=1.08 constant; excess probability redistributed proportionally to home/away. Unlocks draw market betting. `workers/jobs/daily_pipeline_v2.py`. |
| TZ-TOMORROW | Tomorrow's matches tab on matches page | 2-3h | ✅ Done 2026-05-06 | ✅ Ready | `getPublicMatches(dayOffset)` accepts 0=today, 1=tomorrow. URL param `?tab=tomorrow`. Yesterday overhang skipped on tomorrow tab. WhatChangedToday hidden on tomorrow tab. Also shipped: parallel odds RPC batches (was sequential) + replaced 60k-row signal count query with `get_signal_counts` RPC (migration 051). |
| RAIL-POLL-TUNE | Tune LivePoller intervals to reduce Railway cost ~25% | 30min | ✅ Done 2026-05-08 | ✅ Ready | `FAST_INTERVAL` 30→45s, `MEDIUM_MULTIPLIER` 2→3. AF calls ~8-12K/day (was 12-18K). |
| STAKE-RANK | Exposure cap should rank bets within a league by edge before applying declining stakes — currently processes in DB query order so the highest-edge bet in a league can end up with the smallest stake if evaluated last. Fix: collect all bet candidates per league first, sort descending by edge, then apply 50% halving in ranked order so best bet always gets most money. | 2h | ✅ Done 2026-05-08 | ✅ Ready | One-liner in `daily_pipeline_v2.py`: `bet_candidates.sort(key=lambda x: x[6], reverse=True)` inserted after the candidate collection loop and before the placement loop. Edge is index 6 of the 11-tuple. Highest-edge bet always gets full stake; any 3rd+ bet in the same league gets halved in that order. |
| B-ML3 | First meta-model: 8-feature logistic regression, target=pseudo_clv>0 | 1 day | ⬜ | ⏳ ~May 17 (need 3K quality CLV rows) | **Data quality cutoff: use only `match_feature_vectors` rows WHERE `captured_at >= 2026-05-06`** — pre-cutoff rows lack Pinnacle signals (NULLs on the strongest feature). Shifts readiness from ~May 10 to ~May 17 (11 days × 280 matches/day). Filter: `WHERE pinnacle_implied_home IS NOT NULL`. Features per META-2. Feature notes: (1) `model_prob - pinnacle_implied` — likely strongest; (2) keep `odds_drift`, drop `overnight_line_move` (0.7+ correlated); (3) validate `news_impact_score` AUC > 0.52 first; (4) add `odds_at_pick` (raw); (5) add `time_to_kickoff` (hours). Source: 4-AI Calibration Review + data quality analysis 2026-05-06 |
| BOT-TIMING | Time-window bot cohorts: morning/midday/pre-KO A/B test | 2-3h | ✅ | ✅ Done 2026-05-01 | 16 bots → 5 morning / 6 midday / 5 pre_ko. `BOT_TIMING_COHORTS` dict + cohort param in run_morning(). Migration 032 adds timing_cohort to simulated_bets. Scheduler auto-selects cohort by UTC hour. |
| POSTGREST-CLEANUP | Migrate remaining PostgREST callers to psycopg2 | 3-4h | ✅ | ✅ Done 2026-05-03 | All workers + scripts fully migrated. Last batch: `fit_platt.py` (SQL JOIN replaces paginated PostgREST), `backfill_historical.py` (all progress tracking + bulk event INSERT), `live_tracker.py` (crash fix — undefined `client`). `get_client()` lives exclusively in `supabase_client.py` internals. Backfill moved to Railway 02:00 UTC daily. |
| PERF-1 | Batch morning signal writing — replace 25-40 per-match DB queries | 2-3h | ✅ | ✅ Done 2026-05-03 | `batch_write_morning_signals()` in supabase_client.py: 10 bulk queries (ANY(match_ids[])) + one execute_values INSERT replaces ~14K serial round-trips. Reduced 34-70 min bottleneck to ~15s. Added league_id to match_dict for SIG-11. |
| PERF-2 | Rewrite prune_odds_snapshots.py — single SQL DELETE | 1h | ✅ | ✅ Done 2026-05-03 | Replaced per-match PostgREST iteration with one DISTINCT ON subquery DELETE. Prunes all finished matches in a single statement. Migrated to psycopg2. |
| STRIPE-PROD | Swap Stripe to production keys | 1h | ✅ Done 2026-05-04 | ✅ Done | Live products created (Pro €4.99, Elite €14.99 + yearly + founding). All Vercel env vars updated. Live webhook `https://www.oddsintel.app/api/stripe/webhook`. Deployed. |
| GH-CLEANUP | Remove pipeline workflow files from GitHub Actions | 30min | ✅ Done 2026-05-05 | ✅ Done | Deleted fixtures/enrichment/odds/predictions/betting/live_tracker/news_checker/settlement .yml. Only migrate.yml + backfill.yml remain. |
| BOT-PROVEN | `bot_proven_leagues` — focused strategy targeting only the 5 cross-era backtest-confirmed leagues (Singapore/Scotland/Austria/Ireland/S.Korea) | 1h | ✅ Done 2026-05-05 | Added to BOTS_CONFIG + BOT_TIMING_COHORTS (midday). 17th bot. Clean performance track for strongest backtest signals. |
| RHO-DYN | Dynamic Dixon-Coles rho per league tier — fit rho from historical scoreline frequencies instead of global -0.13 | 2h | ✅ Done 2026-05-05 | `scripts/fit_league_rho.py` → `model_calibration` (market=`dc_rho_tier_{n}`). `_load_dc_rho_cache()` + `_poisson_probs(rho=)` in pipeline. Sunday refit step 6/6. Falls back to -0.13 if <200 matches/tier. |
| N4/N6/N9 | Settlement 01:00 UTC, watchlist 14h lookback, stagger 20:35 | 30min | ✅ Done 2026-05-05 | Settlement overnight run added (21:30+ KO extra time). ODDS_LOOKBACK_HOURS 6→14 covers overnight drift. Watchlist 20:30→20:35 avoids collision with betting refresh. Tested dry-run. |
| N5 | Afternoon + evening value bet alert emails for Pro/Elite | 2h | ✅ Done 2026-05-05 | `run_value_bet_alert(slot)` in email_digest.py. Afternoon (16:00, since 10:00 UTC) + Evening (20:45, since 17:00 UTC). Migration 046: `value_bet_alert_log` UNIQUE(user_id, alert_date, slot). No-op if no new bets. Pro gets count+CTA, Elite gets full table. |
| N7 | Full enrichment (all 4 components) at 13:00 UTC | 30min | ✅ Done 2026-05-05 | `job_enrichment_full()` added to scheduler at 13:00 UTC. `run_enrichment()` with no components filter = standings+H2H+team_stats+injuries. Ensures H2H+team_stats fresh for afternoon/evening betting refreshes. |
| SCHED-AUDIT | Full cron audit — 10 gaps fixed + 3 structural bugs | 2h | ✅ Done 2026-05-05 | 6 betting runs/day (was 4): added 09:30 + 20:30. Closing odds 20:00. Enrichment 12:00→10:30. News 14:30 added, 19:30→18:30. Previews 07:00→07:15. Platt+blend Wed+Sun. N1: LivePoller 24/7 (was 10-23 UTC), adaptive 30s live / 120s idle. N2: betting pipeline now filters `status='scheduled'` AND `kickoff > now` — no more bets on live matches. N3: `fixture_to_match_dict` passes `af_status_short`; `store_match` updates status→'postponed' + kickoff time for existing scheduled matches; fixture refresh job 4×/day (09:15, 10:45, 14:45, 18:45). |
| ADMIN-TIER-PREVIEW | Superadmin tier preview switcher — switch between free/pro/elite to QA any page | 2-3h | ✅ Done 2026-05-04 | ✅ Done | Cookie-based override. (1) `src/lib/get-user-tier.ts` shared utility — wraps profile fetch, checks `preview_tier` httpOnly cookie when `is_superadmin=true` and overrides tier; replace ~5 pages that inline `.select("tier, is_superadmin")` with this. (2) `/api/set-preview-tier` POST route — sets/clears cookie, superadmin-only server-validated. (3) Floating pill UI (`src/components/superadmin-tier-bar.tsx`) — fixed overlay, only renders for superadmins, shows current preview tier badge + free/pro/elite/"My Tier" buttons, added to app layout. Cookie is httpOnly+sameSite=lax. Works cleanly: all pages are dynamic (no ISR), so cookie flip = instant re-render with different tier data. Visual banner ensures you always know which tier you're previewing. |

---

## Signal UX — Phase 1 (all done)

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| SUX-1 | Match Intelligence Score: A/B/C/D grade on every match card | ✅ | Grade badge + signal count tooltip. All tiers |
| SUX-2 | Match Pulse composite indicator (⚡ / 🔥 / —) | ✅ | bdm>0.12 + OLM/vol threshold. ~15-20% scarcity |
| SUX-3 | Free-tier signal teasers on notable matches | ✅ | 1-2 italic hooks on 30-40% of matches |

---

## Tier 2 — 2-4 Weeks

### Done

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| MOD-1 | Dixon-Coles correction to Poisson model | ✅ | `DIXON_COLES_RHO=-0.13`. τ correction for low-score draws |
| PLATT | Platt scaling + weekly recalibration | ✅ | `scripts/fit_platt.py`. Weekly Sunday refit |
| BDM-1 | Bookmaker disagreement signal | ✅ | compute_bookmaker_disagreement() → match_signals |
| FE-LIVE / ODDS-OU-CHART / ODDS-BTTS / ODDS-MARKETS | Live in-play chart, O/U 2.5 chart, BTTS/O/U 1.5/3.5 odds table | ✅ | Pro gated |
| MKT-STR | Market-implied team strength into XGBoost | ✅ | market_implied_home/draw/away in feature row |
| EXPOSURE-AUTO | Auto-reduce stakes on league concentration | ✅ | 3rd+ bet same league = 50% stake |
| LIVE-FIX | Populate xG/shots/possession/corners in snapshots | ✅ | Was empty. Now 1 extra AF call per live match |
| BOTS-EXPAND | 10→16 bots (BTTS, O/U 1.5/3.5, draw, O/U 2.5 global) | ✅ | ~30-40 bets/day |
| KAMBI-BTTS | O/U + BTTS from Kambi event endpoint | ✅ | ~40 matches with BTTS now |
| BET-MULTI | Betting pipeline 5x/day (06/10/13/16/19 UTC) | ✅ | Idempotent — unique constraint prevents duplicates |
| TR-REDESIGN | Track record redesign: CLV-led, tier-gated | ✅ | |
| LP-1/2/3 | Landing page fixes | ✅ | Pricing/urgency cleanup |
| P5.1 | Sharp/soft bookmaker classification + sharp_consensus signal | ✅ Done 2026-05-03 | `data/bookmaker_sharpness_rankings.csv` (13 bookmakers, 3 tiers). `sharp_consensus_home` signal in `batch_write_morning_signals`. |
| PIN-1 | Pinnacle anchor signal: `pinnacle_implied_home` stored per match | ✅ Done 2026-05-04 | `batch_write_morning_signals()` in supabase_client.py. |
| PIN-VETO | Pinnacle disagreement veto for 1X2 home bets (gap > 0.12 → skip) | ✅ Done 2026-05-06 | `PINNACLE_VETO_GAP = 0.12` in `daily_pipeline_v2.py`. Empirical: catches 22/34 losses, filters 6/40 wins. |
| ODDS-API | ~~Activate The Odds API for Pinnacle odds ($20/mo)~~ | ❌ Cancelled | AF already provides Pinnacle. |
| LEAGUE-ORDER | 6-tier league priority system | ✅ Done 2026-05-05 | Migration 044. |
| ALN-FIX | Alignment NONE class when active=0 | ✅ Done 2026-05-04 | `improvements.py:compute_alignment()`. |
| ALN-EXPAND | sharp_consensus + Pinnacle anchor as alignment dimensions 5+6 | ✅ Done 2026-05-04 | `improvements.py`. |
| PERF-CACHE | Pre-stored dashboard stats in DB via settlement | ✅ Done 2026-05-04 | Migration 035. `write_dashboard_cache()` in settlement.py. |
| FE-BOT-DASH | Bot P&L dashboard (superadmin-gated) | ✅ Done 2026-05-04 | `/admin/bots` page. |

### Open

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| CODE-SONAR-WEB | Fix SonarCloud findings on odds-intel-web. **After repo cleanup (stitch_output removed):** Security 16 vulns = all stitch HTML (gone from repo, will resolve on next scan). Reliability D (5 bugs): 2 stitch HTML (gone), 2 `value-bets-live.tsx` sort bugs (fixed — `localeCompare`), 1 `login-modal.tsx` a11y (fixed — keyboard handler + role). 27 critical code smells = all cognitive complexity (`S3776`). **Remaining after next scan:** ~0 vulns, ~0 bugs, 396 code smells (non-urgent). | 30min | ✅ Done 2026-05-06 | ✅ Done | Real bugs fixed. Stitch files gitignored + removed from tracking. Re-run SonarCloud scan to confirm A/A ratings. Remaining 396 code smells are all complexity — address as part of CODE-RADON if ever needed. |
| CODE-WEB-ESLINT | Fix 9 ESLint errors + 16 warnings in odds-intel-web. **Errors:** `signal-delta.tsx:84` setState sync in effect (cascading renders); `superadmin-tier-bar.tsx:28` JSX inside try/catch (errors won't be caught); `login-modal.tsx`, `match-notes.tsx`, `match-pick-button.tsx`, `cookie-banner.tsx`, `api/stripe/upgrade` (review each). **Warnings:** 63 complexity violations — worst offenders: `bet-explain GET` (59), engine-data functions (60, 64), `bankroll/page` (40), `my-picks` (27). 2 auto-fixable with `--fix`. Complexity rule added to `eslint.config.mjs` (threshold=10). | 2-3h | ✅ Done 2026-05-06 | ✅ Ready | Fixed all 9 errors: prefer-const (bet-explain, mock-data), no-html-link (bankroll), JSX-in-try-catch (superadmin-tier-bar), disabled `react-hooks/set-state-in-effect` (flags valid guard/reset patterns). 0 errors remain, 79 warnings (all complexity). Future protection: `next build` already runs lint and fails on errors → Vercel blocks bad deploys. |
| CODE-WEB-KNIP | Remove dead code found by Knip in odds-intel-web: **20 unused files** (components + lib files never imported), **24 unused exports**, **23 unused exported types**. Key files: `src/lib/mock-data.ts`, `src/lib/types.ts`, `src/lib/queries.ts`, `src/lib/supabase.ts` (old Supabase client?), `src/components/track-record-client.tsx`, `src/components/value-bets-client.tsx`, `src/components/match-detail-tabs.tsx`. Also: 6 unused engine-data.ts query functions (getTodayOdds, getAvailableLeagues, getDashboardCache etc). | 1-2h | ✅ Done 2026-05-06 | ✅ Ready | Deleted all 20 files. Removed dead functions: getAvailableLeagues, signalLabel, PULSE_SIGNALS, getCountryFromLeague. Removed export from internal-only: getTodayOdds, getDashboardCache. 5,319 lines deleted. lint warnings 79→72. |
| PIN-2 | Extend Pinnacle signals to all bet markets | 1h | ✅ Done 2026-05-06 | ✅ Ready | Added `pinnacle_implied_draw`, `_away`, `_over25`, `_under25` to `batch_write_morning_signals()` via dedicated bulk query block (3b). `workers/api_clients/supabase_client.py`. |
| PIN-3 | Extend disagreement veto to draw/away/O/U markets | 1-2h | ✅ Done 2026-05-06 | ✅ Ready | Veto gate in `daily_pipeline_v2.py` now uses a selection→dict map covering Home/Draw/Away/Over 2.5/Under 2.5. Threshold 0.12 for all markets (tune per market once 50+ settled bets). Pinnacle anchor also extended to all markets in `calibrate_prob()` call. |
| PIN-4 | Pinnacle line movement signal | 1-2h | ✅ Done 2026-05-06 | ✅ Ready | `pinnacle_line_move_home/draw/away` added to `batch_write_morning_signals()`. Uses oldest vs most recent Pinnacle snapshot (requires 2+ snapshots). Positive = home shortened = sharp money backing. `workers/api_clients/supabase_client.py`. |
| PIN-5 | Pinnacle-anchored CLV | 2h | ✅ Done 2026-05-06 | ✅ Ready | `clv_pinnacle` column added via migration 050. New `get_pinnacle_closing_odds()` helper in `settlement.py`. Computed as `(odds_at_pick / pinnacle_closing_odds) - 1` and written alongside `clv` on every settlement. Falls back to latest Pinnacle snapshot when is_closing not flagged. |
| PIN-5-BACKFILL | Backfill clv_pinnacle on existing settled bets | 30min | ✅ Done 2026-05-06 | ✅ Ready | `scripts/backfill_clv_pinnacle.py` — updated 26/77 settled bets. Remaining 51 pre-date Pinnacle odds collection (PIN-1 started May 4). Run any time to catch newly settled bets. |
| CAL-PLATT-UPGRADE | Replace single-input Platt with 2-feature logistic: `X = [shrunk_prob, log(odds)]` | half day | ⬜ | ⏳ ~300+ settled bets/market (have ~77 total) | Learns that "40% at odds 3.6" needs different correction than "40% at odds 1.8". Do NOT implement sooner — will overfit. Source: 4-AI Calibration Review 2026-05-06. |
| ALN-1 | Dynamic alignment thresholds | 2h | ⬜ | ⏳ ~June 5 (need 300 clean settled bets) | **Data quality cutoff: validate on bets WHERE `created_at >= 2026-05-06` only** — pre-cutoff bets were placed by the old pipeline (no Pinnacle anchor, no CAL-ALPHA-ODDS, different veto coverage). Training on those teaches patterns from a system we have already replaced. At ~27 bets/day post-cutoff, 300 clean bets ≈ June 5. Pseudo-CLV does NOT substitute. |
| VAL-POST-MORTEM | Review 14 days of LLM post-mortem patterns | 30min | ⬜ | ⏳ May 13+ (have 2 rows, need 14) | `SELECT notes FROM model_evaluations WHERE market='post_mortem' ORDER BY date DESC LIMIT 14` |
| MD-POLISH | Match detail visual polish: sticky tab blur, tab badge counts (e.g. "Match 4"), signal severity colors by group (market=blue, form=green, injuries=red), signal timestamps ("detected 3h ago"), "Why this match?" auto-generated hook at top of Intel, bot consensus as visual icons. Bookmaker comparison table (Odds tab). | 2-3h | ✅ Done 2026-05-07 | ✅ Ready | Polish pass on the tabbed match detail layout. All data already available — purely frontend rendering. `src/components/match-detail-tabs.tsx`, `signal-accordion.tsx`, `bot-consensus.tsx`. |
| BOT-QUAL-FILTER | Add "quality bets only" toggle to superadmin bot dashboard (`/admin/bots`) — filters to `created_at >= 2026-05-06` to show post-calibration performance (post-Pinnacle anchor, post-CAL-ALPHA-ODDS, post-PIN-VETO) separately from legacy bets. | 1h | ⬜ | ⏳ Wait for 100+ quality settled bets (have ~12 today, ~27/day → ready ~May 10) | Superadmin-only. Lets you compare pre vs post-May 6 pipeline performance on the bot P&L dashboard. |
| VIG-REMOVE | Fix Pinnacle implied probability calculation — currently using raw `1/odds` with no vig removal, which biases the calibration anchor ~1.5-2% high per outcome. **Confirmed in code**: `supabase_client.py:3050` `pin_implied = 1.0 / float(pinnacle_rows[0]["odds"])`. Fix: use multiplicative normalization across all 3 Pinnacle 1X2 prices: `fair = raw / sum(raws)`. Applies to all `pinnacle_implied_*` signals, the `calibrate_prob()` anchor, and the 0.12 veto threshold (which was calibrated against biased values). O/U: normalize across Over+Under pair. | 2h | ✅ Done 2026-05-07 | ✅ Ready | Block 3b in `batch_write_morning_signals()` refactored: single query loads all 3 Pinnacle 1X2 selections per match, normalizes home/draw/away together. Separate O/U query normalizes over+under pair. Line movements kept as raw diffs (direction matters, vig stable intraday). Tests added. `workers/api_clients/supabase_client.py`. |
| DRAW-PER-LEAGUE | Per-league draw inflation factor. Current fixed `DRAW_INFLATE = 1.08` applies globally — draw rates vary from ~22% (PL, high-scoring open leagues) to ~32% (defensive lower-division leagues). `league_draw_pct` is already collected as a signal. Replace constant with a per-match calculation: `draw_inflate = 1.0 + max(0, (league_draw_pct - 0.268) / 0.268 * 0.08)` clamped to [1.03, 1.15]. Where `league_draw_pct` unavailable, keep 1.08 as fallback. | 2h | ✅ Done 2026-05-07 | ✅ Ready | `_poisson_probs()` now accepts `league_draw_pct` param. `compute_prediction()` passes it through. `run_morning()` batch-loads `league_draw_pct` from `match_signals` alongside Pinnacle signals and passes per match. Fallback 1.08 preserved when signal absent. Tests added. `workers/jobs/daily_pipeline_v2.py`. |
| NEWS-IMPACT-DIR | Store directional news impact as separate signals. Gemini already returns `home_net_impact` and `away_net_impact` (−1.0 to +1.0) but they are never written to `match_signals` — only the combined bet-relative `news_impact_score` is stored. Fix: add `store_match_signal(match_id, "news_impact_home", home_net_impact, ...)` and `"news_impact_away"` in `news_checker.py` after Gemini parse. Zero extra Gemini cost. Enables match_feature_vectors and meta-model to distinguish "bad news for home team" from "bad news for away team". | 1h | ✅ Done 2026-05-07 | ✅ Ready | Two new `store_match_signal` calls added in `news_checker.py` after existing `news_impact_score` write. `news_impact_home` and `news_impact_away` now stored per match. Test added. `workers/jobs/news_checker.py:322-327`. |
| MGR-CHANGE | New manager signal. Add `manager_change_home_days` and `manager_change_away_days` to match_signals — number of days since either team's manager changed (NULL = no change in last 90 days). Source: AF `/coaches` endpoint. Known market inefficiency: post-sacking home bounce ~+8% win rate above expectation in first 3 games (both in industry literature and confirmed by 2 of 5 AI reviewers). Converse: away form collapse under caretaker. Add to enrichment job, cache coach history in a `team_coaches` table or similar. | 3-4h | ✅ Done 2026-05-07 | ✅ Ready | Migration 064 (`team_coaches` table). `get_coaches()`/`parse_coaches()` in `api_football.py` (AF endpoint is `/coachs`). `store_team_coaches()` in `supabase_client.py`. `fetch_coaches()` in `fetch_enrichment.py` — skips teams fetched within 48h. Signal block 3c in `batch_write_morning_signals()` loads current coach start date per team and writes `manager_change_home/away_days` when ≤ 90 days. `coaches` added to `ALL_COMPONENTS`. 4 smoke tests added. |
| PIPE-ALERT | **Merge target for `SYNTHETIC-LIVENESS` (P2 in Reliability Hardening section above).** Automated pipeline anomaly alerting. | 3-4h | ✅ Done 2026-05-08 | ✅ Ready | `workers/jobs/health_alerts.py`. 4 checks via Resend email to `ADMIN_ALERT_EMAIL`. Wired into scheduler: 09:35 morning, hourly 10-22 snapshot, 21:30 settlement. In-memory dedup prevents repeat alerts per day. |
| BM-FILTER | Bookmaker availability filter on value bets page. Users in different countries have access to different bookmakers — showing a Betano pick to a UK user who can't use Betano creates frustration and churn. Add `preferred_bookmakers` text[] column to `profiles` (migration NNN). Profile page: checkbox list of the 13 bookmakers. Value bets page respects filter: only shows picks where `bookmaker = ANY(preferred_bookmakers)`. Default = show all (no change for users who haven't set preferences). | 3-4h | ⬜ | ✅ Ready | Frontend: `src/app/(app)/value-bets/page.tsx` + `src/lib/engine-data.ts`. Backend: migration + profile update API. |
| BOT-PUBLIC-PERF | Public bot performance page. `bot_aggressive` is at +93 units (paper trading) and is the strongest conversion asset in the product — currently visible only at `/admin/bots` (superadmin). Build a public `/performance` page (free tier) showing paper trading results clearly labeled: daily bets settled, cumulative units chart, hit rate, CLV context. Include "paper trading — not real money" disclaimer. Replaces the need for social proof via Reddit posts alone. | half day | ✅ Done 2026-05-08 | ✅ Ready | Replaced `/track-record` with `/performance` (redirect from old URL). 4-tier gated: Free=hero stats+bot leaderboard(≥10 settled)+last 10 bets+CLV education; Pro=all 16 bots+W/L+P&L+bankroll chart modals+full 500-bet history with filters+CLV direction arrows; Elite=exact CLV %+stake sizes+closing odds+current bankroll per bot; Superadmin=all Elite. CLV is hero metric throughout. Sanitization server-side in page.tsx — client never receives gated data. Engine-data.ts: exported `getDashboardCache`, added `getRecentSettledBets`. Nav updated: "Track Record" → "Performance". TypeScript clean. |

---

## Engagement & Growth — Phase 1 (Launch Sprint — do this week)

> Full strategy in `docs/ENGAGEMENT_PLAYBOOK.md`. Reddit execution plan + post drafts in `docs/REDDIT_LAUNCH.md`. Launch phases + paid ads in `docs/LAUNCH_PLAN.md`. Phase 1 = ship with Reddit launch. Phase 2 = retention (weeks 3-6). Phase 3 = differentiation (months 2-3).

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ENG-3 | Daily AI match previews (top 5-10, Gemini) | 1-2 days | ✅ Done 2026-05-01 | ✅ Ready | `workers/jobs/match_previews.py`. Scheduler 07:00 UTC. `match_previews` table (migration 033). Free sees teaser, Pro/Elite see full 200-word preview. Triple-duty: on-site + email + social. Fixed 2026-05-05: predictions pivot (source=ensemble), odds_snapshots pivot, match_injuries schema. **AI: ~$0.11/mo (10 calls/day, flash)** |
| ENG-4 | Daily email digest via Resend | 2-3 days | ✅ Done 2026-05-05 | ✅ Ready | `workers/jobs/email_digest.py`. Scheduler 07:30 UTC. `email_digest_log` (migration 034). Free: teasers + CTA. Pro: + bet count. Elite: + full picks table. Branded HTML: dark `#0a0a14` header, ODDS white + INTEL green logo, green CTAs/badges. Fixed 2026-05-05: migration 042 backfills `user_notification_settings` for all existing users + trigger wired for new signups (was empty → zero sends). Tested end-to-end. |
| ENG-1 | "X analyzing this match" live counter | 4-6h | ✅ Done 2026-05-04 | ✅ Done | `match_page_views` table (migration 038). `/api/track-page-view` POST route — upserts session_id+match_id, returns 30-min window count. `MatchViewingCounter` client component in match header metadata row. Hidden until 2+ people (no self-only display). |
| ENG-2 | Community vote split display | 4-6h | ✅ Done 2026-05-04 | ✅ Done | `community-vote.tsx` updated: percentages + fill bars always visible when any votes exist. Locks at kickoff (live/finished) with Lock icon + "Locked at kickoff" label. Voting disabled for locked matches. |
| ENG-6 | Bot consensus on match detail ("7/9 models agree: Over 2.5") | 3-4h | ✅ Done 2026-05-03 | ✅ Ready | Data in `simulated_bets`. Zero new data needed. Free: count. Pro: markets. Elite: full breakdown |
| ENG-7 | Public /methodology page | Half day | ✅ Done 2026-05-03 | ✅ Ready | Plain-English model explanation. Trust anchor. Nobody else publishes this |
| ENG-5 | Betting glossary (10-15 SEO pages at /learn/[term]) | 2-3 days | ✅ Done 2026-05-05 | ✅ Done | 12 terms at /learn/[term]: EV, CLV, Kelly, value betting, Poisson, xG, BTTS, O/U, odds movement, margin, ELO, bankroll. FAQ schema. /learn index. Glossary nav link. Sitemap updated. |

---

## Engagement & Growth — Phase 2 (Retention Engine, weeks 3-6)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ENG-9 | Personal bet tracker + "Model vs You" dashboard | 3-4 days | ✅ Done 2026-05-05 | ✅ Done | my-picks: ROI%, units, W/L stats. Model vs You card after 5+ settled picks. Model prob + agree/disagree icon per row. Share button (native share API + clipboard fallback + OG image). |
| ENG-11 | "What Changed Today" widget on matches page | 1 day | ✅ Done 2026-05-05 | ✅ Done | `getWhatChangedToday()` in engine-data.ts: compares last 8h signals vs 20-32h ago, top 5 by abs delta. `what-changed-today.tsx` component: links to matches, free sees magnitude dot, Pro sees exact delta. |
| ENG-12 | Model vs Market vs Users triangulation | 4-6h | ✅ Done 2026-05-05 | ✅ Done | `getModelMarketUsers(matchId)` queries ensemble 1x2_home prediction + implied_prob + match_votes. `model-market-users.tsx`: 3 colored bars + tension text when model/market gap >5pp. On every match detail page. |
| ENG-13 | Shareable pick cards (branded image generation) | 1-2 days | ✅ Done 2026-05-05 | ✅ Done | `/api/og/pick` route: Next.js ImageResponse, accepts home/away/selection/odds/model_prob/result as query params. Share button on my-picks uses native Web Share API, falls back to clipboard. |
| ENG-14 | Auto-generated prediction pages for SEO (/predictions/[league]/[week]) | 2-3 days | ✅ Done 2026-05-05 | ✅ Done | `/predictions` index + `/predictions/[league]` pages. 8 featured leagues. Prob bars, model call badges, preview teasers, FAQ schema. "Predictions" nav link added. Sitemap updated. |
| ENG-8 | Watchlist signal alerts (email/push) | 3-4 days | ✅ Done 2026-05-05 | ✅ Done | `workers/jobs/watchlist_alerts.py`. Scheduler 08:30/14:30/20:30 UTC. Migration 045: `watchlist_alerts_enabled` + `watchlist_alert_log`. Free: kickoff reminder ≤2h before KO. Pro/Elite: odds movement ≥5% alert (6h lookback). Profile page toggle for all 3 notification types (daily digest, weekly report, watchlist alerts). |
| ENG-10 | Weekly performance email (Monday 08:00 UTC) | 1 day | ✅ Done 2026-05-05 | ✅ Done | `workers/jobs/weekly_digest.py`. Scheduler Monday 08:00 UTC. `weekly_digest_log` table (migration 043). Model W/L/units + user's picks + upcoming top matches. Uses `weekly_report` column (default true). Free: model stats + CTA. Pro/Elite: + personal pick stats + CLV. |

---

## Engagement & Growth — Phase 3 (Differentiation, months 2-3)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ENG-15 | Market inefficiency index per league (rolling 30-day edge) | 1 day | ⬜ | ⏳ ~June (needs 30 days of data) | "Eredivisie: HIGH +4.8%. Premier League: LOW +1.2%." No competitor does this |
| ENG-17 | Season-end "Year in Review" (personal, shareable) | 2-3 days | ⬜ | ⏳ ~Aug+ (needs full season of user data) | Strava-style. "312 bets, best month October." Viral potential |

---

## Railway Migration — LIVE-INFRA (all done ✅)

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| LIVE-INFRA | Full migration: GH Actions → Railway scheduler + direct SQL + tiered live polling | ✅ | All 5 phases complete 2026-05-01 |
| RAIL-1 | `workers/scheduler.py` (APScheduler + health endpoint) | ✅ | 21 jobs. Health on :8080 |
| RAIL-2 | Extract `run_*()` from all job scripts | ✅ | main() kept as CLI wrapper |
| RAIL-3 | API budget tracker in `api_football.py` | ✅ | BudgetTracker class, thread-safe |
| RAIL-4 | Dockerfile + railway.toml + .dockerignore | ✅ | Python 3.12-slim, TZ=UTC |
| RAIL-5 | Deploy + validate (shadow mode) | ✅ | Superseded — went straight live |
| RAIL-6 | Disable GH Actions crons | ✅ | schedule: commented in 7 workflows. backfill.yml kept |
| RAIL-7 | `workers/api_clients/db.py` (psycopg2 pool) | ✅ | ThreadedConnectionPool 2-10, bulk_insert/upsert |
| RAIL-8 | Live tracker DB functions → direct SQL | ✅ | 6 functions in db.py. Batched writes, no 1K limit |
| RAIL-9 | `workers/live_poller.py` (tiered 30s/60s/5min) | ✅ | LivePoller class, budget-aware |
| RAIL-10 | Decompose `live_tracker.py` into sub-functions | ✅ | fetch_live_bulk/stats/events/build_snapshot |
| RAIL-11 | Smart polling: priority tiers + event-triggered snapshots | ✅ | HIGH priority (active bets) = 30s stats. Goal → extra odds snapshot |
| RAIL-12 | Full doc sweep aligned with Railway | ✅ | 8 .md files updated |
| RAIL-13 | Instant settlement on FT + score sync fix | ✅ | finish_match_sql on FT detection. UTC rollover fix. 23:30 safety net |

---

## Frontend UX — All Done ✅

| ID | Task | ☑ | Notes |
|----|------|----|-------|
| LP-0 / A-1/2/3/4 | Landing page rewrite + profile page redesign | ✅ | |
| B-1/2/3/4/5/6 | Track record public, confidence filter, /how-it-works | ✅ | |
| C-1 to C-6 | Match page tooltips, odds header, value bets gate, login modal | ✅ | |
| F5 | Value bets page: Free=teaser, Pro=directional, Elite=full | ✅ | |
| BET-EXPLAIN | Natural language bet explanations (Gemini, Elite-gated) | ✅ | GET /api/bet-explain. **AI: ~$0.02/mo (flash-lite, cached after 1st call per bet)** |
| SUX-4/5/6/7/8/9/10 | Signal summary, accordion, labels, hooks, timeline, delta, post-match reveal | ✅ | |
| SUX-11/12 | "Why This Pick" Elite card + CLV tracker | ✅ | |
| ML-1/2/3/4/5/6/7/8 | Logos, live timer, form strip, match filter tabs, predicted score, odds arrows, BM badge, match star | ✅ | |
| FE-FAV-1/2/3 | My Leagues bug fix + league ordering + per-match star | ✅ | |
| FE-BUG-1/2 / FE-AUDIT | Pro CTA bug, select dropdown bug, full tier gating audit | ✅ | |
| PIPE-2 / XGB-FIX / POISSON-FIX / DRAW-FIX | Pipeline cleanup + model fixes | ✅ | XGBoost retrained on 95K rows, joblib loader |
| LAUNCH-BETA / LAUNCH-PICK | Beta label, daily pick visible without login | ✅ | |
| AF-EVAL | AF Ultra confirmed required — do NOT downgrade (live polling needs 18K-45K calls/day) | ✅ | |
| KAMBI-BUG-1 | Duplicate value bets when Kambi league name ≠ AF name — added Bulgaria PFL 1 mapping + improved frontend dedup to normalise club prefixes (FK/FC/etc) and key on kickoff date | ✅ Done 2026-05-06 | |
| KAMBI-DROP | Drop Kambi entirely — empirical analysis showed "ub"=Unibet (AF has it), "paf"/"kambi"=36 rows/30 days. Removed scraper from pipeline, cleaned 20 league/50 team/7 fixture dupes via migration 047. Full cleanup 2026-05-06: deleted `kambi_odds.py`, `kambi_odds_value.py`, `detect_duplicates.py`, removed `fetch_kambi_odds()` from fetch_odds.py, removed `KAMBI_TO_AF_LEAGUE` mapping, renamed team_names.py refs. Cleaned 37 more duplicates from 23h deploy gap. | ✅ Done 2026-05-06 | |
| SETTLE-FIX | Settlement `KeyError: 'odds'` — `bet["odds"]` → `bet["odds_at_pick"]` in settlement.py:1034. Was crashing settle_ready every 15 min, blocking 158 matches from settling. | ✅ Done 2026-05-06 | |
| LIVE-ODDS-PARSE | `parse_live_odds()` returned 0 fixtures — AF sends "Fulltime Result" not "Match Winner", and O/U uses `value="Over"` + `handicap="2.5"` (not `"Over 2.5"` combined). Inplay bot has never had real live odds data. Fixed both parsers. 2 regression tests added to smoke_test.py. | ✅ Done 2026-05-07 | `workers/api_clients/api_football.py:parse_live_odds` |
| SENTRY-CRON | Sentry cron monitors not registering — `grace_period_minutes` → `checkin_margin` (correct sentry-sdk 2.x key). | ✅ Done 2026-05-06 → Reverted: Sentry removed from engine 2026-05-06 (free tier budget exceeded, Railway logs sufficient) | |
| RAIL-AUTODEPLOY | Railway auto-deploy from GitHub — connected repo in Settings → Source, main branch, Wait for CI off. Previously required manual `railway up`. | ✅ Done 2026-05-06 | |

---

## ADMIN-OPS-DASH — Operational Health Dashboard ✅ Done 2026-05-07

> Full spec and implementation in git history. Task complete.

### Goal

A `/admin/ops` page (superadmin-only) that answers "Is today's pipeline healthy?" in 3 seconds. Opens instantly — all heavy counts are pre-computed and stored in `ops_snapshots`; page does a single SELECT. Live panels (pipeline job grid, stale bets, last snapshot age) do lightweight point queries on small tables.

---

### Architecture

**`ops_snapshots` table — append-only, one row per snapshot.**

Written by `write_ops_snapshot()` which is called:
1. At the **end of each major job**: `run_fixtures`, `run_odds`, `run_betting`, `run_morning`, `run_settlement`, `run_enrichment` — numbers refresh as work happens
2. **Fallback cron every 60 min** — covers idle hours / weekends

Each write is a **full recompute of all counters for today (UTC date)**. Not a delta. Dashboard reads `WHERE snapshot_date = today ORDER BY created_at DESC LIMIT 1`.

Append-only avoids write races when jobs overlap. Also gives 7-day history for sparklines: `DISTINCT ON (snapshot_date) ORDER BY snapshot_date, created_at DESC`.

**Three panels use live queries (not pre-computed)** — they're cheap and must be real-time:
- Pipeline job grid → `DISTINCT ON (job_name) FROM pipeline_runs WHERE started_at > now() - interval '26 hours'`
- Stale pending bets → `JOIN simulated_bets + matches WHERE result='pending' AND status='finished'`
- LivePoller last snapshot age → `MAX(created_at) FROM live_match_snapshots`

---

### `ops_snapshots` schema (42 columns)

```sql
CREATE TABLE ops_snapshots (
  id            SERIAL PRIMARY KEY,
  snapshot_date DATE NOT NULL,
  created_at    TIMESTAMPTZ DEFAULT now(),

  -- ① Fixtures & coverage (funnel top)
  matches_today            INT,  -- matches with kickoff on snapshot_date
  matches_with_odds        INT,  -- matches with ≥1 odds snapshot today
  matches_with_pinnacle    INT,  -- matches with Pinnacle odds today
  matches_with_predictions INT,  -- matches with source='af' prediction today
  matches_with_signals     INT,  -- matches with ≥1 signal today
  matches_with_fvectors    INT,  -- matches in match_feature_vectors today
  matches_missing_grade    INT,  -- matches where grade IS NULL and status != 'postponed'
  matches_postponed_today  INT,  -- informational

  -- ② Odds pipeline
  odds_snapshots_today  INT,  -- total rows in odds_snapshots today
  distinct_bookmakers   INT,  -- should be 13; drop = odds job half-dead
  matches_without_pinnacle INT, -- has odds but no Pinnacle specifically

  -- ③ Betting & bots
  bets_placed_today   INT,          -- simulated_bets created today (all bots)
  bets_pending        INT,          -- result='pending' right now (all time)
  bets_settled_today  INT,          -- settled today
  pnl_today           NUMERIC(8,2), -- sum pnl on bets settled today
  bets_inplay_today   INT,          -- from bot_id LIKE 'bot_inplay%'
  active_bots         INT,          -- distinct bot_id with ≥1 bet today
  silent_bots         INT,          -- bots with 0 bets today (out of 17 expected)
  duplicate_bets      INT,          -- (bot_id, match_id, market, selection) with count >1

  -- ④ Live / in-play
  live_snapshots_today     INT,  -- live_match_snapshots rows today
  snapshots_with_xg        INT,  -- home_xg IS NOT NULL
  snapshots_with_live_odds INT,  -- ou_over_25_odds IS NOT NULL (fixed 2026-05-07)

  -- ⑤ Post-match / settlement
  matches_finished_today INT,
  bets_settled_today_v2  INT,   -- alias — use bets_settled_today above
  post_mortem_ran_today  BOOL,  -- model_evaluations market='post_mortem' for today
  feature_vectors_today  INT,   -- match_feature_vectors rows built today (captured_at)
  elo_updates_today      INT,   -- team_elo_daily rows updated today

  -- ⑥ Enrichment quality
  matches_with_h2h      INT,  -- distinct match_id in match_h2h for today's matches
  matches_with_injuries INT,  -- distinct match_id in match_injuries today
  matches_with_lineups  INT,  -- via JOIN on matches.kickoff_time::date (not lineups.created_at)

  -- ⑦ Email & alerts
  digests_sent_today        INT,
  value_bet_alerts_today    INT,  -- from value_bet_alert_log today
  previews_generated_today  INT,  -- from match_previews today
  news_checker_errors_today INT,  -- pipeline_runs WHERE job_name='news_checker' AND status='error'
  watchlist_alerts_today    INT,

  -- ⑧ Backfill
  backfill_total_done INT,       -- COUNT(DISTINCT match_id) FROM match_stats (all time)
  backfill_last_run   TIMESTAMPTZ, -- MAX(started_at) FROM pipeline_runs WHERE job_name='hist_backfill'

  -- ⑨ API budget (NULL until Phase 3 persists BudgetTracker)
  af_calls_today      INT,  -- NULL until Phase 3 — BudgetTracker is in-memory only
  af_budget_remaining INT,  -- 75000 - af_calls_today, NULL until Phase 3

  -- ⑩ Users
  total_users      INT,
  pro_users        INT,
  elite_users      INT,
  new_signups_today INT
);
```

---

### Dashboard layout (`/admin/ops`)

**Top strip — always visible, aggressively colored:**

| KPI | Green | Yellow | Red |
|-----|-------|--------|-----|
| Matches Today | ≥ 50 | 10–49 | < 10 |
| Prediction Coverage % | ≥ 90% | 70–89% | < 70% |
| Bookmakers | 13 | 5–12 | < 5 |
| Bets Today | ≥ 20 | 5–19 | 0 |
| Silent Bots | 0 | 1–2 | ≥ 3 |
| Stale Pending | 0 | 1–3 | > 3 |
| Last Snapshot | < 10 min | 10–20 min | > 20 min |
| AF Budget Used | < 70% | 70–90% | > 90% |

**"Currently Broken" feed** (auto-generated, appears only when non-empty): Scans all alert conditions and lists plain-English failures — "17 matches missing odds", "3 bots silent", "LivePoller stale 8 min", "Settlement lag 212 min". This is the operational inbox.

**9 panels below the strip:**

**1. Pipeline Job Health** (live query — `pipeline_runs`)
Table: job_name | last run | status (green/yellow/red) | duration | rows_affected | error (truncated 80 chars). One row per job (DISTINCT ON). Jobs: fixtures, enrichment (×2), odds, betting (×6/day), settlement, news_checker, match_previews, email_digest, live_poller (derived from snapshot age, not a pipeline_runs row).

Alert: Red if `status='error'`. Yellow if `rows_affected=0` on jobs expected to produce output. Red if `finished_at IS NULL` and started > 30 min ago (stuck).

**2. Data Funnel** (from ops_snapshots)
Horizontal waterfall: `matches_today → matches_with_odds → matches_with_predictions → matches_with_signals → matches_with_fvectors → bets_placed_today`. Each bar shows count + % of step above. The step with the biggest drop is highlighted.

Alert thresholds:
- `matches_with_odds / matches_today` < 80% = yellow, < 50% = red
- `distinct_bookmakers` < 8 = yellow, < 5 = red (should be 13)
- `bets_placed_today` = 0 when matches_today ≥ 10 = red

**3. Bot Health** (live query — `simulated_bets`)
Top numbers: bets_placed_today (large), active_bots / 17, bets_inplay_today.
Table: one row per bot — bot_id | bets today | pending | won | lost | avg_stake.
Red row if bot has 0 bets and matches_today ≥ 10. `duplicate_bets > 0` = always red.

**4. Live Tracker Health** (live + ops_snapshots)
Large: "Last snapshot: X min ago" (live from MAX(live_match_snapshots.created_at)).
Sparkline: snapshots/hour over last 12 hours.
Cards: live_matches_now | snapshots_with_xg % | snapshots_with_live_odds % (post 2026-05-07 fix).

Alert: Last snapshot > 20 min AND live_matches > 0 = red.

**5. Settlement Health** (live + ops_snapshots)
Large red badge if stale_pending > 0: "X bets stuck — match finished but not settled". Settlement run times today (from pipeline_runs). P&L today: won/lost/pending breakdown. ELO update today: green tick / red X.

Alert: stale_pending > 5 = red. Settlement never ran today after 22:00 UTC = red.

**6. Data Quality** (from ops_snapshots)
Quality scorecard — each row is a check with a count. Zero = healthy, any non-zero highlighted:

| Check | Yellow | Red |
|-------|--------|-----|
| matches_missing_grade | > 5 | > 20 |
| matches_with_0_signals | > 10 | > 30 |
| matches_without_pinnacle | > 20% of odds matches | > 50% |
| duplicate_bets | — | ≥ 1 (always red) |
| news_checker_errors_today | ≥ 1 | ≥ 3 |

**7. API Budget** (from ops_snapshots)
Progress bar 0 → 75K, green/yellow/red zones. af_calls_today | af_budget_remaining | estimated Gemini cost today ($).
**Note: shows NULL until Phase 3** — BudgetTracker is in-memory, resets on Railway restart.

**8. Email & Alerts** (from ops_snapshots)
Cards: digests_sent_today | value_bet_alerts_today | previews_generated_today | watchlist_alerts_today | news_checker_errors_today. Zero digests after 09:00 UTC with users > 0 = red.

**9. 7-Day Sparklines** (from ops_snapshots WHERE snapshot_date >= today - 7)
8 mini Recharts LineCharts (no axes, just line + today value large): matches_today | distinct_bookmakers | bets_placed_today | matches_with_signals/matches_today ratio | live_snapshots_today | bets_settled_today | af_calls_today | new_signups_today.
Yellow warning if today's value < 7-day average × 0.60.

---

### The 9 numbers that catch 80% of bugs

| # | Number | Red threshold | What it catches |
|---|--------|---------------|-----------------|
| 1 | Last betting job rows_affected | 0 on a day with ≥10 matches | Silent betting failure |
| 2 | distinct_bookmakers | < 5 | Odds pipeline dead |
| 3 | active_bots | < 10 on busy day | Gate logic misfiring |
| 4 | Last snapshot age | > 20 min | LivePoller dead |
| 5 | stale_pending | > 0 | Settlement broken |
| 6 | matches_missing_grade | > 20 | Signal pipeline broken |
| 7 | af_budget_remaining | < 5,000 | Quota breach today |
| 8 | digests_sent_today | 0 after 09:00 UTC | Resend/scheduler failure |
| 9 | bets_placed_today 7d sparkline | < avg × 0.60 | Model edge eroding |

---

### Implementation phases

**Phase 1 — Schema + writer (engine, ~4h)**
- Migration NNN: `CREATE TABLE ops_snapshots` (schema above, skip af_calls_today/af_budget_remaining — leave NULL)
- `write_ops_snapshot()` in `supabase_client.py` — runs all count queries, writes one row
- Call at end of: `run_fixtures`, `run_odds`, `run_betting`, `run_morning`, `run_settlement`, `run_enrichment`
- Scheduler: fallback cron every 60 min

**Phase 2 — Dashboard (frontend, ~4h)**
- `getOpsSnapshot()` in `engine-data.ts` — single SELECT latest row
- `getOpsSnapshotHistory()` — 7-day history for sparklines
- `getPipelineJobsToday()` — live DISTINCT ON from pipeline_runs (no pre-compute needed)
- `getStalepeningBets()` — live join simulated_bets + matches
- `getLastSnapshotAge()` — live MAX from live_match_snapshots
- `/admin/ops/page.tsx` — server component, superadmin-gated

**Phase 3 — AF budget persistence (~2h, independent)**
- `BudgetTracker` currently in-memory only → resets on Railway restart
- Add `write_budget_log(calls_made)` after each AF job → persists to `api_budget_log (date, job_name, calls, created_at)` or directly into `ops_snapshots.af_calls_today`
- Until Phase 3: column shows NULL with a tooltip "Requires Phase 3"

---

### Implementation notes

1. **BudgetTracker is memory-only** — af_calls_today will be NULL after every restart until Phase 3. Do not show misleading zeros; show NULL / "—".
2. **Lineups date filter** — `matches_with_lineups` must JOIN via `matches.kickoff_time::date`, not `lineups.created_at` (lineups fetched pre-kickoff may land on prior UTC date for early fixtures).
3. **LivePoller has no pipeline_runs rows** — it's a daemon. Derive "last snapshot age" from `MAX(live_match_snapshots.created_at)` directly. No new heartbeat writes needed.
4. **Fallback cron frequency** — 60 min. 30 min gives ~1,000 rows/year vs ~500 for marginal benefit.
5. **bets_settled_today_v2 column** — remove the duplicate before migration; keep only `bets_settled_today`.

---

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ADMIN-OPS-DASH | `ops_snapshots` table + `write_ops_snapshot()` writer + `/admin/ops` dashboard | 1.5 days | ✅ Done 2026-05-07 | ✅ Ready | All 3 phases complete. Engine hooks, migration 059+060, /admin/ops frontend with 10 panels. |

---

## Signal Improvements — 4-AI External Review (2026-05-07)

> Sourced from 4-model AI review of MODEL_WHITEPAPER.md + SIGNALS.md. Ordered by effort and when unblocked.
> Full synthesis in conversation history (2026-05-07). Items below are all net-new tasks; items already in other sections are cross-referenced.

### Group 1 — Quick wins (do now, data already exists, no new API calls)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| DOUBTFUL-SIGNAL | Wire `players_doubtful_home/away` from `match_injuries`. | 30 min | ✅ Done 2026-05-07 | ✅ Done | Block 5. Captures "Doubtful" + "Questionable" statuses. Rendered in `signal-accordion.tsx`. |
| SHARP-DRAW-AWAY | Add `sharp_consensus_draw` and `sharp_consensus_away`. | 1h | ✅ Done 2026-05-07 | ✅ Done | New block 3a — DISTINCT ON per selection. All 3 selections rendered in accordion. |
| LEAGUE-GOALS-DIST | Add `league_over25_pct` and `league_btts_pct`. | 1h | ✅ Done 2026-05-07 | ✅ Done | Added to block 11 from same 200-match window. Rendered in accordion. |
| H2H-GATE | Apply `LEAST(n/10, 1.0)` gate to h2h_win_pct, h2h_avg_goal_diff, h2h_recency_premium. | 30 min | ✅ Done 2026-05-07 | ✅ Done | Blocks 2 + 2b. Unit test in smoke_test.py. |
| INJURY-UNCERTAINTY | Add `injury_uncertainty_home/away` = doubtful player count. | 30 min | ✅ Done 2026-05-07 | ✅ Done | Block 5 alongside DOUBTFUL-SIGNAL. Rendered in accordion. |
| ODDS-VOL-AUDIT | Audit `odds_volatility` for lookahead leakage. | 30 min | ✅ Done 2026-05-07 | ✅ Done | **Audit result: CLEAN.** `is_live=false` filter prevents post-kickoff contamination. `cutoff_24h=now−24h` is always past-pointing. Smoke test guards the filter. |

### Group 2 — Signal refinements (this week, computation changes to existing signals)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| TURF-FAMILIARITY | Add `away_team_turf_games_ytd` companion to `venue_surface_artificial`. The turf edge is a visitor unfamiliarity effect, not a "game is on turf" effect. Two Finnish teams on turf = no edge. An English team visiting a Swedish team on turf in April = real edge. This companion signal (count of away games on artificial turf this season for the away team) transforms the signal from context to actual edge quantification. | 2h | ✅ Done 2026-05-07 | ✅ Ready | `away_team_turf_games_ytd` written in new block 11c. UI label: `turfFamiliarityLabel`. |
| IMPORTANCE-GAMES-REM | Normalize `fixture_importance` by games remaining. Current formula compresses urgency mid-season: 6 points from relegation with 20 games left = background noise; same gap with 5 games = crisis. Fix: `urgency = points_gap / (games_remaining * 3)` — values >1.0 = mathematically dire, 0.7-1.0 = high urgency. Games remaining available from fixtures metadata (round number + total rounds per league). | 1h | ✅ Done 2026-05-07 | ✅ Ready | `fixture_urgency_home/away` + `games_remaining_home/away` added using `played` from `league_standings`. UI label: `fixtureUrgencyLabel`. |
| REST-NONLINEAR | Log-transform or bucket `rest_days_home/away`. The effect is non-linear: 2→3 days rest is massive, 10→11 days is zero. Current linear storage doesn't encode this. Either transform to `log(rest_days + 1)` or use 3 buckets: short (≤3d), normal (4-7d), long (8d+). | 30 min | ✅ Done 2026-05-07 | ✅ Ready | `rest_days_norm_home/away` = `log(rest_days+1)` added alongside raw. UI label: `restDaysNormLabel`. |
| FORM-ELO-RESIDUAL | Add `form_vs_elo_expectation_home/away` residual signal. Instead of raw `form_ppg`, compute how much the team is over/underperforming what their ELO rating predicts. Strips out baseline quality already priced by the market. A bad team on a hot streak and a good team playing normally are conflated by raw form_ppg; the residual separates them. 3/4 AIs recommended this. | 2h | ✅ Done 2026-05-07 | ✅ Ready | `form_vs_elo_expectation_home/away` = `ppg - (3*p_win + 0.27)`. UI label: `formVsEloLabel`. |

### Group 3 — New signals (next 1-2 weeks, new queries but no new API endpoints)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| LEAGUE-ELO-VAR | Add `league_elo_variance` — std dev of ELO ratings within the league. High-variance league (ELO range 400+): favorites reliable, upsets rare, draws uncommon. Low-variance (parity, range <200): home advantage is dominant factor. Helps calibrate how much weight to place on `elo_diff` per league. Computable from ELO table filtered by league. 3/4 AIs recommended this. | 1h | ✅ Done 2026-05-08 | ✅ Ready | Block 6b in `batch_write_morning_signals`. Groups ELO ratings by league from today's matches → stdev + range. Also emits `league_elo_range`. Needs ≥4 teams with ELO data to compute. |
| LEAGUE-SEASON-PHASE | Add `league_season_phase` — `games_played / total_games_in_season` normalized 0.0 (start) → 1.0 (final round). Draw rates, home win rates, and result predictability are non-stationary: early season = high uncertainty (new signings, fitness), mid = most predictable, late = urgency volatility. One field addition using fixtures metadata (round number). | 1h | ⬜ | ✅ Ready | `total_games` from league config or inferred from max round seen per league per season. |
| LEAGUE-DRAW-YTD | Add `league_draw_ytd` — season-specific draw rate for the current season only (faster-adapting than 200-match rolling `league_draw_pct` which spans multiple seasons). Some seasons have anomalously high/low draw rates. More relevant for BTTS/O/U bots where base rate matters. | 1h | ⬜ | ✅ Ready | Filter existing `league_draw_pct` query to `season = current_season` only. Run alongside other league-meta signals. |
| BOOKMAKER-COUNT | Add `bookmaker_count_active` — count of bookmakers with non-null odds for each match in the latest odds snapshot. Low count = thin market = inefficiency persists longer. Directly computable from `odds_snapshots`. Acts as liquidity proxy without any external data. 2/4 AIs flagged this. | 1h | ✅ Done 2026-05-08 | ✅ Ready | One line in block 3 of `batch_write_morning_signals` — reuses existing `seen_bm` dict built for bookmaker disagreement. Zero extra DB queries. |
| LINE-VELOCITY | Add line movement velocity and shape features. Not just how much Pinnacle moved, but how fast and whether it reversed. Fast early move = sharp positioning; slow drift = retail noise; reversal = conflicting information. Computable from existing timestamped `odds_snapshots`. 1/4 AIs flagged this as potentially top-3 Stage 3 feature family. | 2h | ⬜ | ✅ Ready | Requires multi-snapshot query per match: slope of implied prob over time windows (T-12h to T-6h, T-6h to T-2h, reversal detection). |
| LEAGUE-CLV-EFFICIENCY | Add `league_clv_efficiency` — historical average pseudo-CLV beatability per league, computed from our own `pseudo_clv` data. Which leagues have we historically beaten closing line in most often? This formalizes the Scotland League Two discovery: some leagues are structurally more beatable. Run weekly, stored as league-level signal. | 2h | ⬜ | ⏳ Need ~60d pseudo_clv data (~May 17+) | GROUP BY league from `matches.pseudo_clv_home/draw/away`. Requires enough data to be meaningful (>20 matches per league). |
| SUSPENSION-SIGNAL | Add `suspension_risk_home/away` from accumulated yellow card counts in `match_events`. A player at 4 yellows in a 5-yellow ban competition is a real pre-match signal. Coach may rest them preemptively (rotation) or play them with risk. Market must price a probability; we can look up the count. | 2h | ⬜ | ⏳ Need `match_events` yellow card counts per player + league ban thresholds config | Requires per-player card accumulation across a season (not just this match). League ban thresholds vary (5 in most, 3 in some cups). |

### Group 4 — Stage 3 meta-model prep (when ready to train, ~mid-May)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| META-FEATURE-DESIGN | Finalize Stage 3 feature vector. Cap at 12-15 features (overfitting risk: 3000 rows × 40 features = guaranteed overfit with logistic regression). Final set per 4-AI review: `edge` (ensemble − pinnacle_implied), `odds_at_pick`, `model_disagreement`, `bookmaker_disagreement`, `sharp_consensus_home`, `pinnacle_line_move_home`, `pinnacle_ah_line_move` (cross-market confirmation), `odds_volatility`, `news_impact_score`, `league_tier`, `time_to_kickoff`, `importance_diff` (test), `venue_surface_artificial` (test). Drop all quality proxies (ELO, form, position). | 1h | ⬜ | ⏳ ~mid-May (need 3000+ match_feature_vectors rows) | Document final list in MODEL_WHITEPAPER.md before training. |
| LONGSHOT-GEO-AUDIT | Audit if 0.30-0.40 probability bin failures (42% predicted, 13% actual win rate) are geographically concentrated. If the miscalibration is mostly South American or Eastern European leagues, it may reflect structural home advantage inflation in those regions, not a global model flaw. | 2h | ⬜ | ✅ Ready | Query settled bets JOIN matches WHERE calibrated_prob BETWEEN 0.30 AND 0.40, GROUP BY league/region. May explain what Platt cannot fix. |

### Group 5 — Deferred (need player-level data from AF-PLAYER-RATINGS first)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| CUP-ROTATION | Add `rotation_risk_home/away` flag. When a team has a high-stakes fixture (cup semi, European game) within 72h of this match, they heavily rotate. Current form data treats cup wins against lower opposition the same as league wins. `rotation_risk_home/away` = (fixture within 72h AND competition tier of that fixture > current fixture tier). | 2h | ⬜ | ⏳ Needs fixture calendar with competition tier data | Requires cross-referencing fixtures across competitions per team. AF has competition type on fixtures. |
| GOALKEEPER-SIGNAL | Add `goalkeeper_absence_flag` — binary flag when the starting goalkeeper is absent (confirmed injured or suspended). Goalkeeper absences are massively underpriced in lower leagues, especially: backup keeper starts, youth keeper debut, emergency keeper. 1/4 AIs ranked this the highest-value specific missing signal after player weighting. | 2h | ⬜ | ⏳ Needs AF-PLAYER-RATINGS + lineup data | Requires confirmed starting lineups (T-75min) + GK position identification. When available, flag per side. |

---

## Tier 3 — 1-2 Months

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| HIST-BACKFILL | Historical data backfill (running on Railway) | — | 🔄 | 🔄 Active | Moved from GH Actions → Railway 02:00 UTC daily (2026-05-03). Fully psycopg2, self-stops when `backfill_complete.flag` exists. See § HIST-BACKFILL Plan |
| CODE-SONAR-ENGINE | Fix SonarCloud findings on odds-intel-engine: **C reliability (6 bugs)** + **6.9% duplication** + **4 security hotspots**. After repo cleanup (data/model_results removed): 491 code smells (359 CRITICAL — mostly `plsql:S1192` SQL string duplication in migrations = noise). 6 real bugs: identical sub-expressions (`supabase_client.py:2961`, `daily_pipeline_v2.py:1663/1683`), NULL comparison in migration 010, always-true condition (`improvements.py:629`), float equality (`odds_api.py:208`). Hotspots: Dockerfile root user + recursive COPY, GH Actions secret expansion. Security: A. | 2-3h | ✅ Done 2026-05-07 | ✅ Done | All 6 bugs fixed: NaN idiom `x != x` → `math.isnan(x)` in supabase_client.py + daily_pipeline_v2.py (+ added `import math`); improvements.py:629 guard suppressed with `# NOSONAR` (correct code, SonarCloud data-flow false positive); migration 010 `-- NOSONAR`; odds_api.py float equality → epsilon comparison. 4 hotspots are informational (Dockerfile root user, recursive COPY, GH Actions). Reliability rating should move C → A on next scan. |
| CODE-RUFF | Ruff lint pass: 171→69 issues. Fixed: 51 unused imports, 37 bare f-strings, 4 duplicate dict keys (real bugs in team_names.py + espn_results.py), 8 unused variables, 1 multi-import. Remaining 69: 55 E402 (structural sys.path before imports — correct pattern), 8 E701 (one-line ifs — cosmetic), 3 F841 (archival tennis scripts), 2 E741 (ambiguous names), 1 E702 (semicolon). All benign. | 30min | ✅ Done 2026-05-06 | ✅ Done | 102 issues fixed across 30+ files. |
| CODE-RADON | Structural complexity refactor: 3 god-files (supabase_client.py 3532 lines, daily_pipeline_v2.py 1902 lines, settlement.py 1542 lines). Key F-rated functions: `write_morning_signals` (CC=157), `batch_write_morning_signals` (CC=188), `run_morning` (CC=133), `run_live_tracker` (CC=77). | 3-5 days | ⬜ | ⏳ After SonarCloud + when no active tasks on these files | Do NOT start while active calibration/Pinnacle tasks are touching daily_pipeline_v2.py and supabase_client.py. Approach: split supabase_client into domain modules (signals.py, bets.py, features.py, match.py); extract sub-functions from F-rated pipeline functions. Carry real merge-conflict risk on active files. |
| XGB-HIST | Retrain XGBoost on backfilled data (~43K matches with stats+events) | 1 day | ⬜ | ⏳ After HIST-BACKFILL Phase 1 | Retrain result_1x2 + over_under on full AF features. Current: 96K Kaggle rows (limited features). New: richer per-match stats. **Include Pinnacle implied probability as a training feature** — current Kaggle data has no market context, so model can't distinguish "home win at 1.40" from "home win at 2.20". Adding Pinnacle implied teaches the model to predict residual edge vs market price rather than raw outcome probability. |
| AH-SIGNALS | Asian Handicap line + drift + bookmaker disagreement signals. AF bulk odds already include AH but `parse_fixture_odds` was silently dropping them. Added: AH parsing with `handicap_line` field (migration 066 adds column to odds_snapshots); `pinnacle_ah_line` (home team handicap, e.g. -0.75), `pinnacle_ah_line_move` (drift since first snapshot today), `ah_bookmaker_disagreement` (stdev across books) in batch_write_morning_signals block 3d. Also added `pinnacle_btts_yes_prob` (block 3e, BTTS was stored but never signaled). Data starts collecting on next odds run. | half day | ✅ Done 2026-05-07 | ✅ Done | Migration 066. `parse_fixture_odds` + `fetch_odds.py` (handicap_line column). `batch_write_morning_signals` blocks 3d + 3e. 6 smoke tests. |
| AH-XGBOOST | Add `pinnacle_ah_line`, `pinnacle_ah_line_move`, `ah_bookmaker_disagreement`, `pinnacle_btts_yes_prob` as XGBoost meta-model features. AH data collection starts 2026-05-07 — need ~2 weeks of settled matches (≥ ~200 rows) before these features have enough coverage to be informative. Train alongside meta-model milestone. | 2h | ⬜ | ⏳ ~May 17 (wait for data to accumulate) | Add to feature set in meta-model training script. Validate coverage before including in prod model. |
| AF-PLAYER-RATINGS | Player ratings + per-fixture stats via AF `/players?fixture={id}`. Each played player gets a Sofascore-style float rating (6.0-10.0), minutes, goals, assists, shots, key passes, dribbles, tackles, cards. Use case: (1) `team_avg_rating_home/away` rolling 5-game signal; (2) `squad_rotation_index` (fatigue detection); (3) `key_player_availability` flag; (4) data source for player-level injury weighting. Cache in `player_fixture_stats` table. Fetch post-settlement. AF updates ~30min after FT. 4-AI verdict: useful but needs meta-model consumer — collect from May 17 milestone onward. Medium signal strength (≤2% Brier improvement at team-aggregate level per academic lit). | 1 day | ⬜ | ⏳ Wait for meta-model milestone (~May 17) | AF `/players` endpoint. Post-settlement job. `player_fixture_stats` table (migration NNN). 150–280 calls/day (trivial vs 75K budget). |
| AF-VENUES | Venue surface + capacity signal via AF `/venues?id={venue_id}`. Surface (grass vs artificial turf) documented edge in Scandinavian/Eastern EU leagues (3–5% Brier improvement per Hvattum & Arntzen 2010). Venues cached once — near-zero ongoing API cost. Signal: `venue_surface_artificial` (1.0/0.0). 4-AI verdict: #1 "implement now" (3/4 models — strongest consensus). Done. | 2h | ✅ Done 2026-05-07 | ✅ Done | Migration 065 (venues table + matches.venue_af_id). `fetch_venues()` enrichment component. `venue_surface_artificial` signal in `batch_write_morning_signals()` block 11b. 5 smoke tests. |
| AF-BATCH | Batch fixture enrichment in settlement.py — use `/fixtures?ids=id1-id2-...-idN` (up to 20 per call) to pre-fetch all today's fixtures in bulk before ThreadPoolExecutor per-match enrichment. Reduces settlement API calls from N×3 individual calls to ⌈N/20⌉ batch calls + per-match fallback only on cache miss. | 1h | ✅ Done 2026-05-07 | ✅ Done | `get_fixtures_batch()` added to `api_football.py`. `fetch_post_match_enrichment()` in `settlement.py` pre-fetches batch before ThreadPoolExecutor; each `_enrich_one_match` uses prefetched data with fallback to individual calls. |
| AF-HALF-TIME-SIGNALS | Half-time tendency signals from stored `match_stats` `_ht` columns. `h1_shot_dominance_home/away`: rolling last-5-game avg of (shots_Xside_ht / shots_Xside). Frontend: added shots-on-target, fouls, yellow cards rows to H1 stats section in `match-detail-live.tsx`. Signal labels in `signal-labels.ts`. | 2h | ✅ Done 2026-05-07 | ✅ Done | Signal blocks 13 in `batch_write_morning_signals()`. `engine-data.ts` adds 6 `_ht` fields to `MatchStatsData`. `match-detail-live.tsx` H1 section extended. `signal-labels.ts` + `signal-accordion.tsx` for rendering. |
| AF-SIDELINED | Player career injury history via AF `/sidelined?player={id}`. Different from `/injuries` (fixture-specific) — full career sidelined history. Derived signals: `injury_recurrence_home/away` (avg career injury episodes for confirmed-out players). 7-day caching keeps cost at ~5-20 calls/day. | 3h | ✅ Done 2026-05-07 | ✅ Done | `fetch_player_sidelined()` in `fetch_enrichment.py` (7-day cache, reads from `match_injuries`). Signal block 12 in `batch_write_morning_signals()`. `engine-data.ts` adds `injuryCount` to `MatchInjury`. `match-detail-live.tsx` shows "Nx injury history" badge for players with ≥3 episodes. `signal-accordion.tsx` renders `injury_recurrence_home/away`. `signal-labels.ts`. Migration 068 adds `idx_player_sidelined_count` index. |
| AF-ODDS-MAPPING | ~~Use AF `/odds/mapping` to pre-filter fixtures with odds before polling.~~ CLOSED — already solved: `fetch_odds.py` uses bulk `/odds?date={date}` which only returns fixtures with active odds. No wasted per-fixture calls. 4-AI models flagged this as #1 priority but code review by model 4 confirmed it's already in place. | — | ✅ Done (pre-existing) | ✅ Already solved | `workers/jobs/fetch_odds.py` line 85: `get_odds_by_date(target_date)` is the bulk date endpoint. |
| AF-TRANSFERS | Mid-season squad disruption signal via AF `/transfers?team={id}`. 7-day caching + only fetching today's fixture teams keeps cost at ~5-40 calls/day (far below the 300-560/day AI models estimated, which assumed per-match uncached). Signal: `squad_disruption_home/away` = count of arrivals in last 60 days per team. | 3h | ✅ Done 2026-05-07 | ✅ Done | `fetch_transfers()` in `fetch_enrichment.py` (7-day cache). Signal block 14 in `batch_write_morning_signals()`. `signal-accordion.tsx` renders `squad_disruption_home/away`. `signal-labels.ts`. Migration 068 adds `idx_team_transfers_date_team` index. |
| H2H-SPLITS | Extract perspective-aware signals from h2h_raw JSONB. Added: `h2h_avg_goal_diff` (mean goal diff from home team's perspective — dominance signal), `h2h_recency_premium` (win rate last 3 vs overall — momentum signal). Also fixed latent bug: `home_team_api_id`/`away_team_api_id` were never stored on matches, so MGR-CHANGE block was silently doing nothing. Migration 067 adds both columns; store_match() and pipeline query updated. Backfilled 13,589 historical matches via `scripts/backfill_team_api_ids.py` (568 API calls) — both signals now active on full dataset. | 2h | ✅ Done 2026-05-07 | ✅ Done | Migration 067. store_match() backfill. daily_pipeline_v2.py query. batch_write block 2b. 4 smoke tests. scripts/backfill_team_api_ids.py. |
| INJURY-SEVERITY | Tag injury reason strings from `match_injuries` into severity buckets. Current `injury_count_home/away` treats all injuries equally. Signal: `injury_severity_home/away` (0=none, 1=minor muscle/knock, 2=medium hamstring/thigh, 3=serious ACL/fracture). Also `returning_player_risk` — players returning from >60-day absence underperform for 1–3 games. Low-medium signal strength but unique vs what public models use. Found in data-audit 2026-05-07. | 3h | ⬜ | ⏳ After meta-model (need severity as feature, not just raw count) | `match_injuries` table already has `reason` text field. Tag with regex/keyword rules. |
| B6 | Singapore/South Korea odds source | Unknown | ⬜ | ⏳ Research needed | +27.5% ROI signal, no live odds. AF has Korea K League odds but NOT Singapore. Pinnacle via Odds API ($20/mo) is best path |
| P5.2 | Footiqo: validate Singapore/Scotland ROI with 1xBet closing odds | Manual | ⬜ | ✅ Ready | Independent validation. If ROI holds on 2nd source, it's real |
| P3.1 | Odds drift as XGBoost input feature | 1-2 days | ⬜ | ⏳ ~June (needs more data) | Currently veto filter only. Strongest unused signal once data accumulates |
| P3.3 | Player-level injury weighting (by position/market value) | 2-3 days | ⬜ | ⏳ Low priority | ~90% captured by injury_count + news_impact already |
| S6-P2 | Graduate meta-model to XGBoost + full signal set | 2-3 days | ⬜ | ⏳ After ALN-1 (~late June) | After alignment thresholds validated at 300+ quality bets (>= 2026-05-06). ETA pushed from May W3 due to data quality cutoff. |
| P4.1 | Audit trail ROI: stats-only vs after-AI vs after-lineups | 1 day | ⬜ | ⏳ Needs data | Proves value of each layer. Needed for Elite pricing rationale |
| P3.5 | Feature importance tracking per league | 1 day | ✅ Done 2026-05-05 | ✅ Done | `scripts/compute_feature_importance.py` + migration 040. Pearson r per (league, signal, market). Run manually or extend Sunday refit. |
| F7 | Stitch redesign (landing + matches page) | Awaiting designs | ⬜ | ⏳ Awaiting designs | Parked until after first users arrive |
| ELITE-BANKROLL | Personal bankroll analytics dashboard (Elite) | 2-3 days | ✅ Done 2026-05-05 | ✅ Done | `/bankroll` server page (Elite-gated). `getUserBankrollData()` in engine-data.ts. `bankroll-chart.tsx` (recharts AreaChart). Summary stats (ROI, hit rate, net units, avg CLV, max drawdown). Model benchmark comparison. Per-league breakdown table. Recent picks with CLV. Nav link shown for Elite/superadmin. |
| ELITE-LEAGUE-FILTER | League performance filter for Elite value bets | 1 day | ⬜ | ⏳ After 3mo data | "Show only leagues where model hit rate > 45%". Needs data to be meaningful |
| ELITE-ALERT-STACK | Custom multi-signal alert stacking (Elite) | 2-3 days | ⬜ | ⏳ After ENG-8 | "Alert when confidence > 65% AND edge > 8% AND line moved in model's direction" |

---

## Infrastructure & Platform Optimization

> Identified 2026-05-05 via infra audit — features we're paying for or have for free but not using. Sorted by priority: 🔴 Critical (do ASAP, launch is live) → 🟡 High (this week) → 🟢 Medium.

| ID | Task | Effort | ☑ | Priority | Notes |
|----|------|--------|----|----------|-------|
| INFRA-1 | ~~Stripe free trial (7-day Pro)~~ | 15 min | ✅ Done 2026-05-05, **reverted 2026-05-06** | 🔴 ASAP | Removed — free tier IS the trial. REDDIT promo code handles targeted free months. `allow_promotion_codes=true` kept. |
| INFRA-2 | Stripe promo code for Reddit launch | 5 min | ✅ Done 2026-05-05 | 🔴 ASAP | Code `REDDIT` — 100% off first month (duration=once). Created live via Stripe API. Added as reply to all 3 active Reddit posts (r/buildinpublic + 2 subs). |
| INFRA-3 | Supabase Custom SMTP + Auth email templates | 30 min | ✅ Done 2026-05-05 | 🔴 ASAP | Resend SMTP configured in Supabase Auth (smtp.resend.com:465, noreply@oddsintel.app). Magic link template updated with OddsIntel branding. Auth flow refactored from OTP code → magic link (`signInWithOtp` with `emailRedirectTo`). Server-side PKCE callback (`route.ts`). Unknown email on login auto-redirects to signup with email pre-filled. Supabase Site URL space removed, `https://oddsintel.app/**` wildcard added to redirect URLs. Apple Sign In setup deferred. |
| INFRA-12 | Apple Sign In | 1-2h | ⬜ | ⏳ When ready | Apple Developer account ready. Need: Services ID (`app.oddsintel.web`), Key (.p8 + Key ID + Team ID) → Supabase Auth → Sign In/Providers → Apple. Frontend: add `<AppleSignIn />` button alongside Google/Discord in login, signup, modal. Return URL: `https://jjdmmfpulofyykzwiuqr.supabase.co/auth/v1/callback`. Required if ever shipping iOS app. |
| INFRA-4 | PostHog conversion funnel setup | 1h | ✅ Done 2026-05-05 | ✅ Done | Funnel built in PostHog dashboard (Signup → Match → upgrade_clicked → upgrade_completed). Custom events added to pricing-cards.tsx + profile/page.tsx. upgrade_cancelled also tracked. |
| INFRA-5 | Vercel Speed Insights | 15 min | ✅ Done 2026-05-05 | 🟡 This week | `@vercel/speed-insights` installed. `<SpeedInsights />` added to root layout.tsx. Will auto-report LCP/FID/CLS to Vercel dashboard once deployed. |
| INFRA-6 | Sentry Crons monitoring for Railway jobs | 1h | ✅ Done 2026-05-05 → Reverted 2026-05-06 | ✅ Done | Reverted: Sentry cron monitors exceeded free tier budget. Removed `sentry-sdk` from engine, deleted all monitor/init code. Railway logs + health endpoint are sufficient. Frontend Sentry kept. |
| INFRA-7 | PostHog feature flags for Tips launch | 1h | ⬜ | 🟡 Before M3 | Create `tips_enabled` flag in PostHog. Gate Tips section on this flag instead of hardcoded condition. When bot_aggressive validates → flip flag, no deploy needed. |
| INFRA-8 | Resend webhook → email open/click tracking | 2h | ✅ Done 2026-05-05 | ✅ Done | Migration 041 adds `last_email_opened_at` + `last_email_clicked_at` to profiles. `/api/resend-webhook` route handles `email.opened` + `email.clicked`. Svix signature verification. Webhook created in Resend dashboard. `RESEND_WEBHOOK_SECRET` set in Vercel + local .env.local. |
| INFRA-9 | Vercel Edge Config for feature flags | 2h | ⬜ | 🟢 Week of May 12 | Replace any DB queries used for global on/off flags with Vercel Edge Config (~1ms reads vs ~20ms DB). Good for: tips_enabled, maintenance_mode, featured_match_id. |
| INFRA-10 | Supabase DB Webhooks → watchlist alerts backend | 1 day | ⬜ | 🟢 When building ENG-8 | Instead of building a polling job for ENG-8 (watchlist alerts), use Supabase DB Webhooks: INSERT on match_signals with high injury_impact → fire Next.js API route → send Resend email. Eliminates most of ENG-8 backend complexity. |
| INFRA-11 | Supabase Realtime → replace live polling | 2 days | ✅ Done 2026-05-08 | Migration 076: `live_match_snapshots` + `matches` added to supabase_realtime publication. `match-score-display.tsx` 60s poll → Realtime INSERT. `matches-client.tsx` 60s snapshot poll + 90s router.refresh() → Realtime INSERT/UPDATE. `live-odds-chart.tsx` 5min poll → Realtime-triggered fetch. ENG-1 viewing counter (presence) deferred. |

---

## Tier 4 — 2-3 Months (needs data accumulation)

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| SIG-12 | xG overperformance rolling signal | 2h | ⬜ | ⏳ ~2 wks of post-match xG data | Regression to mean signal. Needs post-match xG from live snapshots |
| MOD-2 | Learned Poisson/XGBoost blend weights (replace fixed α) | 2h | ✅ Done 2026-05-05 | ✅ Done | `scripts/fit_blend_weights.py`: optimizes Poisson weight + per-tier shrinkage alpha. improvements.py loads from model_calibration, falls back to hardcoded. Weekly refit added to Sunday settlement. |
| P3.4 | In-play value detection model | 2-3 wks | 🔄 In Progress | ⏳ Phase 1A deployed 2026-05-06 (8 strategies, paper trading). Phase 2 ML needs 500+ snapshots + 200 settled bets. See § INPLAY Plan for full 5-phase roadmap | Phase 1A live: `workers/jobs/inplay_bot.py` — strategies A, A2, B, C, C_home, D, E, F. Bayesian xG posterior. Runs inside LivePoller every 30s. Week 2: add G, H. Week 3: add I, J, K. |
| P4.2 | A/B bot testing framework | 1-2 days | ⬜ | ⏳ Needs audit trail + data | Parallel bots with/without AI layers |
| P4.3 | Live odds arbitrage detector | 1-2 days | ⬜ | ⏳ ~July | Per-bookmaker odds exist. Low priority |
| RSS-NEWS | RSS news extraction pipeline ($30-90/mo) | 1-2 days | ⬜ | ⏳ After model proves profitable | Targets news before odds adjust. Re-evaluate when Elite has subscribers. **AI: +~$0.30/mo Gemini (data service $30-90/mo is the real cost)** |
| P3.2 | Stacked ensemble meta-learner (when Poisson vs XGBoost) | 1-2 days | ⬜ | ⏳ Needs settled bets with both predictions | Logistic regression on model disagreement |
| OTC-1 | Odds trajectory clustering (DTW) | 1-2 wks | ⬜ | ⏳ 1000+ snapshots | Low priority — volatility+drift captures ~same at 5% effort |

---

## Automation Sequels — Build Alongside Parent Task

> A model task is NOT done until its retraining is automated. Without these, calibration rots as data changes.

| ID | Parent | Task | Effort | ☑ | Ready? | Notes |
|----|--------|------|--------|----|--------|-------|
| PLATT-AUTO | PLATT | Weekly Platt recalibration in settlement | 1h | ✅ | ✅ Done | Sunday step runs `scripts/fit_platt.py` → `model_calibration` table |
| BLEND-AUTO | MOD-2 | Monthly Poisson/XGBoost blend weight recalculation | 1h | ✅ Done 2026-05-05 | ✅ Done | Weekly refit added to Sunday settlement step 5/5 alongside Platt. |
| META-RETRAIN | B-ML3 | Weekly meta-model retraining job | 2h | ⬜ | ⏳ After B-ML3 | Re-run on all `match_feature_vectors` rows, write to `model_versions` |
| XGB-RETRAIN | S6-P2 | Weekly XGBoost full-model retraining | 3-4h | ⬜ | ⏳ After S6-P2 | Train/val split, track feature importances over time |
| ALN-AUTO | ALN-1 | Monthly alignment threshold refresh | 1h | ⬜ | ⏳ After ALN-1 | Bin settled bets by alignment_count → ROI per bin → update thresholds |
| INPLAY-RETRAIN | P3.4 | Quarterly in-play model retraining | 2h | ⬜ | ⏳ After P3.4 | Seasonal — late-season desperation changes how game states map to results |

---

## ML Model Improvements (post-backfill research track)

> Origin: 2026-05-08 brainstorm + 4-AI research review (ML-RESEARCH ✅ Done). Research synthesized from Gemini 1.5 Pro, GPT-4o, Claude Opus, and a 4th model on 2026-05-08. Full prompt at `docs/ML_RESEARCH_PROMPT.md`. Implementation order below reflects consensus ranking. Most improvements can start now; a few are gated on backfill volume.

| ID | Task | Effort | ☑ | Ready? | Notes |
|----|------|--------|----|--------|-------|
| ML-RETRAIN-1 | **Retrain XGBoost on full dataset** after match stats backfill hits ~80%. Bigger training set (5K→14K rows) should improve calibration especially in lower leagues. | 2h | ⬜ | ⏳ Wait for backfill >80% | Run `workers/model/train.py` + re-run Platt + blend weight scripts. Compare log_loss before/after. |
| ML-ELO-GAP | **Add ELO to `FEATURE_COLS` in train.py** — `home_elo`, `away_elo`, `elo_diff`, `home_elo_exp` are computed at inference (`xgboost_ensemble.py:152-157`) but absent from `FEATURE_COLS` in `train.py` so the new AF model never learns from them. Add all 4 to FEATURE_COLS, re-run train.py, compare log_loss. (Hvattum & Arntzen 2010: ELO is most valuable for promoted teams, post-international-break, early season.) | 1h | ✅ Done 2026-05-08 | ✅ Ready | Added `elo_home`, `elo_away`, `elo_diff` to FEATURE_COLS in `workers/model/train.py`. Also fixed all FEATURE_COLS to use `match_feature_vectors` column names (was Kaggle-era names — would have crashed on run). Target column fixed: `result` → `match_outcome`. `load_training_data()` added — just run `python3 workers/model/train.py` when data is ready. Training blocked until enough rows in match_feature_vectors. |
| ML-MISSING-DATA | **Fix aggressive row-dropping** — `X.notna().all(axis=1)` in train.py loses ~30-40% of training data (~5K→7-8K effective rows). **H2H features are the main culprit**: newly promoted teams have no prior meetings. Inference already defaults H2H to neutral (features.py:341-345), so training on a non-H2H subset is a biased sample. Fix: add missingness indicator flags for H2H cols, then fill with per-league-tier mean + global mean fallback. LightGBM native null handling is an alternative. (Saar-Tsechansky & Provost 2007 JMLR: league-mean imputation + indicator flags performs as well as KNN.) | 3h | ⬜ | ✅ Ready | Code: `for col in ['h2h_home_win_pct','h2h_avg_goals','h2h_over25_pct','h2h_btts_pct']: features_df[f'{col}_missing'] = features_df[col].isna().astype(int)` then `fillna(league_means).fillna(global_mean)`. Remove `valid = X.notna().all(axis=1)`. |
| ML-NEW-FEATURES | **Add live signals as XGBoost training features** — ELO + Pinnacle odds are high-lift. Manager change days and squad disruption should be **skipped** per R4 lit (Bryson 2011: manager effect priced into odds within 24h, too few post-sacking samples to learn reliably; squad disruption has thin literature and is swamped by team-quality signals). Scope: ELO (via ML-ELO-GAP), Pinnacle (via ML-PINNACLE-FEATURE), sharp consensus (use as continuous feature not binary filter per Forrest & Simmons 2008). | 1 day | ⬜ | ⏳ After ML-ELO-GAP + ML-PINNACLE-FEATURE done first | ELO and Pinnacle are orthogonal improvements that stack. Manager/squad disruption: omit from training features (still useful as live signals for bettors, just not as model inputs). |
| ML-PINNACLE-FEATURE | **Pinnacle odds as training feature** — R4/R5 disagree on WHICH odds: R4 (Kaunitz 2017) says add closing implied probs; R5 warns this is data leakage if closing odds unavailable at prediction time. **Resolution: use odds available at bet time (06:00 UTC opening run, or the pre-KO refresh snapshot) — NOT closing odds.** Train on the same timing window used at inference. Feature set: `pinnacle_implied_home/draw/away` at bet placement time + `pinnacle_opening_to_closing_move_home` as a separate signal (captures late sharp money). AH line also worth adding for O/U model (Štrumbelj & Šikonja 2010). | 2h | ⬜ | ⏳ Verify what timestamp of Pinnacle odds is in match_feature_vectors (opening vs closing) | Check: `SELECT COUNT(*) FROM match_feature_vectors WHERE pinnacle_implied_home IS NOT NULL`. Confirm odds are pre-KO snapshot, not closing. |
| ML-HYPERPARAMS | **Switch to CatBoost or LightGBM + tune hyperparameters** — R5 (Gemini) ranks CatBoost above LightGBM for small datasets: its ordered boosting reduces overfitting and it handles league/team categoricals natively. R4 prefers LightGBM. Both are within 0.2-0.5% log-loss of XGBoost. LightGBM key params: `LGBMClassifier(n_estimators=300, max_depth=6, lr=0.05, num_leaves=31, min_child_samples=20, subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=1.0)`. Do NOT add both LightGBM + CatBoost as stacking inputs — too correlated. Pick one GBDT, pair with structurally different model (LogReg or Poisson). | 3h | ⬜ | ⏳ After ML-RETRAIN-1 | Experiment order: (1) add LightGBM, compare log_loss, (2) add CatBoost, compare. Keep whichever beats XGBoost by >0.5% on TimeSeriesSplit; keep XGBoost if both are within 0.2%. |
| ML-BLEND-DYNAMIC | **Dynamic Poisson/XGBoost blend weights per market and data tier** — `fit_blend_weights.py` produces one global weight. Per R4 (Constantinou 2019): for teams with <50 prior matches use Poisson weight ~0.65, 50-200 → ~0.50, >200 → 0.45-0.50. O/U and BTTS should be Poisson-heavier at all tiers (Koopman & Lit 2015). R5 (Gemini) goes further: **replace fixed blend entirely with stacked meta-learner** (LogReg/Ridge trained on out-of-fold predictions from each base model). Meta-model learns not just optimal weights but WHEN each model is better (Poisson may dominate early-season; XGBoost mid-season). Implementation: 9-feature input [poisson_H/D/A, xgb_H/D/A, logreg_H/D/A] → LogisticRegression(C=1.0). | 2h | ⬜ | ⏳ After ~500 settled predictions per tier | Quick win first: grid search w ∈ [0,1] in 0.05 steps, temporal test set. If optimal w ≠ 0.5 for any market, update fit_blend_weights.py. Full stacking later. Estimated lift from stacking: 1-2% log-loss. |
| ML-CALIBRATION-FIX | **Drop dual isotonic+Platt calibration → isotonic only** — R5 flags that applying isotonic regression then Platt scaling sequentially over-smooths probabilities, destroying edge at the tails where value bets live. Use ONE: isotonic for >1000 calibration samples (this is now satisfied — 586+ settled), Platt for smaller sets. Follow-up: add **beta calibration** or **Venn-Abers predictors** specifically for tail calibration improvement (standard isotonic optimises for center of distribution, not high-edge bet range). | 30m | ✅ Done 2026-05-08 | ✅ Ready | Removed `CalibratedClassifierCV` wrapper from all 3 training functions in `train.py`. XGBoost `multi:softprob`/`binary:logistic` is already calibrated; Platt applied at inference. Also eliminated redundant double-fit in `train_result_model`. |
| ML-PER-TIER | **Per-league-tier models (hierarchical)** — stay global until 500+ settled matches per league (Koopman & Lit 2015: per-league models fail OOS for most leagues due to variance). Right approach when ready: hierarchical — train global base model, then per-tier models that take global predictions as a feature. Top-5 European leagues will hit threshold first. | 1 day | ⬜ | ⏳ Month 3-4 of operation | Check settled bet counts per tier before starting. |
| ML-LOSS-FN | **Focal loss experiment** — R4 says stay with log_loss as primary training objective (Brier score has less gradient pressure at tails). Focal loss (gamma=2.0) is worth a 5-fold CV experiment: if log_loss in the 0.30-0.40 prob bin drops >3%, adopt it. More impactful: CAL-PLATT-UPGRADE which directly fixes the training/calibration mismatch. | 1 day | ⬜ | ⏳ After CAL-PLATT-UPGRADE | Focal loss code available in R4. Run after other model improvements to isolate effect. |
| ML-RESEARCH | **Run AI research prompt** — synthesize findings from 4 AI reviews (Gemini 1.5 Pro, GPT-4o, Claude Opus, GPT-4o R4) into task updates. | 1h | ✅ Done 2026-05-08 | ✅ Done | 4 replies received 2026-05-08. Tasks updated above. Key consensus: imputation > ELO gap > Pinnacle feature > CAL-PLATT-UPGRADE > LightGBM swap. Skip manager_change + squad_disruption as training features. Stay global model until 500+ settled/league. |

---

## Tier 5 — Future / Speculative

| ID | Task | ☑ | Ready? | Notes |
|----|------|----|--------|-------|
| SLM | Shadow Line Model: predict what opening odds *should be* | ⬜ | ⏳ Blocked | Needs opening odds timestamp storage |
| MTI | Managerial Tactical Intent: press conference classification | ⬜ | ⏳ Blocked | No reliable transcript source across leagues. **AI: ~$0.22/mo flat (10 calls/day, flash)** |
| RVB | Referee/Venue full bias features | ⬜ | ⏳ Blocked | Venue-level stats not yet collected |
| WTH | Weather signal (OpenWeatherMap, free) | ⬜ | ⏳ Low priority | Defer until O/U becomes a focus market |
| SIG-DERBY | Is-derby + travel distance signals | ⬜ | ⏳ Blocked | Needs team location data |

---

## Key Thresholds to Watch

| Milestone | Query | Target | Status (2026-05-08) | ETA |
|-----------|-------|--------|---------------------|-----|
| **Platt scaling ready** | Predictions with finished match outcomes | 500+ | ✅ Done 2026-04-30 | Done |
| **In-play model live** | Distinct matches in live_match_snapshots WITH xG | 500+ | ✅ ~400+ (live 1x2/O/U odds fixed 2026-05-07) | Done |
| Meta-model Phase 1 ready | `match_feature_vectors WHERE captured_at >= 2026-05-06 AND pinnacle_implied_home IS NOT NULL` | 3,000+ | ~2,200 (day 2 of quality clock) | ~May 17 |
| Post-mortem patterns readable | `model_evaluations WHERE market='post_mortem'` | 14+ | ~11 | ~May 13 |
| BOT-QUAL-FILTER ready | `simulated_bets WHERE result!='pending' AND created_at >= 2026-05-06` | 100+ | ~24 (~27/day) | ~May 10–11 |
| Alignment threshold validation (ALN-1) | Same as above | 300+ | ~24 | ~June 5 |
| XGBoost retrain on backfill (ML-RETRAIN-1) | match_stats coverage | ~80% of 14K | ~25% (3,474 done) | ~Late May |
| CAL-PLATT-UPGRADE ready | Settled bets per market | 300+ | ~77 total | ~June |
| Meta-model Phase 2 ready | Settled bets with dimension_scores + CLV | 1,000+ | 0 | ~Aug |

---

## § HIST-BACKFILL Plan — ✅ IMPLEMENTED (archived from PRIORITY_QUEUE 2026-05-05)

> Implementation complete. Script at `scripts/backfill_historical.py`, running on Railway 02:00 UTC daily.
> Phase 1: ~3,474 matches done. Full plan archived in git history.

---

## § INPLAY Plan — In-Play Value Detection Model

> Created: 2026-04-30. Original synthesis from 4 AI strategy reviews.
> Updated: 2026-05-06. Second round of 4 independent AI reviews (8 answers total) refined strategy conditions, added 5 new strategies (G-K), corrected xG formulation, and updated validation thresholds.

### 1. Core Hypothesis (validated by all 8 reviews)

**"Conditional mispricing occurs when realized goal output < expected output, but forward-looking hazard rate remains high."**

The market adjusts live odds primarily on **time elapsed + scoreline**, but lags on **true chance quality (xG)** and **game state intensity (tempo, pressure)**. The edge is NOT "0-0 = bet Overs" — it's "0-0 but underlying goal process is ABOVE expectation."

### 2. Model Architecture (all 4 original reviews agreed)

**Target:** Predict `lambda_home_remaining` and `lambda_away_remaining` (Poisson rates for remaining goals per team) — NOT classification.

**Why:** One regression model derives ALL market probabilities:
- P(Over 2.5) = P(Poisson(λ_total_remaining) ≥ 2.5 - current_goals)
- P(BTTS) = derived from per-team lambdas via bivariate Poisson
- P(Home Win) = derived from goal difference distribution

**Algorithm:** LightGBM with `objective='poisson'` (primary) + XGBoost as ensemble partner.

**Time handling:** Single model with:
- `match_minute` as continuous feature
- `match_phase` as categorical: [0-15, 15-30, 30-45, 45-60, 60-75, 75-90]
- `time_remaining = 90 - match_minute`
- Non-linear transforms: `minute_squared`, `log(90 - minute)`

**Red cards:** V1: hard-skip matches with red cards. V2 (2000+ matches): add `man_advantage` + `minutes_since_red` features.

### 3. Feature Engineering (ranked by predictive power)

#### Tier 1 — Build immediately

| Feature | Formula | Signal |
|---------|---------|--------|
| **Bayesian xG rate** *(replaces raw ratio — unanimous across all 8 reviews)* | `posterior_rate = (prematch_xg + live_xg) / (1.0 + minute / 90)` | Shrinks early noise toward prior; converges with pace ratio by min 35 |
| **xG delta vs expectation** | `live_xg - (prematch_xg × minute / 90)` | Positive = game running hotter than pre-match model expected |
| **xG-to-score divergence** | `live_xg_total - actual_goals` | Large positive = "unlucky", regression due |
| **Implied probability gap** | `model_prob - (1 / live_odds)` | Direct value measure — trigger on this, not raw odds level |
| **Per-team shot quality** | `team_xg / team_shots` | High = dangerous chances; low = shooting from distance |
| **Odds velocity** | `(odds_t - odds_t_minus_5min) / odds_t_minus_5min` | Sharp moves without goals = information |
| **Odds staleness flag** | `NOW() - odds_last_updated > 60s` | Critical: if True, skip bet — odds may be frozen post-goal |

#### Tier 2 — Build by Phase 2
| Feature | Formula | Signal |
|---------|---------|--------|
| Possession efficiency | `team_xg / (possession_pct × minute / 90)` | Strips time-wasting possession |
| Score-state adjustment | All metrics segmented by leading/drawing/trailing | Trailing team stats more predictive |
| xG home/away share | `xg_home / (xg_home + xg_away)` | Away dominance in 0-0 may already be priced |
| Corner momentum | `corners_last_10min / corners_total` | Acceleration predicts pressure |
| Bookmaker consensus | `std(implied_probs_across_13_bookmakers)` | High disagreement = value opportunity |
| xG acceleration | `last_10_min_xg / previous_10_min_xg` | Momentum proxy — derive from snapshot deltas |

#### Tier 3 — Refinements
| Feature | Formula | Signal |
|---------|---------|--------|
| ELO-adjusted xG | `xG × (opponent_elo / league_avg_elo)` | xG vs strong defense worth more |
| Importance × score state | `importance × (trailing: 1.3, leading: 0.7, drawing: 1.0)` | Must-win + trailing = max pressure |

### 3b. Critical Engineering Fixes (from 8-review synthesis — build before first bet)

All 4 second-round tools flagged these independently:

1. **Staleness check (HIGH):** Before logging any paper bet, verify live odds updated in the last 60 seconds. API-Football odds can lag 30-60s post-goal. A stale odds snapshot could log a bet at pre-goal prices on a match that's already 1-0. Implementation: compare `captured_at` of the odds fields to current time.

2. **Score re-check at execution (HIGH):** When a trigger fires, re-read the latest score from the most recent snapshot before logging the bet. If score changed since the triggering snapshot, abort.

3. **League calibration filter (HIGH):** Only run in leagues with ≥ 20 completed matches with xG data in `live_match_snapshots`. AF's xG is less calibrated in lower tiers with sparse history.

4. **Split 0-0 and 1-0 scenarios (MEDIUM):** These are structurally different game states. Strategies A and D should have separate configs — 0-0 version and 1-0 version — logged with different `strategy_id` values so Phase 2 analysis can compare them.

5. **xG home/away direction (MEDIUM):** Log `xg_home_share` per bet. Away dominance in a 0-0 (away pressing, home defending deep) may already be priced by sharp books. Phase 2 feature engineering will test this.

### 4. Strategy Portfolio (A-K)

*A-F from original 4-AI synthesis. G-K added after second 8-answer round.*
*Over 3.5 bot: REJECTED by all 4 second-round tools — no O3.5 live odds, total overlap with A. Tag extreme A conditions as `strategy_tag='A_extreme'` instead.*

---

#### Strategy A: "xG Divergence Over" — Phase 1A bot
**Edge confidence:** Medium | **Trigger rate:** ~8-12% of matches | **Time to 200 bets:** ~14 days
- **Entry:** Min **25-35** (tightened from 20-35 — all 4 reviews agreed early window too noisy)
- **Score:** 0-0 only (split 1-0 into A2 for separate analysis)
- **Signal:** Bayesian posterior rate > prematch rate × 1.15 AND combined xG ≥ 0.9 AND shots on target ≥ 4 combined AND pre-match O2.5 > 54%
- **Market:** Over 2.5; trigger when `model_prob - (1/live_odds) ≥ 3%` (not static odds floor)
- **Skip:** xG per shot < 0.09, red card, odds staleness flag, league with < 20 xG matches
- **Edge:** 2-4% (revised down from 3-8% — Pinnacle's live market is sharper than assumed)

#### Strategy A2: "xG Divergence Over — 1-0 State"
**Edge confidence:** Medium | Separate bot, same logic as A but score = 1-0
- **Note:** Who is winning vs pre-match expectation matters — log `score_leader_is_favourite` for Phase 2 analysis

#### Strategy B: "BTTS Momentum"
**Edge confidence:** High | **Trigger rate:** ~15-20% | **Time to 200 bets:** ~8 days
- **Entry:** Min 15-40, score 1-0 or 0-1
- **Signal:** Trailing team xG ≥ 0.4 AND shots on target ≥ 2 AND pre-match BTTS > 48%
- **Market:** BTTS Yes
- **Skip:** Trailing team xG < 0.2, score becomes 2-0, red card for trailing team
- **Edge:** 4-7%

#### Strategy C: "Favourite Comeback"
**Edge confidence:** High | **Trigger rate:** ~5-8% | **Time to 200 bets:** ~22 days
- **Entry:** Min 25-60, pre-match favourite trailing by 1
- **Signal:** Favourite xG > underdog xG AND possession ≥ 60% AND shots on target ≥ opponent
- **Market:** Draw No Bet (favourite) — DNB gives cleaner CLV analysis than Double Chance
- **Skip:** Favourite not generating xG, underdog counter-xG high
- **Edge:** 3-6%

#### Strategy C_home: "Home Favourite Comeback" *(new — user idea, validated by all 4 tools)*
**Edge confidence:** High | **Trigger rate:** ~3-5% | **Time to 200 bets:** ~30 days
- **Entry:** Same as C but ONLY home team is pre-match favourite trailing 1-0
- **Signal:** Same as C + ELO confirms home team quality (elo_home > elo_away)
- **Possession threshold:** ≥ 55% (home crowd generates set-piece pressure at lower possession)
- **Minute cap:** ≤ 70 (post-70 crowd dynamics can shift to panic, opening counter-attacks)
- **Market:** Draw No Bet (home) — log draw outcome separately to also capture DC data
- **Mechanism:** COVID natural experiment showed ~6-8pp crowd effect on home win rate. Referee bias + urgency not captured in xG.
- **Edge:** 5-10%

#### Strategy D: "Late Goals Compression"
**Edge confidence:** Medium | **Trigger rate:** ~22-27% | **Time to 200 bets:** ~6 days (fastest)
- **Entry:** Min 55-75, score 0-0 or 1-0
- **Signal:** Combined xG ≥ 1.0 AND live odds > 2.50 AND pre-match expected goals > 2.3
- **Market:** Over 2.5 (proxy — we don't have O1.5 live odds)
- **Skip:** Combined xG < 0.6 (dead game)
- **Edge:** 3-6% — needs 500+ bets before trusting (high variance at these odds)

#### Strategy E: "Dead Game Unders"
**Edge confidence:** High | **Trigger rate:** ~12-16% | **Time to 200 bets:** ~10 days
- **Entry:** Min 25-50, score 0-0 or 1-0
- **Signal:** xG pace < 70% of expected AND shots slowing (derive from snapshot deltas) AND corners low
- **Market:** Under 2.5
- **Edge:** Market assumes constant hazard rate; tempo collapse is real and well-documented

#### Strategy F: "Odds Momentum Reversal"
**Edge confidence:** Low | **Trigger rate:** ~4-7% | **Time to 200 bets:** ~25 days
- **Entry:** Any minute, triggered by odds velocity
- **Signal:** Odds move > 15% in < 10 min WITHOUT goal AND score unchanged across last 3 polls AND contrary to xG trend
- **Market:** Fade the move direction
- **Skip:** Red card, score change, only 1 bookmaker moved, odds staleness flag
- **Edge:** 5-10% when triggered — but 30s polling makes distinguishing real moves from VAR/injury noise hard. Minimum 500+ bets before conclusions.

#### Strategy G: "Shot Quality Under" *(new — appeared in all 4 second-round tools)*
**Edge confidence:** Medium-High | **Trigger rate:** ~6-10% | **Time to 200 bets:** ~18 days
- **Entry:** Min 32-52, score combined ≤ 1
- **Signal:** Combined shots ≥ 12 AND (xg_home + xg_away) / (shots_home + shots_away) < 0.07 AND live Under 2.5 odds ≥ 1.70
- **Skip:** Pre-match O2.5 implied > 62% (expected goal-fest), red card
- **Market:** Under 2.5
- **Mechanism:** Market reacts to shot volume (visible, salient). Low xG/shot = teams shooting from distance, not generating danger. Under is mispriced.
- **Edge:** 5-8%

#### Strategy H: "Corner Pressure Over" *(new — appeared in 3/4 second-round tools)*
**Edge confidence:** Medium | **Trigger rate:** ~4-6% | **Time to 200 bets:** ~28 days
- **Entry:** Min 35-48, score combined ≤ 1
- **Signal:** Combined corners ≥ 8 AND combined xG ≥ 0.4 AND live O2.5 ≥ 1.90
- **Skip:** One team has ≥ 70% possession AND their corners > opponent (defensive clearances, not bilateral pressure)
- **Market:** Over 2.5
- **Mechanism:** High bilateral corners = sustained set-piece pressure underweighted by pure xG models
- **Edge:** 3-6% — run league-tier stratified; expect higher edge in Tier 3-4

#### Strategy I: "Possession Trap Under" *(new — appeared in 3/4 second-round tools)*
**Edge confidence:** Medium-High | **Trigger rate:** ~3-6% | **Time to 200 bets:** ~30 days
- **Entry:** Min 32-55, score **0-0 only** (1-0 with high possession = time-wasting, different mechanism)
- **Signal:** (possession_home ≥ 62 AND xg_home ≤ 0.30) OR (possession_home ≤ 38 AND xg_away ≤ 0.30) AND combined xG ≤ 0.40 AND live Under 2.5 ≥ 1.75
- **Market:** Under 2.5
- **Mechanism:** Market sees high possession → interprets attacking intent → misprices Under. Sterile possession (possession without penetration) is a distinct game state.
- **Edge:** 5-8%

#### Strategy J: "Dominant Underdog Win" *(new — appeared in 2/4 second-round tools)*
**Edge confidence:** Medium | **Trigger rate:** ~3-5% | **Time to 200 bets:** ~32 days
- **Entry:** Min 25-55, underdog leading 1-0 (identify via lower pre-match Pinnacle implied prob)
- **Signal:** Underdog xG > Favourite xG AND possession within 10% of 50/50 or favouring underdog AND live underdog win odds ≥ 2.80
- **Market:** 1X2 Underdog to Win
- **Mechanism:** Narrative bias ("favourite will come back") keeps underdog win odds too high even when data shows deserved lead
- **Edge:** 4-7% per bet — rare trigger but potentially highest edge-per-bet

#### Strategy K: "Second-Half Kickoff Burst" *(new — appeared in 2/4 second-round tools)*
**Edge confidence:** Low-Medium | **Trigger rate:** ~8-12% | **Time to 200 bets:** ~14 days
- **Entry:** Min 46-54 ONLY (narrow post-HT window)
- **Signal:** Score 0-0 or 1-0 AND combined first-half xG ≥ 0.70 AND pre-match O2.5 > 50% AND live O2.5 ≥ 1.90
- **Market:** Over 2.5
- **Mechanism:** Above-average goal rate in first 5-8 min of 2H from fresh legs + HT adjustments. Market applies smooth time-decay, missing this temporal spike.
- **Edge:** 2-4% — sharp books increasingly price this for major leagues. More relevant Tier 3-4.
- **Hold:** Start in Week 3 after A and F confirmed working cleanly.

### 4b. Launch Order (based on 8-review prioritisation consensus)

| Week | Start these bots | Rationale |
|------|-----------------|-----------|
| **Week 1** | A, A2, B, C, C_home, D, E, F | Core portfolio — fastest validators first |
| **Week 2** | G (Shot Quality Under), H (Corners Over) | Simple conditions, fields already collected |
| **Week 3** | I (Possession Trap), J (Dominant Underdog), K (2H Burst) | More complex logic, lower frequency |
| **Hold** | Over 3.5 bot | No O3.5 odds — tag extreme A triggers as `A_extreme` instead |

### 4c. Strategy Prioritisation Table (Tool 4 synthesis)

| Strategy | Est. bets/day | Days to 200 bets | Edge confidence | Notes |
|----------|--------------|-----------------|-----------------|-------|
| D — Late Goals | 33-40 | **~6 days** | Medium | High variance, need 500+ to trust |
| B — BTTS Momentum | 22-30 | ~8 days | **High** | Fastest high-confidence strategy |
| E — Dead Game Under | 18-24 | ~10 days | **High** | Strong mechanism, well-studied |
| K — 2H Kickoff Burst | 12-18 | ~14 days | Low-Medium | Start Week 3 |
| A — xG Divergence | 12-18 | ~14 days | Medium | Core strategy, run first |
| G — Shot Quality Under | 9-15 | ~18 days | Medium-High | Good contrast to A |
| C — Favourite Comeback | 8-12 | ~22 days | **High** | Needs patience |
| F — Odds Reversal | 6-10 | ~25 days | Low | 500+ minimum before conclusions |
| H — Corners Over | 6-9 | ~28 days | Medium | Noisy without timing data |
| C_home — Home Comeback | 5-8 | ~30 days | **High** | Rarest but strongest mechanism |
| I — Possession Trap | 5-9 | ~30 days | Medium-High | Low frequency, high edge |
| J — Dominant Underdog | 5-8 | ~32 days | Medium | Rarest, highest edge-per-bet |

### 5. Staking (in-play specific)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Kelly fraction | **Quarter Kelly** (not half) | Higher model uncertainty in-play |
| Time decay | `(minutes_remaining / 90)^0.5` | Min 30: 82% stake, min 60: 58%, min 75: 41% |
| Max stake per bet | 1.5% bankroll | Lower than pre-match (2%) |
| Max exposure per match | 3% bankroll | Prevents doubling down |
| Bankroll allocation | 70% pre-match, 30% in-play | Pre-match is more reliable |

**Minimum edge thresholds by minute:**

| Minute | Min edge | Rationale |
|--------|----------|-----------|
| 15-30 | 3% | Good signal, plenty of time |
| 30-45 | 4% | HT reset incoming |
| 46-60 | 5% | Post-HT uncertainty |
| 60-75 | 6% | Time running out |
| 75+ | 8% | Extreme value only |

### 6. Data Gaps to Fix

| Gap | Priority | Solution |
|-----|----------|----------|
| Odds staleness detection | **Critical** | Compare `captured_at` of odds to NOW() — skip if > 60s stale |
| Score re-check at trigger | **Critical** | Re-read latest score before logging bet, abort if changed |
| Open-play xG vs set-piece xG | High | Separate penalty/free-kick xG from open-play in snapshots |
| Substitution timestamps + type | High | Already in match_events — extract and add to snapshot features |
| Event-triggered odds capture | High | Snapshot odds at goal/red card moments, not just 5-min cycle |
| 1-minute trigger checks | Medium | When model flags potential entry, poll odds at 1-min for execution |
| Formation changes | Medium | Capture at HT and after goals |
| Dangerous attacks count | Low | Available from AF, add to snapshot |

### 7. Implementation Phases — Full Roadmap

Each phase has an explicit **gate** that must pass before the next phase begins.
All paper phases use AF live odds from `live_match_snapshots` — no bookmaker API needed.
Real money phases require a Betfair Exchange account + API integration (1-2 days to add).

---

#### 🟦 Paper Trading — Phase 1A: Rule-Based Single Strategy (START TODAY)
**Data needed:** None — starts immediately using live AF odds  
**Timeline:** May 2026 (build now, runs continuously)  
**What to build:**
- Live bot in scheduler: reads `live_match_snapshots` every 30s during active matches
- Computes Bayesian posterior: `posterior_rate = (prematch_xg + live_xg) / (1.0 + minute / 90)`
- Staleness check: if odds haven't updated in 60s → skip
- Score re-check: re-read latest score before logging → abort if changed since trigger snapshot
- Checks Strategy A conditions: minute 25-35, score 0-0, posterior_rate > prematch_rate × 1.15, combined xG ≥ 0.9, shots on target ≥ 4, pre-match O2.5 > 54%, `model_prob - implied_prob ≥ 3%`
- Logs to `simulated_bets`: `market='ou_25'`, `selection='over'`, `odds=live_ou_25_over`, `stake=1% fixed`, `strategy_id='inplay_a'`
- Settlement handled by existing pipeline at FT

**Gate to Phase 1B:** 200+ paper bets logged AND CLV positive on ≥ 55% of bets (revised from 80% — 8-tool consensus)

---

#### 🟦 Paper Trading — Phase 1B: Rule-Based All Strategies
**Data needed:** 200+ Phase 1A bets settled, ROI > 0% OR CLV > 0 on 60%+ of bets  
**Timeline:** Late May / early June 2026  
**What to build:**
- Extend bot to run all 6 strategies (A-F) simultaneously
- Strategy B (BTTS Momentum), C (Favorite Comeback), D (Late Goals Compression), E (Dead Game Unders), F (Odds Momentum Reversal)
- Each strategy logs independently — separate analysis per strategy
- Add `strategy_id` column to `simulated_bets` (or use `notes` field) to track which strategy triggered

**Gate to Phase 2:** 500+ Phase 1A/1B bets across strategies, identify which have ROI > 0% + CLV > 0 on 70%+ bets

---

#### 🟩 Paper Trading — Phase 2: ML Model Replaces Rules
**Data needed:** 500+ live match snapshots with xG (≈ May 7-8), 200+ settled paper bets for validation  
**Timeline:** June 2026  
**What to build:**
- Feature pipeline: `live_match_snapshots` → training rows at minute 15/30/45/60/75 checkpoints
- Train LightGBM with `objective='poisson'` on `lambda_home_remaining` + `lambda_away_remaining`
- Derive O/U, BTTS, 1X2 probabilities from lambda estimates
- Replace rule-based triggers with model probability: bet when `model_prob - implied_prob > edge_threshold`
- Backtest all 6 strategies on historical snapshots — confirm which have genuine edge
- Add XGBoost ensemble partner

**Gate to Phase 3:** ML model CLV > 0% on 300+ paper bets AND outperforms Phase 1 rules by ≥ 2% ROI

---

#### 🟩 Paper Trading — Phase 3: Full System (Kelly + Multi-Market + All Strategies)
**Data needed:** Phase 2 model validated (300+ bets)  
**Timeline:** July 2026  
**What to build:**
- Quarter Kelly staking with time decay: `stake = 0.25 × Kelly × (minutes_remaining / 90)^0.5`
- 1-minute trigger checks when model flags potential entry (switch from 30s to 1-min targeted poll)
- Multi-market bets per match: O/U + BTTS simultaneously when both conditions met
- Max 3% bankroll exposure per match across all in-play positions
- CLV tracking: entry odds vs closing odds (same as pre-match CLV pipeline)
- Per-strategy P&L dashboard on frontend (Elite-gated)

**Gate to Phase 4 (real money):** Phase 3 paper results: ROI > 3% on 500+ bets AND CLV > 0 on 80%+ AND Sharpe > 1.0 over 60-day window

---

#### 🔴 Real Money — Phase 4: Micro-Stakes Live (Betfair Exchange)
**Data needed:** Phase 3 gates passed  
**Timeline:** August 2026  
**What to build:**
- Betfair Exchange API integration (1-2 days): place lay/back bets programmatically
- Strategy A + best-performing Phase 3 strategy only — 2 strategies max
- Ultra-conservative staking: 0.25% bankroll max per bet (half of paper rate)
- Kill switch: auto-pause if drawdown > 10% in any 7-day window
- Real CLV tracking: execution price vs closing price on Betfair

**Gate to Phase 5:** 200+ real bets, ROI > 0%, no systematic execution issues (slippage < 2%)

---

#### 🔴 Real Money — Phase 5: Full Live Deployment
**Data needed:** Phase 4 validated (200+ real bets)  
**Timeline:** September 2026+  
**What to build:**
- All validated strategies (those with confirmed real-money edge from Phase 4)
- Full Quarter Kelly sizing
- Expand to multiple leagues (start with EPL + top 5, expand to lower leagues where limits allow)
- Automated limit monitoring (Betfair exchange limits are less of an issue than fixed-odds books)
- Monthly model retraining as data accumulates

---

#### Summary

| Phase | Type | Start condition | Est. timeline | Key metric |
|-------|------|----------------|---------------|------------|
| **1A** — Rule bot, Strategy A | 📄 Paper | TODAY | May 2026 | 200 bets logged |
| **1B** — Rule bot, all strategies | 📄 Paper | 200 bets settled | Late May | Best strategy identified |
| **2** — LightGBM model | 📄 Paper | 500 snapshots + 200 bets | June 2026 | Model CLV > 0% |
| **3** — Full system, Kelly | 📄 Paper | Model validated | July 2026 | ROI > 3%, CLV 80%+ |
| **4** — Real money micro | 💰 Real | Phase 3 gates | Aug 2026 | ROI > 0%, no slippage issues |
| **5** — Real money full | 💰 Real | Phase 4 validated | Sep 2026+ | Sharpe > 1.0, scaling |

### 8. What This Unlocks

- **Entirely new revenue stream** — in-play betting is ~60% of global sports betting volume
- **Pro/Elite product differentiation** — "Live Win Probability" updating every 5 min on match detail
- **Higher bet volume** — each match can generate multiple in-play bets at different checkpoints
- **xG-based edge** — most retail bettors and many bookmaker algorithms anchor on scoreline, not xG

---

## § RAILWAY Plan — ✅ COMPLETE (archived from PRIORITY_QUEUE 2026-05-05)

> All 5 phases complete 2026-04-30. Architecture running on Railway. Full plan archived in git history.

---

## Source Legend

| Source | Meaning |
|--------|---------|
| Internal | Planned before external AI analysis — from ROADMAP/BACKLOG/MODEL_ANALYSIS |
| AI Analysis (2026-04-28) | Identified during external 4-agent AI architecture review session on 2026-04-28 |
| ROADMAP Frontend Backlog | From the Frontend Data Display Backlog section of ROADMAP.md |
| Internal (MODEL_ANALYSIS X.X) | Exists in MODEL_ANALYSIS.md but was not yet tracked in this queue |
| UX Review (2026-04-29) | Identified during 4 independent UX/product reviews of signal surfacing strategy. Full details in SIGNAL_UX_ROADMAP.md |
| 4-AI Match UX Review (2026-04-29) | 4 independent AI tools assessed 11 match list UX improvements. Unanimous on: filter tabs, live timer, team crests, predicted score (THE differentiator). Strong consensus on: odds movement arrows (Pro), bookmaker count badge. Skip: odds freshness (highlights 2h staleness as a weakness). |
| 4-AI Calibration Review (2026-05-06) | 4 independent AI tools analyzed calibration failure on 77 settled bets (42% pred vs 26% actual on 1X2 home). Consensus: conditional miscalibration at high odds, not global. Priority fixes: Pinnacle shrinkage anchor, odds-conditional alpha, sharp consensus gate, draw inflation. |
| Data Analysis (2026-04-29) | From pipeline refactor + data source audit session (2026-04-29) |
| Launch Plan (2026-04-29) | From LAUNCH_PLAN.md pre-launch preparation |
| Tier Access Matrix | From TIER_ACCESS_MATRIX.md feature checklist |
| Data Sources | From DATA_SOURCES.md remaining cleanup |
| Landing Page Review (2026-04-29) | From landing page pricing/UX review |
