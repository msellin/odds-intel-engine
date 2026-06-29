"""LINE-VELOCITY — rate-of-change of Pinnacle home implied prob in T-12h..T-2h.

Definition: per-match, fit a linear regression of (implied_prob ~ minutes_to_kickoff)
on snapshots between T-12h and T-2h before KO. The slope is `line_velocity` —
how fast Pinnacle is moving the home line per minute as KO approaches.

Positive velocity = Pinnacle is making home shorter (sharp money on home).
Negative velocity = Pinnacle is making home longer (sharp money against home).
Magnitude matters: bigger absolute slope = stronger sharp flow.

Two outputs:
  1. match_signals row per match with sufficient snapshots: signal_name='line_velocity'
  2. Backtest: do matches with HIGH absolute velocity show higher CLV-beat rate
     than matches with low velocity?

Run:
  python3 scripts/compute_line_velocity.py            # backtest
  python3 scripts/compute_line_velocity.py --write    # backtest + persist
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

from workers.api_clients.db import execute_query, get_conn

console = Console()


def _linear_slope(xs: list[float], ys: list[float]) -> float:
    """Simple linear regression slope (no library dep)."""
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


def _compute_velocities(since: str = "2026-04-01") -> dict[str, float]:
    """Returns {match_id: line_velocity} for matches with ≥3 Pinnacle home
    snapshots in the T-12h..T-2h window before KO.
    Queries via match_id batches to use the match_id index instead of full scan.
    """
    import psycopg2.extras

    # Step 1: get match IDs from matches table (fast — no odds_snapshots scan)
    match_rows = execute_query("""
        SELECT id FROM matches
        WHERE date >= %s AND date < NOW()
        ORDER BY id
    """, (since,))
    match_ids = [str(r["id"]) for r in match_rows]
    if not match_ids:
        return {}

    # Step 2: fetch Pinnacle snapshots per batch using match_id index
    rows: list[dict] = []
    BATCH = 50
    for i in range(0, len(match_ids), BATCH):
        batch = match_ids[i:i + BATCH]
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SET LOCAL statement_timeout = '5min'")
                cur.execute("""
                    SELECT os.match_id, os.timestamp, os.odds, os.minutes_to_kickoff
                    FROM odds_snapshots os
                    WHERE os.match_id = ANY(%s::uuid[])
                      AND os.market = '1x2' AND os.selection = 'home'
                      AND os.bookmaker = 'Pinnacle'
                      AND os.is_live = false
                      AND os.minutes_to_kickoff BETWEEN 120 AND 720
                      AND os.odds > 1.0
                    ORDER BY os.match_id, os.timestamp ASC
                """, (batch,))
                rows.extend(dict(r) for r in cur.fetchall())

    by_match: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        implied = 1.0 / float(r["odds"])
        # x = minutes_to_kickoff (LARGER = earlier in time, smaller = closer to KO)
        # Negate so positive slope = implied prob going UP as KO approaches
        by_match[str(r["match_id"])].append((-r["minutes_to_kickoff"], implied))

    velocities: dict[str, float] = {}
    for mid, points in by_match.items():
        if len(points) < 3:
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        slope = _linear_slope(xs, ys)
        velocities[mid] = slope
    return velocities


def backtest():
    console.print("[bold]LINE-VELOCITY backtest — does sharp-flow magnitude predict CLV-beat?[/bold]")
    velocities = _compute_velocities("2026-04-01")
    if not velocities:
        console.print("[yellow]No matches with sufficient snapshots.[/yellow]")
        return

    # Join to settled bets — does |velocity| correlate with pseudo_clv > 0?
    mids = list(velocities.keys())
    rows = execute_query("""
        SELECT sb.match_id, sb.selection,
               m.pseudo_clv_home, m.pseudo_clv_draw, m.pseudo_clv_away,
               sb.result
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        WHERE sb.match_id = ANY(%s::uuid[])
          AND sb.result IN ('won', 'lost', 'void')
          AND sb.market = '1x2'
    """, (mids,))
    if not rows:
        console.print("[yellow]No settled 1x2 bets to validate against.[/yellow]")
        return

    # Per bet: pick the pseudo_clv matching the selection
    sel_map = {
        "home": "pseudo_clv_home", "Home": "pseudo_clv_home",
        "draw": "pseudo_clv_draw", "Draw": "pseudo_clv_draw",
        "away": "pseudo_clv_away", "Away": "pseudo_clv_away",
    }
    enriched = []
    for r in rows:
        clv_col = sel_map.get(r["selection"])
        if not clv_col:
            continue
        clv = r[clv_col]
        if clv is None:
            continue
        v = velocities.get(str(r["match_id"]))
        if v is None:
            continue
        enriched.append({"abs_v": abs(v), "clv_beat": float(clv) > 0})
    if not enriched:
        console.print("[yellow]No (bet, velocity) pairs after enrichment.[/yellow]")
        return

    # Bin by absolute velocity quartile, measure CLV-beat rate per bin
    enriched.sort(key=lambda x: x["abs_v"])
    n = len(enriched)
    q = n // 4
    quartiles = [enriched[i*q:(i+1)*q] for i in range(4)]
    quartiles[-1] = enriched[3*q:]

    t = Table(title=f"|line velocity| quartile vs CLV-beat rate (n={n:,} bets)")
    for c in ("quartile", "|v| range", "n", "CLV-beat %", "lift vs Q1"):
        t.add_column(c)
    q1_beat = None
    for i, q_rows in enumerate(quartiles, 1):
        if not q_rows:
            continue
        vs = [x["abs_v"] for x in q_rows]
        vmin, vmax = min(vs), max(vs)
        beats = sum(1 for x in q_rows if x["clv_beat"])
        rate = beats / len(q_rows)
        if i == 1:
            q1_beat = rate
            lift = "—"
        else:
            lift = f"{(rate - q1_beat) * 100:+.1f}pp"
        t.add_row(f"Q{i}", f"{vmin:.6f}–{vmax:.6f}", str(len(q_rows)), f"{rate*100:.1f}%", lift)
    console.print(t)

    if quartiles[0] and quartiles[3]:
        q1_rate = sum(1 for x in quartiles[0] if x["clv_beat"]) / len(quartiles[0])
        q4_rate = sum(1 for x in quartiles[3] if x["clv_beat"]) / len(quartiles[3])
        gap = (q4_rate - q1_rate) * 100
        console.print(f"\n[bold]Q4 vs Q1 CLV-beat gap: {gap:+.2f}pp[/bold]")
        if abs(gap) >= 5.0:
            direction = "lower" if gap < 0 else "higher"
            console.print(f"[green]✓ Signal predictive — high-velocity matches have {direction} CLV-beat[/green]")
            if gap < 0:
                console.print("[yellow]Direction: REVERSE — Pinnacle moves line AFTER our bet → we end up "
                              "on the wrong side at close. Meta-model should DOWN-weight high-|v| bets.[/yellow]")
            else:
                console.print("[yellow]Direction: FORWARD — high velocity tracks edge that beats closing.[/yellow]")
        elif abs(gap) >= 2.0:
            console.print("[yellow]Marginal — keep with low weight[/yellow]")
        else:
            console.print("[red]No signal — line velocity isn't predictive in this sample[/red]")


def write_today_signals():
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=14)).isoformat()
    velocities = _compute_velocities(since)
    if not velocities:
        return
    console.print(f"\nWriting {len(velocities):,} line_velocity rows...")
    tuples = [(mid, "line_velocity", v, "market", "derived") for mid, v in velocities.items()]
    with get_conn() as conn:
        with conn.cursor() as cur:
            from psycopg2.extras import execute_values
            execute_values(
                cur,
                """INSERT INTO match_signals
                   (match_id, signal_name, signal_value, signal_group, data_source)
                   VALUES %s""",
                tuples,
            )
        conn.commit()
    console.print(f"[green]✓ Inserted {len(tuples):,} line_velocity rows[/green]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if args.write:
        # Scheduled mode: skip backtest (informational only), just write signals
        write_today_signals()
    else:
        backtest()
        console.print("\n[yellow]Pass --write to also persist signals[/yellow]")


if __name__ == "__main__":
    main()
