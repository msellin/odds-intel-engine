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
| `com.oddsintel.coolbet-daemon-keepalive` | StartInterval 300s + at load | **DAEMON-DEATH-RECURRING fix (2026-09-10):** kickstarts the mac-daemon if its process is dead but the job is still loaded. StartInterval fires on wake (KeepAlive doesn't reliably respawn after sleep; `load -w` doesn't start it — only `kickstart` does). Respects a deliberate `unload` (won't fight it). Log: `dev/active/coolbet-daemon-keepalive.log`. |
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

## FlareSolverr resilience (runs 24/7, self-revives)

Three layers keep FS up without a human — added 2026-09-07 after it was down for
a day:

1. **`restart: always`** on the container (`local/flaresolverr/docker-compose.yml`) — Docker restarts it after any crash and on Docker-daemon start.
2. **launchd keepalive** `com.oddsintel.flaresolverr-keepalive` runs `scripts/ops/flaresolverr_keepalive.sh` **every 180s + at load**. It probes `:8191`; if down it checks the Docker daemon (launches Docker.app if that is down too), then `docker compose up -d`, then re-probes. Revive measured at ~5s for a torn-down container. Log: `dev/active/flaresolverr-keepalive.log`.
3. **daemon-tick health alert** (`workers/jobs/flaresolverr_health.py`) — if revival keeps failing, the mac-daemon pages with the fix command.

So the only case needing a human is Docker itself being unstartable (the alert says so). Verify the stack: `launchctl list | grep flaresolverr` and `docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' oi_local_flaresolverr` (→ `always`).

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
- **ROOT CAUSE of the recurring daily logout (diagnosed 2026-09-10, COOLBET-DAEMON-DEATH-RECURRING).** Do **not** confuse this with the Imperva wall in §2. Here the tab stays on a normal page (e.g. `/et/sport/recommendations`) and `cbauth` is simply **gone from localStorage** ("34 keys present, cbauth missing") — no redirect to `/login`, though the logged-out page shows Coolbet's "STAY COOL" brand slogan, which is what made it look like the §2 wall. The Coolbet JWT is only ~30-min TTL and is kept alive **solely** by the SPA's in-page renew-token timer (~20-min cadence). The CDP-Chrome is an automation window the operator never focuses, so it is permanently **occluded**; Chrome backgrounds and then **freezes** a hidden renderer after ~5 min, which suspends that renew timer → the JWT lapses → the frontend clears `cbauth` in place. Evidence: `cbauth` flapped on a ~30-min cycle in the daemon log, reappearing only on the ticks where the daemon's CDP read woke the frozen renderer. **Two-layer fix now in place:**
  1. **Prevention (the real fix) — ✅ VALIDATED 2026-09-10:** `local/launch_chrome_for_sync.sh` now launches with `--disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding`, so the tab's JS keeps running and renew-token fires on schedule. **Proof:** with the flags live, the session was left untouched (no daemon/probe/login) for 33 min and stayed `valid` with the JWT TTL *rising* 1349s→1677s (renew-token self-fired). Before the flags it lapsed within ~30 min. **Takes effect only on the next launch — quit the CDP-Chrome, re-run the launcher, log in once.** Verify: `ps aux | grep remote-debugging-port=9222` shows the three flags.
  2. **Recovery (defense in depth):** the daemon now runs `_ensure_session_live()` **every tick, before the no-candidate early return** (previously the session was only checked when a pick was ready to place, so on a quiet day the lapse went unnoticed for days). On `logged_out` it self-relogins via `cdp_auto_login` **when `COOLBET_AUTO_LOGIN_ON_HEAL=true`** in `.env` (device-trust → no SMS; rate-limited 1/h). Set that flag on to make recovery unattended.

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

### 6. Unibet-Site odds feed STALE  → no logged-in unibet.ee tab
- **Symptom:** `odds_snapshots` `Unibet-Site` rows stop landing; newest age grows past ~90 min; the best-price router shows 0 Unibet candidates. The `unibet-site-odds.log` shows `reason: "no logged-in unibet.ee tab open (a fresh tab is DataDome-degraded)"`.
- **Cause:** the Unibet-Site sweep (`unibet_odds_feed.run_bulk`, launchd `com.oddsintel.unibet-site-odds` :15/:45) injects fetches on the operator's ESTABLISHED, logged-in unibet.ee tab in CDP-Chrome (:9222). If that tab is gone (e.g. after a CDP-Chrome relaunch) the sweep writes 0 rows. **Unlike Coolbet, Unibet auto-login FAILS** — unibet.ee is behind **DataDome**, which degrades a fresh/automated tab (the header-login button never renders), so `unibet_browser_sync.cdp_auto_login` times out.
- **Fix (operator, manual — required):** open **https://www.unibet.ee** in the CDP-Chrome (:9222) window and log in by hand (dismiss the cookie + spending-limit modals). The next :15/:45 sweep uses that tab. A Telegram alert now fires (deduped 3h, `unibet-site-no-tab`) so this no longer rots silently (`UNIBET-SITE-STALE-ALERT`).
- **NB:** this is the Unibet analogue of the Coolbet session — a permanently-logged-in tab is the price of the injected-fetch sweep. When the Unibet real-money placer/router arm runs unattended it will need the same session-liveness care (a heartbeat), but auto-login is manual-login-bounded here.
