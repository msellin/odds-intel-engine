"""
MATCH-DUPES-CLEANUP — Stage A

Deduplicate the `matches` table by `api_football_id`. For each AF id with >1 row, the oldest
`created_at` row is canonical; dependent FK rows are repointed to canonical (or deleted on
unique-constraint conflict), the dupe `matches` rows are mirrored to `matches_dupe_quarantined`,
then DELETEd.

Run order:
  1. Dry-run (no flags): executes everything inside a transaction, then ROLLBACK. Counts shown
     are exactly what --apply would do.
  2. --apply: same logic, but COMMIT at the end.

Idempotent: re-run after --apply shows zero dupes.
"""

import os
import sys
import argparse
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

console = Console()


# Per-table merge config: how to repoint match_id from dupe → canonical.
# Each tuple in `unique_cols_list` represents one unique-key column-set involving match_id;
# when canonical already has a row at the same other-cols tuple, the dupe-side row must be
# DELETEd before the UPDATE (otherwise it would violate the unique constraint).
TABLES: list[tuple[str, list[tuple[str, ...]]]] = [
    ("daily_unlocks",         []),
    ("lineups",               [("team_id", "player_id")]),
    ("live_match_snapshots",  []),
    ("match_events",          [("af_event_order",)]),
    ("match_feature_vectors", [()]),
    ("match_injuries",        [("player_id",)]),
    ("match_notes",           [("user_id",)]),
    ("match_page_views",      [("session_id",)]),
    ("match_player_stats",    [("player_id",)]),
    ("match_previews",        [("match_date",)]),
    ("match_signals",         []),
    ("match_stats",           [()]),
    ("match_votes",           [("user_id",)]),
    ("match_weather",         [()]),
    ("news_events",           []),
    ("odds_snapshots",        []),
    ("odds_snapshots_quarantined", []),
    ("predictions",           [("market", "source")]),
    ("referee_matches",       [("referee_id",)]),
    ("saved_matches",         [("user_id",)]),
    ("simulated_bets",        [("bot_id", "market", "selection")]),
    ("user_bets",             []),
    ("user_match_favorites",  [("user_id",)]),
    ("user_picks",            [("user_id",)]),
    ("watchlist_alert_log",   [("user_id", "alert_type")]),
]


def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def ensure_quarantine_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS matches_dupe_quarantined (
            LIKE matches INCLUDING DEFAULTS,
            quarantined_at timestamptz DEFAULT now(),
            canonical_id   uuid,
            reason         text DEFAULT 'MATCH-DUPES-CLEANUP'
        )
        """
    )


def stage_dupe_map(cur) -> int:
    """Build the temp _dupe_map table. Returns the number of dupe rows."""
    cur.execute("DROP TABLE IF EXISTS _dupe_map")
    cur.execute(
        """
        CREATE TEMP TABLE _dupe_map (dupe_id uuid PRIMARY KEY, canonical_id uuid NOT NULL)
        ON COMMIT DROP
        """
    )
    cur.execute(
        """
        WITH ranked AS (
            SELECT id,
                   api_football_id,
                   ROW_NUMBER() OVER (PARTITION BY api_football_id ORDER BY created_at, id) rn,
                   FIRST_VALUE(id) OVER (PARTITION BY api_football_id ORDER BY created_at, id) canonical
            FROM matches
            WHERE api_football_id IN (
                SELECT api_football_id FROM matches
                WHERE api_football_id IS NOT NULL
                GROUP BY api_football_id HAVING COUNT(*) > 1
            )
        )
        INSERT INTO _dupe_map (dupe_id, canonical_id)
        SELECT id, canonical FROM ranked WHERE rn > 1
        """
    )
    cur.execute("SELECT COUNT(*) FROM _dupe_map")
    return cur.fetchone()[0]


def repoint_table(cur, table: str, unique_cols_list: list[tuple]) -> tuple[int, int]:
    """Repoint match_id for one table. Returns (rows_updated, rows_deleted_on_conflict).

    For each unique constraint involving match_id:
      1. Drop dupe rows that conflict with the canonical row (canonical wins).
      2. Drop dupe rows that conflict with another dupe pointing to the same canonical
         (oldest dupe.id wins — arbitrary but stable).
    """
    deleted = 0
    for unique_cols in unique_cols_list:
        if unique_cols == ():
            # match_id alone is unique. Delete any dupe row when canonical already has one,
            # then dedupe within the dupe set (keep one row per canonical).
            cur.execute(f"""
                DELETE FROM {table} t USING _dupe_map dm
                WHERE t.match_id = dm.dupe_id
                  AND EXISTS (SELECT 1 FROM {table} t2 WHERE t2.match_id = dm.canonical_id)
            """)
            deleted += cur.rowcount
            cur.execute(f"""
                DELETE FROM {table} t USING _dupe_map dm
                WHERE t.match_id = dm.dupe_id
                  AND EXISTS (
                      SELECT 1 FROM {table} t3
                      JOIN _dupe_map dm3 ON dm3.dupe_id = t3.match_id
                      WHERE dm3.canonical_id = dm.canonical_id
                        AND t3.ctid < t.ctid
                  )
            """)
            deleted += cur.rowcount
        else:
            cmp = " AND ".join(f"t2.{c} IS NOT DISTINCT FROM t.{c}" for c in unique_cols)
            cur.execute(f"""
                DELETE FROM {table} t USING _dupe_map dm
                WHERE t.match_id = dm.dupe_id
                  AND EXISTS (
                      SELECT 1 FROM {table} t2
                      WHERE t2.match_id = dm.canonical_id
                        AND {cmp}
                  )
            """)
            deleted += cur.rowcount
            cmp3 = " AND ".join(f"t3.{c} IS NOT DISTINCT FROM t.{c}" for c in unique_cols)
            cur.execute(f"""
                DELETE FROM {table} t USING _dupe_map dm
                WHERE t.match_id = dm.dupe_id
                  AND EXISTS (
                      SELECT 1 FROM {table} t3
                      JOIN _dupe_map dm3 ON dm3.dupe_id = t3.match_id
                      WHERE dm3.canonical_id = dm.canonical_id
                        AND t3.ctid < t.ctid
                        AND {cmp3}
                  )
            """)
            deleted += cur.rowcount

    cur.execute(
        f"UPDATE {table} SET match_id = dm.canonical_id "
        f"FROM _dupe_map dm WHERE {table}.match_id = dm.dupe_id"
    )
    updated = cur.rowcount
    return (updated, deleted)


def quarantine_and_delete(cur) -> tuple[int, int]:
    cur.execute(
        """
        INSERT INTO matches_dupe_quarantined
        SELECT m.*, now(), dm.canonical_id, 'MATCH-DUPES-CLEANUP'
        FROM matches m JOIN _dupe_map dm ON dm.dupe_id = m.id
        """
    )
    quarantined = cur.rowcount
    cur.execute("DELETE FROM matches WHERE id IN (SELECT dupe_id FROM _dupe_map)")
    deleted = cur.rowcount
    return (quarantined, deleted)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Execute and COMMIT (default: dry-run + rollback)")
    args = ap.parse_args()

    mode = "[bold red]APPLY[/bold red]" if args.apply else "[bold yellow]DRY-RUN[/bold yellow]"
    console.print(f"\n{mode} — MATCH-DUPES-CLEANUP\n")

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()
    # The per-table cross-dupe DELETEs scan via EXISTS join with _dupe_map; with ~3k dupes
    # × ~25 tables this can exceed the 60s default. Lift to 10min for this session only.
    cur.execute("SET LOCAL statement_timeout = '600s'")

    try:
        cur.execute(
            """
            WITH dupes AS (
                SELECT api_football_id, COUNT(*) c FROM matches
                WHERE api_football_id IS NOT NULL
                GROUP BY api_football_id HAVING COUNT(*) > 1
            )
            SELECT COUNT(*), COALESCE(SUM(c), 0), COALESCE(SUM(c-1), 0)
            FROM dupes
            """
        )
        groups, total_rows, extra_rows = cur.fetchone()
        console.print(f"Found {groups} dupe groups, {total_rows} total rows, {extra_rows} extra rows to remove\n")

        if groups == 0:
            console.print("[green]Nothing to do — no duplicates.[/green]")
            return

        ensure_quarantine_table(cur)
        dupe_count = stage_dupe_map(cur)
        console.print(f"Staged {dupe_count} dupe → canonical mappings\n")

        total_updated = 0
        total_deleted = 0
        for table, unique_cols_list in TABLES:
            updated, deleted = repoint_table(cur, table, unique_cols_list)
            total_updated += updated
            total_deleted += deleted
            if updated or deleted:
                console.print(f"  {table:30} updated={updated:>6}  deleted_on_conflict={deleted:>4}")

        quarantined, m_deleted = quarantine_and_delete(cur)
        console.print(f"\n  {'matches':30} quarantined={quarantined:>6}  deleted={m_deleted:>6}")
        console.print(f"\n  TOTAL across FK tables: updated={total_updated}, deleted_on_conflict={total_deleted}")

        if args.apply:
            conn.commit()
            console.print("\n[bold green]✓ Committed.[/bold green]")
        else:
            conn.rollback()
            console.print("\n[yellow]Dry-run rolled back. Re-run with --apply to execute.[/yellow]")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
