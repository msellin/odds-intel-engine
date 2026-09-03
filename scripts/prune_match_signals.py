#!/usr/bin/env python3
"""SIGNALS-STORE-ON-CHANGE — collapse the historical duplication in match_signals.

`SIGNALS-STORE-ON-CHANGE-2026-09-03` stopped the pipeline re-inserting the
whole signal set on every run. That fixes the growth; this reclaims what was
already written.

The table holds 49.3M rows / 13 GB (35 per cent of the database) across only
1.56M distinct (match, signal) pairs — roughly 31 copies of each, 130 on the
busiest match. `elo_home`, `rest_days_home` and `league_position_home` are
written every run and never change.

TWO PASSES, deliberately separate:

  --pass never-read   Deletes the ten signals nothing consumes. They are absent
                      from the production model's feature list, absent from
                      match_feature_vectors, and referenced in the codebase
                      only by the writer. 3.9M rows. Lowest risk: the data has
                      no reader, so there is nothing to regress.

  --pass duplicates   For each (match, signal), keeps the EARLIEST row per
                      distinct value and drops the rest. Every distinct
                      observation survives with its first-seen timestamp, which
                      is what store-on-change would have produced. A signal
                      that oscillates A->B->A keeps both A and B but loses the
                      second A; that is a deliberate trade — the alternative
                      (gap-and-island over 49M rows) costs far more to run for
                      a case no consumer distinguishes.

SAFETY

Dry run by default; --apply is required to delete. Deletes in batches with a
commit per batch so the table is never locked for long and the job can be
interrupted without leaving a transaction open. Nothing here touches a signal
that has a live reader, and `--pass duplicates` never removes a distinct value.

Space is NOT returned to the OS until the table is vacuumed. A plain autovacuum
reuses the space for future writes (fine, and the writer now writes 94 per cent
less); `VACUUM FULL` returns it but takes an exclusive lock, so run that in a
maintenance window, not from here.

    python3 scripts/prune_match_signals.py --pass never-read
    python3 scripts/prune_match_signals.py --pass never-read --apply
    python3 scripts/prune_match_signals.py --pass duplicates --apply --batch 50000
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console  # noqa: E402

console = Console()

# Kept in sync with supabase_client._NEVER_READ_SIGNALS — imported rather than
# copied so the two cannot drift into disagreeing about what is safe to delete.
from workers.api_clients.supabase_client import _NEVER_READ_SIGNALS  # noqa: E402


def _count(cur, sql: str, params=None) -> int:
    cur.execute(sql, params)
    return cur.fetchone()[0]


def pass_never_read(cur, apply: bool, batch: int) -> int:
    names = sorted(_NEVER_READ_SIGNALS)
    total = _count(cur, "SELECT COUNT(*) FROM match_signals WHERE signal_name = ANY(%s)", (names,))
    console.print(f"  never-read signals: {len(names)} names, [bold]{total:,}[/bold] rows")
    if not apply or not total:
        return total
    deleted = 0
    while True:
        cur.execute(
            """DELETE FROM match_signals
                WHERE ctid IN (SELECT ctid FROM match_signals
                                WHERE signal_name = ANY(%s) LIMIT %s)""",
            (names, batch),
        )
        n = cur.rowcount
        cur.connection.commit()
        deleted += n
        if n:
            console.print(f"    deleted {deleted:,}/{total:,}", end="\r")
        if n < batch:
            break
        time.sleep(0.05)          # let autovacuum and live writes breathe
    console.print(f"    deleted {deleted:,} rows" + " " * 20)
    return deleted


def pass_duplicates(cur, apply: bool, batch: int) -> int:
    total = _count(cur, "SELECT COUNT(*) FROM match_signals")
    keep = _count(cur, """
        SELECT COUNT(*) FROM (
            SELECT DISTINCT ON (match_id, signal_name, signal_value) 1
              FROM match_signals
             ORDER BY match_id, signal_name, signal_value, captured_at) x""")
    console.print(f"  total rows {total:,}, distinct (match, signal, value) {keep:,}")
    console.print(f"  redundant: [bold]{total - keep:,}[/bold] ({100.0 * (total - keep) / total:.1f}%)")
    if not apply:
        return total - keep
    deleted = 0
    while True:
        # Delete rows that are NOT the earliest occurrence of their value.
        cur.execute(
            """DELETE FROM match_signals
                WHERE ctid IN (
                  SELECT ctid FROM (
                    SELECT ctid, ROW_NUMBER() OVER (
                             PARTITION BY match_id, signal_name, signal_value
                             ORDER BY captured_at) rn
                      FROM match_signals) t
                   WHERE t.rn > 1 LIMIT %s)""",
            (batch,),
        )
        n = cur.rowcount
        cur.connection.commit()
        deleted += n
        console.print(f"    deleted {deleted:,}", end="\r")
        if n < batch:
            break
        time.sleep(0.05)
    console.print(f"    deleted {deleted:,} rows" + " " * 20)
    return deleted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pass", dest="which", required=True,
                    choices=("never-read", "duplicates"))
    ap.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    ap.add_argument("--batch", type=int, default=50000)
    args = ap.parse_args()

    from workers.api_clients.db import get_conn

    console.print(f"\n[bold]match_signals prune — {args.which}[/bold] "
                  f"[dim]({'APPLY' if args.apply else 'DRY RUN'})[/dim]\n")
    with get_conn() as conn, conn.cursor() as cur:
        before = _count(cur, "SELECT COUNT(*) FROM match_signals")
        n = (pass_never_read if args.which == "never-read" else pass_duplicates)(
            cur, args.apply, args.batch)
        after = _count(cur, "SELECT COUNT(*) FROM match_signals")

    console.print(f"\n  rows {before:,} -> {after:,}")
    if not args.apply:
        console.print(f"  [yellow]Dry run — would remove {n:,}. Re-run with --apply.[/yellow]")
    else:
        console.print("  [dim]Space is reused by future writes after autovacuum. To return "
                      "it to the OS run VACUUM FULL in a maintenance window — it takes an "
                      "exclusive lock.[/dim]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
