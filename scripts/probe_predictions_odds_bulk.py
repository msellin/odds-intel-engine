"""
Probe /predictions and /odds for undocumented bulk parameters.

Predictions docs say only ?fixture=ID is accepted. Try:
  - ?ids=A-B-C  (mirrors /fixtures, /injuries, /sidelined pattern)
  - ?league=X&season=Y (mirrors /standings, /odds league filter)
  - ?date=YYYY-MM-DD (mirrors /injuries, /odds date filter)

Odds docs say page size = 10 fixtures. Test whether ?bookmaker=ID filter
reduces total page count (i.e. is paging by fixture, not by row).

Costs ~10 AF calls. Run once.
"""

import os
import sys
import time
from pathlib import Path
from datetime import date

from dotenv import load_dotenv
sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from workers.api_clients.api_football import _get  # noqa: E402
from workers.api_clients.db import execute_query  # noqa: E402


def _hr(label: str):
    print()
    print("=" * 70)
    print(f"  {label}")
    print("=" * 70)


def probe_predictions_bulk():
    _hr("/predictions  — test undocumented bulk params")

    # Get 3 real AF fixture IDs from today/yesterday
    rows = execute_query(
        """SELECT api_football_id FROM matches
           WHERE api_football_id IS NOT NULL
             AND date >= NOW() - INTERVAL '2 days'
             AND date <= NOW() + INTERVAL '2 days'
           ORDER BY date DESC LIMIT 3"""
    )
    fids = [r["api_football_id"] for r in rows]
    if len(fids) < 2:
        print("  Not enough recent fixtures with AF IDs; aborting probe")
        return
    print(f"  Sample fixture IDs: {fids}")

    # Baseline: 1 call works, returns 1 prediction
    print()
    print(f"  → Baseline: ?fixture={fids[0]}")
    try:
        d = _get("predictions", {"fixture": fids[0]})
        resp = d.get("response", [])
        errs = d.get("errors") or {}
        print(f"    errors={errs}  response[]={len(resp)}")
    except Exception as e:
        print(f"    EXC: {e}")

    # Bulk attempt 1: ?ids=A-B-C
    print()
    print(f"  → Attempt: ?ids={'-'.join(map(str, fids))}")
    try:
        d = _get("predictions", {"ids": "-".join(map(str, fids))})
        resp = d.get("response", [])
        errs = d.get("errors") or {}
        print(f"    errors={errs}  response[]={len(resp)}")
        if resp and len(resp) > 1:
            print("    ✓ multi-fixture response — BULK MIGHT WORK")
        elif resp:
            print("    ✗ only 1 in response — bulk param ignored or partial")
    except Exception as e:
        print(f"    EXC: {e}")

    # Bulk attempt 2: ?fixtures=A-B-C
    print()
    print(f"  → Attempt: ?fixtures={'-'.join(map(str, fids))}")
    try:
        d = _get("predictions", {"fixtures": "-".join(map(str, fids))})
        resp = d.get("response", [])
        errs = d.get("errors") or {}
        print(f"    errors={errs}  response[]={len(resp)}")
    except Exception as e:
        print(f"    EXC: {e}")

    # Bulk attempt 3: ?date=today
    today = date.today().isoformat()
    print()
    print(f"  → Attempt: ?date={today}")
    try:
        d = _get("predictions", {"date": today})
        resp = d.get("response", [])
        errs = d.get("errors") or {}
        print(f"    errors={errs}  response[]={len(resp)}")
        if resp and len(resp) > 5:
            print(f"    ✓ multi-fixture response ({len(resp)}) — DATE BULK MIGHT WORK")
    except Exception as e:
        print(f"    EXC: {e}")

    # Bulk attempt 4: ?league=X&season=Y (no fixture)
    # Pick a league + season we know has fixtures today
    lr = execute_query(
        """SELECT l.api_football_id AS lid, m.season AS season, COUNT(*) AS n
           FROM matches m JOIN leagues l ON l.id = m.league_id
           WHERE m.date >= NOW() - INTERVAL '1 day' AND m.date <= NOW() + INTERVAL '1 day'
             AND l.api_football_id IS NOT NULL AND m.season IS NOT NULL
           GROUP BY l.api_football_id, m.season
           ORDER BY n DESC LIMIT 1"""
    )
    if lr:
        lid, season = lr[0]["lid"], lr[0]["season"]
        print()
        print(f"  → Attempt: ?league={lid}&season={season}")
        try:
            d = _get("predictions", {"league": lid, "season": season})
            resp = d.get("response", [])
            errs = d.get("errors") or {}
            print(f"    errors={errs}  response[]={len(resp)}")
            if resp and len(resp) > 1:
                print(f"    ✓ multi-fixture response ({len(resp)}) — LEAGUE BULK MIGHT WORK")
        except Exception as e:
            print(f"    EXC: {e}")


def probe_odds_bookmaker_filter():
    _hr("/odds  — test if ?bookmaker= reduces page count")

    today = date.today().isoformat()

    # Baseline: ?date=today, page=1
    print()
    print(f"  → Baseline: ?date={today}&page=1 (no bookmaker filter)")
    try:
        d = _get("odds", {"date": today, "page": 1})
        paging = d.get("paging", {})
        resp = d.get("response", [])
        errs = d.get("errors") or {}
        print(f"    errors={errs}  paging={paging}  response[]={len(resp)} fixtures on page 1")
        if resp:
            sample = resp[0]
            bm_count = len(sample.get("bookmakers", []))
            print(f"    sample fixture has {bm_count} bookmakers")
    except Exception as e:
        print(f"    EXC: {e}")

    # With bookmaker=8 (Bet365)
    print()
    print(f"  → ?date={today}&bookmaker=8&page=1 (Bet365 only)")
    try:
        d = _get("odds", {"date": today, "bookmaker": 8, "page": 1})
        paging = d.get("paging", {})
        resp = d.get("response", [])
        errs = d.get("errors") or {}
        print(f"    errors={errs}  paging={paging}  response[]={len(resp)}")
        if resp:
            sample = resp[0]
            bm_count = len(sample.get("bookmakers", []))
            print(f"    sample fixture has {bm_count} bookmakers (filter applied: should be 1)")
    except Exception as e:
        print(f"    EXC: {e}")

    # With bet=1 (1X2 only)
    print()
    print(f"  → ?date={today}&bet=1&page=1 (1X2 / Match Winner only)")
    try:
        d = _get("odds", {"date": today, "bet": 1, "page": 1})
        paging = d.get("paging", {})
        resp = d.get("response", [])
        errs = d.get("errors") or {}
        print(f"    errors={errs}  paging={paging}  response[]={len(resp)}")
        if resp:
            sample = resp[0]
            bm_count = len(sample.get("bookmakers", []))
            bets_per_bm = [len(b.get("bets", [])) for b in sample.get("bookmakers", [])]
            print(f"    sample fixture: {bm_count} bookmakers, bets/bm: {bets_per_bm[:5]}")
    except Exception as e:
        print(f"    EXC: {e}")


if __name__ == "__main__":
    probe_predictions_bulk()
    probe_odds_bookmaker_filter()
