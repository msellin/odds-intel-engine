#!/usr/bin/env python3
"""EGRESS-PROBE — which bookmaker endpoints are reachable from THIS host?

Written 2026-09-16 for the VPS-consolidation question: "which Mac jobs can move
to the VPS, and for the ones that can't, what exactly is blocking them?"

The repo had accumulated inherited claims about this ("Hetzner IP + Linux Chrome
fingerprint", "Epicbet hits no bot-protection") that were each wrong in some
part and each cost days. This script replaces the claims with a measurement.

It probes every book we collect over BOTH transports we have:

    direct  — plain requests, honest UA
    fs      — through FlareSolverr (a real Chromium), same egress IP

and classifies what came back. The transport pair is the diagnostic: a wall that
FS clears is a CHALLENGE (solvable by being a real browser); a wall that FS also
hits is a FIREWALL RULE or a reputation block (solvable only by a different IP).

USAGE
    python3 scripts/ops/egress_probe.py                 # this host, both transports
    python3 scripts/ops/egress_probe.py --direct-only   # skip FS
    python3 scripts/ops/egress_probe.py --json          # machine-readable
    python3 scripts/ops/egress_probe.py \
        --proxy socks5://10.8.0.2:1080                  # via the residential egress

THE POINT: run it on the Mac and on the VPS and diff. Then, after any egress
change (WireGuard to home, a proxy), run it on the VPS again — a row that flips
to OK is a job that can move. Nothing here writes to the DB or places anything.

Politeness: ONE request per endpoint per run, honest User-Agent, no retries,
no rotation. Do not put this on a short timer.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request

UA = "OddsIntelOps/1.0 (egress reachability probe; contact: margus@dolmit.com)"
FS_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191").rstrip("/")

# RESIDENTIAL-EGRESS (2026-09-22). When set, every probe goes out through this
# proxy instead of the host's own route. This is the whole point of the tool:
# run it plain and again with --proxy, and a row that flips to OK is a job that
# can move. NOT a fallback — if the proxy is unreachable the probe FAILS, it
# does not quietly use the datacenter IP. A silent fallback is how
# EPICBET-403-FROM-VPS reported success for six days.
PROXY: str | None = None

# ── endpoints ────────────────────────────────────────────────────────────────
# Each is the CHEAPEST read that proves the transport works: a listing call, no
# auth, no session, no side effects. `needs` records what the real job requires
# BEYOND reachability, which is what decides whether reachability is sufficient.
ENDPOINTS = [
    dict(
        book="Epicbet", wall="Cloudflare (challenge)",
        url="https://www.epicbet.com/s/core-proxy/public/sport-base/foCategory.getByCountry"
            "?input=" + urllib.parse.quote(json.dumps(
                {"country": "EE", "language": "en", "isLiveBet": False}, separators=(",", ":"))),
        ok=lambda b: '"result"' in b or '"data"' in b,
        needs="nothing — anonymous REST",
    ),
    dict(
        book="Coolbet", wall="Imperva",
        url="https://www.coolbet.com/s/sbgate/category/fo-tree/et?country=EE",
        ok=lambda b: "categoryId" in b or '"id"' in b,
        needs="Imperva cookies for the full sweep; placement additionally needs CDP-Chrome",
    ),
    dict(
        book="Pinnacle-guest", wall="Cloudflare (WAF rule)",
        url="https://guest.api.arcadia.pinnacle.com/0.1/sports/29/matchups?withSpecials=false",
        ok=lambda b: b.strip().startswith("[") or '"id"' in b,
        needs="nothing — public guest API, no key",
    ),
    dict(
        book="Unibet-Site", wall="DataDome",
        url="https://www.unibet.ee/betting/odds",
        ok=lambda b: "unibet" in b.lower() and "datadome" not in b.lower(),
        needs="a HUMAN-ESTABLISHED logged-in CDP tab — reachability is NOT sufficient",
    ),
    # SHARP-ANCHOR-CANDIDATES-2026-09-19. Added after
    # `scripts/anchor_book_sharpness_research.py` ranked every book in
    # odds_snapshots by Shin-de-vigged 1X2 log-loss, paired per fixture against
    # Pinnacle over 90 days: 1xBet (dLL -0.0003, n=15,194), Marathonbet
    # (-0.0003, n=15,290) and BetVictor (+0.0001, n=13,168) were the three
    # closest to Pinnacle and statistically tied with it. We currently get all
    # three only through API-Football, i.e. with AF's lag baked in.
    #
    # ⚠️ THESE ARE PAGE-LEVEL PROBES, NOT API PROBES, AND THAT IS DELIBERATE.
    # The three entries above each hit a REAL endpoint whose shape we have
    # observed. We have not observed these books' odds APIs, and writing a
    # guessed `/api/...` path here would put a fabricated endpoint in the one
    # file whose job is to replace claims with measurements — the exact failure
    # PINNACLE-LIMITS-VALIDITY-GATE refused ("deliberately NOT writing untested
    # scraper code against an endpoint shape known only from research").
    # So these answer ONLY "does this host answer us at all, and what wall is in
    # front of it". An OK here is necessary for a sweep and nowhere near
    # sufficient — the endpoint shape is a separate, later piece of work.
    dict(
        book="1xBet", wall="GEO-BLOCK (measured 2026-09-19: redirects to /en/block, HTTP 203)",
        url="https://1xbet.com/en/line/football",
        ok=lambda b: "football" in b.lower() and "just a moment" not in b.lower(),
        needs="UNKNOWN — page-level probe only; odds API shape not yet observed",
    ),
    dict(
        book="Marathonbet", wall="none measured (HTTP 200, real page, Mac AND VPS)",
        url="https://www.marathonbet.com/en/betting/Football",
        ok=lambda b: "football" in b.lower() and "just a moment" not in b.lower(),
        needs="UNKNOWN — page-level probe only; odds API shape not yet observed",
    ),
    dict(
        book="BetVictor", wall="geo: 403 from EE residential, OK from the VPS",
        url="https://www.betvictor.com/en-gb/sports/football",
        ok=lambda b: "football" in b.lower() and "just a moment" not in b.lower(),
        needs="UNKNOWN — page-level probe only; odds API shape not yet observed",
    ),
    dict(
        book="API-Football", wall="none (control)",
        url="https://v3.football.api-sports.io/status",
        ok=lambda b: '"response"' in b or '"errors"' in b,
        needs="API key (omitted here — a 499/403 still proves the host is reachable)",
    ),
]

# ── classification ───────────────────────────────────────────────────────────
MARKERS = [
    ("CF-WAF-RULE",   r"Attention Required|Sorry, you have been blocked|error code: 1020"),
    ("CF-CHALLENGE",  r"Just a moment|cf[-_]chl|Checking your browser|_cf_chl_opt"),
    ("IMPERVA",       r"Pardon Our Interruption|_Incapsula_Resource|x-iinfo"),
    ("DATADOME",      r"datadome|geo\.captcha-delivery"),
]


# A landing path that means "we refuse your country", not "you are a bot". These
# need a different egress, exactly like a reputation block — but they are NOT
# solvable by FlareSolverr, so telling them apart from a challenge matters.
GEO_BLOCK_PATHS = ("/block", "/restricted", "/geo-block", "/blocked")


def classify(status, body, err=None, final_url=None):
    if final_url and any(final_url.rstrip("/").endswith(p) for p in GEO_BLOCK_PATHS):
        return "GEO-BLOCK"
    if err:
        low = str(err).lower()
        if "ip is banned" in low or "cloudflare has blocked" in low:
            return "CF-WAF-RULE"
        if "timed out" in low or "timeout" in low:
            return "TIMEOUT/BLACKHOLE"
        return "ERROR"
    for name, pat in MARKERS:
        if re.search(pat, body or "", re.I):
            return name
    if status and 200 <= status < 300:
        return "OK"
    return f"HTTP-{status}"


# ── transports ───────────────────────────────────────────────────────────────
def _opener():
    """urllib opener honouring PROXY. socks5h:// and socks5:// both resolve DNS
    at the PROXY (the remote end), never locally — resolving here would leak the
    lookup to the local resolver and, on the Mac, to an EMTA-poisoned answer."""
    if not PROXY:
        return urllib.request.build_opener()
    if PROXY.startswith("socks"):
        try:
            import socks  # type: ignore
            import socket as _s
            host, _, port = PROXY.split("://", 1)[1].rpartition(":")
            socks.set_default_proxy(socks.SOCKS5, host, int(port), rdns=True)
            _s.socket = socks.socksocket
            return urllib.request.build_opener()
        except ImportError:
            raise SystemExit("--proxy socks5:// needs PySocks (pip install pysocks), "
                             "or pass an http:// proxy instead")
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


def probe_direct(url, timeout=25):
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with _opener().open(req, timeout=timeout) as r:
            body = r.read(200_000).decode("utf-8", "replace")
            # GEO-BLOCK-DETECTION-2026-09-19: the final URL is load-bearing. 1xBet
            # answers a redirect to /en/block with HTTP **203** and a full HTML
            # page, so status and body-length both look like success and the
            # probe reported "OK?-unexpected-body" — which reads as "reachable,
            # marker needs tuning" when the truth is "this host refuses our
            # country". Carry the landing URL so classify() can tell them apart.
            return dict(status=r.status, body=body, final_url=r.geturl(),
                        ms=int((time.time() - t0) * 1000))
    except urllib.error.HTTPError as e:
        body = e.read(200_000).decode("utf-8", "replace")
        return dict(status=e.code, body=body, ms=int((time.time() - t0) * 1000))
    except Exception as e:
        return dict(status=None, body="", err=e, ms=int((time.time() - t0) * 1000))


def probe_fs(url, timeout=90):
    t0 = time.time()
    payload = json.dumps({"cmd": "request.get", "url": url, "maxTimeout": 60000}).encode()
    req = urllib.request.Request(FS_URL + "/v1", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        if d.get("status") != "ok":
            return dict(status=None, body="", err=d.get("message", "FS error"),
                        ms=int((time.time() - t0) * 1000))
        sol = d.get("solution") or {}
        return dict(status=sol.get("status"), body=sol.get("response") or "",
                    ms=int((time.time() - t0) * 1000))
    except Exception as e:
        return dict(status=None, body="", err=e, ms=int((time.time() - t0) * 1000))


def fs_alive():
    try:
        req = urllib.request.Request(FS_URL + "/v1", data=b'{"cmd":"sessions.list"}',
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("status") == "ok"
    except Exception:
        return False


def egress_ip():
    for u in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read().decode().strip()
        except Exception:
            continue
    return "unknown"


def main():
    ap = argparse.ArgumentParser(description="Probe bookmaker reachability from this host")
    ap.add_argument("--direct-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--book", help="probe only this book")
    ap.add_argument("--proxy", help="route every probe through this proxy, "
                                    "e.g. socks5://10.8.0.2:1080 (the residential egress)")
    a = ap.parse_args()
    global PROXY
    PROXY = a.proxy

    ip = egress_ip()
    fs_up = (not a.direct_only) and fs_alive()
    host = socket.gethostname()

    if not a.json:
        print(f"host={host}  egress_ip={ip}  flaresolverr={'up' if fs_up else 'not used'}"
              + (f"  proxy={PROXY}" if PROXY else ""))
        print(f"{'BOOK':<14} {'WALL':<24} {'DIRECT':<20} {'VIA FS':<20}")
        print("-" * 82)

    out = []
    for e in ENDPOINTS:
        if a.book and a.book.lower() not in e["book"].lower():
            continue
        d = probe_direct(e["url"])
        dv = classify(d.get("status"), d.get("body"), d.get("err"), d.get("final_url"))
        if dv == "OK" and not e["ok"](d.get("body") or ""):
            dv = "OK?-unexpected-body"

        fv = "-"
        if dv == "GEO-BLOCK":
            # FlareSolverr shares this host's egress IP, so it cannot clear a
            # country block — spending a 90s browser solve to re-learn that is
            # pure waste, and a green FS cell here would be actively misleading.
            fv = "n/a (same IP — FS cannot clear a geo block)"
        elif fs_up and dv != "OK":
            f = probe_fs(e["url"])
            fv = classify(f.get("status"), f.get("body"), f.get("err"))
            if fv == "OK" and not e["ok"](f.get("body") or ""):
                fv = "OK?-unexpected-body"
        elif dv == "OK":
            fv = "n/a (direct works)"

        row = dict(book=e["book"], wall=e["wall"], direct=dv, via_fs=fv,
                   direct_ms=d.get("ms"), needs=e["needs"], egress_ip=ip, host=host)
        out.append(row)
        if not a.json:
            print(f"{e['book']:<14} {e['wall']:<24} {dv:<20} {fv:<20}")

    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print()
        print("READING IT: FS clears it → CHALLENGE, a browser problem, fixable HERE.")
        print("            FS also blocked → FIREWALL/REPUTATION, an IP problem, needs a different egress.")
        print("            OK but 'needs' names a session → reachability is necessary, NOT sufficient.")
        for r in out:
            print(f"  · {r['book']:<14} needs: {r['needs']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
