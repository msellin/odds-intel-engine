"""One-shot probe to see what Coolbet returns for a matched event.

Searches for a single team, picks the first matched event, then dumps:
  - the raw search response (truncated)
  - the raw sidebets response (truncated)
  - what our parser made of it

Goal: figure out why bulk ingest sees `0 markets parsed` on events that did
match. Hypothesis space:
  a) search returns no betOffers (just metadata) AND sidebets returns []
  b) sidebets returns betOffers but our criterion_label patterns miss them
  c) endpoint contract has changed (different field names, different URLs)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.automation.coolbet_session import CoolbetSession
from workers.automation.coolbet_placer import (
    _SEARCH_URL, _SIDEBETS_URL, _ODDS_URL,
)
from workers.automation.coolbet_explorer import parse_bet_offer


def probe(team: str) -> None:
    session = CoolbetSession()

    print(f"\n──── 1) SEARCH for {team!r} ────")
    resp = session.get(_SEARCH_URL, params={
        "search": team, "country": "EE", "language": "en", "layout": "EUROPEAN",
    })
    print(f"status={resp.status_code}")
    data = resp.json()
    print(json.dumps(data, indent=2)[:3000])
    raw_events = (
        data if isinstance(data, list)
        else data.get("events") or data.get("results") or []
    )
    if not raw_events:
        print("(no events) — try another team")
        return
    event = raw_events[0]
    event_id = event.get("id")
    print(f"\nFirst event id = {event_id}")
    print(f"Top-level keys: {sorted(event.keys())}")
    print(f"betOffers present? {bool(event.get('betOffers'))} "
          f"(count={len(event.get('betOffers') or [])})")

    print(f"\n──── 2) SIDEBETS for match_id={event_id} ────")
    resp = session.get(_SIDEBETS_URL, params={
        "matchId": event_id, "country": "EE",
        "language": "en", "layout": "EUROPEAN",
    })
    print(f"status={resp.status_code}")
    if resp.status_code != 200:
        print(f"body: {resp.text[:600]}")
    else:
        sb = resp.json()
        print(f"Top-level keys: {sorted(sb.keys())}")
        print(f"betOffers present? {bool(sb.get('betOffers'))} "
              f"(count={len(sb.get('betOffers') or [])})")
        if sb.get("betOffers"):
            print("\nFirst 3 bet_offers (criterion + outcomes):")
            for bo in sb["betOffers"][:3]:
                crit = bo.get("criterion") or {}
                print(f"  - {crit.get('englishLabel') or crit.get('label')}: "
                      f"{len(bo.get('outcomes') or [])} outcomes")

    # Common alternative endpoint shapes — probe to see which (if any) work
    print(f"\n──── 3) ALT endpoint probes for event {event_id} ────")
    alt_paths = [
        f"https://www.coolbet.com/s/sbgate/sports/fo-event/{event_id}",
        f"https://www.coolbet.com/s/sbgate/sports/event/{event_id}",
        f"https://www.coolbet.com/s/sbgate/sports/fo-match/{event_id}",
        f"https://www.coolbet.com/s/sb-odds/odds/current/fo?eventId={event_id}",
    ]
    for url in alt_paths:
        try:
            r = session.get(url, params={"language": "en"} if "?" not in url else {})
            print(f"  {url[:80]:80s} → {r.status_code} (body={len(r.text)} bytes)")
        except Exception as e:
            print(f"  {url[:80]:80s} → ERROR {e}")


if __name__ == "__main__":
    team = sys.argv[1] if len(sys.argv) > 1 else "Shanghai Shenhua"
    probe(team)
