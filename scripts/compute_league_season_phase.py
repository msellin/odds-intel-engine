"""LEAGUE-SEASON-PHASE — per-match early/mid/late season tag.

For each (league, season): season_start = MIN(date), season_end = MAX(date).
For each match: season_progress = (match.date - season_start) / (season_end - season_start).
Phase bucket:
  early  (0.00 — 0.33)
  mid    (0.33 — 0.67)
  late   (0.67 — 1.00)

Stored in match_signals:
  signal_name = 'season_progress'  → float [0..1]
  signal_name = 'season_phase'     → 'early' / 'mid' / 'late' (in signal_text, but
                                      we'll store the float instead and let
                                      consumers bucket)

Backtest: do late-season matches have different over-2.5 rate, draw rate,
or home-win rate than early-season matches in the same leagues? If yes,
the model can benefit from learning the seasonal pattern.

Run:
  python3 scripts/compute_league_season_phase.py            # backtest only
  python3 scripts/compute_league_season_phase.py --write    # backtest + persist
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


def _compute_phases() -> dict[str, float]:
    """Returns {match_id: season_progress} for all settled + upcoming matches.

    Season window = min/max match date per (league, season). For leagues with
    < 50 matches in the season, skip (not enough to estimate the window).
    """
    rows = execute_query("""
        SELECT m.id AS match_id, m.date, m.league_id, m.season,
               lr.season_start, lr.season_end
        FROM matches m
        JOIN (
            SELECT league_id, season,
                   MIN(date) AS season_start,
                   MAX(date) AS season_end,
                   COUNT(*) AS n_total
            FROM matches
            GROUP BY league_id, season
            HAVING COUNT(*) >= 50
        ) lr ON lr.league_id = m.league_id AND lr.season = m.season
        WHERE m.date IS NOT NULL
    """)
    out = {}
    for r in rows:
        span = (r["season_end"] - r["season_start"]).total_seconds()
        if span <= 0:
            continue
        prog = (r["date"] - r["season_start"]).total_seconds() / span
        prog = max(0.0, min(1.0, prog))
        out[str(r["match_id"])] = prog
    return out


def backtest():
    console.print("[bold]LEAGUE-SEASON-PHASE backtest — does season phase shift outcomes?[/bold]")
    phases = _compute_phases()
    if not phases:
        console.print("[yellow]No matches with computable season phase.[/yellow]")
        return

    # Join to settled match outcomes
    mids = list(phases.keys())
    rows = execute_query("""
        SELECT id AS match_id, result, score_home, score_away
        FROM matches
        WHERE id = ANY(%s::uuid[])
          AND score_home IS NOT NULL
          AND date >= '2026-03-01'
    """, (mids,))
    if not rows:
        console.print("[yellow]No settled matches to validate.[/yellow]")
        return

    # Bucket by phase, measure outcome rates per bucket
    buckets = {"early": [], "mid": [], "late": []}
    for r in rows:
        prog = phases.get(str(r["match_id"]))
        if prog is None:
            continue
        if prog < 0.33:
            phase = "early"
        elif prog < 0.67:
            phase = "mid"
        else:
            phase = "late"
        total = r["score_home"] + r["score_away"]
        buckets[phase].append({
            "result": r["result"],
            "over25": total > 2,
            "btts": (r["score_home"] > 0 and r["score_away"] > 0),
        })

    t = Table(title=f"Season phase vs outcomes (n={sum(len(v) for v in buckets.values()):,} matches since 2026-03-01)")
    for c in ("phase", "n", "home %", "draw %", "away %", "over2.5 %", "btts %"):
        t.add_column(c)
    early_metrics = None
    for phase in ("early", "mid", "late"):
        bs = buckets[phase]
        if not bs:
            t.add_row(phase, "0", "-", "-", "-", "-", "-")
            continue
        n = len(bs)
        h = sum(1 for x in bs if x["result"] == "home") / n
        d = sum(1 for x in bs if x["result"] == "draw") / n
        a = sum(1 for x in bs if x["result"] == "away") / n
        o = sum(1 for x in bs if x["over25"]) / n
        b = sum(1 for x in bs if x["btts"]) / n
        if phase == "early":
            early_metrics = (h, d, a, o, b)
            row = [phase, str(n), f"{h*100:.1f}%", f"{d*100:.1f}%", f"{a*100:.1f}%", f"{o*100:.1f}%", f"{b*100:.1f}%"]
        else:
            eh, ed, ea, eo, eb = early_metrics
            row = [
                phase, str(n),
                f"{h*100:.1f}% ({(h-eh)*100:+.1f})",
                f"{d*100:.1f}% ({(d-ed)*100:+.1f})",
                f"{a*100:.1f}% ({(a-ea)*100:+.1f})",
                f"{o*100:.1f}% ({(o-eo)*100:+.1f})",
                f"{b*100:.1f}% ({(b-eb)*100:+.1f})",
            ]
        t.add_row(*row)
    console.print(t)

    # Verdict: any market shifts by >2pp between early and late?
    if buckets["early"] and buckets["late"]:
        e = buckets["early"]
        l = buckets["late"]
        shifts = []
        for market, key in [("home", lambda x: x["result"] == "home"),
                            ("draw", lambda x: x["result"] == "draw"),
                            ("away", lambda x: x["result"] == "away"),
                            ("over2.5", lambda x: x["over25"]),
                            ("btts", lambda x: x["btts"])]:
            er = sum(1 for x in e if key(x)) / len(e)
            lr = sum(1 for x in l if key(x)) / len(l)
            shifts.append((market, (lr - er) * 100))
        max_shift = max(shifts, key=lambda x: abs(x[1]))
        console.print(f"\n[bold]Largest late-vs-early shift: {max_shift[0]} {max_shift[1]:+.2f}pp[/bold]")
        if abs(max_shift[1]) >= 3.0:
            console.print(f"[green]✓ Signal adds value — {max_shift[0]} rate shifts materially by season phase[/green]")
        elif abs(max_shift[1]) >= 1.5:
            console.print("[yellow]Marginal — keep with low weight[/yellow]")
        else:
            console.print("[red]No material shift — season phase doesn't help in this sample[/red]")


def write_today_signals():
    phases = _compute_phases()
    upcoming = execute_query("""
        SELECT id FROM matches
        WHERE date >= NOW() - INTERVAL '1 day'
          AND date <= NOW() + INTERVAL '7 days'
    """)
    upcoming_ids = {str(r["id"]) for r in upcoming}
    rows_to_write = [(mid, prog) for mid, prog in phases.items() if mid in upcoming_ids]
    if not rows_to_write:
        return
    tuples = [(mid, "season_progress", float(p), "league", "derived") for mid, p in rows_to_write]
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
    console.print(f"\n[green]✓ Inserted {len(tuples):,} season_progress rows[/green]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    backtest()
    if args.write:
        write_today_signals()
    else:
        console.print("\n[yellow]Pass --write to also persist signals[/yellow]")


if __name__ == "__main__":
    main()
