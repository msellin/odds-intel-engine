# Coolbet Runbook — where to look when Coolbet breaks

Coolbet is the **only bookmaker the operator places real money at**, so its
collection + placement chain is load-bearing. This is the diagnostic reference:
the architecture, the failure modes, and the one-line fix for each. Written
2026-09-07 after a ~day-long outage whose real cause (FlareSolverr down) took an
hour to find because the symptom (HTTP 404 on every endpoint) looked like three
other things.

Companion docs: `WORKFLOWS.md` → "🍎 Mac-side jobs" (the launchd table);
`project_coolbet_limitations` and `feedback_*coolbet*` memories (behavioural
rules).

---

## The chain (every hop can fail independently)

```
operator's Mac (RESIDENTIAL IP)          ← Imperva blocks the VPS/Hetzner IP + Linux Chrome fingerprint
   │
   ├─ CDP-Chrome on :9222 (real logged-in Chrome, shared Default profile)
   │     → holds the session + JWT; harvests Imperva cookies (reese84, visid_incap…)
   │
   ├─ FlareSolverr Docker  oi_local_flaresolverr  :8191   ← THE hop that was down 2026-09-07
   │     → proxies every Coolbet HTTP call through a real browser so Imperva says
   │       "Challenge not detected". WITHOUT it, calls fall back to plain requests
   │       and Imperva soft-404s them.
   │
   ├─ Coolbet sportsbook API  (www.coolbet.com/s/…)
   │     → endpoints below
   │
   └─ VPS Postgres  odds_snapshots(bookmaker='Coolbet')  +  coolbet_session_state
```

**Transport rule:** the odds READER and PLACER both route through FlareSolverr
(named FS session `coolbet_prod`). Only interactive login drives CDP-Chrome
directly. The VPS scheduler cannot do any of this — it has the wrong IP — which
is why all Coolbet jobs run on the Mac via launchd.

## Endpoints (verified live 2026-09-07 — paths are STABLE, do not assume "retired" on a 404)

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/s/sbgate/category/fo-tree/et?country=EE` | competition tree (categoryId + `matches_count`) |
| GET  | `/s/sbgate/sports/fo-category/?categoryId=<id>&country=EE&isMobile=0&language=et&layout=EUROPEAN&limit=<n>&matchTypeFilter=all` | events in a competition (bulk listing) |
| GET  | `/s/sbgate/sports/search/v2?search=<q>&country=EE&language=en&layout=EUROPEAN` | per-match fuzzy search (fixture → Coolbet event) |
| POST | `/s/sbgate/sports/fo-match` | a match's main markets (no odds) |
| GET  | `/s/sbgate/sports/fo-market/sidebets?matchId=<id>&…` | a match's sidebet markets (corners, cards, 1H…) |
| POST | `/s/sb-odds/odds/current/fo` | simple-market odds (line=0) |
| POST | `/s/sb-odds/odds/current/fo-line/` | line-market odds (OU, AH) — body `{"marketIds":[[…]]}` |

To re-capture these after a Coolbet UI change: drive CDP-Chrome (:9222, already
logged in) to a competition page + a `/et/sport/match/<id>` page with
`Network.enable` and read `requestWillBeSent`. The search box is
`input[name="sportSearch"]`.

## Launchd jobs (Mac only)

| Label | Cadence | What |
|-------|---------|------|
| `com.oddsintel.coolbet-mac-daemon` | poll every 30 min (continuous) | placement daemon — reads qualified `simulated_bets`, matches to Coolbet, places (PAPER: `execute=False`) + Telegram-signals. Probes FS health at the top of each tick. |
| `com.oddsintel.coolbet-odds-snapshot` | :03 / :33 | `coolbet_explorer --days 2` — bulk odds into `odds_snapshots`. |
| `com.oddsintel.coolbet-feed-watchdog` | :20 / :50 | cookie refresh + staleness. |
| `com.oddsintel.coolbet-ui-placer` | — | UI-driven placement path. |

`cs2-coolbet-scanner` was **removed 2026-09-07** (referenced deleted esports code).

Manage: `launchctl list | grep oddsintel` · `launchctl kickstart -k gui/$(id -u)/<label>` · `tail -f dev/active/<name>.log`.

Pause/resume the two footprint jobs safely (auto-resume built in):
`scripts/ops/coolbet_pause_resume.sh {pause|resume|status}`.

---

## Failure modes — symptom → cause → fix

The trap: **FS-down, Imperva-challenge, expired-session, and no-fixtures all
present differently but can each stop collection.** Diagnose in this order.

### 1. FlareSolverr is down  → HTTP **404** (authenticated) on every endpoint
- **Symptom:** sweep logs `fo-category unavailable (HTTP Error 404)` + `search unavailable (HTTP Error 404)` then `Coolbet unreachable — sweep aborted`. But the **browser** gets 200 on the same URLs.
- **Tell:** `curl http://localhost:8191/` → connection refused. `docker ps` shows no `oi_local_flaresolverr`.
- **Fix:** `cd local/flaresolverr && docker compose up -d` — verify `curl http://localhost:8191/` returns `{"msg":"FlareSolverr is ready!"}`. Container has `restart: unless-stopped`.
- **Alert:** `workers/jobs/flaresolverr_health.py`, called from the daemon tick — pages with this exact fix. Added after 2026-09-07 when this went unalerted for a day.

### 2. Imperva challenge  → the "STAY COOL" / "Pardon Our Interruption" wall
- **Symptom:** the browser page body is ~9 chars (`STAY COOL`) or contains `Pardon Our Interruption`; `x-iinfo` response header present.
- **Tell:** raw `curl` of `coolbet.com` returns the interstitial HTML even at HTTP 200.
- **Fix:** this is genuine bot-detection escalation, usually triggered by our own request volume from one IP. Reduce footprint (`coolbet_pause_resume.sh pause`), let the flag decay, load the site in a **foreground** real-Chrome tab to solve the challenge (a backgrounded `--no-startup-window` instance can't complete the JS PoW). Do **not** build a challenge solver. The token is TLS/JA3-bound, so replaying `reese84` into plain `requests` cannot work — this is exactly why FS (a real browser) is mandatory.

### 3. Session / JWT expired  → `logged_out`
- **Symptom:** `coolbet_session_state.session_healthy=false`, heal log `session expired — operator must log in`.
- **Tell:** `python -m workers.automation.coolbet_browser_sync --full-heal --full-heal-dry-run` → `state=logged_out`.
- **Fix:** log into coolbet.com in CDP-Chrome (:9222). Auto-login: `--cdp-auto-login` (reads `COOLBET_USER/PASS`, waits for SMS). Then `--full-heal` persists the JWT and clears `placement_paused`. Reading odds does **not** need the JWT (anon-read); only placement does.

### 4. Placement self-paused  → daemon runs but places nothing
- **Symptom:** signals generate, odds flow, but 0 placements. `coolbet_session_state.placement_paused=true`, reason `daemon self-pause: N consecutive errors`.
- **Cause:** the daemon self-pauses after `SELFPAUSE_AFTER_MINUTES` of errored ticks to stop hammering Coolbet during an outage. Once the outage is fixed the pause stays **latched** until cleared.
- **Fix (once session healthy + odds flowing):** `UPDATE coolbet_session_state SET placement_paused=false WHERE id=1;` or `--full-heal` (clears it on a state transition). A daemon **restart** also clears it.
- **Note:** only genuine failures (`error`, `search_blocked`) count toward the self-pause. Legitimate declines — odds-floor, drift, exposure — are **skips, not errors** (fixed 2026-09-07 in ODDS-FLOOR-SKIP-NOT-ERROR; before that a below-floor day falsely self-paused a healthy daemon).

### 5. No placeable bet today  → this is CORRECT, not a failure
- **Symptom:** daemon tick logs `qualified=N` but `placed=0`, every candidate `skip … below the 2.80 odds floor`.
- **Cause:** all qualifying candidates are priced below the executable odds floor (CLV goes negative below ~2.80). The bot is correctly declining -EV prices.
- **Fix:** none — thin/below-floor value is a normal day. Verify it is this and not #1–4 by checking `qualified>0` and that the skips are `odds_floor`, not `search_blocked`.

---

## One-shot diagnostics

```bash
# is FS up?
curl -s http://localhost:8191/ | head -c 80

# full session/JWT state (read-only)
python3 -m workers.automation.coolbet_browser_sync --full-heal --full-heal-dry-run

# run a single placement tick and watch the whole chain (paper, no writes with --dry-run)
python3 -m workers.automation.coolbet_mac_daemon --once

# is Coolbet odds landing?  (DB timestamps display ~3h behind wall-clock — compare deltas, not absolutes)
psql "$DATABASE_URL" -c "select max(\"timestamp\"), count(*) from odds_snapshots where bookmaker='Coolbet' and \"timestamp\">=now()-interval '40 min'"

# FS health check (the alert, dry)
python3 -m workers.jobs.flaresolverr_health --dry-run
```
