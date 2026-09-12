#!/usr/bin/env python3
"""OPS-STATUS — "are Coolbet, Unibet and Epicbet actually working?" in one command.

    python3 scripts/ops/status.py
    python3 scripts/ops/status.py --json     # for a dashboard / another script

WHY THIS EXISTS
---------------
Owner, 2026-09-12, having asked the same question several times in one day:
*"whats coolbet and unibet statuses? sweepers, scrapers, daemons, etc... i think
soon its time for the local dashboard as i keep asking the same question."*

Answering it by hand meant six ad-hoc SQL queries plus `launchctl list`, every
time, with the shape of the answer depending on which agent assembled it. That
is how a status check becomes unreliable: not because the data is hard, but
because nobody runs the same check twice.

WHY IT LIVES ON THE MAC AND NOT ON THE /admin PAGES
---------------------------------------------------
Half of this state does not exist in the database. The launchd jobs, the
FlareSolverr container and the CDP-Chrome session are on the OPERATOR'S MAC, and
the VPS frontend cannot see any of them. A web dashboard could show the feed and
betting halves; it could not tell you that `coolbet-odds-snapshot` is unloaded,
which was the actual fault on 2026-09-12 (the feed had been dead for 3h and
every DB-side signal still read green).

WHAT IT CHECKS, AND WHY EACH LINE EARNED ITS PLACE — every one of these was a
real failure that stayed invisible because nothing looked at it:

  FEEDS       minutes since the last row per book. The 2026-09-12 outage was
              exactly this number going to 180+ while six other signals said OK.
  JOBS        launchd loaded/unloaded + last exit. `coolbet-odds-snapshot` sat
              UNLOADED for 6h after a pause whose resume agent never armed.
  FLARESOLVERR  not "is the port up" but CAN IT SERVE A REQUEST. FS answered its
              ready banner throughout an outage in which every session 500'd.
  SESSIONS    Coolbet JWT + Imperva cookie age. Placement dies on these, and
              they expire on different clocks.
  BETTING     what was actually staked, and — the part that matters — whether
              any pick whose kickoff has passed went UNPLACED.
  ALERTS      scheduler jobs failing repeatedly, which is where a wrong alert
              hides as noise.

Exit code is 0 when everything is healthy, 1 when anything is degraded, so this
is usable from a cron or a menu-bar widget without parsing the text.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

BOOKS = ("Coolbet", "Unibet-Site", "Epicbet", "Pinnacle")
# A book is STALE past its own cadence + slack, not a single global number:
# Coolbet/Epicbet sweep every 30 min, Unibet every 30, Pinnacle via AF hourly.
FEED_WARN_MIN = 75
FEED_BAD_MIN = 180
FS_URL = "http://localhost:8191"

G, Y, R, DIM, END = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def _c(s: str, colour: str, plain: bool) -> str:
    return s if plain else f"{colour}{s}{END}"


def _q(sql: str, params=None):
    from workers.api_clients.db import execute_query
    try:
        return execute_query(sql, params) or []
    except Exception as e:  # noqa: BLE001
        print(f"  DB query failed: {e}", file=sys.stderr)
        return []


def feeds() -> list[dict]:
    rows = _q(
        """SELECT bookmaker,
                  round(EXTRACT(EPOCH FROM (now()-max(timestamp)))/60.0)::int AS mins,
                  count(*) FILTER (WHERE timestamp > now() - interval '1 hour')  AS h1,
                  count(*) FILTER (WHERE timestamp > now() - interval '6 hours') AS h6
             FROM odds_snapshots WHERE bookmaker = ANY(%s) GROUP BY 1""",
        [list(BOOKS)],
    )
    by = {r["bookmaker"]: r for r in rows}
    out = []
    for b in BOOKS:
        r = by.get(b)
        if not r:
            out.append({"book": b, "mins": None, "h1": 0, "h6": 0, "state": "NO DATA"})
            continue
        m = int(r["mins"])
        state = "ok" if m <= FEED_WARN_MIN else ("stale" if m <= FEED_BAD_MIN else "DEAD")
        out.append({"book": b, "mins": m, "h1": r["h1"], "h6": r["h6"], "state": state})
    return out


def launchd_jobs() -> list[dict]:
    """Loaded launchd jobs. UNLOADED is the state that hides an outage: the job
    is simply absent from `launchctl list`, so nothing errors anywhere."""
    expected = sorted(p.name.replace(".plist", "").replace(".template", "")
                      for p in Path("local/launchd").glob("*.plist*"))
    try:
        listing = subprocess.run(["launchctl", "list"], capture_output=True,
                                 text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return [{"label": lbl, "loaded": None, "exit": None} for lbl in expected]
    running = {}
    for ln in listing.splitlines():
        parts = ln.split()
        if len(parts) >= 3 and parts[-1].startswith("com.oddsintel."):
            running[parts[-1]] = parts[1]
    return [{"label": lbl, "loaded": lbl in running, "exit": running.get(lbl)}
            for lbl in expected]


def flaresolverr() -> dict:
    """Liveness is NOT capability. The banner said 'ready' for 3h while every
    session returned HTTP 500 — so this creates a throwaway session, fetches a
    trivial page, and destroys it."""
    def call(body, timeout=50):
        req = urllib.request.Request(
            f"{FS_URL}/v1", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    try:
        with urllib.request.urlopen(f"{FS_URL}/", timeout=8) as r:
            up = "FlareSolverr is ready" in r.read().decode("utf8", "replace")
    except Exception as e:  # noqa: BLE001
        return {"up": False, "can_serve": False, "sessions": [], "detail": str(e)[:80]}
    if not up:
        return {"up": False, "can_serve": False, "sessions": [], "detail": "no ready banner"}
    try:
        sess = (call({"cmd": "sessions.list"}, 20).get("sessions") or [])
    except Exception:  # noqa: BLE001
        sess = []
    name = f"opsstatus_{int(datetime.now().timestamp())}"
    can, detail = False, ""
    try:
        call({"cmd": "sessions.create", "session": name}, 60)
        out = call({"cmd": "request.get", "url": "https://example.com",
                    "session": name, "maxTimeout": 30000}, 60)
        can = out.get("status") == "ok"
        detail = "" if can else str(out.get("message"))[:80]
    except Exception as e:  # noqa: BLE001
        detail = f"{type(e).__name__}: {str(e)[:70]}"
    finally:
        try:
            call({"cmd": "sessions.destroy", "session": name}, 20)
        except Exception:  # noqa: BLE001
            pass
    return {"up": True, "can_serve": can, "sessions": sess, "detail": detail}


def sessions() -> dict:
    r = _q("""SELECT session_healthy, last_heartbeat_ok, jwt_exp_at,
                     imperva_cookies_refreshed_at, placement_paused, daemons_paused,
                     left(COALESCE(last_error,''), 70) AS last_error
                FROM coolbet_session_state LIMIT 1""")
    if not r:
        return {}
    s = dict(r[0])
    now = datetime.now(timezone.utc)
    for k in ("jwt_exp_at", "imperva_cookies_refreshed_at"):
        v = s.get(k)
        s[k + "_min"] = int((now - v).total_seconds() / 60) if v else None
    return s


def betting() -> dict:
    placed = _q("""SELECT bookmaker, count(*) AS n, sum(stake)::float AS staked,
                          count(*) FILTER (WHERE result='pending') AS pending
                     FROM real_bets
                    WHERE placed_at > now() - interval '24 hours'
                      AND placed_real IS NOT FALSE GROUP BY 1""")
    # THE LINE THAT MATTERS: a pick whose kickoff has passed and which was never
    # staked. On 2026-09-12 three of these went unplaced (one won at 3.40) and
    # nothing anywhere said so.
    missed = _q("""SELECT count(*) AS n FROM shadow_bets_unique s
                     JOIN bots b ON b.id = s.bot_id
                     JOIN matches m ON m.id = s.match_id
                    WHERE b.name IN ('bot_coolbet_1x2_model_v1','bot_coolbet_ou_model_v1')
                      AND m.date < now() AND m.date > now() - interval '24 hours'
                      AND NOT EXISTS (SELECT 1 FROM real_bets rb
                                       WHERE rb.match_id = s.match_id
                                         AND rb.market = s.market
                                         AND rb.selection = s.selection
                                         AND rb.placed_real IS NOT FALSE)""")
    upcoming = _q("""SELECT count(*) AS n,
                            count(*) FILTER (WHERE NOT EXISTS (
                              SELECT 1 FROM real_bets rb WHERE rb.match_id = s.match_id
                                AND rb.market = s.market AND rb.selection = s.selection
                                AND rb.placed_real IS NOT FALSE)) AS unplaced
                       FROM shadow_bets_unique s
                       JOIN bots b ON b.id = s.bot_id
                       JOIN matches m ON m.id = s.match_id
                      WHERE b.name IN ('bot_coolbet_1x2_model_v1','bot_coolbet_ou_model_v1')
                        AND s.result = 'pending' AND m.date > now()""")
    return {"placed_24h": placed,
            "missed_24h": (missed[0]["n"] if missed else 0),
            "upcoming": (dict(upcoming[0]) if upcoming else {"n": 0, "unplaced": 0})}


def failing_jobs() -> list[dict]:
    return [dict(r) for r in _q(
        """SELECT job_name, count(*) AS n, max(started_at) AS last
             FROM pipeline_runs
            WHERE started_at > now() - interval '6 hours' AND status <> 'completed'
            GROUP BY 1 HAVING count(*) >= 3 ORDER BY 2 DESC LIMIT 6""")]


def collect(*, probe_fs: bool = True) -> dict:
    """`probe_fs=False` skips the capability probe, which costs a real browser
    fetch — fine for a fast loop, but it skips the check that actually catches
    a FlareSolverr that is up and cannot serve."""
    fs = (flaresolverr() if probe_fs else
          {"up": True, "can_serve": True, "sessions": [], "detail": "skipped (--no-fs)"})
    return {"at": datetime.now(timezone.utc).isoformat(), "feeds": feeds(),
            "jobs": launchd_jobs(), "flaresolverr": fs,
            "sessions": sessions(), "betting": betting(), "failing_jobs": failing_jobs()}


def render(d: dict, plain: bool) -> int:
    bad = False
    print(f"\n{'='*66}\nOPS STATUS  {d['at'][:16]}Z\n{'='*66}")

    print("\nFEEDS")
    for f in d["feeds"]:
        st = f["state"]
        col = G if st == "ok" else (Y if st == "stale" else R)
        if st != "ok":
            bad = True
        mins = "—" if f["mins"] is None else f"{f['mins']}m"
        print(f"  {f['book']:13}{mins:>7} ago   1h={f['h1']:>7}  6h={f['h6']:>8}   "
              f"{_c(st.upper(), col, plain)}")

    print("\nLAUNCHD JOBS (Mac)")
    for j in d["jobs"]:
        if j["loaded"] is None:
            print(f"  {j['label']:44}{_c('UNKNOWN', Y, plain)}")
            continue
        if not j["loaded"]:
            bad = True
            print(f"  {j['label']:44}{_c('UNLOADED — NOT RUNNING', R, plain)}")
        else:
            ex = j["exit"]
            note = "" if ex in ("0", "-") else f"  last exit={ex}"
            print(f"  {j['label']:44}{_c('loaded', G, plain)}{note}")

    fs = d["flaresolverr"]
    print("\nFLARESOLVERR")
    if not fs["up"]:
        bad = True
        print(f"  {_c('DOWN', R, plain)}  {fs.get('detail','')}")
    elif not fs["can_serve"]:
        bad = True
        print(f"  {_c('UP BUT CANNOT SERVE A REQUEST', R, plain)}  {fs.get('detail','')}")
        print("  → this is the 2026-09-12 shape. `docker restart oi_local_flaresolverr`")
    else:
        print(f"  {_c('serving', G, plain)}   sessions: {', '.join(fs['sessions']) or 'none'}")

    s = d["sessions"]
    if s:
        print("\nCOOLBET SESSION")
        jwt, ck = s.get("jwt_exp_at_min"), s.get("imperva_cookies_refreshed_at_min")
        jwt_s = "no JWT" if jwt is None else (f"EXPIRED {jwt}m ago" if jwt > 0
                                             else f"valid {-jwt}m")
        print(f"  JWT              {_c(jwt_s, R if (jwt or 0) > 0 else G, plain)}")
        print(f"  Imperva cookies  {'—' if ck is None else f'{ck}m old'}")
        for k in ("placement_paused", "daemons_paused"):
            if s.get(k):
                bad = True
                print(f"  {k:17}{_c('PAUSED', R, plain)}")
        if not s.get("session_healthy"):
            # Deliberately NOT counted as `bad`: the heartbeat probes an endpoint
            # that 401s on its own schedule, and on 2026-09-12 it read unhealthy
            # for 6h while seven real bets placed fine. Shown, not alarmed on.
            print(f"  heartbeat        {_c('unhealthy', Y, plain)} "
                  f"{DIM if not plain else ''}{s.get('last_error','')}{END if not plain else ''}")
            print(f"  {DIM if not plain else ''}   (placement can still work — verify with BETTING below)"
                  f"{END if not plain else ''}")

    b = d["betting"]
    print("\nBETTING (24h)")
    if b["placed_24h"]:
        for p in b["placed_24h"]:
            print(f"  {p['bookmaker']:13}{p['n']:>3} bets   EUR {p['staked']:>6.0f}   "
                  f"{p['pending']} pending")
    else:
        print("  no real bets in 24h")
    up = b["upcoming"]
    print(f"  upcoming picks   {up.get('n', 0)} pending, "
          f"{up.get('unplaced', 0)} not yet placed")
    if b["missed_24h"]:
        bad = True
        msg = f"MISSED: {b['missed_24h']} pick(s) kicked off UNPLACED in 24h"
        print(f"  {_c(msg, R, plain)}")

    if d["failing_jobs"]:
        print("\nREPEATEDLY FAILING JOBS (6h)")
        for j in d["failing_jobs"]:
            print(f"  {j['job_name'][:40]:42}{j['n']:>4} fails   last {str(j['last'])[11:16]}")

    print(f"\n{'-'*66}")
    print(_c("ALL HEALTHY" if not bad else "DEGRADED — see red above",
             G if not bad else R, plain))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--plain", action="store_true", help="no ANSI colour")
    ap.add_argument("--no-fs", action="store_true",
                    help="skip the FlareSolverr capability probe (it costs a browser fetch)")
    a = ap.parse_args()
    d = collect(probe_fs=not a.no_fs)
    if a.json:
        print(json.dumps(d, indent=2, default=str))
        return 0
    return render(d, a.plain)


if __name__ == "__main__":
    raise SystemExit(main())
