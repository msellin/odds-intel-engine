# VPS migration — session state, 2026-09-23 08:00 UTC

Read this plus `PRIORITY_QUEUE.md` and you can continue cold. Written before
context compaction; everything below was verified, not assumed.

---

## The one-line state

**Coolbet, Epicbet and the in-play collector all run on the VPS now, egressing
through a €6.50/mo Estonian exit node. Five jobs remain on the Mac, and most of
them exist only to serve jobs that have left.**

## What changed today, and why it worked

The whole migration was blocked on a belief that turned out to be wrong: that
Coolbet/Epicbet/Pinnacle reject the VPS because it is a **datacenter**, so only
the operator's **residential** line could work. That is why the plan involved a
Raspberry Pi, a garage router and a WireGuard tunnel to a MacBook.

A zone.ee box (**AS49604 Zone Media, Tallinn — an Estonian HOSTING ASN**)
disproved it:

```
                 Hetzner (FI)      zone.ee (EE hosting)   home (EE residential)
Epicbet          CF-CHALLENGE  ->  OK                     OK
Pinnacle-guest   CF-WAF-RULE   ->  OK                     OK
Coolbet          blocked       ->  OK                     (flagged)
BetVictor        OK            ->  403                    403
```

**BetVictor is the row that settles it.** It works from Hetzner and 403s from the
operator's home line — and zone.ee gets 403 too. So an Estonian *hosting* IP is
classified like an Estonian *eyeball* IP. **The discriminator is COUNTRY, not
datacenter-vs-residential.** Every earlier conclusion had those two variables
confounded because the only data points were a Finnish datacenter and an Estonian
home line.

## Architecture

```
Hetzner VPS (8 vCPU / 16 GB) — ALL COMPUTE
  scheduler (94 jobs), Postgres, FlareSolverr, PostgREST, nginx, web app
  oddsintel-zone-egress.service  ->  ssh -N -D 127.0.0.1:1081
                                        |
zone.ee (1 vCPU / 2 GB) — EXIT NODE ONLY -+
  no docker, no FlareSolverr, no collectors, no DB. load 0.02, 382 MB used.
  Verified: only sshd/chrony/agetty run there.
```

Selection is **per-request**, via `COOLBET_RESIDENTIAL_PROXY` and
`EPICBET_RESIDENTIAL_PROXY` (both `socks5h://127.0.0.1:1081` in the scheduler
unit). **Not a default route** — BetVictor proves a blanket route would silently
break a book that works today.

## Moved to the VPS (verified writing)

| Job | Evidence |
|---|---|
| Epicbet in-play collector | `oddsintel-inplay-collector.service`, board refreshed |
| Epicbet near-kickoff | `oddsintel-near-kickoff-epicbet.timer`, fails 0 |
| Epicbet pre-match sweep | direct, **FlareSolverr never opened**, 537 cats / 546 ms |
| **Coolbet odds sweep** | registered `:03/:33`; **1,508 rows / 30 matches / 36 markets** |
| Coolbet feed watchdog | parked with it |
| **Coolbet near-kickoff third** | VPS timer now `--books Epicbet,Coolbet` |
| **Mac FlareSolverr + its 2 caretakers** | ⛔ **RETIRED** — see below |

Mac plists for these are in `~/Library/LaunchAgents/paused/`.

## Still on the Mac — 5, all CDP-bound

| Job | Why it is still here |
|---|---|
| ~~`unibet-site-odds`~~ | **MOVED to VPS 2026-09-23** — scheduler `unibet_site_odds`, logged-out Chrome; login not needed to read |
| ~~`near-kickoff-capture`~~ | **MOVED 2026-09-23** — VPS timer now `--books Epicbet,Coolbet,Unibet-Site` |
| `cdp-watch` | watches that Chrome |
| `coolbet-cdp-selfheal` | watches that Chrome · ⚠️ `exit=1`, logged out |
| `vps-postgres-tunnel` | load-bearing: `.env` DATABASE_URL is `localhost:5433` |

**Every remaining job exists because of CDP-Chrome.** Unibet is the only feed
still on the Mac; the other three are its caretakers plus the DB tunnel. So the
Mac's job list will not shrink further until Unibet moves — which needs a
persistent logged-in Chromium on the VPS (`unibet-on-vps-plan.md`, ~½ day).

### Retired 2026-09-23: the Mac's FlareSolverr

Stopped, container kept (`docker start oi_local_flaresolverr` to revive),
`restart=no` so a reboot does not. `flaresolverr-keepalive` and `mac-fs-sweep`
are parked.

**Evidence it was safe:** 6 hours of container logs showed 62 requests to
`example.com` and 6 to Coolbet `fo-tree`. The Coolbet ones predate parking the
sweep. Every `example.com` request was `flaresolverr-keepalive` probing liveness
— i.e. **a keepalive keeping alive a service nothing else used.** The remaining
Mac jobs are all CDP-only. ~430 MB reclaimed.

## Traps that cost real time today — do not re-learn these

1. **A dry run of the wrong code path proves nothing.** The board sweep
   (`fo-tree`) was green — 98 categories, 41 s — while the real job failed on the
   very next endpoint. `fo-tree` survives ONE warmup navigation; `fo-category`
   and `search` need TWO. With one, they return **HTTP 500**, which reads as
   FlareSolverr dying rather than a challenge that never resolved.
2. **Never replay the Mac's Imperva cookies from another egress.** `reese84` is
   bound to the client that minted it, and Imperva **blackholes** a mismatch
   rather than 403-ing it — so it hangs for 30 s and looks like "Coolbet is
   slow". On a proxied egress the session must mint its own identity.
3. **"Not the host IP" is not "the right IP".** The first egress check accepted a
   session still bound to the MacBook tunnel while the configured proxy was
   zone.ee. It now compares against the configured proxy's own egress.
4. **`pgrep -f "<pattern>"` matches your own shell** if the command line contains
   the pattern. Cost ~15 min believing a finished restore was still running, and
   produced a false "FlareSolverr is running on zone.ee".
5. **StartInterval LaunchAgents do not fire on this Mac.** Four self-healing jobs
   were dead, including `flaresolverr-keepalive`. All converted to
   `StartCalendarInterval`; smoke `LAUNCHD-NO-STARTINTERVAL` prevents regression.

## Open, needing the owner

- **P0 `ANON-ROLE-READS-EVERYTHING`** — the `anon` role has SELECT on all 128
  tables via `api.oddsintel.app`. Verified exploitable with the public key:
  `coolbet_placement_attempts`, `pg_stat_statements`, `picks_board` all return
  data. Not fixed because revoking blind breaks the site. **Strong lead: a
  `web_anon` role already exists with SELECT on only 2 of 128 tables** — that
  looks like the intended design and PostgREST is pointed at the wrong role.
- **#022 `real_bets` duplicates** — the row says 5 groups; there are **19**, and
  11 have *different odds/stake hours apart*, so they are plausibly genuine
  separate bets rather than double-clicks. All 38 rows are settled and scored.
  Recommended: add the unique index scoped to NEW rows only and leave history
  alone.
- **#023 OWN picks public** — subset of the P0 above.
- `coolbet-cdp-selfheal` is `exit=1`: CDP-Chrome is logged out, needs a hand
  login in a FOREGROUND window. Does not affect the odds sweep.

## Housekeeping

**Four times today a parallel session swept this work into unrelated commits**
(`#084 capture the baseline`, `LINEUP FEATURES (#086)`). The code is committed,
pushed and working, but the history says a real-money price-path migration was
part of a lineup-features change. Relevant if anyone bisects.
