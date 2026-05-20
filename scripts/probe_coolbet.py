"""Dump raw Coolbet responses for a known matched event id.

Usage:
    python3 scripts/probe_coolbet.py 5500980          # match id directly
    python3 scripts/probe_coolbet.py "Shanghai Shenhua"  # team name → first hit

Dumps:
  1) SEARCH response (if a team was given) so we see the search payload shape
  2) fo-match POST response for the event id (new main-markets endpoint)
  3) sidebets GET response WITH the marketTypeGroupId+matchStatus params

Goal: find why bulk ingest still parses 0 markets even when 3/3 value-bet
matches are matched on Coolbet. Either fo-match returns a shape my dict/list
handler doesn't catch, or sidebets is still empty after the param fix.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.automation.coolbet_session import CoolbetSession
from workers.automation.coolbet_placer import (
    _SEARCH_URL, _SIDEBETS_URL, _FO_MATCH_URL,
)


def _truncate(obj, n=2500) -> str:
    s = json.dumps(obj, indent=2, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"\n... [truncated, total {len(s)} bytes]"


def probe(arg: str) -> None:
    session = CoolbetSession()

    # Resolve to a numeric match id — accept either an id or a team name.
    try:
        match_id = int(arg)
        print(f"\nUsing match id directly: {match_id}")
    except ValueError:
        print(f"\n──── SEARCH for {arg!r} ────")
        r = session.get(_SEARCH_URL, params={
            "search": arg, "country": "EE", "language": "en", "layout": "EUROPEAN",
        })
        print(f"status={r.status_code}")
        sd = r.json()
        print("Top-level keys:", sorted(sd.keys()) if isinstance(sd, dict) else "(list)")
        events = sd if isinstance(sd, list) else (sd.get("events") or sd.get("results") or [])
        if not events:
            print("No events — abort.")
            return
        ev = events[0]
        print(f"\nFirst event id={ev.get('id')} name={ev.get('name')}")
        print(f"Event top-level keys: {sorted(ev.keys())}")
        print(f"betOffers present? {bool(ev.get('betOffers'))}")
        match_id = int(ev["id"])

    print(f"\n──── POST /s/sbgate/sports/fo-match (matchIds=[{match_id}]) ────")
    body = {
        "language": "en", "country": "EE", "layout": "EUROPEAN",
        "locale": "en", "matchIds": [str(match_id)],
    }
    r = session.post(_FO_MATCH_URL, json=body)
    print(f"status={r.status_code}")
    if r.status_code != 200:
        print(f"body: {r.text[:600]}")
    else:
        payload = r.json()
        print(f"payload type: {type(payload).__name__}")
        if isinstance(payload, dict):
            print(f"top-level keys: {sorted(payload.keys())}")
        elif isinstance(payload, list):
            print(f"list length: {len(payload)}")
            if payload:
                print(f"first item keys: {sorted(payload[0].keys()) if isinstance(payload[0], dict) else '(non-dict)'}")
        print("\nFULL PAYLOAD (truncated):")
        print(_truncate(payload, 3000))

    print(f"\n──── GET sidebets (with marketTypeGroupId=15, matchStatus=OPEN) ────")
    r = session.get(_SIDEBETS_URL, params={
        "matchId": match_id, "country": "EE", "language": "en",
        "layout": "EUROPEAN", "marketTypeGroupId": 15, "matchStatus": "OPEN",
    })
    print(f"status={r.status_code}")
    if r.status_code != 200:
        print(f"body: {r.text[:600]}")
    else:
        sb = r.json()
        print(f"Top-level keys: {sorted(sb.keys()) if isinstance(sb, dict) else '(list)'}")
        bo = sb.get("betOffers") if isinstance(sb, dict) else None
        print(f"betOffers count: {len(bo) if bo else 0}")
        if bo:
            print("\nFirst 3 bet_offers (criterion + outcomes count):")
            for offer in bo[:3]:
                crit = offer.get("criterion") or {}
                label = crit.get("englishLabel") or crit.get("label")
                print(f"  - {label}  →  {len(offer.get('outcomes') or [])} outcomes")
            print("\nFULL FIRST OFFER:")
            print(_truncate(bo[0], 1500))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/probe_coolbet.py <match_id_or_team_name>")
        sys.exit(1)
    probe(sys.argv[1])
