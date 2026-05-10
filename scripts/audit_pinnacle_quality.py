"""OU-MARKET-FEATURES Phase A — Pinnacle odds quality audit.

Run before extending MFV with OU/BTTS market features. The same data path
has produced wrong OU odds 10x already (most recent fix today: 9d4166e
OU-PIN-REQUIRED). Default assumption: data is dirty until proven otherwise.

Five checks:
  A.1 Pinnacle 1X2 outliers (odds < 1.05 or > 30) + investigate matches
  A.2 Pinnacle OU 2.5 outliers + overround sanity per (match, timestamp)
  A.3 Pinnacle BTTS overround sanity
  A.4 Mislabel sweep — bookmaker label hygiene per market
  A.5 Coverage measurement on finished matches in v12/v13 training window
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.supabase_client import execute_query
from rich.console import Console
from rich.table import Table

console = Console()


def section(title: str) -> None:
    console.print(f"\n[bold cyan]{'═' * 70}[/bold cyan]")
    console.print(f"[bold cyan]{title}[/bold cyan]")
    console.print(f"[bold cyan]{'═' * 70}[/bold cyan]")


def a1_pinnacle_1x2_outliers() -> None:
    section("A.1 — Pinnacle 1X2 outliers (odds < 1.05 OR > 30)")

    # Aggregate by (match_id, selection) to deduplicate the snapshot stream.
    rows = execute_query(
        """
        SELECT os.match_id, os.selection,
               MIN(os.odds) AS min_odds, MAX(os.odds) AS max_odds,
               COUNT(*) AS n_snaps,
               m.date AS match_date,
               ht.name AS home, at.name AS away,
               l.name AS league
        FROM odds_snapshots os
        JOIN matches m ON m.id = os.match_id
        LEFT JOIN teams ht ON ht.id = m.home_team_id
        LEFT JOIN teams at ON at.id = m.away_team_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE os.bookmaker = 'Pinnacle' AND os.market = '1x2'
          AND (os.odds < 1.05 OR os.odds > 30)
        GROUP BY os.match_id, os.selection, m.date, ht.name, at.name, l.name
        ORDER BY m.date DESC LIMIT 100
        """,
        (),
    )
    if not rows:
        console.print("[green]✓ Zero Pinnacle 1X2 outlier matches.[/green]")
        return
    console.print(f"[yellow]⚠ {len(rows)} (match_id, selection) pairs with extreme Pinnacle 1X2 odds:[/yellow]\n")
    t = Table(show_header=True)
    for col in ["league", "match", "date", "selection", "odds (min..max)", "snaps"]:
        t.add_column(col)
    for r in rows[:30]:
        odds_range = f"{float(r['min_odds']):.3f}..{float(r['max_odds']):.3f}"
        match_label = f"{r['home']} vs {r['away']}"
        t.add_row(
            (r["league"] or "?")[:25],
            match_label[:40],
            str(r["match_date"])[:10],
            r["selection"],
            odds_range,
            str(r["n_snaps"]),
        )
    console.print(t)


def a2_pinnacle_ou25() -> None:
    section("A.2 — Pinnacle OU 2.5 overround (last 30d)")
    # Overround check catches outliers indirectly — any wildly-priced row
    # that's a true mislabel will produce overround far outside [1.02, 1.10].
    # Use timestamp directly (indexed) — JOIN to matches blew the timeout.

    # Overround on finished matches in the last 30d. Use timestamp directly
    # (no JOIN to matches) — that's the indexed column on odds_snapshots.
    overround_results = execute_query(
        """
        WITH latest AS (
            SELECT DISTINCT ON (match_id, selection)
                   match_id, selection, odds
            FROM odds_snapshots
            WHERE bookmaker = 'Pinnacle' AND market = 'over_under_25'
              AND is_live = false
              AND timestamp >= NOW() - INTERVAL '30 days'
            ORDER BY match_id, selection, timestamp DESC
        ),
        paired AS (
            SELECT match_id,
                   MAX(CASE WHEN selection = 'over'  THEN odds END) AS over_odds,
                   MAX(CASE WHEN selection = 'under' THEN odds END) AS under_odds
            FROM latest
            GROUP BY match_id
            HAVING COUNT(DISTINCT selection) = 2
        )
        SELECT
            COUNT(*) AS n_total,
            COUNT(*) FILTER (WHERE (1.0/over_odds + 1.0/under_odds) BETWEEN 1.02 AND 1.10) AS n_clean,
            COUNT(*) FILTER (WHERE (1.0/over_odds + 1.0/under_odds) > 1.10) AS n_overround_high,
            COUNT(*) FILTER (WHERE (1.0/over_odds + 1.0/under_odds) < 1.02) AS n_overround_low
        FROM paired
        """,
        (),
    )
    if overround_results:
        r = overround_results[0]
        n_total = int(r["n_total"]) or 1
        n_clean = int(r["n_clean"])
        n_high = int(r["n_overround_high"])
        n_low = int(r["n_overround_low"])
        console.print(
            f"\n[bold]Pinnacle OU 2.5 overround (last 30d):[/bold]\n"
            f"  Total paired: {n_total:,}\n"
            f"  Clean (1.02-1.10): {n_clean:,} ({n_clean/n_total*100:.1f}%)\n"
            f"  Overround > 1.10: {n_high:,} ({n_high/n_total*100:.1f}%)\n"
            f"  Overround < 1.02: {n_low:,} ({n_low/n_total*100:.1f}%) [arbitrage-shape, very suspect]"
        )

    bad = execute_query(
        """
        WITH latest AS (
            SELECT DISTINCT ON (match_id, selection) match_id, selection, odds
            FROM odds_snapshots
            WHERE bookmaker = 'Pinnacle' AND market = 'over_under_25'
              AND is_live = false
              AND timestamp >= NOW() - INTERVAL '30 days'
            ORDER BY match_id, selection, timestamp DESC
        ),
        paired AS (
            SELECT match_id,
                   MAX(CASE WHEN selection = 'over'  THEN odds END) AS over_odds,
                   MAX(CASE WHEN selection = 'under' THEN odds END) AS under_odds
            FROM latest GROUP BY match_id
            HAVING COUNT(DISTINCT selection) = 2
        )
        SELECT p.match_id, p.over_odds, p.under_odds,
               (1.0/p.over_odds + 1.0/p.under_odds) AS overround
        FROM paired p
        WHERE (1.0/p.over_odds + 1.0/p.under_odds) NOT BETWEEN 1.02 AND 1.10
        ORDER BY (1.0/p.over_odds + 1.0/p.under_odds) DESC LIMIT 15
        """,
        (),
    )
    if bad:
        console.print("\n[yellow]Sample of suspicious Pinnacle OU 2.5 pairs:[/yellow]")
        match_ids = [str(r["match_id"]) for r in bad]
        meta = execute_query(
            "SELECT m.id, ht.name AS home, at.name AS away, l.name AS league "
            "FROM matches m "
            "LEFT JOIN teams ht ON ht.id = m.home_team_id "
            "LEFT JOIN teams at ON at.id = m.away_team_id "
            "LEFT JOIN leagues l ON l.id = m.league_id "
            "WHERE m.id = ANY(%s::uuid[])",
            (match_ids,),
        )
        meta_by_id = {row["id"]: row for row in meta}
        for r in bad:
            m = meta_by_id.get(r["match_id"], {})
            console.print(
                f"  [{(m.get('league') or '?')[:20]:20s}] {m.get('home', '?')} vs {m.get('away', '?')}  "
                f"over={float(r['over_odds']):.3f} under={float(r['under_odds']):.3f}  "
                f"overround={float(r['overround']):.4f}"
            )


def a3_pinnacle_btts() -> None:
    section("A.3 — Pinnacle BTTS overround (last 30d)")
    overround_results = execute_query(
        """
        WITH latest AS (
            SELECT DISTINCT ON (match_id, selection) match_id, selection, odds
            FROM odds_snapshots
            WHERE bookmaker = 'Pinnacle' AND market = 'btts'
              AND is_live = false
              AND timestamp >= NOW() - INTERVAL '30 days'
            ORDER BY match_id, selection, timestamp DESC
        ),
        paired AS (
            SELECT match_id,
                   MAX(CASE WHEN selection = 'yes' THEN odds END) AS yes_odds,
                   MAX(CASE WHEN selection = 'no'  THEN odds END) AS no_odds
            FROM latest GROUP BY match_id
            HAVING COUNT(DISTINCT selection) = 2
        )
        SELECT
            COUNT(*) AS n_total,
            COUNT(*) FILTER (WHERE (1.0/yes_odds + 1.0/no_odds) BETWEEN 1.02 AND 1.10) AS n_clean,
            COUNT(*) FILTER (WHERE (1.0/yes_odds + 1.0/no_odds) > 1.10) AS n_high,
            COUNT(*) FILTER (WHERE (1.0/yes_odds + 1.0/no_odds) < 1.02) AS n_low
        FROM paired
        """,
        (),
    )
    if overround_results:
        r = overround_results[0]
        n_total = int(r["n_total"]) or 1
        n_clean = int(r["n_clean"])
        n_high = int(r["n_high"])
        n_low = int(r["n_low"])
        console.print(
            f"  Total paired: {n_total:,}\n"
            f"  Clean (1.02-1.10): {n_clean:,} ({n_clean/n_total*100:.1f}%)\n"
            f"  Overround > 1.10: {n_high:,} ({n_high/n_total*100:.1f}%)\n"
            f"  Overround < 1.02: {n_low:,} ({n_low/n_total*100:.1f}%)"
        )


def a4_mislabel_sweep() -> None:
    section("A.4 — Bookmaker label hygiene (1X2, OU 2.5, BTTS — last 30d)")
    rows = execute_query(
        """
        SELECT bookmaker, market, COUNT(*) AS n
        FROM odds_snapshots
        WHERE market IN ('1x2', 'over_under_25', 'btts')
          AND timestamp >= NOW() - INTERVAL '30 days'
        GROUP BY bookmaker, market
        HAVING COUNT(*) > 100
        ORDER BY market, n DESC
        """,
        (),
    )
    cur_market = None
    n_pinnacle_like = 0
    for r in rows:
        if r["market"] != cur_market:
            cur_market = r["market"]
            console.print(f"\n[bold]{cur_market}[/bold]")
        bk = r["bookmaker"]
        flag = ""
        if bk and "pin" in (bk or "").lower():
            flag = "  [red]⚠ pinnacle-like label[/red]" if bk != "Pinnacle" else "  [green]canonical[/green]"
            n_pinnacle_like += 1
        console.print(f"  {(bk or 'NULL'):30s} {int(r['n']):>10,}{flag}")
    if n_pinnacle_like == 0:
        console.print("\n[red]No 'Pinnacle' rows seen at all.[/red]")


def a5_coverage() -> None:
    section("A.5 — Pinnacle pre-KO coverage on finished matches (last 90d)")
    # Split into 4 separate fast queries (the combined CTE timed out at the
    # JOIN through both tables). Each individual query is index-friendly:
    # finished count uses (status, date) on matches; the per-market counts
    # use (bookmaker, market, timestamp) on odds_snapshots.

    finished = execute_query(
        "SELECT COUNT(*) AS n FROM matches "
        "WHERE status = 'finished' AND date >= NOW() - INTERVAL '90 days'",
        (),
    )
    n_fin = int(finished[0]["n"]) if finished else 1

    def pin_count(market: str) -> int:
        rows = execute_query(
            """
            SELECT COUNT(DISTINCT os.match_id) AS n
            FROM odds_snapshots os
            JOIN matches m ON m.id = os.match_id
            WHERE os.bookmaker = 'Pinnacle' AND os.market = %s AND os.is_live = false
              AND os.timestamp >= NOW() - INTERVAL '120 days'
              AND m.status = 'finished'
              AND m.date >= NOW() - INTERVAL '90 days'
            """,
            (market,),
        )
        return int(rows[0]["n"]) if rows else 0

    n_1x2 = pin_count("1x2")
    n_ou25 = pin_count("over_under_25")
    n_btts = pin_count("btts")

    n_fin = max(n_fin, 1)
    t = Table(show_header=True, title="Pinnacle pre-KO coverage on finished matches (last 90d)")
    t.add_column("Market")
    t.add_column("Matches with ≥1 row", justify="right")
    t.add_column("Coverage", justify="right")
    t.add_row("Total finished (last 90d)", f"{n_fin:,}", "100.0%")
    t.add_row("1X2", f"{n_1x2:,}", f"{n_1x2/n_fin*100:.1f}%")
    t.add_row("OU 2.5", f"{n_ou25:,}", f"{n_ou25/n_fin*100:.1f}%")
    t.add_row("BTTS", f"{n_btts:,}", f"{n_btts/n_fin*100:.1f}%")
    console.print(t)


def a2b_ou25_timestamp_skew() -> None:
    """For pairs with overround > 1.10, check whether over and under come
    from snapshots taken far apart in time. If yes, the issue is snapshot
    skew (fixable in feature SQL) — not a mislabel."""
    section("A.2b — OU 2.5 timestamp skew on suspicious pairs")
    rows = execute_query(
        """
        WITH latest AS (
            SELECT DISTINCT ON (match_id, selection)
                   match_id, selection, odds, timestamp
            FROM odds_snapshots
            WHERE bookmaker = 'Pinnacle' AND market = 'over_under_25'
              AND is_live = false
              AND timestamp >= NOW() - INTERVAL '30 days'
            ORDER BY match_id, selection, timestamp DESC
        ),
        paired AS (
            SELECT match_id,
                   MAX(CASE WHEN selection = 'over'  THEN odds END) AS over_odds,
                   MAX(CASE WHEN selection = 'under' THEN odds END) AS under_odds,
                   MAX(CASE WHEN selection = 'over'  THEN timestamp END) AS over_ts,
                   MAX(CASE WHEN selection = 'under' THEN timestamp END) AS under_ts
            FROM latest GROUP BY match_id
            HAVING COUNT(DISTINCT selection) = 2
        )
        SELECT
            COUNT(*) AS n_total,
            COUNT(*) FILTER (
                WHERE (1.0/over_odds + 1.0/under_odds) > 1.10
                  AND ABS(EXTRACT(EPOCH FROM (over_ts - under_ts))) < 60
            ) AS n_high_same_ts,
            COUNT(*) FILTER (
                WHERE (1.0/over_odds + 1.0/under_odds) > 1.10
                  AND ABS(EXTRACT(EPOCH FROM (over_ts - under_ts))) >= 60
            ) AS n_high_skewed
        FROM paired
        """,
        (),
    )
    if rows:
        r = rows[0]
        n_high_same = int(r["n_high_same_ts"])
        n_high_skew = int(r["n_high_skewed"])
        console.print(
            f"Of the 66 suspicious-overround OU 2.5 pairs:\n"
            f"  Same timestamp (within 60s) — likely real mislabel: {n_high_same}\n"
            f"  Different timestamps — snapshot skew (probably benign):  {n_high_skew}"
        )


def main() -> None:
    a1_pinnacle_1x2_outliers()
    a2_pinnacle_ou25()
    a2b_ou25_timestamp_skew()
    a3_pinnacle_btts()
    a4_mislabel_sweep()
    a5_coverage()


if __name__ == "__main__":
    main()
