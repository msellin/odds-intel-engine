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
operator's Mac (RESIDENTIAL IP)          ← Imperva blocks the VPS/Hetzner IP. **IP ONLY** — see note below
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

> **⚠️ CORRECTED 2026-09-16 (VPS-CONSOLIDATION).** This diagram used to read
> *"Hetzner IP **+ Linux Chrome fingerprint**"*. **The fingerprint half is wrong.**
> The Mac's own working odds reader IS Linux Chromium in Docker
> (`docker exec oi_local_flaresolverr chromium --version` → Chromium 148 on Debian
> 12), which §2 already says elsewhere: *"FlareSolverr is a different browser in
> Docker with its own fingerprint"*. Measured: the **same VPS**, **same Linux
> Chromium**, a warm FS session with warmup navigations, routed through a
> residential-EE tunnel, returned **170,237 bytes of real `fo-tree`** (reproduced
> ×2, verified as genuine: 30 categories, `Premier League`, `matches_count`,
> `Jalgpall`). The identical session over the Hetzner IP never solves the
> challenge. **Imperva gates on the IP alone**, so a residential egress — not a
> different browser — is what would let this run on the VPS.
>
> **Method warning for anyone re-testing:** a COLD, unseeded FS call *or* a plain
> `curl` is challenged from **both** IPs (999 vs 1000 bytes; 965 vs 964). Testing
> without a warm named session + warmup navigations measures nothing and reads as
> a false negative. That was this investigation's first result.
> Plan: `dev/active/VPS_FEATURE_MATRIX.md`.

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

### 1b. ⭐ Incapsula INTERSTITIAL (HTTP 200, ~900 bytes) → NOT a block. It self-resolves.

**Read this before ever pausing the footprint again.** On 2026-09-11 this cost a
**15-hour "outage" that was never an outage**, and the pause was what kept it
broken.

- **Symptom:** every Coolbet fetch returns a small body and nothing parses;
  `--probe` says CHALLENGED; logs say "fo-tree unreachable — board NOT
  enumerated". Looks exactly like an escalation.
- **Tell:** the response is **HTTP 200** (not 403), ~900 bytes, and the body
  contains `_Incapsula_Resource` plus an `incident_id` and your own `cip=<ip>`.
- **Cause:** that is Imperva's standard **JS challenge**, served on the FIRST
  request of a fresh browser context. It is **self-resolving** — FlareSolverr's
  browser executes the script, receives `reese84`, and the **next request on the
  same FS session returns real content**. Measured: attempt 1 = 886 bytes,
  attempt 2 = **217,615 bytes**.
- **Why it looked permanent:** we had no retry, so the interstitial was returned
  to callers as the answer. And every `--probe` was a *first* request on a fresh
  context, so it always saw the interstitial and always reported CHALLENGED.
  **Pausing the sweep guaranteed it could never clear**, because the only thing
  that clears it is making a second request.
- **Why FlareSolverr does not solve it:** FS attempts a solve only when it
  DETECTS a challenge, and its detection targets Cloudflare. An HTTP 200 with a
  normal-looking body sails straight through as a success.
- **Fix (shipped):** `coolbet_session._looks_like_incapsula` + a bounded retry on
  the SAME session in `_fs_get`. One extra request per fresh session, not per
  call. Smoke `INCAPSULA-SELF-RESOLVES`.
- **This is NOT §2.** §2 is the real wall: a ~9-char `STAY COOL` body, or
  `Pardon Our Interruption`, usually with a 403. If you see HTTP 200 +
  `_Incapsula_Resource`, you are here, not there — **do not pause the feed.**

### 1c. ⭐ Interstitial that does NOT self-resolve → you are in §7, not §1b

**Read this WITH §1b — they look identical for the first 900 bytes and the fix
is opposite.** Measured 2026-09-12, Coolbet odds dead 3h.

- **Symptom:** identical to §1b — HTTP 200, ~995 bytes, `NOINDEX, NOFOLLOW`.
- **The distinguishing tell is the SECOND request, not the first:**

  | | request 1 (fresh context) | request 2 (same session) |
  |---|---|---|
  | **§1b, self-resolving** | 200, ~900 B interstitial | 200, ~217 KB real payload |
  | **§1c/§7, escalated**   | 200, ~995 B interstitial | **FS HTTP 500 after 45-60s** |

  A 500 *after a timeout* is FlareSolverr's "Error solving the challenge.
  Timeout" — the tell §7 already documents. The session is then **poisoned**:
  every later request on it 500s, including ones that worked seconds earlier.
- **What DOES work while escalated, and what it proves:** seeding the request
  with the Imperva cookies harvested from the operator's own logged-in
  CDP-Chrome returns the real payload in ~1s on a **fresh** context:

  ```
  fresh context, NO seed   -> 200,     995 bytes (interstitial, then poisoned)
  fresh context, WITH seed -> 200, 223,820 bytes (real board, reusable)
  ```

  That is IMPERVA-SEED-FS (`coolbet_session._imperva_seed_cookies`), and it is
  now wired into `_fs_get`/`_fs_post`/warmup as a **first-contact** seed.
  **It is a floor, not a cure.** It makes first contact survive; it does not
  un-escalate the flag, and `search/v2` still poisons a warm session.
- **Do NOT conclude "Coolbet is blocking us" from a probe.** Every `--probe` is
  a first request on a fresh context, so post-seed it reports **OK even while
  the sweep cannot complete**. Judge from `odds_snapshots` row counts, never
  from the probe alone.
- **Fix:** §7. Reduce footprint (`coolbet_pause_resume.sh pause`, which now
  genuinely arms its resume — see below), let the flag decay.

### ⚠️ `coolbet_pause_resume.sh pause` did not arm a resume until 2026-09-12

Its header promised *"the resume is a launchd job, not a note to a human"*, and
the `pause` branch only ever unloaded the two jobs. **Nothing was ever armed**,
so every use of the §7 lever created exactly the silent multi-day outage the
comment warns about. Fixed: `pause [MINUTES]` (default 90) now writes and loads
a one-shot `com.oddsintel.coolbet-resume` agent, and **fails loudly** if it
cannot. Always confirm with `coolbet_pause_resume.sh status` → `resume agent:
ARMED`. Pinned by smoke `LIVENESS-IS-NOT-CAPABILITY`.

### ⚠️ 2b. OUR OWN DIAGNOSTIC PROBES CAN CAUSE THE WALL — added 2026-09-22

**A fresh, unseeded FlareSolverr session earns a NEW Imperva visitor identity
every time.** During the 2026-09-22 residential-egress work roughly **15 such
probes** were made from the operator's residential IP in a day, each doing warmup
navigations under a brand-new `visid_incap_*`. Later that day the feed hit the §2
wall and needed an identity reset. Causation is not proven — the feed had also
been down 4.3h for an unrelated launchd reason, so there is no clean before/after
— but this is precisely the "our own request volume" pattern §2 already blames,
and visitor churn is a stronger version of it than repeated probes under one id.

**Two rules that follow:**

1. **Never diagnose feed health with a fresh unseeded session.** It is challenged
   from EVERY IP, including the Mac's — so it cannot distinguish "the feed is
   walled" from "this probe has no cookies". It produces a false alarm AND
   burns a visitor identity. This mistake was made twice on 2026-09-22 despite
   being written down.
2. **The only honest health check is the DB.** Did `odds_snapshots(bookmaker=
   'Coolbet')` get rows on the last tick? That is the question. `fo-tree`
   returning a challenge to a session you just created answers nothing:
   ```sql
   select max(timestamp), count(*) from odds_snapshots
   where bookmaker='Coolbet' and timestamp >= now() - interval '2 hours';
   ```
   On 2026-09-22 this read "15:38, 2,769 rows" — healthy — at the same moment a
   fresh probe was reporting WALL.

### ⛔ 2a. "STAY COOL" IS NOT THE IMPERVA WALL — corrected 2026-09-13

**This section told you for weeks that a ~9-character `STAY COOL` body IS §2.
It is not, and that error is why "the Coolbet login doesn't work" recurred so
many times without ever being fixed: every attempt went down the bot-detection
path for a fault that has nothing to do with bot detection.**

Measured side by side on one machine, minutes apart:

| | response |
|---|---|
| CDP-Chrome showing STAY COOL | HTTP 200, **504,929 bytes of real Coolbet SPA**, **zero** Imperva markers, zero console errors, zero failed requests, zero HTTP ≥400 |
| same Mac, plain request | **6,058-byte Imperva JS challenge** — `_Incapsula_Resource` + "Pardon Our Interruption" |

The interstitial carries Imperva's fingerprints everywhere. STAY COOL carries
none. **A tiny rendered body means the SPA rendered nothing — it does not say
who stopped it.** Always check the RAW HTML, which is what
`scripts/diagnose/coolbet_login_state.py` now does.

**Ruled out by measurement against the STAY COOL state — do not repeat these:**

| Tried | Result |
|---|---|
| all 5 Imperva **cookies** cleared | unchanged |
| localStorage `reese84` (732 chars) + `uuid` cleared | unchanged |
| fresh `goto` to `/et/login` | unchanged |
| `/et/sport`, `/et/`, `/en/login` | all identical → **site-wide, not a login page problem** |
| console / network | no errors, no failed requests, no 4xx |
| foreground tab + JS PoW time | unchanged |

**✅ ANSWERED 2026-09-13.** The operator's **normal Chrome loaded Coolbet fine
and logged in first try** — same machine, same IP, same account. So the block is
**bound to the CDP profile ON DISK**: not the IP, not the account, not the
cookies, not the process (a restart did not clear it either).

**THE FIX — and it is now AUTOMATIC:**

```bash
python3 scripts/ops/coolbet_cdp_rebootstrap.py            # check
python3 scripts/ops/coolbet_cdp_rebootstrap.py --apply    # heal
```

Scheduled as `com.oddsintel.coolbet-cdp-selfheal` (:25/:55), rate-limited to one
heal per 6h because the copy moves several GB. It quits CDP-Chrome, **parks** the
walled profile (moved, never deleted — it is the evidence), re-copies the
operator's normal profile, relaunches, and **syncs the JWT into
`coolbet_session_state`**. That last step is easy to forget and makes a
successful heal look broken: the browser holds a valid token while the placer,
the health ping and the router all read the stale DB row.

**THE ARCHITECTURE LESSON: never log in through CDP-Chrome.** Coolbet walls that
profile. Keep your OWN Chrome logged in; the automation copies from it. The
operator ask changes from "log into the window Coolbet blocks" to "stay logged
into your normal browser", which is the one thing that reliably works.

**THREE TIERS, cheapest first** — the self-heal picks by what is actually wrong,
which matters because the tiers differ by four orders of magnitude in cost:

| Symptom | Tier | Cost |
|---|---|---|
| CDP-Chrome not running, **or answering :9222 but not drivable** | **relaunch** | seconds, no profile touched, **never rate-limited** |
| up + rendering, no JWT (a lapsed session — the common case) | **auto-login** (`--cdp-auto-login`, creds from `.env`) | seconds |
| up but walled (STAY COOL) | **re-bootstrap** (profile re-copy) | GB, max once / 6h |

**And the tier escalates on repeated failure** (`CDP-SELFHEAL-CANNOT-ESCALATE`,
2026-09-21). After **3 consecutive failures of the same tier** the ladder moves
up one rung: auto-login → relaunch → re-bootstrap. A success, or a different
tier being chosen in between, resets the count; a rate-limit *skip* does not
count as a failure. Three rather than one so a single timed-out page cannot
trigger a multi-GB re-copy — at the :25/:55 cadence that is ~1.5h.

> **Why this exists.** The chooser picks by SYMPTOM and knew nothing of HISTORY,
> so a tier that could not work was re-chosen every 30 minutes forever. Measured
> in `dev/active/cdp-lifecycle.jsonl` on 2026-09-21: **auto-login failed 105
> times**, the last 8 consecutively over 3.5h with an identical error.
>
> It was unreachable-by-construction, not unlucky. `:9222/json/version` answered
> 200 with a Chrome version string while `connect_over_cdp` died with *"Frame was
> detached"* — so `cdp_up=True` blocked relaunch, the failed probe left
> `walled=None` which blocked re-bootstrap, and the only tier left used the same
> dead driver. **`diagnose()` now reports `cdp_usable` separately**: the endpoint
> answering and the driver being able to attach are different questions, and only
> the second one is what a remedy needs. Fixed live the same day — the escalated
> relaunch restored a drivable browser (`cdp_usable: true`, `walled: false`).

Two bugs found by running it against a real system, both worth knowing:
- v1 asked `cdp_up AND no JWT`, so a **dead browser** gave `needs heal: False`
  tick after tick for hours. *A reviver that stands down because its subject is
  down is worse than none — it looks like supervision.*
- v2 then sent every missing token to the multi-GB copy, making the most
  ROUTINE event (a 30-min JWT lapsing) the most expensive one. Auto-login has
  been in the repo all along and takes seconds.

**The human case is now genuinely rare:** auto-login handles a lapsed session
(verified 2026-09-13, "✓ logged in", no SMS). A human is needed only if Coolbet
demands SMS/2FA, or if the normal Chrome profile is also signed out so a
re-bootstrap copies nothing.

Verified end-to-end 2026-09-13: profile re-copied → `JWT: valid (ttl 1734s)` →
`--refresh-jwt` → health-ping `✓ maintenance probe succeeded in 3.70s`.

Run `python3 scripts/diagnose/coolbet_login_state.py` FIRST, every time. It
distinguishes wall / no-form / logged-in in one read, which is the distinction
that kept being guessed.

### 2. Imperva challenge  → the "Pardon Our Interruption" wall
- **Symptom:** the raw HTML contains `Pardon Our Interruption` or `_Incapsula_Resource`; `x-iinfo` response header present. (**NOT** a short `STAY COOL` body — see §2a.)
- **Tell:** raw `curl` of `coolbet.com` returns the interstitial HTML even at HTTP 200.
- **⚠️ CHECK OUR OWN RETRY LOOPS FIRST (2026-09-13).** Before blaming volume in
  general, look at what WE are sending into the wall. `coolbet_health_ping` runs
  an AUTHENTICATED probe every 5 min and, while logged out, cannot succeed:
  measured **143 failed authenticated probes in 12 hours** from one residential
  IP into an endpoint already answering the wall. A health check retrying into a
  challenge is exactly the "own request volume" this section blames — it was
  feeding the condition it was reporting. Fixed with a circuit breaker
  (`health_ping._skip_reason`): no usable credential → no request, still marked
  unhealthy, reopens by itself when a live JWT appears. Smoke
  `HEALTH-PING-CIRCUIT-BREAKER`.
- **NOTE the two transports are escalated SEPARATELY.** On 2026-09-13 the
  FS-routed odds sweep was storing 33k rows a pass while CDP-Chrome's login
  showed STAY COOL. FlareSolverr is a different browser in Docker with its own
  fingerprint; the operator's Chrome is a different client entirely. **So a
  walled login does NOT mean the feed is blocked, and pausing a healthy feed
  does not clear a login wall.** Check both before reaching for the pause lever.
- **If a FOREGROUND tab still walls, the flag is on the VISITOR ID, not on today's
  behaviour.** `visid_incap_*` is long-lived — observed expiry **6,024 hours,
  about eight months** — so waiting, foregrounding and reducing load all fail to
  clear it, because the mark is attached to the visitor rather than to current
  traffic. Reset it:
  ```bash
  python3 scripts/ops/coolbet_reset_imperva_identity.py          # dry run
  python3 scripts/ops/coolbet_reset_imperva_identity.py --apply
  ```
  Surgical — five Imperva cookies, consent/analytics untouched. **Then RELOAD in
  the foreground tab and log in PROMPTLY**: the FS odds sweep is seeded from this
  browser's cookies (re-harvested when they go 2h stale), so a harvest landing
  mid-challenge seeds FlareSolverr with a challenge-state set and can take down a
  healthy feed. ⚠️ This resets an identity rather than removing a cause — do the
  footprint work FIRST (see the retry-loop note above), and if you are running it
  repeatedly the identity is not the problem.
- **Fix:** this is genuine bot-detection escalation, usually triggered by our own request volume from one IP. Reduce footprint (`coolbet_pause_resume.sh pause`), let the flag decay, load the site in a **foreground** real-Chrome tab to solve the challenge (a backgrounded `--no-startup-window` instance can't complete the JS PoW). Do **not** build a challenge solver. The token is TLS/JA3-bound, so replaying `reese84` into plain `requests` cannot work — this is exactly why FS (a real browser) is mandatory.

### ⭐ 2b. CDP-Chrome will not STAY up  → launchd reaped it (fixed 2026-09-16)

**Symptom.** `:9222` refused. The self-heal log repeats the SAME four lines every
30 minutes, forever, each tick reporting success:

```
CDP up      : False
  - relaunch rc=0
  - no JWT after relaunch — trying auto-login
  ✓ browser relaunched — but NO JWT yet (profile has no session).
```

**This reads like a login problem and is not one.** On 2026-09-16 it ran **70
times over 17 hours**, took Coolbet dark ~6h and Unibet-Site ~20h, and no login
would have fixed it — the profile's session was fine the whole time.

**Cause.** `relaunch()` shells out to `local/launch_chrome_for_sync.sh`, which
starts Chrome with a trailing `&`. **launchd kills everything left in a job's
process group when the job exits** unless `AbandonProcessGroup` is set. Chrome
was in that group. So each tick really did start Chrome — the launcher polls
`:9222` and confirms it before returning, which is why `rc=0` was honest — and
then launchd killed it seconds later when the job finished. Google's own updater
agent sets this flag for the same reason.

**Fix (already applied).** `AbandonProcessGroup` is now `<true/>` in both
`com.oddsintel.coolbet-cdp-selfheal.plist` and `com.oddsintel.cdp-watch.plist`.
Pinned by smoke test `CDP-CHROME-NOT-REAPED`, which fails if a launchd job that
can start Chrome lacks the flag.

**Verify in one line** — a job with the flag prints `abandon process group`:

```bash
launchctl print gui/$(id -u)/com.oddsintel.coolbet-cdp-selfheal | grep -i abandon
```

**Tell this apart from §3 (a genuinely expired session):** here `pgrep -f
Chrome-CDP-OddsIntel` returns **0** a few minutes after a tick. In §3 Chrome is
running and rendering, and only the token is gone.

**⚠️ The self-heal cannot escalate out of this on its own** — see
`CDP-SELFHEAL-CANNOT-ESCALATE` in PRIORITY_QUEUE. `coolbet_cdp_rebootstrap.py`
picks the `relaunch` tier whenever `cdp_up` is false, and BOTH escalation
branches require `cdp_up == True`, so a Chrome that will not stay up loops on the
cheap tier indefinitely. The comment beside that branch claims the next tick
escalates; it does not.

---

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
   unattended instead: `COOLBET_AUTO_LOGIN_ON_HEAL=true` makes
   `ensure_session_live()` self-relogin via device-trust (no SMS, rate-limited 1/h).
   **ENABLED 2026-09-11 — and note where.** It is set in the **feed-watchdog's
   launchd plist** (`local/launchd/com.oddsintel.coolbet-feed-watchdog.plist`),
   not `.env`: the watchdog is the process that calls `ensure_session_live()`
   since it inherited session-keep on 2026-09-10, and a plist is reproducible
   from git where `.env` is not. **Until then the capability was DEAD** — the
   only plist setting it belonged to the RETIRED paper mac-daemon, and `.env`
   never had it, so every "just set this flag" recommendation had been landing
   in a file no running process read. Takes effect on the watchdog's next load
   (it is currently unloaded by the footprint pause).

  2. **Recovery (defense in depth):** the daemon now runs `_ensure_session_live()` **every tick, before the no-candidate early return** (previously the session was only checked when a pick was ready to place, so on a quiet day the lapse went unnoticed for days). On `logged_out` it self-relogins via `cdp_auto_login` **when `COOLBET_AUTO_LOGIN_ON_HEAL=true`** (device-trust → no SMS; rate-limited 1/h). ⚠️ That daemon is RETIRED (2026-09-10) — the live equivalent is the feed-watchdog calling `ensure_session_live()`, and the flag is now set in **its** plist (see §3 above), not `.env`.

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
  ```bash
  python3 scripts/ops/launchd_drift_check.py --verbose
  ```
  It compares **parsed** plists, so reindentation is not reported as drift and the
  offending KEYS are named. The old `diff -q` one-liner byte-compared and produced
  3 false alarms out of 7 lines on 2026-09-11 (two whitespace-only, one
  deliberately-retired job) — see `RELIABILITY_LEDGER.md` #3 for why a noisy guard
  is a broken guard.
- **Note the repeat offence:** `COOLBET-GET-NO-TIMEOUT-2026-09-04` recorded the
  identical misattribution — *"the feed watchdog cheerfully re-harvested cookies at
  a problem that was never about cookies."* When odds die, check TRANSPORT before
  cookies.

### ⭐ 6b. Odds dead for hours, `fo-tree` HTTP **500** after ~61s → WEDGED FS SESSION (SELF-HEALS since 2026-09-18)

> **⚠️ UPDATED the same evening (WEDGE-PROBE-EARLY + WATCHDOG-HEALS-INPLAY).** Two
> gaps in the morning's self-heal, both found by a live outage:
>
> 1. **The probe waited on the 3h staleness clock.** The session wedged at 16:09
>    UTC and at 18:05 the watchdog was still printing `HEALTHY — last Coolbet odds
>    2.0h ago`, because 2.0 < `FEED_STALE_H`=3.0. The probe now runs once the feed
>    has missed a single sweep (`WEDGE_PROBE_AFTER_H`=0.75), which bounds a wedge
>    at roughly one watchdog interval (~45 min) instead of 3h+. The 3h clock stays
>    for the slower diagnoses (job not loaded, CDP down, cookie age).
> 2. **Only the odds reader had a healer.** `coolbet_odds_reader` and
>    `coolbet_inplay` wedged in the SAME minute; the in-play collector sat dead two
>    hours emitting `errors 8` per cycle until a human destroyed its session by
>    hand. `heal_inplay_session()` now probes and heals it on its own evidence —
>    it writes no `odds_snapshots` rows, so the odds staleness clock can never
>    speak for it. **Any future long-lived FS session needs its own healer; that
>    is now the rule, not a nicety.**
>
> Symptom for the in-play arm specifically: `cycle N | targets 8 | rows 0 | empty
> 0 | errors 8` with `Read timed out (read timeout=30)` per target and cycles
> stretching to ~243s. FlareSolverr itself reads healthy and a FRESH session gets
> coolbet.com in 0.68s — that combination IS the wedge signature.
> `_destroy_fs_session` refuses `coolbet_prod` outright.
- **Symptom:** identical in the DB to §6 and §7 — no Coolbet rows for hours,
  `Board sweep enumerated 0 categories`, every :03/:33 — while the watchdog logs
  `STALE_COOKIES … probably NOT the cookies` and re-harvests every 20 min.
- **The tell is the SHAPE of the failure, and it is unambiguous:**

  | | §6 (NO_FS) | **§6b (wedged)** | §7 (Imperva flag) |
  |---|---|---|---|
  | error | `Read timed out` | **HTTP 500** | HTTP 200 / 403 |
  | timing | ~30s | **fixed ~61s** | fast, or 45-60s on a poisoned session |
  | body | — | **0 bytes** | ~900-1000 bytes |

  A **fixed** duration with **zero** bytes is a timeout, not a verdict Coolbet
  rendered. Nothing on the other end answered at all.

- **AN INTERSTITIAL ACTUALLY SEEN OUTRANKS ALL OF THE ABOVE** (added 2026-09-21,
  `COOLBET-PROBE-CALLS-IMPERVA-A-WEDGE`). `CoolbetSession` now sets
  `saw_incapsula` when it observes an Imperva interstitial, and
  `probe_coolbet_reachable` returns **`challenged`** on that flag regardless of
  timing or byte count. The table is *inference*; a sighting is *direct evidence
  of who answered*, and once the session's Incapsula retries are exhausted a real
  block and a stuck tab both end as `500 / long / 0 bytes` and cannot be told
  apart by shape at all.

  > **Why this is worth a rule.** On 2026-09-18 the probe printed
  > `Incapsula interstitial on …/fo-tree` and then returned `wedged` with the
  > detail *"that is a stuck session, not a challenge verdict"* — contradicting
  > its own evidence one line earlier, at the worst possible moment. Acting on
  > the wrong verdict meant destroying FS sessions repeatedly and restarting the
  > container **against a live flag, which hardens it**.

- **⛔ THE TABLE ABOVE IS NECESSARY BUT NOT SUFFICIENT — you MUST probe a FRESH
  session before destroying anything (added 2026-09-20).** On 2026-09-19/20 the
  §7 Imperva flag produced the EXACT §6b signature on the sweep's own session:
  `fo-tree` HTTP 500, fixed **60.5s**, **0 bytes**. It is not a wedge —
  FlareSolverr simply cannot solve a live challenge and times out. The tell is
  the second probe:

  | | §6b (wedged) | §7 (Imperva flag) |
  |---|---|---|
  | sweep's own session | 500 / ~61s / 0B | 500 / ~61s / 0B — **identical** |
  | **a FRESH session** | **OK, fast, real board** | **fails the same way** |
  | `coolbet.com` homepage | loads | `_incapsula_` challenge page |
  | FlareSolverr itself | healthy | healthy |

  Measured that night: named 60.5s/0B, fresh 60.6s/0B, FS healthy (HLTV 1.4 MB,
  `sessions.create` 0.29s), homepage a 6,078-byte `_incapsula_` page. **That is
  §7, and destroying sessions there HARDENS the block (§7's own rule).** The
  watchdog now runs this second probe and fails SAFE to `BLOCKED` on anything
  but a clean fresh `ok`, because the destroy is the irreversible half.
  Smoke `WEDGE-NEEDS-A-FRESH-SESSION`.

  One command: `python3 -c "from workers.automation.coolbet_explorer import
  probe_coolbet_reachable as p; print(p(session_name='coolbet_odds_reader'));
  print(p(session_name='throwaway_probe'))"` with `FLARESOLVERR_URL` set —
  the local `.env` still holds a dead Railway URL.
- **Cause:** the per-session Chrome tab inside FlareSolverr crashed. **FlareSolverr
  itself stays healthy** — `GET /` returns ready, `sessions.list` lists the session,
  `docker ps` says `(healthy)` — so every container-level check reads green while
  every request on that one session 500s. Measured 2026-09-18: 9.7h outage,
  container up 4 days and healthy throughout.
- **Confirm in two commands** (the second is what separates this from §7):
  ```bash
  COOLBET_FLARE_SESSION=coolbet_odds_reader FLARESOLVERR_URL=http://localhost:8191 \
    python3 -m workers.automation.coolbet_explorer --probe
  ```
  ```bash
  FLARESOLVERR_URL=http://localhost:8191 \
    python3 -m workers.automation.coolbet_explorer --probe --fresh-session
  ```
  `WEDGED` (61.1s, 0 bytes) on the named session + `OK` (2.0s, 190,708 bytes) on a
  fresh one = this section. If the FRESH probe also fails, you are in §7 — the flag
  is live and destroying sessions makes it worse.
- **Fix — destroy ONLY the sweep's session.** It is recreated on the next sweep:
  ```bash
  FLARESOLVERR_URL=http://localhost:8191 python3 scripts/diagnose/flaresolverr_recover.py \
    --session coolbet_odds_reader --apply
  ```
  > ⚠️ **Never `--all`, and never `coolbet_prod`.** The sweep runs on
  > `coolbet_odds_reader` (FS-SESSION-ISOLATION 2026-07-05, set in the plist);
  > `coolbet_prod` is the **real-money UI placer's authed** session. Destroying it
  > to fix a read-only feed drops the placement path.
- **Self-heal (WEDGED-SESSION-SELF-HEAL 2026-09-18):** the feed watchdog runs
  the first probe itself on the stale-feed-with-fresh-cookies path and destroys the
  reader session on `wedged`, bounding this at ~30 min instead of "until someone
  notices `/performance` has stopped moving". `challenged` routes to `BLOCKED`
  instead — opposite remedy, see §7. Before that date this branch only *printed*
  "check transport first" and refreshed cookies anyway; see
  [`RELIABILITY_LEDGER.md`](RELIABILITY_LEDGER.md) §1, fourth row.
  > ⚠️ **It did not actually work until 2026-09-21** (`COOLBET-WEDGE-SELFHEAL-NEVER-FIRED`).
  > `_destroy_fs_session` read `FLARESOLVERR_URL` alone, which on the Mac still
  > holds the pre-RAILWAY-ELIMINATION Railway host and now 404s — so for three
  > days it diagnosed every wedge correctly and posted the destroy to a dead
  > server: **16 `WEDGED_SESSION` verdicts on 09-19/20, 16 `fs_session_destroy_failed`,
  > zero successes.** It now resolves the same candidates `coolbet_session._fs_call`
  > does (`COOLBET_FS_LOCAL_URL` first) and tries each. If you are reading this
  > during an incident, the manual command above still works and always did —
  > note it sets `FLARESOLVERR_URL=http://localhost:8191` inline, which is
  > precisely the override the watchdog was missing.

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
- **Recurrence 2026-09-23 (#108) and the guard it produced (#110 BOOK-FOOTPRINT):**
  the zone.ee exit was flagged after ~7,500 Coolbet requests in 8 h (peak 1,517/h),
  6,033 of them search fallbacks — and nothing was counting. Every Coolbet read now
  goes through `workers/utils/footprint.py`: counted per clock hour across all
  processes in `book_footprint` (migration 392), **refused before sending** once the
  hour's budget (500; env `FOOTPRINT_BUDGET_COOLBET`) is spent, and bot-check pages /
  errors / >20 s responses counted too. `/admin/feeds` → Coolbet → details shows
  "Requests this hour N / budget"; the block turns amber at 80% of budget or when
  bot-check answers exceed 5% (≥5) — the warning that comes BEFORE the block.
  **POSTs (placement) are counted but never refused.**
  Since #151 (2026-09-25) the sweep also defers fixtures ≥ 3 h from kickoff once the hour
  passes 400/500 (`footprint.has_headroom`), so the last 100 stay for the near-kickoff
  close and the health ping; a refusal records its `host/proc/pid` in
  `book_footprint.refused_by` (migration 445) — read that row first when refusals appear
  under budget.
  **The reserve is not holding (found 2026-09-26, #142):** Coolbet was at 500/500 in every
  hour of 09-25/26 and the must-run callers (`near_kickoff_capture` = the closing price,
  `health_ping`) were refused 18-184 times an hour. Since migration 465 each hour's
  `book_footprint.requests_by` says WHO spent it ({scheduler job | process: n}) — read it
  before touching the budget or the reserve:
  `SELECT hour, requests, refused, requests_by FROM book_footprint WHERE book='Coolbet' ORDER BY hour DESC LIMIT 6;`
- **And the sweep itself shrank (#091, 2026-09-23):** the scheduled job now runs the
  board sweep, not `run_bulk`'s search fallback. Verify coverage any time, from an
  IP Coolbet is not blocking, with `scripts/coolbet_board_coverage_diff.py
  --horizon-hours 48 --cache board.json` (~100 requests; `--from-cache` re-matches
  with zero requests).

### 5. No placeable bet today  → this is CORRECT, not a failure
- **Symptom:** daemon tick logs `qualified=N` but `placed=0`, every candidate `skip … below the 2.80 odds floor`.
- **Cause:** all qualifying candidates are priced below the executable odds floor (CLV goes negative below ~2.80). The bot is correctly declining -EV prices.
- **Fix:** none — thin/below-floor value is a normal day. Verify it is this and not #1–4 by checking `qualified>0` and that the skips are `odds_floor`, not `search_blocked`.

---

## ⭐ START HERE — one command for "is everything working?"

```bash
python3 scripts/ops/status.py            # full check, exits 1 if degraded
python3 scripts/ops/status.py --no-fs     # fast: skips the browser probe
python3 scripts/ops/status.py --json      # for a dashboard or a widget
```

Feeds (minutes since the last row per book), launchd jobs **including ones that
are UNLOADED**, FlareSolverr probed for **capability not liveness**, Coolbet JWT
and Imperva cookie age, what was actually staked in 24h, and — the line that
matters — **picks that kicked off with no bet on them**.

It exists because this question was being answered by hand, six ad-hoc queries
at a time, differently each time. On its first run it found two faults the
hand-assembled version had missed that same hour. Prefer it to the one-shots
below; reach for those once it tells you *which* thing is broken.

## One-shot diagnostics

```bash
# is FS up?
curl -s http://localhost:8191/ | head -c 80

# full session/JWT state (read-only)
python3 -m workers.automation.coolbet_browser_sync --full-heal --full-heal-dry-run

# (the old `python3 -m workers.automation.coolbet_mac_daemon --once` tick is gone — the paper
#  daemon was DELETED 2026-09-25, #162 W4.6; the live placers are scripts/place_coolbet_ui.py
#  and workers/automation/best_price_router.py, both paused)

# is Coolbet odds landing?  (DB timestamps display ~3h behind wall-clock — compare deltas, not absolutes)
psql "$DATABASE_URL" -c "select max(\"timestamp\"), count(*) from odds_snapshots where bookmaker='Coolbet' and \"timestamp\">=now()-interval '40 min'"

# FS health check (the alert, dry)
python3 -m workers.jobs.flaresolverr_health --dry-run
```

### 6. Unibet-Site odds feed STALE

> **CHANGED 2026-09-23 (UNIBET-ON-VPS).** The sweep now runs on the VPS (scheduler job `unibet_site_odds`) against a **logged-OUT** tab in `oddsintel-unibet-chrome.service`, with `login=False` — so the self-revive below no longer runs for the feed, and "logged out" is not a cause. Check instead: `systemctl status oddsintel-unibet-chrome` (Chrome up? a `unibet.ee` page in `curl 127.0.0.1:9222/json/list`?), `oddsintel-zone-egress` (the Estonian exit), and the job's `reason` in `pipeline_runs` (`quickbrowse returned no country RNs` / `aborted — repeated non-200` = DataDome throttling this IP). **Do not script logins on the VPS**: from that egress a login gets a DataDome slider captcha, and retries raise the bot score. The text below describes the Mac-era logged-in path, still used by the Unibet placer.

- **Symptom:** `odds_snapshots` `Unibet-Site` rows stop landing; newest age grows past ~90 min; the best-price router shows 0 Unibet candidates.
- **Cause:** the Unibet-Site sweep (`unibet_odds_feed.run_bulk`, launchd `com.oddsintel.unibet-site-odds` :15/:45) injects fetches on the operator's established, **logged-in** unibet.ee tab in CDP-Chrome (:9222). If that session logs out (Chrome relaunch, cookie expiry) the sweep writes 0 rows.
- **Self-heal (2026-09-10 — no operator action normally needed):** `run_bulk` now calls `unibet_browser_sync.ensure_logged_in()` before every sweep — if logged out it runs `cdp_auto_login()` (reads `UNIBET_USER`/`UNIBET_PASS` from `.env`, fills the login modal on the **existing** tab), rate-limited to once/30min. **Auto-login through DataDome WORKS** — verified `logged_in ✓`, 1229 rows written on the next sweep. The earlier "manual only, DataDome blocks it" belief was wrong on two counts: (a) `unibet_browser_sync` never loaded `.env`, so the creds were invisible and auto-login no-op'd on "missing credentials"; (b) `login_via_modal` raced the header-login button (bare 5s click) — fixed with a `wait_for_selector(state="visible")`. This is the Unibet analogue of Coolbet's `auto_self_heal` auto-login step (`COOLBET_AUTO_LOGIN_ON_HEAL`).
- **When a human IS needed:** only if self-revive can't recover — SMS/2FA on the account, a changed login selector, or `UNIBET_USER`/`UNIBET_PASS` missing from `.env`. THEN a deduped Telegram alert fires (3h, `unibet-site-no-tab`) naming which case, and you open https://www.unibet.ee in CDP-Chrome (:9222) and log in by hand. Requires: `UNIBET_USER`/`UNIBET_PASS` in `.env`, and a live CDP-Chrome (:9222).

### 7. Board sweep RAN but stored 0 rows  → matcher/parse regression (not a coverage gap)
- **Symptom:** a completed board sweep (`coolbet_explorer.run_board_sweep`) walked the board — `events_seen > 0` in the summary line — but wrote **nothing** (`stored_rows == 0`). From the outside this looks identical to "Coolbet genuinely offers nothing near-term", which is why it hid as a silent failure. A deduped Telegram alert (3h, `coolbet-sweep-zero-rows`) now fires naming the counters (`events_seen`, `near_term`, `matched`, `stored_rows`).
- **Cause:** almost always a matcher (`coolbet_matching.match_event_to_af`) or parser (`parse_market` / `store_coolbet_snapshots_for_match`) regression — events are seen but none match an AF fixture, or matched events parse to zero storable rows. A true coverage gap (nothing near-term) is rare and would also show `near_term == 0`, which distinguishes it.
- **Fix:** compare `matched` vs `near_term` in the alert — if `near_term > 0` but `matched == 0`, suspect the matcher (team-name normalisation, date window); if `matched > 0` but `stored_rows == 0`, suspect `parse_market` / the O/U line mapping. The alarm is output-based and NEVER raises (wrapped in try/except, `not dry_run` guarded); the next :03/:33 pass retries automatically. Added by COOLBET-INGEST-HARDENING (2026-09-10).

### 8. Another fixture's prices stored under ours, but the names are nothing alike → CROSSED FS RESPONSE (fixed 2026-09-23)
- **Symptom:** `coolbet_price_sanity` fires `🚨 Coolbet wrong-fixture prices` (FAV-INVERTED), yet the two fixtures share no name tokens (Grorud v Moss ← Hammarby W v Rangers W), so `fuzzy_match_event` cannot have paired them. The tell is in `odds_snapshots`: a single write burst for the match holds **two different 1x2 sets**, one from the fo-match POST (plain requests, correct) and one from the sidebets GET (FlareSolverr, wrong), and it is thinner than that match's usual bursts.
- **Cause:** two processes drove the same FS session (one browser tab) at once, typically a manual `coolbet_explorer` run started alongside the scheduled sweep. FS returns whatever page the tab last loaded, so a request can come back holding another request's body. The FS container log shows it: two `Incoming request` lines on `coolbet_prod` before either `Response in`.
- **Fix / guards:** `_fs_call` now serialises every call per session name across processes (`flock` in `COOLBET_FS_LOCK_DIR`, default `/tmp/oddsintel-fs-locks`), and `_fs_get` discards any `/s/sbgate/` response whose reported URL differs from the requested one, logging `FS returned a CROSSED response`. The lock is **host-local**: something on another host sharing the FS instance is caught only by the URL tripwire. Manual runs are still safe; they queue behind the sweep per request. Details that matter: the lock file is created 0666 in a sticky dir, so a root/sudo run cannot lock other users out; `_fs_call` stretches its client timeout past FS's `maxTimeout`, so a timed-out caller never frees a tab FS is still navigating; and `probe_coolbet_reachable` subtracts time spent queued on the lock, so a busy session is never misread as `wedged` and destroyed. The URL tripwire is partial by nature: FS reads the URL and the body at slightly different moments, so the lock is the real fix.
