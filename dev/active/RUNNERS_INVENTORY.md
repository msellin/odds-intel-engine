# Every runner we have — Mac vs VPS, and what it would take to move

**Live state 2026-09-22 ~15:00 UTC.** From `launchctl list`, `systemctl`,
`docker ps` and `odds_snapshots` freshness. Not from docs.

## Where things stand

| | Count |
|---|---|
| VPS — scheduler jobs | **82** (one process) |
| VPS — standalone services | **2** running, 1 dead |
| Mac — loaded launchd jobs | **10** |
| Mac — LaunchDaemons (egress) | **2** |
| Mac — parked (deliberately off) | **4** |

---

## 🍎 MAC — the migration question lives here

| Job | Cadence | What it does | Move to VPS? |
|---|---|---|---|
| `coolbet-odds-snapshot` | :03/:33 | Coolbet board + sidebets → `odds_snapshots` | 🟢 **Transport PROVEN 2026-09-22** (136 KB real fo-tree via egress). Needs a volume/price-diff validation pass, then cut over. **Biggest remaining win.** |
| `coolbet-feed-watchdog` | :20/:50 | Cookie refresh, staleness verdict, JWT session-keep, Telegram heal drain | 🟡 **Split.** DB-judging half moves now; `ensure_session_live` needs CDP-Chrome |
| `unibet-site-odds` | :15/:45 | unibet.ee SPA `contest-page` via raw CDP | 🟡 Needs a persistent Chromium+Xvfb on the VPS. **Not IP, not DataDome** — VPS loads the full 2.4 MB SPA. ~½ day |
| `near-kickoff-capture` | every 5 min | Closing prices: Coolbet + Unibet + Epicbet | 🟡 **Split three ways.** Epicbet third moves now; Coolbet third with the sweep; Unibet third with the Chrome |
| `cdp-watch` | every 5 min | Logs CDP-Chrome up/down transitions | 🔴 Follows the Chrome |
| `coolbet-cdp-selfheal` | :25/:55 | Heavy CDP re-bootstrap probe | 🔴 Follows the Chrome · ⚠️ **`exit=1`** |
| `flaresolverr-keepalive` | 180s | Revives the Mac's FS Docker | 🟡 Follows whatever still needs the Mac FS |
| `mac-fs-sweep` | — | Reaps orphaned FS sessions | 🟡 Same |
| `coolbet-resume` | one-shot | Re-arms the two paused Coolbet jobs | 🔴 Follows them |
| `vps-postgres-tunnel` | always | autossh → VPS Postgres, for Mac-side jobs | 🟢 **Delete once the Mac stops running DB jobs** · ⚠️ **`exit=1`** |

### Mac LaunchDaemons — the egress (new 2026-09-22)

| Daemon | What | Notes |
|---|---|---|
| `residential-egress` | 120s keepalive for the WireGuard tunnel (`10.8.0.2`) | ⚠️ **Cannot move to the VPS — ever.** Its whole value is being an Estonian *residential* IP. Mac-independence needs a Pi or a WireGuard router |
| `residential-socks` | `microsocks` on `10.8.0.2:1080`, launchd-supervised | `KeepAlive` config-verified, restart not yet behaviour-tested |

### Parked (deliberately off)

| Job | Why |
|---|---|
| `coolbet-ui-placer` | **Real money.** `reese84` TLS-bound to the operator's Chrome. OWN paused (mig 343) + disarmed (mig 354). **Stays on the Mac regardless** |
| `best-price-router` | Follows the placers |
| `inplay-collector` | ✅ **Moved to the VPS 2026-09-22** |
| `inplay-coolbet-collector` | Never started — Coolbet in-play is a separate Imperva-budget question |

---

## 🖥 VPS — already there

| Service | What |
|---|---|
| `oddsintel-scheduler` | **82 registered jobs** in one root process |
| `oddsintel-inplay-collector` | ✅ Epicbet in-play, moved 2026-09-22, via residential egress |
| `oddsintel-heartbeat` | ⚠️ **inactive/dead** — worth a look or a deletion |
| `oi_local_flaresolverr` | Docker. Serves the VPS Epicbet pre-match sweep |
| `oddsintel-postgrest-1` | Docker, PostgREST |
| Postgres 17, nginx, pm2 web | Shared with CrossRank + BoxRank |

### The 82 scheduler jobs, grouped

| Group | n |
|---|---|
| Odds ingest (AF bulk, Epicbet, closing snap, freshness, price sanity) | 13 |
| Model / predictions (calibration, blend, MFV, shadows) | 13 |
| Betting / bots (refresh, shadow run, triggers, paper bots, picks) | 18 |
| Settlement (pipeline, CLV, reconcile) | 6 |
| Signals / enrichment | 8 |
| Health / alerts | 8 |
| Maintenance | 3 |
| Other (fixtures, morning chain, dashboards, backfills) | 13 |

---

## Live odds freshness (last 6h, 14:52 UTC)

| Book | Rows 6h | Last write | Source |
|---|---|---|---|
| 1xBet, Bet365, Betano, Betfair, BetVictor, Marathonbet, Pinnacle, William Hill | 163k…17k | 3 min | 🖥 VPS (AF bulk) |
| Unibet-Site | 20,365 | 5 min | 🍎 Mac |
| SBO | 6,145 | 13 min | 🖥 VPS |
| Epicbet | 65,494 | 17 min | 🖥 VPS |
| **Coolbet** | **875** | **258 min** ⛔ | 🍎 Mac — **outage, see below** |

## ⛔ Live outage found while compiling this

**Coolbet odds were down 4.3 hours** (last write 10:35 UTC). Both
`coolbet-odds-snapshot` and `coolbet-feed-watchdog` were unloaded. The
`coolbet-resume` agent **fired twice and logged "loaded" for both** — while
`launchctl list` showed neither. Reloaded with `launchctl bootstrap`; both now
RUNNING.

**This is the second occurrence.** `RESUME-LOADED-BUT-NOT-RUNNING` (2026-09-20)
cost **14 hours** and its recorded cause was the Mac being asleep across the fire
time, so the registration never took. The script already verifies and retries —
and it still happened.

**This is the strongest argument yet for the migration.** Coolbet is the
executable price basis for every real stake, and it has now silently gone dark
twice in three days because a laptop slept. Nothing on the VPS has that failure
mode.

---

## Suggested order

| # | Work | Why now |
|---|---|---|
| 1 | **Coolbet sweep → VPS** | Transport already proven. Removes the exact failure that just cost 4.3h |
| 2 | **Egress → Pi or router** | Until this, the VPS still depends on the Mac being awake. Check the router first — free if it does WireGuard |
| 3 | Split `near-kickoff-capture`; move the Epicbet third | Cheap, no new infrastructure |
| 4 | Split `coolbet-feed-watchdog`; move the DB half | Cheap |
| 5 | Unibet Chromium on the VPS | ~½ day; do after 1–2 |
| 6 | Delete `vps-postgres-tunnel`, fix/remove `oddsintel-heartbeat`, fix `coolbet-cdp-selfheal` | Three known-failing/dead things |
| — | **Placer stays on the Mac** | Real money, TLS-bound token. Not a migration candidate |
