"""Audit why bot_ou15_defensive and bot_ah_away_dog stopped firing.

Hypothesis:
  - bot_ou15_defensive: OU-PIN-REQUIRED (commit 9d4166e, 2026-05-10) drops OU
    rows at placement when Pinnacle has no matching (match, market, selection).
  - bot_ah_away_dog:    AH-NO-QUARTER (commit 7aa9015, 2026-05-12) skips quarter
    lines + PIN-VETO-EXT (commit 4be859b, 2026-05-12) adds best-book IP anchor
    veto.

Five sections:
  1. Daily bet count for both bots since 2026-05-01 (confirm the silence).
  2. shadow_bets for both bots since silence (any candidates surviving filters?).
  3. Pinnacle OU 1.5 coverage on recent matches (bot_ou15_defensive hunting ground).
  4. AH quarter-vs-full/half snapshot counts on recent matches (bot_ah_away_dog impact).
  5. Per-league cross of bot_ou15_defensive winners (pre-May-8) against current
     Pinnacle OU 1.5 coverage — does the bot's home turf still get priced?
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.supabase_client import execute_query
from rich.console import Console
from rich.table import Table

console = Console()


def section(title: str) -> None:
    console.print(f"\n[bold cyan]{'═' * 78}[/bold cyan]")
    console.print(f"[bold cyan]{title}[/bold cyan]")
    console.print(f"[bold cyan]{'═' * 78}[/bold cyan]")


def s1_daily_bet_counts() -> None:
    section("1) Daily simulated_bets count per bot since 2026-05-01")
    rows = execute_query(
        """
        SELECT b.name AS bot,
               DATE(sb.pick_time) AS day,
               COUNT(*) AS n_bets
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE b.name IN ('bot_ou15_defensive', 'bot_ah_away_dog')
          AND sb.pick_time >= '2026-05-01'
        GROUP BY b.name, DATE(sb.pick_time)
        ORDER BY b.name, day
        """,
        (),
    )
    if not rows:
        console.print("[yellow]No simulated_bets rows found for these bots since 2026-05-01.[/yellow]")
        return
    t = Table(show_header=True)
    t.add_column("Bot")
    t.add_column("Day")
    t.add_column("Bets", justify="right")
    for r in rows:
        t.add_row(r["bot"], str(r["day"]), str(r["n_bets"]))
    console.print(t)


def s2_shadow_bets() -> None:
    section("2) shadow_bets counts per bot per cohort since 2026-05-08")
    rows = execute_query(
        """
        SELECT b.name AS bot,
               DATE(sh.pick_time) AS day,
               sh.shadow_cohort,
               COUNT(*) AS n,
               ROUND(AVG(sh.edge_percent)::numeric, 2) AS avg_edge
        FROM shadow_bets sh
        JOIN bots b ON b.id = sh.bot_id
        WHERE b.name IN ('bot_ou15_defensive', 'bot_ah_away_dog')
          AND sh.pick_time >= '2026-05-08'
        GROUP BY b.name, DATE(sh.pick_time), sh.shadow_cohort
        ORDER BY b.name, day, sh.shadow_cohort
        """,
        (),
    )
    if not rows:
        console.print(
            "[yellow]No shadow_bets rows for these bots since 2026-05-08.[/yellow]\n"
            "  → Candidates aren't surviving the filters at any cohort window.\n"
            "    Shadow mode goes through the same code path as the real run,\n"
            "    so OU-PIN-REQUIRED / AH-NO-QUARTER / PIN-VETO-EXT block these too."
        )
        return
    t = Table(show_header=True)
    t.add_column("Bot")
    t.add_column("Day")
    t.add_column("Cohort")
    t.add_column("Shadow bets", justify="right")
    t.add_column("Avg edge %", justify="right")
    for r in rows:
        t.add_row(r["bot"], str(r["day"]), r["shadow_cohort"], str(r["n"]), str(r["avg_edge"]))
    console.print(t)


def s3_ou15_coverage_by_book() -> None:
    section("3) OU 1.5 coverage by book — Pinnacle vs Coolbet vs either (matches 2026-05-08+)")
    # Compare Pinnacle (current OU-PIN-REQUIRED anchor) vs Coolbet (real placement
    # venue) vs either. If Coolbet covers leagues Pinnacle doesn't, switching to
    # COOLBET-OR-PIN-REQUIRED resurrects the bot's hunting ground.
    rows = execute_query(
        """
        WITH matches_in_window AS (
            SELECT m.id, m.date
            FROM matches m
            WHERE m.date >= '2026-05-08'
              AND m.date < CURRENT_DATE + INTERVAL '1 day'
        )
        SELECT DATE(m.date) AS day,
               COUNT(DISTINCT m.id) AS total_matches,
               COUNT(DISTINCT m.id) FILTER (WHERE os.match_id IS NOT NULL) AS with_any,
               COUNT(DISTINCT m.id) FILTER (WHERE os.bookmaker = 'Pinnacle') AS with_pin,
               COUNT(DISTINCT m.id) FILTER (WHERE os.bookmaker = 'Coolbet')  AS with_cb,
               COUNT(DISTINCT m.id) FILTER (
                 WHERE os.bookmaker IN ('Pinnacle', 'Coolbet')
               ) AS with_either
        FROM matches_in_window m
        LEFT JOIN odds_snapshots os
          ON os.match_id = m.id AND os.market = 'over_under_15'
        GROUP BY DATE(m.date)
        ORDER BY day
        """,
        (),
    )
    if not rows:
        console.print("[red]No matches found in window.[/red]")
        return
    t = Table(show_header=True)
    t.add_column("Day")
    t.add_column("Matches", justify="right")
    t.add_column("Any OU 1.5", justify="right")
    t.add_column("Pinnacle", justify="right")
    t.add_column("Coolbet", justify="right")
    t.add_column("Either", justify="right")
    t.add_column("Coolbet uplift", justify="right")
    for r in rows:
        uplift = (r["with_either"] or 0) - (r["with_pin"] or 0)
        t.add_row(
            str(r["day"]),
            str(r["total_matches"]),
            str(r["with_any"]),
            str(r["with_pin"]),
            str(r["with_cb"]),
            str(r["with_either"]),
            f"+{uplift}" if uplift else "0",
        )
    console.print(t)
    console.print(
        "\n[dim]Coolbet uplift = matches Coolbet covers but Pinnacle doesn't.[/dim]\n"
        "[dim]These are the matches a COOLBET-OR-PIN-REQUIRED rule would re-open.[/dim]"
    )


def s4_ah_quarter_vs_full() -> None:
    section("4) AH snapshot counts: quarter vs full/half (last 12 days)")
    # AH lines are stored in odds_snapshots with market='asian_handicap'.
    # selection encodes the handicap; we need handicap_line from the row.
    # Migration 75e4e09 (AH-HANDICAP-LINE) added handicap_line column.
    rows = execute_query(
        """
        SELECT DATE(m.date) AS day,
               COUNT(*) FILTER (
                 WHERE ABS(MOD((os.handicap_line * 100)::int, 50)) = 25
               ) AS quarter_rows,
               COUNT(*) FILTER (
                 WHERE ABS(MOD((os.handicap_line * 100)::int, 50)) = 0
               ) AS full_half_rows,
               COUNT(*) AS total_rows
        FROM odds_snapshots os
        JOIN matches m ON m.id = os.match_id
        WHERE os.market = 'asian_handicap'
          AND m.date >= '2026-05-08'
          AND m.date < CURRENT_DATE + INTERVAL '1 day'
          AND os.handicap_line IS NOT NULL
        GROUP BY DATE(m.date)
        ORDER BY day
        """,
        (),
    )
    if not rows:
        console.print("[yellow]No AH snapshots found (or handicap_line column empty).[/yellow]")
        return
    t = Table(show_header=True)
    t.add_column("Day")
    t.add_column("Total AH rows", justify="right")
    t.add_column("Quarter (skipped)", justify="right")
    t.add_column("Full/Half (kept)", justify="right")
    t.add_column("Quarter % skipped", justify="right")
    for r in rows:
        pct = (
            100.0 * r["quarter_rows"] / r["total_rows"]
            if r["total_rows"]
            else 0
        )
        t.add_row(
            str(r["day"]),
            str(r["total_rows"]),
            str(r["quarter_rows"]),
            str(r["full_half_rows"]),
            f"{pct:.1f}%",
        )
    console.print(t)


def s5_bot_ou15_league_cross() -> None:
    section("5) bot_ou15_defensive: pre-silence winning leagues × Pinnacle vs Coolbet OU 1.5 coverage")
    rows = execute_query(
        """
        WITH bot_leagues AS (
            SELECT l.id AS league_id,
                   l.name AS league,
                   COUNT(*) AS pre_bets
            FROM simulated_bets sb
            JOIN bots b ON b.id = sb.bot_id
            JOIN matches m ON m.id = sb.match_id
            JOIN leagues l ON l.id = m.league_id
            WHERE b.name = 'bot_ou15_defensive'
              AND sb.pick_time < '2026-05-10'
              AND sb.pick_time >= '2026-04-01'
            GROUP BY l.id, l.name
        ),
        recent AS (
            SELECT m.league_id,
                   COUNT(DISTINCT m.id) AS matches_in_window,
                   COUNT(DISTINCT m.id) FILTER (
                     WHERE EXISTS (
                       SELECT 1 FROM odds_snapshots os
                       WHERE os.match_id = m.id
                         AND os.bookmaker = 'Pinnacle'
                         AND os.market = 'over_under_15'
                     )
                   ) AS matches_with_pin,
                   COUNT(DISTINCT m.id) FILTER (
                     WHERE EXISTS (
                       SELECT 1 FROM odds_snapshots os
                       WHERE os.match_id = m.id
                         AND os.bookmaker = 'Coolbet'
                         AND os.market = 'over_under_15'
                     )
                   ) AS matches_with_cb
            FROM matches m
            WHERE m.date >= '2026-05-08'
              AND m.date < CURRENT_DATE + INTERVAL '1 day'
            GROUP BY m.league_id
        )
        SELECT bl.league,
               bl.pre_bets,
               COALESCE(rp.matches_in_window, 0) AS recent_matches,
               COALESCE(rp.matches_with_pin, 0) AS pin_covered,
               COALESCE(rp.matches_with_cb, 0) AS cb_covered
        FROM bot_leagues bl
        LEFT JOIN recent rp ON rp.league_id = bl.league_id
        ORDER BY bl.pre_bets DESC
        LIMIT 30
        """,
        (),
    )
    if not rows:
        console.print("[yellow]No pre-silence bets found for bot_ou15_defensive.[/yellow]")
        return
    t = Table(show_header=True)
    t.add_column("League")
    t.add_column("Pre-bets", justify="right")
    t.add_column("Recent matches", justify="right")
    t.add_column("Pinnacle OU 1.5", justify="right")
    t.add_column("Coolbet OU 1.5", justify="right")
    t.add_column("Verdict", justify="left")
    for r in rows:
        pin = r["pin_covered"] or 0
        cb = r["cb_covered"] or 0
        recent = r["recent_matches"] or 0
        if recent == 0:
            verdict = "off-season"
        elif pin > 0:
            verdict = "OK today"
        elif cb > 0:
            verdict = "COOLBET-ONLY (gated by PIN-REQUIRED)"
        else:
            verdict = "no sharp coverage"
        t.add_row(
            r["league"] or "—",
            str(r["pre_bets"]),
            str(recent),
            str(pin),
            str(cb),
            verdict,
        )
    console.print(t)


def main() -> None:
    s1_daily_bet_counts()
    s2_shadow_bets()
    s3_ou15_coverage_by_book()
    s4_ah_quarter_vs_full()
    s5_bot_ou15_league_cross()


if __name__ == "__main__":
    main()
