#!/usr/bin/env python3
"""BOOK-UPLIFT-REPORT — what is a second bookmaker actually worth?

Answers one question with evidence: if we could place at whichever of two books
offers the better price, how much more would we get per stake than betting the
incumbent alone?

Why a script and not a cron: `odds_snapshots` already retains months of history
(3.5 months / 66M rows as of 2026-09-02), so the uplift is computable
retrospectively over any window. A scheduled snapshotter would add a new job to
babysit in order to record something the database already holds — and this repo
has spent enough time this week on jobs that silently stopped writing.

    python3 scripts/book_uplift_report.py                    # last 24h
    python3 scripts/book_uplift_report.py --hours 168        # last week
    python3 scripts/book_uplift_report.py --b Coolbet --a Epicbet

THE TRAP THIS SCRIPT EXISTS TO AVOID
------------------------------------
Asian handicap `selection` is only 'home' / 'away' — the line lives in a
separate `handicap_line` column. Joining two books on (match, market,
selection) alone therefore compares a -0.5 quote against a -1.5 quote and
reports nonsense: the first run of this comparison showed Epicbet **+22.62%**
better on AH, where the line-matched answer is **+0.86%**. Same class of error
as ANALYSIS_GOTCHAS #16 (AH CLV by fixed line is invalid).

Every join below matches on `handicap_line` with IS NOT DISTINCT FROM, so NULL
(the non-handicap markets) pairs with NULL rather than dropping the row.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

console = Console()

# One row per (match, market, selection, line, book): the most recent quote in
# the window. Comparing every historical snapshot would weight busy fixtures
# by how often we happened to poll them.
_LATEST = """
    SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.handicap_line, o.bookmaker)
           o.match_id, o.market, o.selection, o.handicap_line, o.bookmaker, o.odds
      FROM odds_snapshots o
      JOIN matches m ON m.id = o.match_id
     WHERE o.bookmaker IN (%(a)s, %(b)s)
       AND o.timestamp > NOW() - (%(hours)s || ' hours')::interval
       AND o.odds > 1
       {upcoming}
     ORDER BY o.match_id, o.market, o.selection, o.handicap_line, o.bookmaker,
              o.timestamp DESC
"""


def _q(hours: int, a: str, b: str, upcoming: bool):
    return _LATEST.format(upcoming="AND m.date > NOW()" if upcoming else "")


def coverage(cur, hours: int, a: str, b: str, upcoming: bool) -> dict:
    cur.execute(f"""
        WITH latest AS ({_q(hours, a, b, upcoming)}),
             cov AS (SELECT match_id, bookmaker FROM latest GROUP BY 1, 2)
        SELECT
          (SELECT COUNT(DISTINCT match_id) FROM cov WHERE bookmaker = %(a)s),
          (SELECT COUNT(DISTINCT match_id) FROM cov WHERE bookmaker = %(b)s),
          (SELECT COUNT(*) FROM (SELECT match_id FROM cov GROUP BY 1
                                  HAVING COUNT(*) = 2) x)
    """, {"a": a, "b": b, "hours": str(hours)})
    only_a, only_b, both = cur.fetchone()
    return {"a": only_a, "b": only_b, "both": both}


def per_market(cur, hours: int, a: str, b: str, upcoming: bool) -> list[tuple]:
    cur.execute(f"""
        WITH latest AS ({_q(hours, a, b, upcoming)}),
        paired AS (
          SELECT x.market, x.odds AS odds_a, y.odds AS odds_b
            FROM latest x
            JOIN latest y
              ON  x.match_id = y.match_id
             AND  x.market   = y.market
             AND  x.selection = y.selection
             -- The whole point: NULL line pairs with NULL, and a -0.5 never
             -- pairs with a -1.5.
             AND  x.handicap_line IS NOT DISTINCT FROM y.handicap_line
           WHERE x.bookmaker = %(a)s AND y.bookmaker = %(b)s
        )
        SELECT market,
               COUNT(*),
               ROUND((100.0 * COUNT(*) FILTER (WHERE odds_b > odds_a)
                      / COUNT(*))::numeric, 1),
               ROUND(AVG(100.0 * (odds_b - odds_a) / odds_a)::numeric, 2),
               ROUND(AVG(100.0 * (GREATEST(odds_a, odds_b) - odds_a)
                         / odds_a)::numeric, 2)
          FROM paired
         GROUP BY market
        HAVING COUNT(*) >= 20
         ORDER BY COUNT(*) DESC
    """, {"a": a, "b": b, "hours": str(hours)})
    return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hours", type=int, default=24, help="lookback window (default 24)")
    ap.add_argument("--a", default="Coolbet", help="incumbent book (the baseline)")
    ap.add_argument("--b", default="Epicbet", help="challenger book")
    ap.add_argument("--all-fixtures", action="store_true",
                    help="include kicked-off fixtures (default: upcoming only)")
    args = ap.parse_args()
    upcoming = not args.all_fixtures

    from workers.api_clients.db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cov = coverage(cur, args.hours, args.a, args.b, upcoming)
        rows = per_market(cur, args.hours, args.a, args.b, upcoming)

    console.print(f"\n[bold]Line-shopping {args.a} vs {args.b}[/bold] — "
                  f"last {args.hours}h, "
                  f"{'upcoming fixtures only' if upcoming else 'all fixtures'}\n")

    both, only_a, only_b = cov["both"], cov["a"] - cov["both"], cov["b"] - cov["both"]
    console.print(f"  fixtures priced by BOTH : [bold]{both}[/bold]   "
                  f"({args.a} only {only_a} · {args.b} only {only_b})")
    if not both:
        console.print("[yellow]  No overlap in this window — nothing to compare.[/yellow]")
        return 0

    t = Table(show_header=True, header_style="bold")
    t.add_column("market"); t.add_column("pairs", justify="right")
    t.add_column(f"{args.b} better", justify="right")
    t.add_column("avg diff", justify="right")
    t.add_column("uplift (best-of-2)", justify="right")
    tot = wsum = 0
    for market, n, pct_better, avg_diff, uplift in rows:
        t.add_row(market, str(n), f"{pct_better}%", f"{avg_diff:+.2f}%", f"{uplift:+.2f}%")
        tot += n; wsum += n * float(uplift)
    console.print(t)

    if tot:
        w = wsum / tot
        console.print(f"\n  [bold]weighted uplift vs {args.a} alone: {w:+.2f}%[/bold]  (n={tot})")
        console.print("  [dim]extra return per stake IF the better book can actually be "
                      "taken — ignores account limits, and an edge that gets you "
                      "limited is worth nothing.[/dim]")
        # The bot fires at a 3% true edge (place_coolbet_ui.BOT_THRESHOLDS), so
        # express the uplift against the bar it has to clear.
        console.print(f"  [dim]for scale: bot_coolbet_value_v1 fires at a 3% true edge, "
                      f"so this is {w / 3.0 * 100:.0f}% of that threshold again.[/dim]\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
