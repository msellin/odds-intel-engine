# Everything we run — and whether it can live on the VPS

**Compiled 2026-09-16** from `launchctl list`, `workers/scheduler.py` (82 unique
registered jobs), `docker ps`, `systemctl`, and the live egress tests in
[`vps-migration-map.md`](vps-migration-map.md). Verdicts marked ✅ **PROVEN** were
measured, not reasoned.

## Headline

| | Count | |
|---|---|---|
| Already on the VPS | **82 scheduler jobs + 9 infra services** | nothing to do |
| Mac-side, movable **today** | **5 of 11** | no new infrastructure |
| Mac-side, movable with **home egress** | **2 of 11** | ✅ proven working 2026-09-16 |
| Mac-side, **cannot move** | **4 of 11** | 3 are Chrome-babysitters; 1 is the real-money placer |

**So: 9 of 11 Mac jobs can move.** What stays is the logged-in Chrome and the
things that exist only to keep it alive.

---

## Layer 1 — Infrastructure (all already on the VPS)

| Component | What | Verdict |
|---|---|---|
| Postgres 17 | `shared_buffers=4GB`, shared by OddsIntel + CrossRank + BoxRank | ✅ already there |
| PostgREST ×2 | `oddsintel-postgrest-1`, `crossrank-postgrest-1` (docker) | ✅ already there |
| nginx | `api.oddsintel.app` + site vhosts, Cloudflare Flexible SSL | ✅ already there |
| Next.js web | `odds-intel-web` via pm2 :3000 | ✅ already there |
| FlareSolverr | `oi_local_flaresolverr` (bridge net) — serves Epicbet | ✅ already there |
| GitHub Actions runner | CrossRank self-hosted runner | ✅ already there |
| netdata / uptime-kuma | monitoring | ✅ already there |
| Nightly backup | 03:30 UTC → Hetzner Storage Box | ✅ already there |
| **A second FlareSolverr** | would be needed for Coolbet on **host network** (bridge cannot reach a tunnel) | ⬜ to add with §4 |

## Layer 2 — VPS scheduler: 82 registered jobs (`oddsintel-scheduler.service`)

All already on the VPS. Listed for completeness — **none of these is the question.**

| Group | n | Examples |
|---|---|---|
| Odds ingest | 13 | AF bulk odds, Epicbet snapshot, closing snap, Pinnacle drift, freshness watchdogs, price sanity |
| Model / predictions | 13 | calibration, blend refit, MFV refresh, feature densify, line velocity, O/U + 1X2 model shadows |
| Betting / bots | 18 | betting refresh, shadow run, pick triggers, paper bots (corners / team-total / 1H 1X2), picks publish, manual placement drain |
| Settlement | 6 | settlement pipeline, settle-ready, settle reconcile, CLV, half-scores backfill |
| Signals / enrichment | 8 | injuries, standings, H2H, news checker, xG overperformance, injury severity, season phase, player ratings |
| Health / alerts | 8 | stall watchdog, pipeline failure alerter, feed alerts, budget sync, Stripe reconcile |
| Maintenance | 3 | FS session sweep, orphaned-run cleanup, anon-user prune |
| Other | 13 | fixtures, morning chain, dashboard cache, daily perf email, ALN auto-tune, backfills |
| **LivePoller** | thread | 24/7 background thread (45s live / 120s idle) for scores + settlement |

> **One dormant job matters:** `job_coolbet_odds_snapshot` **exists in
> `scheduler.py` but is NOT registered** (no `add_job`). It is the VPS-side
> Coolbet reader, parked when Imperva blocked the box. **Once the egress is
> fixed it is registered, not written** — the code is already there.

## Layer 3 — Mac launchd: the actual migration question

### ✅ Move today — no new infrastructure, no new risk

| Job | Cadence | Hits | Why it moves |
|---|---|---|---|
| `inplay-collector` | 45s loop | Epicbet live board | Epicbet-only (`BOOK = "Epicbet"`). **Tested: VPS+FS → OK.** Becomes a scheduler job. |
| `near-kickoff-capture` — **Epicbet third** | every 5 min | Epicbet | Same. Split the job; this third goes. |
| `coolbet-feed-watchdog` — **DB half** | :20/:50 | nothing (reads Postgres) | Makes **no** Coolbet HTTP calls — judges staleness from the DB, which is already on the VPS. |
| `flaresolverr-keepalive` | 180s | local FS | The VPS already runs FS; a systemd timer replaces the plist. |
| `vps-postgres-tunnel` | always | — | **Deleted, not moved.** Exists only so the Mac can reach VPS Postgres. |

### ✅ PROVEN movable with a residential egress (WireGuard)

| Job | Cadence | Blocker | Evidence |
|---|---|---|---|
| `coolbet-odds-snapshot` | :03/:33 | Imperva refuses the **IP** — not the fingerprint | **Same VPS, same Linux Chromium, warm FS session through an EE residential tunnel → 170,237 bytes of real `fo-tree` data.** Datacenter IP: challenge, never solved. Reproduced ×2. |
| `near-kickoff-capture` — **Coolbet third** | every 5 min | same | same fix |

### ❌ Cannot move

| Job | Blocker | What it would take |
|---|---|---|
| `unibet-site-odds` | **Not IP** — the VPS reaches unibet.ee fine (tested OK). DataDome gates on a **human-established logged-in tab**; a fresh/background CDP tab got 500/204 **from the residential IP already** (2026-09-09). | Persistent Chrome on the VPS with a real profile, logged in once interactively over Xvfb/VNC — **and only through the home tunnel**, or an EE account logging in from a Finnish datacenter is exactly what fraud systems escalate on. Unverified; budget a real spike. |
| `coolbet-feed-watchdog` — **session-keep half** | Needs CDP-Chrome (`ensure_session_live`) | Follows the Chrome |
| `cdp-watch` + `coolbet-cdp-selfheal` | Exist *only* to babysit that Chrome | Follow the Chrome |
| `paused/coolbet-ui-placer` | **Real money.** `reese84` is **TLS-bound** to the operator's own Chrome; a replayed token is blackholed, not 403'd | **Don't.** Even if technically possible, this moves the logged-in identity of the money account onto a box running three other products and a public web server. Already parked + disarmed (mig 343/354). |
| `paused/best-price-router` | Inherits both placers' constraints | Follows the placers |

## Layer 4 — Things that are not jobs

| Feature | Where | Verdict |
|---|---|---|
| `/picks`, `/performance`, match detail, admin | Next.js on VPS pm2 | ✅ already there |
| Supabase Auth (`auth.users`) + Storage (models bucket) | Supabase cloud | ⚪ stays — deliberate, post-migration |
| Stripe webhook, Sentry, Resend email | VPS web | ✅ already there |
| Telegram alerting + heal commands | VPS scheduler | ✅ already there |
| **Claude Code dev environment** | Mac | ⬜ **the original question** — `claude remote-control` in tmux; gated on RAM (§Phase 0) |

---

## Pinnacle — CORRECTED 2026-09-16: works from the Mac, and home egress unlocks it

An earlier pass here claimed Pinnacle was "broken from both hosts". **That was
wrong** — it used the system resolver and measured an EMTA DNS sinkhole.

| Resolver | Answer | |
|---|---|---|
| Telia EE (88.196.221.10) | `195.80.107.145` | **EMTA DNS sinkhole** → silent timeout |
| 1.1.1.1 / 8.8.8.8 | `104.18.42.200`, `172.64.145.56` | **real Cloudflare** |

Connecting to the real Cloudflare IP with correct SNI from the Mac: **HTTP 200,
400KB+ of matchups.** So the EMTA block is **DNS-only** and a public resolver
bypasses it entirely — which is how the `AF-PINNACLE-NOT-PINNACLE-2026-09-14`
n=92 test ran, and it still works.

**Consequence, and it is the opposite of what this doc said:** the VPS's Pinnacle
block is Cloudflare WAF 1020 **on the datacenter IP**, and the residential IP is
not CF-blocked. So **home egress + a public resolver should unlock Pinnacle on
the VPS too** — the same tunnel that fixes Coolbet. Worth testing in the same
sitting; it is one `curl`.
