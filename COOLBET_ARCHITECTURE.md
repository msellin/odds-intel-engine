# Coolbet Automation Architecture

> Single source of truth for how the Coolbet placement + ingest stack is wired.
> Read this before touching any `workers/automation/coolbet_*.py`, the Mac launchd
> job, or anything FlareSolverr-related.
>
> Last updated: 2026-06-12

---

## The split, in one paragraph

**Railway runs read-only plumbing 24/7** (odds snapshots, signaler, heartbeats,
esports/tennis ingest). **The Mac at home runs authenticated placement** because
Coolbet's Imperva flags Railway IPs and forces SMS-2FA re-login. **CDP-Chrome on
the Mac** holds a long-lived logged-in Coolbet session — its localStorage is the
source of fresh JWTs. **FlareSolverr (Docker on the Mac)** is the shared
Imperva-bypass proxy used by both Coolbet and HLTV scrapers.

```
                     Railway worker (cloud, 24/7)
                     ─────────────────────────────
                     • coolbet_explorer.run_bulk     (odds, anon-read)
                     • coolbet_signaler              (Telegram signals)
                     • coolbet_health_ping           (heartbeat)
                     • sweep_stale_sessions          (FS GC)
                     • cs2_coolbet_scanner/placer    (CS2, --record only)
                     • coolbet_tennis_scanner        (tennis odds)
                                  │
                                  ▼ writes simulated_bets, reads odds
                       ┌─────────────────────────┐
                       │   Supabase Postgres     │
                       │  coolbet_session_state  │ ← JWT lives here
                       │  simulated_bets         │ ← qualified picks
                       │  real_bets              │ ← placement audit
                       └─────────────────────────┘
                                  ▲
                                  │ reads simulated_bets, writes real_bets
                     Mac at home (residential IP)
                     ─────────────────────────────
                     • launchd: coolbet_mac_daemon  ─ every 30 min
                            │
                            ├──► CoolbetSession (auth + transport)
                            │       │
                            │       ├──► FlareSolverr  (Docker :8191)
                            │       │       └─ GETs go through real Chrome
                            │       │           internally → Imperva pass
                            │       │
                            │       └──► requests.Session (plain)
                            │               └─ POSTs (cookies harvested
                            │                   from a FS GET first)
                            │
                            └──► CDP-Chrome  (port :9222, your tab)
                                    ├─ source of fresh JWT (localStorage)
                                    └─ source of pending-tickets list
                                       (dedup against manual bets)
```

---

## The four components

### 1. Mac daemon — `workers/automation/coolbet_mac_daemon.py`

**Process:** launchd job `com.oddsintel.coolbet-mac-daemon` (plist at
`local/launchd/com.oddsintel.coolbet-mac-daemon.plist`). One Python process,
lives forever, polls every `COOLBET_MAC_POLL_S=1800` (30 min). launchd
auto-restarts on crash; `ThrottleInterval=30` prevents busy-loop on config error.

**Per tick:**
1. `load_qualified_bets()` — read `simulated_bets` where `user_placed_at IS
   NULL AND user_skipped_at IS NULL`, edge passes filters, KO ∈ [-12h, +48h].
   If empty: silent exit (don't even touch Chrome/FS — avoids unwanted
   window-flash).
2. `_sync_placed_bets_from_coolbet()` — connect to CDP-Chrome, intercept
   `/s/sbgate/bets/history` response, mark matches as `user_placed_at` so we
   don't re-place anything you bet manually.
3. `place_all_bets(record=True, execute=True)` — for survivors, search market →
   resolve odds_id → POST `/s/bets/bets`. Writes `real_bets` row per success.
4. Counters logged; sleep until next tick.

**What it does NOT do:** No JWT minting (delegated to `CoolbetSession`), no
Telegram, no odds ingest, no SMS enrollment.

**Manage:**
```
launchctl list | grep oddsintel                                # is it loaded?
launchctl kickstart -k gui/$(id -u)/com.oddsintel.coolbet-mac-daemon  # restart
tail -f dev/active/coolbet-mac-daemon.log                      # tail logs
launchctl unload ~/Library/LaunchAgents/com.oddsintel.coolbet-mac-daemon.plist  # stop
```

---

### 2. CDP-Chrome — the long-lived logged-in browser

**Process:** Real Google Chrome launched with `--remote-debugging-port=9222`
against a dedicated profile (`~/Library/Application Support/Google/Chrome-CDP-OddsIntel`).
Launch script: `local/launch_chrome_for_sync.sh`. Stays open during betting
hours; you can use the window normally (placing bets, reading account).

**Two jobs:**

#### a) Source of fresh JWT (the auth seam)

Coolbet's frontend JS calls `/s/auth/renew-token` every ~20 min in the
background. The renewed JWT lands in `localStorage['cbauth']`. So as long as
this Chrome window is open, that storage slot always has a valid JWT.

`workers/automation/coolbet_browser_sync.extract_jwt_from_cdp()` connects via
CDP, finds an existing coolbet.com tab (reuses to avoid window-flash), reads
the storage slot, validates `exp > now + 60s`, returns the Bearer string.

`coolbet_session._login()` calls this *before* falling back to API login when
the DB-persisted JWT has expired. Result: zero-touch operation while the
browser is running. No SMS unless the browser itself logs out (rare — weeks).

#### b) Source of bet-history dedup

`fetch_pending_bets_via_cdp()` hits `/s/sbgate/bets/history` in a CDP tab and
returns the pending tickets list. The Mac daemon uses this to mark
`simulated_bets.user_placed_at` for bets you placed manually — prevents
double-placement.

**Why CDP and not a spawned Chromium:** Imperva trusts long-warmed residential
browsers. Spawned Chromium starts cold, gets challenged, the SPA never
initialises, API calls 403. Confirmed empirically 2026-06-12.

**Manage:**
```
./local/launch_chrome_for_sync.sh                              # start
curl -s http://localhost:9222/json/version                     # health check
ls /Users/margussellin/Library/Application\ Support/Google/Chrome-CDP-OddsIntel/  # profile
```

---

### 3. FlareSolverr — the Imperva-bypass proxy

**Process:** Docker container, exposes `http://localhost:8191/v1`. POST a JSON
command, FS executes it through its own headless Chrome internally and returns
the response. Persists named browser sessions (`coolbet_prod`, `hltv_*`) that
hold Imperva cookies between requests.

**Used by:**
- `workers/automation/coolbet_session.CoolbetSession`
    - Every `session.get(...)` → `_fs_get(...)` → FS routes through real Chrome.
    - Imperva-challenged search/match/odds endpoints all go through here.
    - **`session.post(...)` does NOT use FS** — see "POST quirk" below.
- `scripts/esports/flaresolverr_client.py` → every CS2 HLTV scraper.

**POST quirk:** FlareSolverr v2 force-encodes POST bodies as `application/
x-www-form-urlencoded` and truncates JSON to ~4 bytes (verified via httpbin
echo, 2026-06-11). So `CoolbetSession.post()` uses plain `requests.Session()`
*with cookies harvested from a FS GET*. Imperva validates cookies + UA on the
POST and accepts it because the cookies are real (FS-issued, just-minted).

**Used cookies after harvest:** `reese84`, `visid_incap_723517`, `nlbi_*`,
`incap_ses_*`, plus `uuid` (Coolbet's own deviceId, generated client-side and
required in the placement payload — we persist ours in
`coolbet_session_state.device_id`).

**Why we can't remove it:** Coolbet's authenticated GETs require Imperva pass.
HLTV scraping (~10 scripts) needs it too. Removing it kills both ingest paths.

**Manage:**
```
docker ps | grep flaresolverr                                  # is it up?
docker logs -f $(docker ps -q --filter ancestor=ghcr.io/flaresolverr/flaresolverr:latest)
python3 scripts/diagnose/flaresolverr.py                        # diagnostic + sweep
```

---

### 4. Railway worker — read-only plumbing 24/7

**Process:** `workers/scheduler.py` (APScheduler in-process). Long-running on
Railway, ~$5/mo. Triggered by `railway.toml` start command.

**Coolbet-related jobs:**

| Job | Schedule | Module | Auth? |
|---|---|---|---|
| `coolbet_odds_snapshot` | 7-22 UTC, every 30 min | `coolbet_explorer.run_bulk` | anon |
| `coolbet_health_ping` | every 5 min | `scripts/coolbet/health_ping.py` | uses DB JWT |
| `coolbet_sweep_stale_sessions` | every 30 min | `scripts/coolbet/sweep_stale_sessions.py` | n/a |
| `cs2_coolbet_scanner` | 7-22 UTC, every 30 min | `scripts/esports/cs2_coolbet_scanner.py` | anon |
| `cs2_coolbet_placer` | 10-23 UTC, every 30 min | `scripts/esports/cs2_coolbet_placer.py --record` | anon |
| `coolbet_tennis_scanner` | 7-22 UTC, every 30 min | `scripts/tennis/place_coolbet_tennis.py --record` | anon |
| Telegram signaler | fires from betting refresh jobs | `coolbet_signaler.send_signals` | anon |

**The signaler is the Mac daemon's safety net.** It always runs — even if the
Mac is asleep or Docker is stopped — and sends Telegram inline-button signals.
You can place from your phone if automation is offline.

**Why Railway never logs in:** every Railway-side call is either anon-read or
uses the DB-persisted JWT (no `/s/auth/login`). Imperva 403's
`/s/auth/login` from Railway IPs, which is what triggered the 2026-06-11 SMS
storm — `COOLBET_ALLOW_API_LOGIN` default = `false` guards this hard.

---

## One placement, end-to-end

```
06:00 UTC ─ Railway: betting pipeline writes 18 simulated_bets rows
06:00 UTC ─ Railway: signaler sends 18 Telegram messages to your phone
06:30 UTC ─ Mac daemon wakes:
            1. SELECT 18 qualifying picks
            2. CDP-Chrome → /s/sbgate/bets/history → 3 already placed manually
               → mark those rows user_placed_at, drop from candidates (now 15)
            3. For each of 15:
               a. CoolbetSession._ensure_auth():
                  - manual_jwt expired? try extract_jwt_from_cdp()
                  - got fresh JWT from CDP? adopt + persist to DB ✓
               b. FS GET /search/v2 → find event_id
               c. FS GET /fo-match → resolve market_id + outcome_id + odds_id
               d. plain POST /s/bets/bets (cookies from FS harvest)
               e. Write real_bets row
            4. tick counters logged
21:00 UTC ─ Railway: settlement reads real_bets → settles → updates bot PnL/ELO/CLV
```

---

## Failure modes & recovery

| Symptom | Likely cause | Fix |
|---|---|---|
| Daemon logs "JWT expired AND api_login is disabled" every tick | CDP-Chrome closed OR localStorage cleared | Open CDP-Chrome → `python3 -m workers.automation.coolbet_browser_sync --refresh-jwt` |
| `extract_jwt_from_cdp` returns None | No coolbet.com tab open in CDP-Chrome | Open a coolbet.com tab → re-run |
| CDP connect fails at `:9222` | Chrome not running with debug port | `./local/launch_chrome_for_sync.sh` |
| FS calls fail with "FLARESOLVERR_URL unset" | Docker stopped OR env stripped | `docker start flaresolverr` and verify launchd plist sets FLARESOLVERR_URL |
| `placement_paused=true` in DB | Emergency stop (SMS-spam guard, operator pause, etc.) | Verify root cause is fixed → `UPDATE coolbet_session_state SET placement_paused=false, placement_paused_reason=NULL WHERE id=1;` |
| Imperva 403 on FS GET | Cookies stale (rotate fast) | Wait one cycle — `_refresh_cookies_from_fs()` re-harvests on next call |

**Emergency stop (the kill switch):**
```sql
UPDATE coolbet_session_state SET placement_paused=true,
       placement_paused_reason='operator: <why>' WHERE id=1;
```
Both the Mac daemon (`coolbet_placer.is_placement_paused`) and any future
placer path read this and short-circuit.

---

## Files in play

### Active (don't delete)
| File | Purpose |
|---|---|
| `workers/automation/coolbet_mac_daemon.py` | The placement daemon (launchd) |
| `workers/automation/coolbet_browser_sync.py` | CDP-Chrome integration (JWT + bet-history dedup) |
| `workers/automation/coolbet_session.py` | JWT + cookies + FS transport |
| `workers/automation/coolbet_state.py` | DB session-state helpers (JWT persist, placement_paused, device_id) |
| `workers/automation/coolbet_placer.py` | `place_all_bets()` — market resolve + POST |
| `workers/automation/coolbet_explorer.py` | Odds ingest (used from Railway) |
| `workers/automation/coolbet_signaler.py` | Telegram signals (Railway) |
| `workers/automation/coolbet_inplay.py` | In-play module |
| `scripts/coolbet/flaresolverr_login_enroll.py` | SMS enrollment — emergency only |
| `scripts/coolbet/health_ping.py` | Heartbeat (Railway) |
| `scripts/coolbet/sweep_stale_sessions.py` | FS GC (Railway) |
| `local/launchd/com.oddsintel.coolbet-mac-daemon.plist` | launchd job definition |
| `local/launch_chrome_for_sync.sh` | CDP-Chrome launcher |

### Deprecated (slated for removal — see PRIORITY_QUEUE)
| File | Replaced by |
|---|---|
| `scripts/coolbet_daemon.py` | Mac daemon (smoke_test still pins it) |
| `scripts/coolbet_refresh_jwt.py` | `extract_jwt_from_cdp` |
| `scripts/coolbet_refresh_debug.py` | n/a |
| `scripts/coolbet_browser_setup.py` | `--refresh-jwt` CLI |
| `scripts/_daemon_handlers.py` | n/a (old daemon's Telegram handlers) |
| `scripts/place_one_real_bet.py` | n/a (manual test) |
| `scripts/coolbet/session_heartbeat.py` | `scripts/coolbet/health_ping.py` |
| `scripts/coolbet/flaresolverr_login_test.py` | one-shot diagnostic |
| `scripts/coolbet/inspect_2fa_endpoint.py` | one-shot diagnostic |

---

## Environment / secrets

Minimum `.env` for the Mac side:
```
DATABASE_URL=...                 # Supabase
COOLBET_USER=...                 # used only by flaresolverr_login_enroll
COOLBET_PASS=...                 #   (NOT used by daemon — CDP-JWT path)
FLARESOLVERR_URL=http://localhost:8191
COOLBET_CHROME_CDP_URL=http://localhost:9222
COOLBET_FLARE_SESSION=coolbet_prod
# COOLBET_MANUAL_JWT is no longer needed once CDP-JWT path is live —
# left as bootstrap fallback for cold start before any CDP refresh.
```

`COOLBET_ALLOW_API_LOGIN` is intentionally absent (default false) — the kill
switch that stopped the 2026-06-11 SMS storm. Only set true if you knowingly
want `/s/auth/login` to fire (which triggers SMS every time).
