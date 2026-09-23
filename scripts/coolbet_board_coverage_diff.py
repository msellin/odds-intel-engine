"""COOLBET BOARD vs BULK coverage diff ([[#091]] / [[#110]] step 2, 2026-09-23).

Question: can the scheduled Coolbet sweep switch from `run_bulk` (per-fixture
search fallback — ~7,500 requests in 8 h on 2026-09-23, which got the exit IP
flagged, #108) to `run_board_sweep` (fo-tree + one listing per category, ~20×
fewer requests) WITHOUT losing the fixtures that matter?

What this does — READ ONLY, nothing is written except the request footprint:
  1. Walks Coolbet's football board the way `run_board_sweep` does (fo-tree +
     every category's event list — the near-term skip memo is IGNORED so every
     category is seen), and matches each event to our fixtures with the same
     matcher. No market or odds requests: ~1 request per category.
  2. Reads the pairings `run_bulk` recorded in `book_event_map` for fixtures in
     the same window.
  3. Diffs the two match_id sets, split by hours-to-kickoff, and for every
     fixture only `run_bulk` found says WHY the board missed it: its Coolbet
     event was not on the board at all (category missing from fo-tree), or it
     was on the board but the matcher did not pair it.

Run from any IP Coolbet is not blocking (2026-09-23: the Mac, while the VPS exit
is flagged):
    python3 scripts/coolbet_board_coverage_diff.py --horizon-hours 48
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402

_BUCKETS = ((0, 6), (6, 12), (12, 24), (24, 48), (48, 96))


def _bucket(ko: datetime, now: datetime) -> str:
    h = (ko - now).total_seconds() / 3600
    for lo, hi in _BUCKETS:
        if h < hi:
            return f"{lo}-{hi}h"
    return ">96h"


def walk_board(horizon_hours: float, sleep_s: float, cache: Path | None = None,
               from_cache: bool = False) -> dict:
    from workers.automation.coolbet_explorer import (
        _load_af_candidates, enumerate_coolbet_football_categories)
    from workers.automation.coolbet_matching import match_event_to_af
    from workers.automation.coolbet_placer import _parse_iso_start, fetch_events_for_league
    from workers.automation.coolbet_session import CoolbetSession

    import json
    if from_cache:
        # Re-match a saved board without sending Coolbet a single request.
        saved = json.loads(cache.read_text())
        cats, listings = saved["cats"], {int(k): v for k, v in saved["listings"].items()}
        session = None
    else:
        session = CoolbetSession(require_auth=False)
        cats = enumerate_coolbet_football_categories(session)
        listings = {}
    if not cats:
        raise SystemExit("fo-tree returned no categories — Coolbet unreachable from this IP")
    af = _load_af_candidates(horizon_hours)
    horizon = datetime.now(timezone.utc) + timedelta(hours=horizon_hours)
    out = {"categories": len(cats), "cat_fail": 0, "events": 0, "near_term": 0,
           "board_event_ids": set(), "matched": {}, "unmatched": [], "requests": 1}
    for i, cat in enumerate(cats, 1):
        try:
            if from_cache:
                events = listings.get(int(cat["id"]), [])
            else:
                events = fetch_events_for_league(session, cat["id"], raise_on_error=True)
                listings[int(cat["id"])] = events
                out["requests"] += 1
        except Exception as e:  # noqa: BLE001
            out["cat_fail"] += 1
            print(f"  category {cat.get('name')} failed: {e}", file=sys.stderr)
            continue
        for ev in events:
            out["events"] += 1
            out["board_event_ids"].add(str(ev.get("id")))
            if (ev.get("status") not in (None, "OPEN")) or not ev.get("home") or not ev.get("away"):
                continue
            start = _parse_iso_start(ev.get("start"))
            if start is not None and start > horizon:
                continue
            out["near_term"] += 1
            row, score, _ = match_event_to_af(ev["home"], ev["away"], ev.get("iso"), start, af)
            if row is None:
                out["unmatched"].append((cat.get("name"), ev["home"], ev["away"], ev.get("start")))
                continue
            out["matched"][row["id"]] = (str(ev["id"]), score)
        if i % 25 == 0:
            print(f"  …{i}/{len(cats)} categories, matched {len(out['matched'])}", file=sys.stderr)
        if not from_cache:
            time.sleep(sleep_s)
    if cache and not from_cache:
        cache.write_text(json.dumps({"cats": cats, "listings": listings}, default=str))
    if from_cache:
        out["requests"] = 0
    return out


def bulk_pairings(horizon_hours: float) -> dict[str, dict]:
    rows = execute_query(
        """SELECT DISTINCT ON (bem.match_id) bem.match_id::text AS match_id, bem.book_event_id,
                  bem.matched_at, m.date AS ko, l.name AS league, l.country,
                  ht.name AS home, at2.name AS away
             FROM book_event_map bem
             JOIN matches m ON m.id = bem.match_id
             JOIN leagues l ON l.id = m.league_id
             JOIN teams ht ON ht.id = m.home_team_id
             JOIN teams at2 ON at2.id = m.away_team_id
            WHERE bem.bookmaker = 'Coolbet'
              AND m.date > now() AND m.date < now() + make_interval(hours => %s)
            ORDER BY bem.match_id, bem.matched_at DESC""", (int(horizon_hours),)) or []
    return {r["match_id"]: r for r in rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon-hours", type=float, default=48)
    ap.add_argument("--sleep", type=float, default=0.6, help="between category requests")
    ap.add_argument("--cache", type=Path, help="save the walked board here (JSON)")
    ap.add_argument("--from-cache", action="store_true",
                    help="re-match the board saved in --cache; sends Coolbet nothing")
    a = ap.parse_args()

    now = datetime.now(timezone.utc)
    bulk = bulk_pairings(a.horizon_hours)
    board = walk_board(a.horizon_hours, a.sleep, a.cache, a.from_cache)
    kos = {r["id"]: r["ko"] for r in execute_query(
        "SELECT id::text AS id, date AS ko FROM matches WHERE id = ANY(%s::uuid[])",
        (list(set(board["matched"]) | set(bulk)),)) or []}
    b_ids = {m for m in board["matched"] if kos.get(m) and kos[m] > now}
    k_ids = set(bulk)

    print(f"\nBoard walk: {board['categories']} categories ({board['cat_fail']} failed), "
          f"{board['events']} events, {board['near_term']} within {a.horizon_hours:.0f} h, "
          f"{len(board['matched'])} matched; {board['requests']} requests")
    print(f"run_bulk pairings in window (book_event_map): {len(k_ids)}")
    print(f"\n{'window':>8} {'both':>6} {'board only':>11} {'bulk only':>10}")
    both, bo, ko_ = Counter(), Counter(), Counter()
    for m in b_ids & k_ids:
        both[_bucket(kos[m], now)] += 1
    for m in b_ids - k_ids:
        bo[_bucket(kos[m], now)] += 1
    for m in k_ids - b_ids:
        ko_[_bucket(kos[m], now)] += 1
    for lo, hi in _BUCKETS:
        k = f"{lo}-{hi}h"
        if both[k] or bo[k] or ko_[k]:
            print(f"{k:>8} {both[k]:>6} {bo[k]:>11} {ko_[k]:>10}")
    print(f"{'total':>8} {len(b_ids & k_ids):>6} {len(b_ids - k_ids):>11} {len(k_ids - b_ids):>10}")

    missing = sorted(k_ids - b_ids, key=lambda m: kos[m])
    if missing:
        print("\nOnly run_bulk found these — why the board missed each:")
        for m in missing:
            r = bulk[m]
            why = ("on the board, matcher did not pair it" if r["book_event_id"] in board["board_event_ids"]
                   else "its Coolbet event is NOT on the board (category absent from fo-tree)")
            print(f"  {r['ko']:%a %H:%M}  {r['country']}/{r['league']}: {r['home']} v {r['away']} — {why}")
    if board["unmatched"]:
        print(f"\nBoard events in window the matcher did not pair: {len(board['unmatched'])} "
              f"(first 15; many are leagues we have no fixture for)")
        for u in board["unmatched"][:15]:
            print(f"  {u[0]}: {u[1]} v {u[2]} ({u[3]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
