# Coolbet Automation Roadmap

> Single source of truth for everything Coolbet-related. Architecture, status,
> done tasks, queued tasks, ideas. Update as work ships.
>
> Last updated: 2026-05-20

---

## Architecture

**One daemon, many separately-callable pieces.** Already shipped.

```
                  ┌────────────────────────────────────────┐
                  │  scripts/coolbet_daemon.py             │
                  │  (foreground, all-in-one loop)         │
                  │  ─ keepalive  every 20m                │
                  │  ─ odds       every 30m                │
                  │  ─ placement  every  5m                │
                  └─────────┬──────────────────────────────┘
                            │   calls
                            ▼
            ┌───────────────────────────────────────────┐
            │  workers/automation/         (libraries)  │
            │   ├ coolbet_session.py    auth + throttle │
            │   ├ coolbet_explorer.py   odds + markets  │
            │   └ coolbet_placer.py     place_all_bets  │
            └───────────────────────────────────────────┘
                            ▲
                            │   also used standalone:
            ┌───────────────┴───────────────────────────┐
            │ python3 -m workers.automation.coolbet_explorer ...│
            │ python3 scripts/place_coolbet_bets.py     │
            │ python3 scripts/audit_silent_bots.py      │
            │ (Railway scheduler jobs — same libraries) │
            └───────────────────────────────────────────┘
```

Each piece can be invoked on its own — daemon is just the convenient bundle.

---

## Current status by component

| Component | Path | Status |
|---|---|---|
| Session (auth, JWT 30m, Imperva cookies, keep_alive, jwt_seconds_remaining, **throttle**) | `workers/automation/coolbet_session.py` | ✅ Working |
| Odds explorer (markets+odds new schema, parse_market, resolve_placement_target, fetch_match_markets, fetch_odds_for_markets) | `workers/automation/coolbet_explorer.py` | ✅ Working |
| Placer (`place_all_bets` dry/record/execute, new-schema market resolution) | `workers/automation/coolbet_placer.py` | ✅ Working (don't `--execute` until guardrails) |
| Foreground daemon (3 loops, --place-mode safety default) | `scripts/coolbet_daemon.py` | ✅ Working |
| Audit script | `scripts/audit_silent_bots.py` | ✅ Working (6 sections) |
| Scheduler — odds snapshot every 30m | `workers/scheduler.py` | ⚠️ May 403 from Railway IP (Imperva). Error-isolated. |
| Scheduler — keepalive every 20m | `workers/scheduler.py` | ⚠️ Same |

---

## ✅ Done

| ID | Date | Description |
|---|---|---|
| **COOLBET-ODDS-SNAPSHOT** | 2026-05-20 | Markets+odds ingest on new Coolbet schema. fo-match + sidebets + odds (simple + line endpoints). parse_market maps to our shape. |
| **COOLBET-OR-PIN-REQUIRED** | 2026-05-20 ❌ dropped | Audit showed Pinnacle covers bot leagues; Coolbet uplift = 0. Not the blocker. |
| **SHADOW-RETIRED-OK** | 2026-05-20 | Retired bots produce shadow_bets so the alpha-recovery criterion is measurable. |
| **COOLBET-DAEMON** | 2026-05-20 | Foreground daemon — 3 loops on independent cadences. Safe default placement mode. |
| **COOLBET-KEEPALIVE** | 2026-05-20 | session.keep_alive() + jwt_seconds_remaining + scheduler job every 20m. JWT TTL is 1820s. |
| **COOLBET-PLACER-NEW-SCHEMA** | 2026-05-20 | Placer per-bet loop rewritten for new Coolbet schema. resolve_placement_target maps our bet → Coolbet (market_id, outcome_id, odds_id, current_odds). |
| **COOLBET-HUMAN-PACED** | 2026-05-20 | Every CoolbetSession.get/post routes through _throttle() with 0.8–2.0s jittered gap. Anti-scraper defense. |
| **COOLBET-DELETE-REDUNDANT** | 2026-05-20 | Deleted coolbet_keepalive.py + probe_coolbet.py — daemon supersedes them. |
| **COOLBET-PREFLIGHT** | 2026-05-20 | `scripts/coolbet_preflight.py` runs 5 checks before daemon starts. Daemon aborts if critical checks fail. |
| **BOT-OU15-DIAGNOSE (section 6)** | 2026-05-20 | Audit section 6 tests ACCESSIBLE-BM hypothesis. Verdict: 13.3% non-accessible — ACCESSIBLE-BM not the cause. |
| **BOT-FUNNEL-DIAGNOSTIC** | 2026-05-20 | Per-bot candidate-funnel logging. Smoking gun on bot_ou15_defensive: 97/98 candidates die at ↓edge — pure threshold starvation. |
| **BOT-OU15-EDGE-REPAIR** | 2026-05-20 | Thresholds relaxed (T1/T2 4%, T3/T4 3%) as 2-week paper experiment. |
| **BOT-COHORTS-ALL** | 2026-05-20 | All bots set to cohort='all' — fire at every betting_refresh window. Dedup prevents duplicates. |
| **SHADOW-COHORT-CONSTRAINT** | 2026-05-20 | Migration 112 — shadow_bets accepts HHMM scheduler labels (was silently failing). |
| **COOLBET-PLACE-PAYLOAD-MATCHED** | 2026-05-20 | Bet placement POST body + headers matched byte-for-byte against a captured browser bet curl. Earlier 400 (`GenericBadRequestError "Invalid request"`) traced to extra schema keys (`currency`, `acceptOddsChanges`) + populated `outcomeName` (browser sends `""`). Now sends only the keys the browser sends; `last-failed-placement.json` dumped to `~/.coolbet-daemon/` on any non-2xx for future diffing. |
| **COOLBET-PARSER-ANY-SHAPE** | 2026-05-20 | `_place_bet_api` tolerates any Coolbet success-response shape (dict / list / bare string / nested `{ticket:…}`, `{data:…}`, `{result:…}`). Always dumps `last-placement-{success,failed}.json` to `~/.coolbet-daemon/`. Bug surfaced on first execute-mode trial: bet landed at Coolbet (€9.76 Catania vs Lecco, ticket=26052017-…) but parser crashed extracting ticket id → row backfilled manually. |
| **COOLBET-FIRST-REAL-EXECUTE** | 2026-05-20 | First real-money execute-mode placement landed end-to-end. Catania vs Lecco O/U 2.5 Over @ 2.25, €9.76, bot_aggressive_v2, Kelly stake, 5.0% edge. Coolbet ticket `26052017-e574-4c58-9886-f40118688344` (#123). Backfilled `real_bets=f1882899-…`. Settlement job already wires `real_bets` into the post-match settle pass. |
| **COOLBET-MANUAL-JWT** | 2026-05-20 | Smart-ID / 2FA accounts can't use `/s/auth/login` API. Bypass: set `COOLBET_MANUAL_JWT` to a `cbauth` Bearer pasted from browser; session decodes `sub`+`login_session_id` from the JWT payload and skips API login entirely. Preflight check 2 surfaces TTL when MANUAL_JWT is set. |
| **COOLBET-AUTO-COOKIE-REFRESH** | 2026-05-20 ⚠️ superseded | Headless-Chrome path turned out non-viable (Coolbet session doesn't survive Chrome process restarts even with persistent profile + saved cookies — frontend security-wipes auth localStorage on cold load). Setup + refresh scripts kept as fallback discovery tools but daemon no longer depends on them. Replaced by COOLBET-JWT-API-RENEW. |
| **COOLBET-JWT-API-RENEW** | 2026-05-20 | Discovered Coolbet's frontend hits `POST /s/auth/renew-token` (empty `{}` body, authenticated by current JWT in `cbauth`) every ~20 min to swap to a fresh token. Hooked into the same endpoint from Python: `CoolbetSession.renew_jwt_via_api()` posts, parses any-shape JSON response for the new JWT, calls `_adopt_manual_jwt()` to swap in-memory, persists to `.env` via `dotenv.set_key`. Daemon's renewal task now calls this directly — pure API, no browser, no Chrome, no Smart-ID. Renews at 20-min cadence to mimic browser pattern. Operator only needs Smart-ID + paste fresh JWT when renewal returns 401/403 (session truly expired, ~daily). Telegram alert on dead-JWT with recovery command. Validated live: TTL went 712s → 1799s on first call. **This is the change that converts the daemon from babysitting-every-30-min to ~30-sec-attention-per-day operations.** |
| **COOLBET-MAINTENANCE-KEEPALIVE** | 2026-05-20 | Switched the keepalive endpoint from `/fo-category` (heavy) to `/s/casino/fo/maintenance?licence=EE` (~2KB) — what Coolbet's frontend pings every 5 min. Daemon cadence tightened 20m → 5m to match browser pattern. Two wins: smaller per-call payload, exact-match fingerprint for Imperva. Live-verified 210ms response. |
| **COOLBET-INPLAY-SNAPSHOTS (Mode B paper default)** | 2026-05-20 | Measures slippage between inplay-bot decisions and Coolbet live markets, AND surfaces inplay PnL in existing dashboards / daily summary / bot ROI reports. Migration 115 adds `coolbet_inplay_snapshots` + `AFTER INSERT` trigger on `simulated_bets` that `NOTIFY inplay_bet_fired` when `xg_source IS NOT NULL`. Daemon runs a dedicated psycopg2 `LISTEN` thread; each NOTIFY → 1 Coolbet GET (`fetch_match_markets(live=True)` + `fetch_odds_for_markets`) → 1 snapshot row → 1 `real_bets` row (Mode B). **Zero polling on either side** — purely event-driven, ~1 Coolbet call per inplay signal (~30-50/day expected → trivial Imperva load). Three modes shipped (capture/paper/execute), default `paper`. `--inplay-mode` CLI flag + `/inplay_mode` Telegram command + `_CTRL` runtime override. Mode A (capture) = data-only, no real_bets row. Mode B (paper, default) = capture + `real_bets` row with `notes='inplay paper'`, no POST. Mode C (execute) = paper + POST `/s/bets/bets` (REAL MONEY, gated by `--max-stake-per-bet`). 20% odds-drop tolerance flags `odds_drop_too_large` outcome before B/C side effects fire. |

---

## ⬜ Open — critical path to live auto-placement

| ID | Pri | Effort | Impact | Description |
|---|---|---|---|---|
| **COOLBET-SAFETY-GUARDRAILS** | P0 | 1-2h | Critical — gates first real-money runs | `--max-bets-per-hour N` throttle, `--max-stake-per-bet €X` override, `--bot-filter bot1,bot2`, `--pause-after-loss €N` kill-switch, `--max-edge-pct N` (refuse absurd edges = model bug / odds error), `--require-confirm` (y/n prompt per bet for first live runs). Must precede first `--place-mode=execute`. |
| **TELEGRAM-NOTIFY** | P1 | 30m–1h | Observable without being at the laptop | Send-only Telegram from daemon. Three notifications: (1) Imperva 403 = cookies expired (loud), (2) placement success/failure in execute mode (so you know real money moved), (3) end-of-day summary. Reuses user's existing IBKR Telegram bot (just add `[OI]` prefix) or new chat. ~50 lines, `requests` only (no new deps). Subsumes COOLBET-IMPERVA-ALERT + COOLBET-DAILY-SUMMARY's delivery channel. |

## ⬜ Open — operational visibility (do before leaving daemon unattended)

| ID | Pri | Effort | Impact | Description |
|---|---|---|---|---|
| **COOLBET-DAILY-SUMMARY** | P1 | 1h | Operator visibility | At UTC end-of-day: emit one summary — bets placed, total stake, paper-vs-real ROI delta, anomalies (skipped due to odds drop, no_market, etc.). Goes to Telegram if TELEGRAM-NOTIFY is wired; stdout/file otherwise. |
| **COOLBET-PERSISTENT-LOG** | P1 | 30m | After-the-fact diagnosis | `logs/coolbet_daemon-YYYY-MM-DD.log` rotating file alongside stdout. |
| **COOLBET-HEALTHCHECK** | P2 | 30m | External monitoring | HTTP `/healthz` on localhost:8765 returning JSON `{jwt_ttl, last_keepalive, last_odds, last_place, errors_last_hour}`. |
| **COOLBET-STATE-PERSISTENCE** | P2 | 1h | Clean resume after restart | `~/.coolbet-daemon-state.json` holding last timestamps + bets-seen set. Restart picks up where it left off. |

## ⬜ Open — coverage + cleanup

| ID | Pri | Effort | Impact | Description |
|---|---|---|---|---|
| **COOLBET-WIDER-ODDS-POLL** | P2 | 30m | More signal data | `--odds-mode=bets-only\|wide\|leagues`. `wide` = all upcoming matches in `--days` window. Useful for seeding historical Coolbet coverage. |
| **COOLBET-BTTS-DC-AH-MTIDS** | P2 | 30m | Cleaner parsing, locale-stable | Once we observe Coolbet BTTS / DC / AH markets (haven't appeared yet in small-league test matches), capture their `market_type_id` values and add to `_MTID_BTTS` / `_MTID_DC` / `_MTID_AH` in `coolbet_explorer.py`. Name-based fallback works but is locale-fragile. |
| **COOLBET-DEDUP-DUPES** | P2 | 30m | Storage hygiene | OU lines appear in both fo-match (main) and sidebets (depth) → 2× rows per ingest. Dedup by (market_id, outcome_id) inside `store_coolbet_snapshots_for_match` before insert. Not breaking, just wasteful. |
| **COOLBET-ACTIVE-HOURS** | P3 | 30m | Quiet overnight | `--active-hours 6-23` skips keepalive + polling overnight. Minor API/log noise reduction. Also less obviously bot-like. |
| **COOLBET-RAILWAY-KILL** | P3 | 5m | Reduce noise | If Railway-scheduled jobs 403 every cycle, remove them. Currently error-isolated so cost is just log noise. |

## ⬜ Open — speed/latency (defer until placement is live and CLV matters)

| ID | Pri | Effort | Impact | Description |
|---|---|---|---|---|
| **COOLBET-FAST-PLACE** | P3 | 1h | React quicker to new bets | Tighten placement loop 5m → 1m, OR adaptive: 1m for first 5m after each `betting_refresh`, 10m otherwise. |
| **COOLBET-EVENT-DRIVEN-PLACE** | P3 | 2-3h | Zero-lag placement | Postgres `LISTEN`/`NOTIFY` from `betting_refresh` → daemon fires placement immediately. Defer until single-digit-minute lag proves insufficient. |
| **COOLBET-TWO-TIER-POLL** | P3 | 1h | Coverage + cost balance | Bets-only every 30m + nightly wide sweep at 04:00. Better historical coverage. |
| **COOLBET-LEAGUE-FILTER** | P3 | 30m | Trim API calls | Limit odds polling to top-tier leagues we'd actually bet on. Niche if `--bets-only` is default. |
| **COOLBET-MULTI-MODES** | P3 | 30m | Dev ergonomics | `--once`, `--odds-only`, `--place-only`, `--no-keepalive`. |
| ✅ ~~**TELEGRAM-COMMANDS**~~ | Done 2026-05-20 | — | Shipped: `/help` `/status` `/pause` `/resume` `/place_mode` `/summary` `/relogin`. Bot listener runs in daemon background thread. Chat-ID whitelist for security. `/place_mode execute` refuses unless `--max-stake-per-bet` was provided at daemon launch. |
| ✅ ~~**COOLBET-AUTO-COOKIE-REFRESH**~~ | Done 2026-05-20 | — | See moved entry above. Shipped: one-time `coolbet_browser_setup.py` + headless `coolbet_refresh_jwt.py` + daemon task every 25 min + Telegram `/relogin`. |
| **COOLBET-SMARTID-AUTOFILL** | P2 | 2-3h | Fully unattended re-auth | Follow-up to COOLBET-AUTO-COOKIE-REFRESH. When Chrome profile session expires (currently surfaced via Telegram alert), today's recovery is "rerun the setup script at laptop". Better: Telegram `/relogin` auto-clicks Smart-ID button + auto-fills Estonian ID code (from `COOLBET_SMART_ID_CODE` env) → user just taps PIN1 on phone. Needs login DOM selectors (login-dom.json is captured by setup script for exactly this purpose). |
| **COOLBET-STATUS-PRETTIER** | P3 | 30m | Operator UX | `/status` Telegram reply currently strips Rich's box-draw chars and wraps in `<pre>` — looks like a code block / md table on mobile. Reformat as proper Telegram HTML: bold headers, emoji icons inline, per-line bullets. CLI stdout output stays Rich-pretty; only the mobile rendering needs polishing. Optional cosmetic. |

---

## Recommended sequence (toward live auto-placement)

1. ✅ ~~COOLBET-PREFLIGHT~~ — done 2026-05-20
2. **COOLBET-SAFETY-GUARDRAILS** (1-2h) — non-negotiable before first `--execute`
3. **TELEGRAM-NOTIFY** (30m-1h) — Imperva alerts + placement notifications + observable without laptop
4. *Flip to:* `--place-mode=execute --require-confirm --max-bets-per-hour 3 --max-stake-per-bet 5` → first live placements with training wheels
5. **COOLBET-DAILY-SUMMARY** (1h) + **COOLBET-PERSISTENT-LOG** (30m) — close observability
6. *Loosen training-wheel limits as confidence grows*
7. Everything else (P2/P3) — as needs surface

Total remaining to safe-live: **~2-4h of focused work** + paper-test cycle after step 4.

---

## Current state (live)

As of 2026-05-20 ~12:00 UTC, the daemon is running in tmux session `coolbet` in dry mode:

```
tmux new -s coolbet                    # already running
python3 scripts/coolbet_daemon.py      # already executing inside tmux
                                       # detached: Ctrl-B then D
```

Cadences confirmed live:
- KEEPALIVE every 20m — first heartbeat ✓ at startup, JWT TTL ~30 min
- ODDS SNAPSHOT every 30m — first cycle found 4 value-bet matches
- PLACEMENT every 5m — `--place-mode=dry`, fuzzy-matching successfully

What to do today:
- Leave it running. Watch logs via `tmux attach -t coolbet` whenever curious.
- After tomorrow's pipeline cohorts, check `python3 scripts/audit_silent_bots.py` → expect bot_ou15_defensive firing again (BOT-OU15-EDGE-REPAIR + BOT-COHORTS-ALL changes should kick in immediately at next betting_refresh).

What NOT to do today:
- Don't flip `--place-mode=execute` until COOLBET-SAFETY-GUARDRAILS ships. Dry-run is the only safe live mode currently.

---

## Out-of-band: bot_ou15_defensive silence investigation

This sits adjacent to Coolbet but the silence isn't a Coolbet problem. Status of the investigation as of 2026-05-20:

- ❌ OU-PIN-REQUIRED (May 10) — Pinnacle covers 80–100% of bot leagues, not the cause
- ❌ COOLBET-OR-PIN-REQUIRED — would not move the needle (0 uplift)
- ❌ ACCESSIBLE-BM (May 11) — only 13.3% non-accessible. Not the cause.
- ❌ PIN-VETO-EXT (May 12) — bot silent since May 8, can't explain pre-shipping
- ❌ ALN-1 (May 12) — same, ships post-silence
- ❌ MFV inference activation (May 10) — same
- ❌ May 17 retrain — bot silent 9 days before retrain
- ❓ KILL-SWITCH-FLAGS (May 8) — could `paper_betting` or similar have been inadvertently set?
- ❓ STAKE-RANK / API-RETRY-WRAPPER (May 8) — unlikely to affect candidate generation
- ❓ Pure variance — bot had earlier 0-bet days (May 2, 7) so noise is possible, but 12 consecutive zeros vs a 22-bet day before is suspicious
- ❓ **Most likely remaining**: candidate generation funnel — log per-bot, per-filter, how many candidates fall out where. Requires either temporary `--verbose-funnel` pipeline flag or a new audit section replicating `_load_today_from_db` logic.

**Next step (if continuing):** add a `--verbose-funnel` flag to `daily_pipeline_v2.run_morning` that logs per-bot candidate count at each filter step (markets present → in odds_range → above edge threshold → not vetoed → final). Run morning pipeline once with the flag; output tells us definitively where bot_ou15_defensive's candidates die.
