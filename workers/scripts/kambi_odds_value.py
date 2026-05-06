"""
Diagnostic: Is Kambi worth keeping?

Queries historical odds_snapshots to answer:
  1. What bookmakers do we have, and how many odds rows each?
  2. For each match+market+selection, which bookmaker had the BEST odds?
     → What % of the time is the winner a Kambi bookmaker (unibet/paf)?
  3. For each match+market+selection, which had the WORST odds?
     → What % of the time is the loser a Kambi bookmaker?
  4. When Kambi wins, by how much? (average margin over the next-best)
  5. Breakdown by market (1X2 home/draw/away, O/U, BTTS)

Run from project root:
  python workers/scripts/kambi_odds_value.py
  python workers/scripts/kambi_odds_value.py --days 90   # look back further
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.db import execute_query
from rich.console import Console
from rich.table import Table

console = Console()
# "ub" = Kambi operator code for Unibet (stored as "ub" from Kambi scraper)
# "paf" = Kambi operator code for Paf
# "kambi" = another Kambi source label seen in the data
# Note: AF *also* provides "Unibet" separately — that is NOT Kambi
KAMBI_BOOKMAKERS = {"ub", "paf", "kambi"}


def run(lookback_days: int = 30):
    console.rule(f"[bold cyan]Kambi Odds Value Analysis — last {lookback_days} days")

    # ── 1. Bookmaker inventory ─────────────────────────────────────────────
    console.print("\n[bold]1. Bookmaker inventory[/bold]")
    rows = execute_query("""
        SELECT
            bookmaker,
            count(*) AS total_rows,
            count(DISTINCT match_id) AS matches_covered,
            round(avg(odds)::numeric, 3) AS avg_odds
        FROM odds_snapshots
        WHERE timestamp >= now() - (%(days)s || ' days')::interval
          AND is_live = false
        GROUP BY bookmaker
        ORDER BY total_rows DESC
    """, {"days": lookback_days})

    t = Table("Bookmaker", "Rows", "Matches", "Avg odds", "Source")
    for r in rows:
        source = "KAMBI" if r["bookmaker"] in KAMBI_BOOKMAKERS else "AF"
        t.add_row(
            r["bookmaker"],
            f"{r['total_rows']:,}",
            f"{r['matches_covered']:,}",
            str(r["avg_odds"]),
            f"[green]{source}[/green]" if source == "KAMBI" else source,
        )
    console.print(t)

    # ── 2. Best-odds winner per match+market+selection ──────────────────────
    console.print("\n[bold]2. Who provides the BEST odds most often?[/bold]")
    rows = execute_query("""
        WITH ranked AS (
            SELECT
                match_id,
                market,
                selection,
                bookmaker,
                odds,
                ROW_NUMBER() OVER (
                    PARTITION BY match_id, market, selection
                    ORDER BY odds DESC
                ) AS rn
            FROM odds_snapshots
            WHERE timestamp >= now() - (%(days)s || ' days')::interval
              AND is_live = false
              AND odds > 1.0
        )
        SELECT
            bookmaker,
            count(*) AS times_best,
            round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct
        FROM ranked
        WHERE rn = 1
        GROUP BY bookmaker
        ORDER BY times_best DESC
    """, {"days": lookback_days})

    total_best = sum(r["times_best"] for r in rows)
    kambi_best = sum(r["times_best"] for r in rows if r["bookmaker"] in KAMBI_BOOKMAKERS)
    kambi_best_pct = 100 * kambi_best / total_best if total_best else 0

    t = Table("Bookmaker", "Times best", "% of all", "Source")
    for r in rows:
        source = "KAMBI" if r["bookmaker"] in KAMBI_BOOKMAKERS else "AF"
        style = "green" if source == "KAMBI" else ""
        t.add_row(
            r["bookmaker"],
            f"{r['times_best']:,}",
            f"{r['pct']}%",
            source,
            style=style,
        )
    console.print(t)
    console.print(
        f"[bold]Kambi total (unibet+paf): {kambi_best:,} of {total_best:,} "
        f"({kambi_best_pct:.1f}% of best-odds slots)[/bold]"
    )

    # ── 3. Worst-odds loser per match+market+selection ─────────────────────
    console.print("\n[bold]3. Who provides the WORST odds most often?[/bold]")
    rows = execute_query("""
        WITH ranked AS (
            SELECT
                match_id,
                market,
                selection,
                bookmaker,
                odds,
                ROW_NUMBER() OVER (
                    PARTITION BY match_id, market, selection
                    ORDER BY odds ASC
                ) AS rn
            FROM odds_snapshots
            WHERE timestamp >= now() - (%(days)s || ' days')::interval
              AND is_live = false
              AND odds > 1.0
        )
        SELECT
            bookmaker,
            count(*) AS times_worst,
            round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct
        FROM ranked
        WHERE rn = 1
        GROUP BY bookmaker
        ORDER BY times_worst DESC
    """, {"days": lookback_days})

    t = Table("Bookmaker", "Times worst", "% of all", "Source")
    for r in rows:
        source = "KAMBI" if r["bookmaker"] in KAMBI_BOOKMAKERS else "AF"
        style = "red" if source == "KAMBI" else ""
        t.add_row(r["bookmaker"], f"{r['times_worst']:,}", f"{r['pct']}%", source, style=style)
    console.print(t)

    # ── 4. When Kambi wins, by how much? ───────────────────────────────────
    console.print("\n[bold]4. When Kambi has the best odds — margin vs next-best[/bold]")
    rows = execute_query("""
        WITH per_slot AS (
            SELECT
                match_id,
                market,
                selection,
                max(odds) AS best_odds,
                max(odds) FILTER (WHERE bookmaker NOT IN ('unibet','paf')) AS best_non_kambi,
                max(odds) FILTER (WHERE bookmaker IN ('unibet','paf')) AS best_kambi,
                bool_or(bookmaker IN ('unibet','paf')) AS has_kambi
            FROM odds_snapshots
            WHERE timestamp >= now() - (%(days)s || ' days')::interval
              AND is_live = false
              AND odds > 1.0
            GROUP BY match_id, market, selection
        )
        SELECT
            market,
            count(*) AS slots_with_both,
            -- slots where Kambi strictly beats all AF bookmakers
            count(*) FILTER (WHERE best_kambi > best_non_kambi) AS kambi_wins,
            count(*) FILTER (WHERE best_kambi = best_non_kambi) AS kambi_ties,
            count(*) FILTER (WHERE best_kambi < best_non_kambi) AS kambi_loses,
            -- when Kambi wins: average edge it provides
            round(avg(
                CASE WHEN best_kambi > best_non_kambi
                THEN best_kambi - best_non_kambi ELSE NULL END
            )::numeric, 4) AS avg_margin_when_wins,
            round(max(
                CASE WHEN best_kambi > best_non_kambi
                THEN best_kambi - best_non_kambi ELSE NULL END
            )::numeric, 4) AS max_margin_when_wins
        FROM per_slot
        WHERE has_kambi AND best_non_kambi IS NOT NULL
        GROUP BY market
        ORDER BY market
    """, {"days": lookback_days})

    t = Table("Market", "Both present", "Kambi wins", "Kambi ties", "Kambi loses",
              "Avg margin (win)", "Max margin (win)")
    for r in rows:
        wins = r["kambi_wins"] or 0
        total = r["slots_with_both"] or 1
        pct = 100 * wins / total
        t.add_row(
            r["market"],
            f"{r['slots_with_both']:,}",
            f"{wins:,} ({pct:.1f}%)",
            f"{r['kambi_ties'] or 0:,}",
            f"{r['kambi_loses'] or 0:,}",
            str(r["avg_margin_when_wins"] or "—"),
            str(r["max_margin_when_wins"] or "—"),
        )
    console.print(t)

    # ── 5. By selection (home / draw / away) ───────────────────────────────
    console.print("\n[bold]5. Best-odds winner breakdown by selection (1X2 only)[/bold]")
    rows = execute_query("""
        WITH ranked AS (
            SELECT
                match_id,
                selection,
                bookmaker,
                odds,
                ROW_NUMBER() OVER (
                    PARTITION BY match_id, selection
                    ORDER BY odds DESC
                ) AS rn
            FROM odds_snapshots
            WHERE timestamp >= now() - (%(days)s || ' days')::interval
              AND is_live = false
              AND market IN ('1x2', '1X2', 'Match Winner')
              AND odds > 1.0
        )
        SELECT
            selection,
            count(*) AS total,
            count(*) FILTER (WHERE bookmaker IN ('unibet','paf')) AS kambi_wins,
            round(100.0 * count(*) FILTER (WHERE bookmaker IN ('unibet','paf')) / count(*), 1) AS kambi_pct
        FROM ranked
        WHERE rn = 1
        GROUP BY selection
        ORDER BY selection
    """, {"days": lookback_days})

    t = Table("Selection", "Total slots", "Kambi wins", "Kambi %")
    for r in rows:
        t.add_row(r["selection"], f"{r['total']:,}", f"{r['kambi_wins']:,}", f"{r['kambi_pct']}%")
    console.print(t)

    # ── 6. Verdict ────────────────────────────────────────────────────────
    console.rule("[bold]Verdict")
    console.print(
        f"\nKambi bookmakers (unibet + paf) provide the best odds on "
        f"[bold cyan]{kambi_best_pct:.1f}%[/bold cyan] of all match+market+selection slots "
        f"where they have coverage.\n\n"
        "Interpretation:\n"
        "  • >10% → Kambi meaningfully improves best-odds quality. Worth keeping.\n"
        "  • 2–10% → Marginal but non-zero. Weigh against dedup complexity cost.\n"
        "  • <2%  → Statistically irrelevant. Removing Kambi loses almost nothing.\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    args = parser.parse_args()
    run(args.days)
