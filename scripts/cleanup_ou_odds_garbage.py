"""
ODDS-QUALITY-CLEANUP — Stage B: purge garbage Over/Under rows from odds_snapshots.

Two passes:
  1. Hard-delete OU rows from blacklisted bookmakers
     (api-football, api-football-live, William Hill).
  2. Pair-validation sweep: for each (match_id, bookmaker, market, timestamp)
     with both an over and an under row, drop both if 1/over + 1/under < 1.02
     (mathematically impossible market — every legit feed has overround ≥ 2%).

Quarantine: targeted rows are first copied into odds_snapshots_quarantined
(same schema + 1 reason column) for forensic rollback.

Dry-run by default. Pass --apply to execute.

Idempotent: repeated --apply runs after a clean state are no-ops.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query, execute_write, get_conn
from workers.utils.odds_quality import BLACKLISTED_OU_SOURCES, MIN_OU_IMPLIED_SUM

console = Console()


def ensure_quarantine_table() -> None:
    """Create the quarantine table on first run."""
    execute_write(
        """
        CREATE TABLE IF NOT EXISTS odds_snapshots_quarantined (
          LIKE odds_snapshots INCLUDING DEFAULTS,
          quarantine_reason text NOT NULL,
          quarantined_at timestamptz NOT NULL DEFAULT NOW()
        )
        """
    )


def show_baseline_counts() -> None:
    rows = execute_query(
        """SELECT bookmaker, market, COUNT(*) AS n
           FROM odds_snapshots
           WHERE market LIKE 'over_under_%'
           GROUP BY 1, 2
           ORDER BY 1, 2"""
    )
    table = Table(title="Baseline OU row counts per (bookmaker, market)")
    table.add_column("bookmaker")
    table.add_column("market")
    table.add_column("rows", justify="right")
    for r in rows:
        bm = r["bookmaker"] or "(null)"
        mark_blacklisted = " ← BLACKLIST" if bm in BLACKLISTED_OU_SOURCES else ""
        table.add_row(bm + mark_blacklisted, r["market"], f"{r['n']:,}")
    console.print(table)


def purge_blacklisted(apply: bool) -> int:
    """Pass 1: hard-delete OU rows from blacklisted bookmakers."""
    sources = list(BLACKLISTED_OU_SOURCES)
    count_rows = execute_query(
        """SELECT COUNT(*) AS n FROM odds_snapshots
           WHERE market LIKE 'over_under_%%'
             AND bookmaker = ANY(%s::text[])""",
        (sources,),
    )
    n = count_rows[0]["n"] if count_rows else 0
    console.print(
        f"[bold]Pass 1 — blacklisted sources:[/bold] {n:,} rows match "
        f"({', '.join(sources)})"
    )
    if n == 0 or not apply:
        return n

    # Quarantine then delete in one transaction.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO odds_snapshots_quarantined
                   SELECT *, 'blacklisted_source' AS quarantine_reason, NOW() AS quarantined_at
                     FROM odds_snapshots
                    WHERE market LIKE 'over_under_%%'
                      AND bookmaker = ANY(%s::text[])""",
                (sources,),
            )
            cur.execute(
                """DELETE FROM odds_snapshots
                    WHERE market LIKE 'over_under_%%'
                      AND bookmaker = ANY(%s::text[])""",
                (sources,),
            )
            conn.commit()
    console.print(f"[green]  ✓ deleted {n:,} blacklisted rows (quarantined first)[/green]")
    return n


def purge_impossible_pairs(apply: bool) -> int:
    """Pass 2: drop both sides of (over, under) pairs whose 1/o + 1/u < 1.02."""
    # Find ids of rows that participate in an impossible pair.
    # Self-join odds_snapshots on the (match_id, bookmaker, market, timestamp) tuple.
    rows = execute_query(
        f"""
        SELECT o1.id AS over_id, o2.id AS under_id,
               o1.match_id, o1.bookmaker, o1.market, o1.timestamp,
               o1.odds AS over_odds, o2.odds AS under_odds
          FROM odds_snapshots o1
          JOIN odds_snapshots o2
            ON o1.match_id  = o2.match_id
           AND o1.bookmaker = o2.bookmaker
           AND o1.market    = o2.market
           AND o1.market LIKE 'over_under_%%'
           AND o1.timestamp = o2.timestamp
           AND o1.selection = 'over'
           AND o2.selection = 'under'
           AND o1.odds > 1.0 AND o2.odds > 1.0
           AND (1.0 / o1.odds + 1.0 / o2.odds) < {MIN_OU_IMPLIED_SUM}
        """
    )
    if not rows:
        console.print("[bold]Pass 2 — impossible pairs:[/bold] 0 pairs found")
        return 0

    over_ids = [r["over_id"] for r in rows]
    under_ids = [r["under_id"] for r in rows]
    all_ids = over_ids + under_ids
    console.print(
        f"[bold]Pass 2 — impossible pairs:[/bold] {len(rows):,} pairs "
        f"({len(all_ids):,} rows) where 1/over + 1/under < {MIN_OU_IMPLIED_SUM}"
    )
    if not apply:
        return len(all_ids)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO odds_snapshots_quarantined
                   SELECT *, 'impossible_pair' AS quarantine_reason, NOW() AS quarantined_at
                     FROM odds_snapshots
                    WHERE id = ANY(%s::uuid[])""",
                (all_ids,),
            )
            cur.execute(
                "DELETE FROM odds_snapshots WHERE id = ANY(%s::uuid[])",
                (all_ids,),
            )
            conn.commit()
    console.print(f"[green]  ✓ deleted {len(all_ids):,} rows from impossible pairs (quarantined first)[/green]")
    return len(all_ids)


def show_post_audit() -> None:
    """Re-run the per-bookmaker invalid-rate audit on what's left."""
    rows = execute_query(
        """
        WITH pairs AS (
          SELECT o1.bookmaker,
                 1.0 / o1.odds + 1.0 / o2.odds AS s
            FROM odds_snapshots o1
            JOIN odds_snapshots o2
              ON o1.match_id  = o2.match_id
             AND o1.bookmaker = o2.bookmaker
             AND o1.market    = o2.market
             AND o1.market LIKE 'over_under_%%'
             AND o1.timestamp = o2.timestamp
             AND o1.selection = 'over'
             AND o2.selection = 'under'
        )
        SELECT bookmaker, COUNT(*) AS n,
               ROUND(AVG(s)::numeric, 3) AS avg_sum,
               ROUND((SUM(CASE WHEN s < 1.0 THEN 1 ELSE 0 END)::numeric / COUNT(*)) * 100, 2)
                 AS pct_invalid
          FROM pairs
         GROUP BY bookmaker
        HAVING COUNT(*) > 30
         ORDER BY pct_invalid DESC
        """
    )
    table = Table(title="Post-cleanup OU pair quality per bookmaker")
    table.add_column("bookmaker")
    table.add_column("pairs", justify="right")
    table.add_column("avg_sum", justify="right")
    table.add_column("%invalid", justify="right")
    for r in rows:
        table.add_row(r["bookmaker"], f"{r['n']:,}", str(r["avg_sum"]), f"{r['pct_invalid']}%")
    console.print(table)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    args = p.parse_args()

    apply = args.apply
    console.print(f"[bold cyan]ODDS-QUALITY-CLEANUP — Stage B {'APPLY' if apply else 'DRY-RUN'}[/bold cyan]")

    if apply:
        ensure_quarantine_table()

    show_baseline_counts()
    n1 = purge_blacklisted(apply)
    n2 = purge_impossible_pairs(apply)
    console.print(
        f"\n[bold]Total rows {'deleted' if apply else 'would be deleted'}:[/bold] "
        f"{n1 + n2:,} ({n1:,} blacklisted + {n2:,} impossible-pair sides)"
    )
    if apply:
        show_post_audit()


if __name__ == "__main__":
    main()
