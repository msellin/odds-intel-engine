"""INJURY-SEVERITY — bucket match_injuries by severity, compute team weighted score.

Replaces raw injury count with severity-weighted score per team per match.

Severity buckets (keyword-driven on AF reason/type text):
  SEVERE (weight 3.0)   — ACL, Achilles, Cruciate, Tendon, Surgery, Fracture,
                          Broken, Operation, Meniscus
  MODERATE (weight 1.5) — Knee, Hamstring, Groin, Thigh, Ankle, Calf, Back,
                          Hip, Muscle, Shoulder, Foot, Leg
  MINOR (weight 0.5)    — Knock, Illness, Virus, Cold, Fitness, Suspended,
                          Yellow Cards, Red Card, Bruise, Cramp, Dental
  UNKNOWN (weight 1.0)  — anything else (Injury / Inactive / blank)

Per-match severity score:
  severity_score = sum(weight_of_each_listed_player)

Stored in match_signals as:
  signal_name = 'injury_severity_score_home' | 'injury_severity_score_away'

Run:
  python3 scripts/compute_injury_severity.py            # dry run
  python3 scripts/compute_injury_severity.py --write    # persist
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

SEVERE_KEYWORDS = ("acl", "achilles", "cruciate", "tendon", "surgery", "fracture",
                   "broken", "operation", "meniscus")
MODERATE_KEYWORDS = ("knee", "hamstring", "groin", "thigh", "ankle", "calf", "back",
                     "hip", "muscle", "shoulder", "foot", "leg")
MINOR_KEYWORDS = ("knock", "illness", "virus", "cold", "fitness", "suspend",
                  "yellow card", "red card", "bruise", "cramp", "dental")

WEIGHTS = {"SEVERE": 3.0, "MODERATE": 1.5, "MINOR": 0.5, "UNKNOWN": 1.0}


def classify(reason: str | None) -> str:
    if not reason:
        return "UNKNOWN"
    r = reason.lower()
    for k in SEVERE_KEYWORDS:
        if k in r:
            return "SEVERE"
    for k in MODERATE_KEYWORDS:
        if k in r:
            return "MODERATE"
    for k in MINOR_KEYWORDS:
        if k in r:
            return "MINOR"
    return "UNKNOWN"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    console.print("[bold]INJURY-SEVERITY — bucketing match_injuries by severity[/bold]")

    rows = execute_query("""
        SELECT match_id, team_side, reason, player_type, status
        FROM match_injuries
        WHERE team_side IN ('home','away')
    """)
    console.print(f"  Loaded {len(rows):,} match_injuries rows")

    # Aggregate: (match_id, team_side) → severity_score, bucket counts
    score: dict[tuple, float] = defaultdict(float)
    counts: dict[tuple, dict[str, int]] = defaultdict(lambda: {"SEVERE": 0, "MODERATE": 0, "MINOR": 0, "UNKNOWN": 0})
    for r in rows:
        key = (r["match_id"], r["team_side"])
        bucket = classify(r["reason"])
        score[key] += WEIGHTS[bucket]
        counts[key][bucket] += 1

    # Per-bucket distribution
    bucket_totals: dict[str, int] = {"SEVERE": 0, "MODERATE": 0, "MINOR": 0, "UNKNOWN": 0}
    for cmap in counts.values():
        for k, v in cmap.items():
            bucket_totals[k] += v
    t = Table(title="Severity distribution (match_injuries)")
    for c in ("bucket", "n", "weight", "weight_total"):
        t.add_column(c)
    for k in ("SEVERE", "MODERATE", "MINOR", "UNKNOWN"):
        t.add_row(k, str(bucket_totals[k]), f"{WEIGHTS[k]:.1f}", f"{bucket_totals[k] * WEIGHTS[k]:.1f}")
    console.print(t)

    write_rows: list[tuple] = []
    for (mid, side), sc in score.items():
        write_rows.append((mid, f"injury_severity_score_{side}", float(sc), "team", "derived"))
    console.print(f"\nWould write {len(write_rows):,} match_signals rows")
    if not args.write:
        console.print("[yellow]Dry run — pass --write to insert[/yellow]")
        return

    inserted = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            from psycopg2.extras import execute_values
            for cs in range(0, len(write_rows), 1000):
                chunk = write_rows[cs: cs + 1000]
                execute_values(
                    cur,
                    """INSERT INTO match_signals
                       (match_id, signal_name, signal_value, signal_group, data_source)
                       VALUES %s""",
                    chunk,
                )
                inserted += len(chunk)
        conn.commit()
    console.print(f"[green]✓ Inserted {inserted:,} match_signals rows[/green]")


if __name__ == "__main__":
    main()
