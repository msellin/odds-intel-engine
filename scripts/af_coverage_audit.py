"""AF-COVERAGE-AUDIT — validate league coverage flags against actual AF responses.

Picks N leagues across the `coverage_events` and `coverage_lineups` flag space,
grabs a recent fixture from each, hits AF `/fixtures/events` and
`/fixtures/lineups`, compares the response against the stored flag.

If flags are reliable, we can gate the LivePoller events/lineups fetches by
league flag and save AF calls (events runs every ~135s during live matches,
so a coverage gate saves ~30 calls/day per match that doesn't have events).

Run:
  python3 scripts/af_coverage_audit.py             # 20 leagues, no DB writes
  python3 scripts/af_coverage_audit.py --n 40
"""
from __future__ import annotations
import argparse
import sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query
from workers.api_clients.api_football import _get

console = Console()


def _audit_one(fixture_id: int) -> tuple[int, int]:
    """Returns (n_events, n_lineups) from AF for a fixture, 0 on error."""
    n_events = n_lineups = 0
    try:
        ev = _get("fixtures/events", {"fixture": fixture_id})
        n_events = len(ev.get("response", []) or [])
    except Exception:
        pass
    try:
        ln = _get("fixtures/lineups", {"fixture": fixture_id})
        n_lineups = len(ln.get("response", []) or [])
    except Exception:
        pass
    return n_events, n_lineups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()

    console.print(f"[bold]AF-COVERAGE-AUDIT — sampling {args.n} leagues[/bold]")

    # Pick leagues — balance true/false on events flag
    leagues = execute_query("""
        SELECT l.id, l.name, l.country, l.tier,
               l.coverage_events, l.coverage_lineups
        FROM leagues l
        WHERE l.is_active = TRUE
          AND l.api_football_id IS NOT NULL
        ORDER BY l.priority DESC, l.tier ASC
        LIMIT 200
    """)
    true_leagues = [l for l in leagues if l["coverage_events"]][: args.n // 2]
    false_leagues = [l for l in leagues if not l["coverage_events"]][: args.n // 2]
    sample = true_leagues + false_leagues
    console.print(f"  Sampling {len(sample)} leagues "
                  f"({len(true_leagues)} flagged events=true, {len(false_leagues)} flagged events=false)")

    # For each league, find a recent finished match
    rows = []
    for league in sample:
        match = execute_query("""
            SELECT id, api_football_id, date
            FROM matches
            WHERE league_id = %s
              AND status = 'finished'
              AND api_football_id IS NOT NULL
              AND date < NOW()
            ORDER BY date DESC LIMIT 1
        """, (league["id"],))
        if not match:
            continue
        fixture_id = match[0]["api_football_id"]
        n_events, n_lineups = _audit_one(fixture_id)
        rows.append({
            "league": f"{league['country']} / {league['name']}",
            "tier": league["tier"],
            "flag_events": league["coverage_events"],
            "actual_events": n_events,
            "flag_lineups": league["coverage_lineups"],
            "actual_lineups": n_lineups,
        })

    if not rows:
        console.print("[yellow]No matches found to sample.[/yellow]")
        return

    # Compute confusion: flag vs reality
    truth: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        ev_flag = "yes" if r["flag_events"] else "no"
        ev_actual = "yes" if r["actual_events"] > 0 else "no"
        truth["events"][f"{ev_flag}→{ev_actual}"] += 1
        ln_flag = "yes" if r["flag_lineups"] else "no"
        ln_actual = "yes" if r["actual_lineups"] > 0 else "no"
        truth["lineups"][f"{ln_flag}→{ln_actual}"] += 1

    t = Table(title="Coverage flag vs actual AF response")
    for c in ("market", "flag→actual", "n"):
        t.add_column(c)
    for market in ("events", "lineups"):
        for key, n in sorted(truth[market].items()):
            t.add_row(market, key, str(n))
    console.print(t)

    # Verdict
    for market in ("events", "lineups"):
        m = truth[market]
        true_pos = m.get("yes→yes", 0)
        false_neg = m.get("yes→no", 0)
        false_pos = m.get("no→yes", 0)
        true_neg = m.get("no→no", 0)
        flag_accuracy = (true_pos + true_neg) / max(sum(m.values()), 1)
        console.print(f"  {market}: flag accuracy {flag_accuracy*100:.1f}% — "
                      f"safe-to-gate? {'✓' if flag_accuracy >= 0.95 else '✗ false-negatives would skip real data'}")
        if false_neg > 0:
            console.print(f"    [yellow]{false_neg} matches flagged false but AF actually returned data — flag is OVER-conservative[/yellow]")
        if false_pos > 0:
            console.print(f"    [red]{false_pos} matches flagged true but AF returned 0 — gate would waste calls[/red]")


if __name__ == "__main__":
    main()
