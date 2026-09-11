# Coolbet Runbook — where to look when Coolbet breaks

Coolbet is the **only bookmaker the operator places real money at**, so its
collection + placement chain is load-bearing. This is the diagnostic reference:
the architecture, the failure modes, and the one-line fix for each. Written
2026-09-07 after a ~day-long outage whose real cause (FlareSolverr down) took an
hour to find because the symptom (HTTP 404 on every endpoint) looked like three
other things.

**Recurring patterns:** if this incident feels familiar, it probably is — see
[`RELIABILITY_LEDGER.md`](RELIABILITY_LEDGER.md) for the failure patterns that have
each bitten more than once (and the guards that now exist) before re-deriving one.

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
| `com.oddsintel.coolbet-odds-snapshot` | :03 / :33 | `coolbet_explorer --days 2` — bulk odds into `odds_snapshots`. |
| `com.oddsintel.coolbet-feed-watchdog` | :20 / :50 | cookie refresh + odds-feed staleness **+ session-keep (JWT heal via `coolbet_browser_sync.ensure_session_live`) + operator Telegram heal-command drain** — took these over when the paper mac-daemon was retired 2026-09-10 (pause/resume stay on the webhook). |
| `com.oddsintel.coolbet-ui-placer` | — | UI-driven placement path. |

`cs2-coolbet-scanner` was **removed 2026-09-07** (referenced deleted esports code).

> **PAPER-DAEMON RETIRED 2026-09-10.** `coolbet-mac-daemon` (paper, `execute=False`) and its `coolbet-daemon-keepalive` were legacy and are gone (plists archived in `~/Library/LaunchAgents/retired-2026-09-10/`). Paper simulation for model refinement is the PIPELINE's `simulated_bets`/`shadow_bets` (VPS), NOT the daemon; real money is the **UI placer** (`coolbet-ui-placer`). The daemon's only unique roles (session-keep + operator heal control) moved to the feed-watchdog. This matches Unibet (no daemon; periodic `ensure_logged_in` heal). "Can I place real money now?" → `python3 -m workers.automation.coolbet_control --status`.

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
  1b. **THE PREVENTION FIX WAS ONLY HALF THE STORY — corrected 2026-09-10 (evening).**
   The renderer-freeze diagnosis above is real and the flags do work, but they do
   NOT stop the recurring logout, and the session kept dying with the flags
   verified live. The remaining cause is **Coolbet's own inactivity timeout**, and
   it is an application feature, not a browser bug or bot-detection. Evidence,
   read straight out of the CDP tab's localStorage while it sat logged out:
   ```
   localStorage keys              = 35        (a full, real SPA — NOT an Imperva shell)
   cbauth                         = missing
   lastActiveRoute                = "/et/sport/match/6102801"
   isTwoMinuteWarningModalVisible = "true"    ← Coolbet's own 2-min warning fired
   isLogoutInProgress             = "true"    ← Coolbet logged US out
   lastActivityTime               = 2026-09-10T14:03:26Z
   ```
   The watchdog log shows the matching shape: `valid` at 16:20 → `logged_out` at
   16:40 → **`valid` again at 17:20** (right after the operator touched the browser
   at 17:03) → `logged_out` at 17:40, then permanently. The session survives ~30-40
   min after *human* activity and then Coolbet signs it out. **The watchdog's 20-min
   CDP read does not count as activity** — it reads localStorage without generating
   any user input.
   **Consequence for diagnosis:** the logged-out page shows Coolbet's "STAY COOL"
   brand slogan, which is why this keeps getting misfiled as the §2 Imperva wall.
   Use the key count to tell them apart: **35 keys = inactivity logout; a ~9-char
   body = the real wall.**
   **Consequence for fixes:** a fresher JWT cannot help, because the app is
   deliberately ending the session. Do not try to defeat the timeout by synthesising
   user activity — it is a responsible-gambling control. Make **re-login** cheap and
   unattended instead: set `COOLBET_AUTO_LOGIN_ON_HEAL=true` in `.env` so
   `ensure_session_live()` self-relogins via device-trust (no SMS, rate-limited 1/h).

  2. **Recovery (defense in depth):** the daemon now runs `_ensure_session_live()` **every tick, before the no-candidate early return** (previously the session was only checked when a pick was ready to place, so on a quiet day the lapse went unnoticed for days). On `logged_out` it self-relogins via `cdp_auto_login` **when `COOLBET_AUTO_LOGIN_ON_HEAL=true`** in `.env` (device-trust → no SMS; rate-limited 1/h). Set that flag on to make recovery unattended.

### 4. Placement self-paused  → daemon runs but places nothing
- **Symptom:** signals generate, odds flow, but 0 placements. `coolbet_session_state.placement_paused=true`, reason `daemon self-pause: N consecutive errors`.
- **Cause:** the daemon self-pauses after `SELFPAUSE_AFTER_MINUTES` of errored ticks to stop hammering Coolbet during an outage. Once the outage is fixed the pause stays **latched** until cleared.
- **Fix (once session healthy + odds flowing):** `UPDATE coolbet_session_state SET placement_paused=false WHERE id=1;` or `--full-heal` (clears it on a state transition). A daemon **restart** also clears it.
- **Note:** only genuine failures (`error`, `search_blocked`) count toward the self-pause. Legitimate declines — odds-floor, drift, exposure — are **skips, not errors** (fixed 2026-09-07 in ODDS-FLOOR-SKIP-NOT-ERROR; before that a below-floor day falsely self-paused a healthy daemon).

### 6. Odds dead for hours, `fo-tree` 30s read timeouts  → COOLBET_NO_FS + stale installed plist
- **Symptom:** `fo-tree fetch failed (attempt N/3): Read timed out (read timeout=30)`
  → `Board sweep enumerated 0 categories`. Repeats every :03/:33. Meanwhile the
  watchdog logs `STALE_COOKIES — Imperva re-challenges…` and re-harvests cookies
  every 20 min. **The cookies are innocent.** This is transport.
- **Tell:** the sweep log contains `CoolbetSession NO_FS mode — 6 cookies from db`.
  Confirm with a 2-request A/B on one endpoint:
  ```
  COOLBET_NO_FS=true  → 30.5s ReadTimeout
  COOLBET_NO_FS=false →  0.6s HTTP 200, 181KB   (measured 2026-09-10)
  ```
- **Cause:** `COOLBET_NO_FS=true` sends plain `requests` carrying a `reese84`
  cookie replayed out of CDP-Chrome. That token is **TLS-bound**, so Imperva
  rejects the mismatched client — and it **blackholes rather than 403s**, which is
  why a block presents as a network hang and reads like an outage. NO_FS was the
  right call in 2026-07 when the only FlareSolverr was a dead Railway host; it
  became wrong the moment a local FS existed on :8191.
- **THE ACTUAL TRAP (2026-09-10, 5.3h of no odds):** the repo plist was **already
  correct**. The plist *launchd was running* was the stale 2026-07-08 copy in
  `~/Library/LaunchAgents/` that still set `COOLBET_NO_FS=true`. **Editing
  `local/launchd/*.plist` does not reload launchd.** Nothing detected the drift.
- **Fix:**
  ```bash
  cp local/launchd/com.oddsintel.coolbet-odds-snapshot.plist ~/Library/LaunchAgents/
  launchctl unload ~/Library/LaunchAgents/com.oddsintel.coolbet-odds-snapshot.plist
  launchctl load   ~/Library/LaunchAgents/com.oddsintel.coolbet-odds-snapshot.plist
  launchctl kickstart -k gui/$(id -u)/com.oddsintel.coolbet-odds-snapshot
  ```
  Verified: `Board sweep — 189 Coolbet categories` (was 0 all evening).
- **Guard:** smoke `COOLBET-CDP-COOKIE-EXPORT` step 7 now diffs the INSTALLED plist
  against the repo copy and fails on drift. Check every plist at once with:
  `for f in local/launchd/*.plist; do diff -q "$f" ~/Library/LaunchAgents/$(basename $f); done`
- **Note the repeat offence:** `COOLBET-GET-NO-TIMEOUT-2026-09-04` recorded the
  identical misattribution — *"the feed watchdog cheerfully re-harvested cookies at
  a problem that was never about cookies."* When odds die, check TRANSPORT before
  cookies.

### 7. The feed dies most days → WE are very likely the cause (footprint)
- **Symptom:** the Imperva challenge (§2) recurs daily no matter what is patched.
  FlareSolverr's own log is the tell:
  ```
  fresh FS session → "Challenge not detected!"               200 in 1.5s
  reused session   → "Error solving the challenge. Timeout"  500 after 60s
  ```
- **Cause (2026-09-11):** our own request volume. The board sweep fetched EVERY
  football category's event list every pass, then discarded events beyond
  `--horizon-hours` — **802 events fetched, 215 near-term**, i.e. ~192 category
  requests every 30 min with ~73% of the payload thrown away. Passes take long
  enough to overlap, so the sweep is effectively a continuous request stream at
  one bookmaker from one residential IP, all day. The runbook has always said
  §2 is "usually triggered by our own request volume from one IP" — we simply
  never reduced the volume, which is why it came back daily.
- **Fix (BOARD-SWEEP-NEARTERM-SKIP):** remember categories with nothing
  near-term and stop paying for them every pass. Two safeguards, both essential:
  **never permanent** (re-probed every `_CAT_PROBE_EVERY`=6 passes, ~3h at
  :03/:33 — otherwise the zero becomes true by construction) and **fail open**
  (a missing/corrupt memo sweeps everything; failing closed would look exactly
  like "Coolbet offers nothing"). A failed fetch is never recorded as empty, so
  an outage cannot teach the cache that a healthy league is dead.
- **Also available:** `--kickoff-band LO:HI` on the bulk path scopes a pass to
  fixtures kicking off in that window. Measured 2026-09-11 over 48h / 2,026
  fixtures: `0-6h`=64, `6-24h`=373, `24-48h`=1589 — i.e. **78% of the bulk load
  was fixtures a day or more away**, whose prices move entirely before we bet.
- **Immediate lever when it IS escalated:** `scripts/ops/coolbet_pause_resume.sh
  pause` (auto-resume armed). Leave the UI placer running — it is the real-money
  path and is not what draws the challenge.
- **Do NOT** "fix" this with more IPs or by rotating FS sessions to get a fresh
  un-escalated context. That dodges the detection instead of removing what
  triggers it, and it leaves the load — the actual problem — in place.

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

### 6. Unibet-Site odds feed STALE  → session logged out (SELF-HEALS now)
- **Symptom:** `odds_snapshots` `Unibet-Site` rows stop landing; newest age grows past ~90 min; the best-price router shows 0 Unibet candidates.
- **Cause:** the Unibet-Site sweep (`unibet_odds_feed.run_bulk`, launchd `com.oddsintel.unibet-site-odds` :15/:45) injects fetches on the operator's established, **logged-in** unibet.ee tab in CDP-Chrome (:9222). If that session logs out (Chrome relaunch, cookie expiry) the sweep writes 0 rows.
- **Self-heal (2026-09-10 — no operator action normally needed):** `run_bulk` now calls `unibet_browser_sync.ensure_logged_in()` before every sweep — if logged out it runs `cdp_auto_login()` (reads `UNIBET_USER`/`UNIBET_PASS` from `.env`, fills the login modal on the **existing** tab), rate-limited to once/30min. **Auto-login through DataDome WORKS** — verified `logged_in ✓`, 1229 rows written on the next sweep. The earlier "manual only, DataDome blocks it" belief was wrong on two counts: (a) `unibet_browser_sync` never loaded `.env`, so the creds were invisible and auto-login no-op'd on "missing credentials"; (b) `login_via_modal` raced the header-login button (bare 5s click) — fixed with a `wait_for_selector(state="visible")`. This is the Unibet analogue of Coolbet's `auto_self_heal` auto-login step (`COOLBET_AUTO_LOGIN_ON_HEAL`).
- **When a human IS needed:** only if self-revive can't recover — SMS/2FA on the account, a changed login selector, or `UNIBET_USER`/`UNIBET_PASS` missing from `.env`. THEN a deduped Telegram alert fires (3h, `unibet-site-no-tab`) naming which case, and you open https://www.unibet.ee in CDP-Chrome (:9222) and log in by hand. Requires: `UNIBET_USER`/`UNIBET_PASS` in `.env`, and a live CDP-Chrome (:9222).

### 7. Board sweep RAN but stored 0 rows  → matcher/parse regression (not a coverage gap)
- **Symptom:** a completed board sweep (`coolbet_explorer.run_board_sweep`) walked the board — `events_seen > 0` in the summary line — but wrote **nothing** (`stored_rows == 0`). From the outside this looks identical to "Coolbet genuinely offers nothing near-term", which is why it hid as a silent failure. A deduped Telegram alert (3h, `coolbet-sweep-zero-rows`) now fires naming the counters (`events_seen`, `near_term`, `matched`, `stored_rows`).
- **Cause:** almost always a matcher (`coolbet_matching.match_event_to_af`) or parser (`parse_market` / `store_coolbet_snapshots_for_match`) regression — events are seen but none match an AF fixture, or matched events parse to zero storable rows. A true coverage gap (nothing near-term) is rare and would also show `near_term == 0`, which distinguishes it.
- **Fix:** compare `matched` vs `near_term` in the alert — if `near_term > 0` but `matched == 0`, suspect the matcher (team-name normalisation, date window); if `matched > 0` but `stored_rows == 0`, suspect `parse_market` / the O/U line mapping. The alarm is output-based and NEVER raises (wrapped in try/except, `not dry_run` guarded); the next :03/:33 pass retries automatically. Added by COOLBET-INGEST-HARDENING (2026-09-10).
