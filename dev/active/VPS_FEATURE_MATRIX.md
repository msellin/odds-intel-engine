# VPS feature matrix — can it run on the VPS?

**Every component we run, with a verdict.** Compiled 2026-09-16 from `launchctl
list`, `workers/scheduler.py` (82 registered jobs), `docker ps`, `systemctl`, and
live egress tests. Nothing was installed on the VPS to produce this.

### Legend

| Mark | Meaning |
|---|---|
| ✅ **Already** | Runs on the VPS today |
| ✅ **Yes — now** | Can move with no new infrastructure |
| 🔧 **Yes — needs egress** | Works once the VPS has a residential EE egress (**proven**) |
| 🔧 **Yes — needs a VPS Chrome** | Works once a persistent logged-in Chromium exists there (**reachability proven**) |
| ⚠️ **Technically yes — don't** | Possible, but the risk outweighs the gain |
| ⚪ **Stays elsewhere** | Deliberately hosted off-box |

---

## A. Data collection

| Feature | Today | Can run on VPS? | Evidence / what's needed |
|---|---|---|---|
| API-Football ingest (fixtures, odds ×13 books, live, lineups, injuries, standings, H2H, events, player stats) | VPS | ✅ **Already** | 82 scheduler jobs |
| Epicbet pre-match odds | VPS | ✅ **Already** | CF *challenge*; FS on the VPS solves it → 200, 552 categories |
| Epicbet **live board** (`inplay-collector`) | Mac | ✅ **Yes — now** | Epicbet-only (`BOOK = "Epicbet"`). Same transport as above |
| Near-kickoff capture — **Epicbet third** | Mac | ✅ **Yes — now** | Split the job; this third has no blocker |
| **Coolbet odds sweep** (`coolbet-odds-snapshot`) | Mac | 🔧 **Yes — needs egress** | **PROVEN**: warm FS session via EE tunnel → **170,237 bytes** of real `fo-tree`; datacenter IP never solves the challenge. Reproduced ×2 |
| Near-kickoff capture — **Coolbet third** | Mac | 🔧 **Yes — needs egress** | Same fix |
| **Unibet-Site odds** (`unibet-site-odds`) | Mac | 🔧 **Yes — needs a VPS Chrome** | **Not IP, not DataDome** — VPS Chromium loads the full **2.4 MB SPA from the datacenter IP** and is issued a `datadome` cookie. Needs Chromium + Xvfb + persistent profile; `UNIBET_CHROME_CDP_URL` already env-driven, login already automated. ~½ day |
| Near-kickoff capture — **Unibet third** | Mac | 🔧 **Yes — needs a VPS Chrome** | Rides the same Chrome |
| **Pinnacle guest API** (fresh sharp anchor) | Mac (research script) | 🔧 **Yes — needs egress + public DNS** | EMTA block is **DNS-only** (Telia → sinkhole `195.80.107.145`; 1.1.1.1 → real CF `104.18.42.200`). VPS is CF-WAF-1020 on the datacenter IP. Tunnel + public resolver should clear both. **Untested end-to-end** |
| ESPN settlement backup | VPS | ✅ **Already** | — |
| ~~Unibet-Kambi~~ | retired 2026-09-15 | — | Feed-divergent; not placeable |

## B. Model & predictions

| Feature | Today | Can run on VPS? | Notes |
|---|---|---|---|
| Poisson + XGBoost ensemble, 3-tier fallback | VPS | ✅ **Already** | |
| Platt recalibration, blend refit, DC rho | VPS | ✅ **Already** | Wed + Sun in the settlement pipeline |
| ELO ratings | VPS | ✅ **Already** | |
| MFV feature store (B-ML3 v2, form momentum, densify) | VPS | ✅ **Already** | |
| Signal jobs (xG overperf, injury severity, line velocity, season phase, draw rate, player ratings, team scoring rates) | VPS | ✅ **Already** | |
| National-team / WC predictor | VPS | ✅ **Already** | |
| Gemini news analysis | VPS | ✅ **Already** | 5×/day |
| Model bundles (storage) | Supabase Storage | ⚪ **Stays elsewhere** | 222 MB models bucket — deliberate |

## C. Betting, bots & placement

| Feature | Today | Can run on VPS? | Notes |
|---|---|---|---|
| Betting refresh (hourly :05/:35) | VPS | ✅ **Already** | |
| Shadow-bot run (all bots → `shadow_bets`) | VPS | ✅ **Already** | 32 snapshots/day |
| Paper bots (corners, team-total, 1H 1X2) | VPS | ✅ **Already** | |
| Coolbet model O/U + 1X2 shadows | VPS | ✅ **Already** | Reads Coolbet prices **from the DB** — no HTTP |
| Pick generator, trigger engine, picks publish | VPS | ✅ **Already** | |
| Placement gate (`assert_may_place`) | VPS | ✅ **Already** | |
| Manual placement queue drain | VPS | ✅ **Already** | |
| **Coolbet UI placer — REAL MONEY** | Mac (parked) | ⚠️ **Technically yes — don't** | `reese84` is **TLS-bound** to the operator's Chrome; a replayed token is blackholed. Moving it puts the money account's logged-in identity on a box running 3 other products + a public web server. Parked + disarmed (mig 343/354) |
| **Unibet placer / best-price router** | Mac (parked) | ⚠️ **Technically yes — don't** | Follows the placers |

## D. Settlement & accounting

| Feature | Today | Can run on VPS? | Notes |
|---|---|---|---|
| Settlement pipeline (21:00 / 23:30 / 01:00) | VPS | ✅ **Already** | |
| Per-match live settle on FT | VPS | ✅ **Already** | LivePoller thread |
| CLV computation, settle reconcile | VPS | ✅ **Already** | |
| Post-match stats, ELO update, prune | VPS | ✅ **Already** | |

## E. Ops, monitoring & session-keeping

| Feature | Today | Can run on VPS? | Notes |
|---|---|---|---|
| Health alerts, stall watchdog, pipeline-failure alerter | VPS | ✅ **Already** | |
| Telegram alerting + heal-command drain | VPS | ✅ **Already** | |
| Odds/feed freshness watchdogs | VPS | ✅ **Already** | |
| Budget sync, Stripe reconcile | VPS | ✅ **Already** | |
| FlareSolverr session sweep | VPS | ✅ **Already** | |
| Deploy drift check (daily 06:00) | GH Actions | ✅ **Already** | |
| Nightly backup → Storage Box | VPS | ✅ **Already** | 3-day local / 90-day remote |
| `coolbet-feed-watchdog` — **DB-judging half** | Mac | ✅ **Yes — now** | Makes **no** Coolbet HTTP calls; judges from Postgres, already on the box |
| `coolbet-feed-watchdog` — **JWT session-keep half** | Mac | 🔧 **Yes — needs a VPS Chrome** | Follows the Chrome it keeps alive |
| `flaresolverr-keepalive` | Mac | ✅ **Yes — now** | systemd timer replaces the plist |
| `cdp-watch`, `coolbet-cdp-selfheal` | Mac | ⚠️ **Follows the placer** | They exist *only* to babysit the Chrome the placer needs. If the placer stays, so do they |
| `vps-postgres-tunnel` | Mac | ✅ **Deleted, not moved** | Exists only so the Mac can reach VPS Postgres |

## F. Data & serving layer

| Feature | Today | Can run on VPS? | Notes |
|---|---|---|---|
| Postgres 17 (134 tables, 3 products) | VPS | ✅ **Already** | `shared_buffers=4GB` |
| PostgREST ×2 | VPS | ✅ **Already** | docker, host-network :3012 |
| nginx + Cloudflare Flexible SSL | VPS | ✅ **Already** | |
| Next.js frontend (pm2 :3000) | VPS | ✅ **Already** | |
| Stripe checkout / webhook / portal | VPS | ✅ **Already** | |
| Sentry, Resend email | VPS | ✅ **Already** | |
| **Supabase Auth** (`auth.users`, 52 users) | Supabase | ⚪ **Stays elsewhere** | Deliberate post-migration |

## G. Developer environment

| Feature | Today | Can run on VPS? | Notes |
|---|---|---|---|
| **Claude Code** (phone/browser driven) | Mac | ✅ **Yes — now** ⚠️ *RAM-gated* | `claude remote-control` in tmux, `--spawn worktree`, `--capacity 3`. Outbound HTTPS only, no inbound port. **Blocked on the RAM decision** |
| Dev checkout separate from deploy checkout | — | ✅ **Required** | `/opt/dev/…`, never `/opt/odds-intel-engine` |
| **Chromium + Xvfb** | not installed | 🔧 **To add** | The only genuinely new install (Unibet) |
| **Host-network FlareSolverr** | not installed | 🔧 **To add** | Existing FS is on a **bridge** net and cannot reach a tunnel |
| **WireGuard endpoint at home** | — | 🔧 **To add** | Router, Pi, or the Mac. Unlocks Coolbet + Pinnacle; makes Unibet account-safe |

---

## Totals

| Verdict | Count |
|---|---|
| ✅ Already on the VPS | 82 scheduler jobs + 13 infra/serving components |
| ✅ Can move now, no new infra | **5** Mac jobs |
| 🔧 Needs residential egress (proven) | **2** Mac jobs + Pinnacle |
| 🔧 Needs a VPS Chrome (reachability proven) | **2** Mac jobs |
| ⚠️ Technically possible, advised against | **4** (placers + their Chrome-watchers) |
| ⚪ Deliberately elsewhere | **2** (Supabase Auth + Storage) |

## The binding constraint is no longer bot-walls

Every wall dissolved under measurement. **RAM is what actually gates this now:**
15.98 GB total, 5.9 GB available, and **1.6 GB of 2 GB swap already in use** before
adding Claude Code (~0.4–1.5 GB/session) and Chromium (~0.4–1 GB). Resolve that
first — tune `shared_buffers`, evict CrossRank web (2.2 GB), or resize — because
adding either to a swapping box is how a "mystery slow pipeline" week starts.

## What is proven vs inferred

**Proven by measurement:** Coolbet via residential egress (170,237 bytes, ×2);
Unibet SPA reachable from the VPS datacenter IP (2.4 MB + `datadome` cookie);
Epicbet via VPS FS; Pinnacle EMTA block is DNS-only (real CF IP → HTTP 200, 400 KB).

**Inferred, not yet tested:** Unibet `contest-page` capture from a *logged-in* VPS
Chrome; Pinnacle from the VPS through tunnel + public DNS; Coolbet at full sweep
volume with per-match sidebets under production cadence.

Each has an acceptance test in its own doc, and every one of them is a **diff
against the Mac's current rows** — same prices, or it has not worked.
