"""Full Coolbet response-shape probe for one match.

Hits the four endpoints the match page uses and dumps each raw response:
  1) POST /s/sbgate/sports/fo-match              — markets list (no odds)
  2) GET  /s/sbgate/sports/fo-market/sidebets    — side markets (no odds)
  3) POST /s/sb-odds/odds/current/fo             — odds for SIMPLE markets
  4) POST /s/sb-odds/odds/current/fo-line/       — odds for LINE markets

Goal: confirm the response shape of the two odds endpoints so the explorer
can stitch (markets from #1+#2) × (odds from #3+#4) into (market, selection,
odds, line) tuples ingestible by store_coolbet_odds_snapshot.

Usage:
    python3 scripts/probe_coolbet.py 5500980
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.automation.coolbet_session import CoolbetSession
from workers.automation.coolbet_placer import _SIDEBETS_URL, _FO_MATCH_URL, _ODDS_URL

_ODDS_LINE_URL = "https://www.coolbet.com/s/sb-odds/odds/current/fo-line/"


def _trunc(obj, n=2500) -> str:
    s = json.dumps(obj, indent=2, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"\n... [truncated, total {len(s)} bytes]"


def probe(match_id: int) -> None:
    session = CoolbetSession()

    # ── 1) fo-match (markets, no odds) ───────────────────────────────────────
    print(f"\n──── 1) POST /s/sbgate/sports/fo-match  matchIds=[{match_id}] ────")
    r = session.post(_FO_MATCH_URL, json={
        "language": "en", "country": "EE", "layout": "EUROPEAN",
        "locale": "en", "matchIds": [str(match_id)],
    })
    print(f"status={r.status_code}")
    fo = r.json() if r.status_code == 200 else {}
    matches = fo.get("matches") or []
    simple_mids: list[int] = []   # line == 0 markets (1X2, BTTS, DC)
    line_mids:   list[int] = []   # line != 0 markets (OU, AH, ...)
    for m in matches:
        for mkt in m.get("markets") or []:
            mid = mkt.get("id")
            if not mid:
                continue
            line = mkt.get("line")
            try:
                line_val = float(line)
            except (TypeError, ValueError):
                line_val = 0.0
            if line_val == 0.0 and (line is None or str(line) in ("0", "0.0", "")):
                simple_mids.append(int(mid))
            else:
                line_mids.append(int(mid))
    print(f"matches={len(matches)}  simple_mids={len(simple_mids)}  line_mids={len(line_mids)}")
    print(f"first simple_mid={simple_mids[:3]}  first line_mid={line_mids[:3]}")

    # ── 2) sidebets (side markets, no odds) ──────────────────────────────────
    print(f"\n──── 2) GET sidebets (limit=13) ────")
    r = session.get(_SIDEBETS_URL, params={
        "matchId": match_id, "country": "EE", "language": "en",
        "layout": "EUROPEAN", "limit": 13, "matchStatus": "OPEN",
    })
    print(f"status={r.status_code}")
    if r.status_code == 200:
        sb = r.json()
        print(f"top-level keys: {sorted(sb.keys()) if isinstance(sb, dict) else '(list)'}")
        sb_markets = sb.get("markets") or []
        print(f"sidebets markets count: {len(sb_markets)}")
        if sb_markets:
            print("first market keys:", sorted(sb_markets[0].keys()))
            for mkt in sb_markets:
                mid = mkt.get("id")
                if not mid:
                    continue
                line = mkt.get("line")
                try:
                    line_val = float(line)
                except (TypeError, ValueError):
                    line_val = 0.0
                if line_val == 0.0 and (line is None or str(line) in ("0", "0.0", "")):
                    simple_mids.append(int(mid))
                else:
                    line_mids.append(int(mid))

    # ── 3) odds for simple markets ───────────────────────────────────────────
    if simple_mids:
        print(f"\n──── 3) POST /s/sb-odds/odds/current/fo  (simple, {len(simple_mids)} mids) ────")
        r = session.post(_ODDS_URL, json={
            "where": {"market_id": {"in": simple_mids[:20]}},
        })
        print(f"status={r.status_code}")
        if r.status_code == 200:
            payload = r.json()
            print(f"payload type: {type(payload).__name__}")
            if isinstance(payload, dict):
                print(f"top-level keys: {sorted(payload.keys())}")
            elif isinstance(payload, list):
                print(f"list length: {len(payload)}")
                if payload:
                    print(f"first item keys: {sorted(payload[0].keys()) if isinstance(payload[0], dict) else '(non-dict)'}")
            print("\nFULL PAYLOAD (truncated):")
            print(_trunc(payload, 2500))
        else:
            print(f"body: {r.text[:600]}")

    # ── 4) odds for line markets (nested array) ──────────────────────────────
    if line_mids:
        # Browser groups related lines together. Use a single group of all line
        # mids for the probe — server should accept it.
        grouped = [line_mids[:20]]
        print(f"\n──── 4) POST /s/sb-odds/odds/current/fo-line/  ({len(grouped[0])} mids, 1 group) ────")
        r = session.post(_ODDS_LINE_URL, json={"marketIds": grouped})
        print(f"status={r.status_code}")
        if r.status_code == 200:
            payload = r.json()
            print(f"payload type: {type(payload).__name__}")
            if isinstance(payload, dict):
                print(f"top-level keys: {sorted(payload.keys())}")
            elif isinstance(payload, list):
                print(f"list length: {len(payload)}")
                if payload:
                    print(f"first item type: {type(payload[0]).__name__}")
                    if isinstance(payload[0], dict):
                        print(f"first item keys: {sorted(payload[0].keys())}")
                    elif isinstance(payload[0], list) and payload[0]:
                        print(f"first sublist length: {len(payload[0])}, first item keys: "
                              f"{sorted(payload[0][0].keys()) if isinstance(payload[0][0], dict) else 'n/a'}")
            print("\nFULL PAYLOAD (truncated):")
            print(_trunc(payload, 3000))
        else:
            print(f"body: {r.text[:600]}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/probe_coolbet.py <match_id>")
        sys.exit(1)
    probe(int(sys.argv[1]))
