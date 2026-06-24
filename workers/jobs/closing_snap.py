"""
OddsIntel — Closing-line snapshot job.

Fires every 5 min during peak betting hours and snapshots fresh odds for
matches with kickoff in the next 15 min. Stores with is_closing=TRUE.

Purpose: maximize coverage of true closing-line snaps so clv_pinnacle and
clv (any-book) can be computed reliably at settlement.

Schedule: every 5 min, 12:00-23:00 UTC.

Cost: ~1 AF call per imminent match. On a busy evening with 20 matches
imminent, that is ~20 calls × 12 cycles/hr = 240 calls/hr (~2400 calls
over a 10-hour window — well under the 7500/day Ultra budget).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.api_football import _get, parse_fixture_odds
from workers.api_clients.db import execute_query, bulk_insert
from workers.utils.odds_quality import filter_garbage_ou_rows

console = Console()

WINDOW_PRE_MIN = 15   # minutes before kickoff
WINDOW_POST_MIN = 5   # grace after kickoff in case we're a tick late


def _imminent_matches() -> list[dict]:
    now = datetime.now(timezone.utc)
    lo = now - timedelta(minutes=WINDOW_POST_MIN)
    hi = now + timedelta(minutes=WINDOW_PRE_MIN)
    return execute_query(
        """SELECT id, api_football_id, date
           FROM matches
           WHERE api_football_id IS NOT NULL
             AND status IN ('scheduled', 'live')
             AND date BETWEEN %s AND %s""",
        (lo.isoformat(), hi.isoformat()),
    )


def run_closing_snap() -> dict:
    """Snapshot fresh odds for imminent matches. Returns counts."""
    matches = _imminent_matches()
    if not matches:
        return {"matches": 0, "rows": 0}

    now_iso = datetime.now(timezone.utc).isoformat()
    all_tuples: list[tuple] = []
    cols = ["match_id", "bookmaker", "market", "selection", "odds",
            "timestamp", "is_closing", "minutes_to_kickoff",
            "handicap_line", "is_opening"]

    for m in matches:
        af_id = m["api_football_id"]
        match_id = m["id"]
        ko = m["date"]
        if isinstance(ko, str):
            ko = datetime.fromisoformat(ko.replace("Z", "+00:00"))
        if ko.tzinfo is None:
            ko = ko.replace(tzinfo=timezone.utc)
        mtk = int((ko - datetime.now(timezone.utc)).total_seconds() / 60)

        try:
            resp = _get("odds", {"fixture": af_id})
        except Exception as e:
            console.print(f"  [yellow]AF /odds fixture={af_id}: {e}[/yellow]")
            continue

        parsed = parse_fixture_odds(resp.get("response", []))
        if not parsed:
            continue
        parsed = filter_garbage_ou_rows(parsed)
        if not parsed:
            continue

        for row in parsed:
            all_tuples.append((
                match_id,
                row["bookmaker"],
                row["market"],
                row["selection"],
                row["odds"],
                now_iso,
                True,
                mtk,
                row.get("handicap_line"),
                False,
            ))

    if not all_tuples:
        return {"matches": len(matches), "rows": 0}

    bulk_insert("odds_snapshots", cols, all_tuples, page_size=2000)
    return {"matches": len(matches), "rows": len(all_tuples)}


def main():
    res = run_closing_snap()
    console.print(
        f"[green]closing_snap: {res['matches']} matches, "
        f"{res['rows']} odds rows stored[/green]"
    )


if __name__ == "__main__":
    main()
