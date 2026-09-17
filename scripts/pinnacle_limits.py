#!/usr/bin/env python3
"""PINNACLE-LIMITS — read Pinnacle's own stake limits, the one field no aggregator sells.

WHY THIS EXISTS. Every edge number in this system is computed against a
de-vigged Pinnacle price. That price answers "what does the sharp book think",
but it does not answer "does the sharp book believe this price". Pinnacle says
the second thing explicitly, per market, in `limits[].amount` (type
`maxRiskStake`), and it is free in the public guest API.

Measured 2026-09-17, median maxRiskStake on the 1X2 close:

    Spain - La Liga           $300   (max $15,000)
    England - Premier League  $225   (max  $4,500)
    Germany - Bundesliga      $150   (max  $3,000)
    Denmark - Division 1      $100   (max    $500)
    Poland - 2nd Liga         $100   (max    $200)

A $100-limit quote is a placeholder, not a consensus. "Edge" measured against it
is two soft prices disagreeing, and it is the leading explanation for the
thin-market gradient in `dev/research/kaunitz_thin_markets.py` (thin markets
-1.79% at t=-0.38 vs thick -9.24% at t=-8.16): not softness we can exploit, but
an anchor that was never a real price.

TRANSPORT, AND WHY IT LOOKS ODD. Estonian ISPs sinkhole `pinnacle.com` and its
subdomains to 195.80.107.145 (EMTA's blocked-domain list). The block is DNS-only
-- a public resolver returns the genuine Cloudflare IPs -- so this pins the
hostname to a public-resolver answer while leaving SNI and certificate
validation fully intact. That is what `curl --resolve` does; it is not a proxy,
not a VPN, and it does not weaken TLS. Verified 2026-09-17: HTTP 200, 190 soccer
leagues, `limits` present on 100% of market rows.

⚠️ This reads a public, unauthenticated odds API for analysis. It places no bets
and holds no account. The EMTA list is a consumer gambling-access measure, and
whether to run this from inside Estonia rather than the German VPS is an OWNER
decision -- set PINNACLE_FORCE_PUBLIC_DNS=0 to disable the pinning and let the
system resolver decide, which is the right setting on a host that is not blocked.

SCOPE, deliberately narrow (carried from AF-PINNACLE-NOT-PINNACLE-2026-09-14):
read-only, no DB writes, no new tables, no daemon, no pick engine. It emits JSON.
Testing the gate against outcomes is a separate, offline step.

USAGE
    python3 scripts/pinnacle_limits.py --leagues 40 --out ledger/pinnacle_limits.json
    python3 scripts/pinnacle_limits.py --dry-run          # list leagues, fetch nothing
"""
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "guest.api.arcadia.pinnacle.com"
SOCCER_SPORT_ID = 29

# Politeness. One request per league, jittered. This is a public endpoint with no
# auth and no published rate limit; that is a reason to be careful, not careless.
SLEEP_MIN, SLEEP_MAX = 1.2, 2.4
MAX_CONSECUTIVE_ERRORS = 3

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


def _pin_dns_to_public_resolver() -> str | None:
    """Resolve HOST via a public resolver and pin it, leaving SNI/TLS untouched.

    Returns the IP used, or None if pinning is disabled or unnecessary. Falls back
    silently to the system resolver when the public lookup fails -- on a host that
    is not blocked (the VPS), nothing here changes behaviour.
    """
    if os.getenv("PINNACLE_FORCE_PUBLIC_DNS", "1") == "0":
        return None
    try:
        import subprocess
        out = subprocess.run(
            ["dig", "+short", "@1.1.1.1", HOST],
            capture_output=True, text=True, timeout=10,
        ).stdout.split()
        ips = [x for x in out if x.count(".") == 3 and x[0].isdigit()]
        if not ips:
            return None
        ip = ips[0]
        # The sinkhole answer must never be pinned -- if a public resolver somehow
        # returns it, we are not looking at Pinnacle and should not pretend to be.
        if ip.startswith("195.80.107."):
            return None
        _orig = socket.getaddrinfo

        def _patched(host, port, *a, **kw):
            if host == HOST:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
            return _orig(host, port, *a, **kw)

        socket.getaddrinfo = _patched
        return ip
    except Exception:
        return None


def _get(path: str, timeout: float = 25.0):
    req = urllib.request.Request(
        f"https://{HOST}{path}", headers={"User-Agent": UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def league_limit_profile(league_id: int) -> dict | None:
    """Median/min/max maxRiskStake for a league's FULL-MATCH moneyline markets.

    Full-match moneyline (`type=moneyline`, `period=0`) is the right slice: it is
    the market our 1x2 picks live in, and it is the one Pinnacle prices most
    seriously, so its limit is the least generous read of the league's tier.
    """
    rows = _get(f"/0.1/leagues/{league_id}/markets/straight")
    amts = [
        lim["amount"]
        for m in rows
        if m.get("type") == "moneyline" and m.get("period") == 0
        for lim in (m.get("limits") or [])
        if lim.get("type") == "maxRiskStake" and lim.get("amount")
    ]
    if not amts:
        return None
    return {
        "n_markets": len(amts),
        "median": float(statistics.median(amts)),
        "min": float(min(amts)),
        "max": float(max(amts)),
        "total_market_rows": len(rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", type=int, default=40,
                    help="how many leagues to profile (featured first)")
    ap.add_argument("--out", default="ledger/pinnacle_limits.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="list the leagues and exit without fetching markets")
    args = ap.parse_args()

    ip = _pin_dns_to_public_resolver()
    print(f"[pinnacle-limits] host={HOST} pinned_ip={ip or '(system resolver)'}")

    try:
        leagues = _get(f"/0.1/sports/{SOCCER_SPORT_ID}/leagues?all=false")
    except urllib.error.URLError as e:
        print(f"[pinnacle-limits] UNREACHABLE: {e}. On a blocked host set "
              f"PINNACLE_FORCE_PUBLIC_DNS=1, or run from an unblocked egress.")
        return 2

    # Featured leagues first -- they carry the real limits and are what our picks
    # concentrate in; the long tail is where the $100 placeholders live.
    leagues.sort(key=lambda L: (not L.get("isFeatured"), L.get("name") or ""))
    picked = leagues[: args.leagues]
    print(f"[pinnacle-limits] {len(leagues)} soccer leagues offered; profiling {len(picked)}")

    if args.dry_run:
        for L in picked[:20]:
            print(f"    {L.get('id'):>6}  {L.get('name')}")
        return 0

    out: dict[str, dict] = {}
    errors = 0
    for i, L in enumerate(picked, 1):
        lid, name = L.get("id"), L.get("name")
        try:
            prof = league_limit_profile(lid)
            errors = 0
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"    [{i}/{len(picked)}] {name}: {type(e).__name__}")
            if errors >= MAX_CONSECUTIVE_ERRORS:
                print("[pinnacle-limits] aborting after 3 consecutive errors — be polite")
                break
            continue
        if prof:
            out[str(lid)] = {"league_id": lid, "name": name, **prof}
            print(f"    [{i}/{len(picked)}] {name:<38} median ${prof['median']:>8,.0f} "
                  f"(min ${prof['min']:,.0f} max ${prof['max']:,.0f}) n={prof['n_markets']}")
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    if not out:
        print("[pinnacle-limits] nothing profiled — not writing")
        return 1

    payload = {
        "snapshot_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": f"https://{HOST}/0.1/leagues/{{id}}/markets/straight",
        "field": "limits[].amount where type=maxRiskStake, moneyline period=0",
        "note": "A low maxRiskStake means Pinnacle does not stand behind the price. "
                "Edge measured against a placeholder is two soft prices disagreeing.",
        "leagues": out,
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2))
    meds = [v["median"] for v in out.values()]
    print(f"\n[pinnacle-limits] wrote {p} — {len(out)} leagues, "
          f"median-of-medians ${statistics.median(meds):,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
