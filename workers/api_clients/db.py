"""
OddsIntel — Direct PostgreSQL Client

Connection pool for direct SQL access via Supabase's Supavisor pooler.
Used by the live tracker and live poller for fast bulk operations.
Replaces PostgREST HTTP API for high-frequency paths (15s polling cycles).

Benefits over PostgREST:
  - No 1K row cap (PostgREST default max_rows caused silent data loss)
  - Real JOINs (PostgREST requires multiple queries + Python merge)
  - Bulk INSERT via execute_values (10-50x faster than individual HTTP POSTs)
  - No URL length limits on IN clauses
  - Persistent connections (no HTTP overhead per query)
"""

import os
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2 import pool
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()

console = Console()

DATABASE_URL = os.getenv("DATABASE_URL", "")

_pool: pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> pool.ThreadedConnectionPool:
    """Get or create the connection pool singleton (thread-safe)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:  # Double-check after acquiring lock
                if not DATABASE_URL:
                    raise ValueError(
                        "DATABASE_URL not set. Get it from Supabase Dashboard → "
                        "Settings → Database → Connection string (Pooler mode)."
                    )
                _pool = pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=10,
                    dsn=DATABASE_URL,
                    connect_timeout=10,
                )
                console.print(f"[dim]DB pool created (2-10 connections)[/dim]")
    return _pool


@contextmanager
def get_conn():
    """Context manager that gets a connection from the pool and returns it after use."""
    p = get_pool()
    conn = p.getconn()
    try:
        yield conn
    finally:
        p.putconn(conn)


def execute_query(sql: str, params=None) -> list[dict]:
    """Execute a read query, return list of dicts."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def execute_write(sql: str, params=None) -> int:
    """Execute a write query, return rows affected."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount


def bulk_insert(table: str, columns: list[str], rows: list[tuple],
                on_conflict: str = "DO NOTHING") -> int:
    """
    Bulk insert via execute_values — 10-50x faster than individual INSERTs.

    Args:
        table: Table name
        columns: List of column names
        rows: List of tuples, one per row
        on_conflict: Conflict clause (e.g. "DO NOTHING" or
                     "ON CONSTRAINT xyz DO UPDATE SET col = EXCLUDED.col")
    Returns:
        Number of rows inserted
    """
    if not rows:
        return 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cols = ", ".join(columns)
            sql = f"INSERT INTO {table} ({cols}) VALUES %s ON CONFLICT {on_conflict}"
            psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
            conn.commit()
            return cur.rowcount


def bulk_upsert(table: str, columns: list[str], rows: list[tuple],
                conflict_columns: list[str], update_columns: list[str]) -> int:
    """
    Bulk upsert — insert or update on conflict.

    Args:
        table: Table name
        columns: All column names being inserted
        rows: List of tuples
        conflict_columns: Columns that form the unique constraint
        update_columns: Columns to update on conflict
    """
    if not rows:
        return 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cols = ", ".join(columns)
            conflict = ", ".join(conflict_columns)
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_columns)
            sql = (f"INSERT INTO {table} ({cols}) VALUES %s "
                   f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}")
            psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
            conn.commit()
            return cur.rowcount


def pool_status() -> dict:
    """Return pool stats for the health endpoint."""
    if _pool is None:
        return {"status": "not_initialized"}
    return {
        "status": "active",
        "min_connections": _pool.minconn,
        "max_connections": _pool.maxconn,
    }


# ── Live Tracker DB Functions (direct SQL replacements) ────────────────────
# These replace the PostgREST versions in supabase_client.py for the live
# tracker's hot path. Batched writes instead of individual HTTP POSTs.

from datetime import datetime, date, timezone


def build_af_id_map(target_date: str = None) -> dict[int, dict]:
    """
    Build {api_football_id: match_record} for all of a day's matches.
    Replaces _build_af_id_map() — no 1K row limit.
    """
    target_date = target_date or date.today().isoformat()
    rows = execute_query(
        """SELECT id, api_football_id, home_team_id, away_team_id,
                  date, status, lineups_fetched_at
           FROM matches
           WHERE date::date = %s
             AND api_football_id IS NOT NULL""",
        (target_date,)
    )
    return {int(r["api_football_id"]): r for r in rows}


def find_match_by_teams_and_date(home_team: str, away_team: str,
                                  match_date: str) -> dict | None:
    """Look up a match by team names and date. Replaces get_match_by_teams_and_date()."""
    rows = execute_query(
        """SELECT m.id, m.status, m.date
           FROM matches m
           JOIN teams th ON m.home_team_id = th.id
           JOIN teams ta ON m.away_team_id = ta.id
           WHERE th.name = %s AND ta.name = %s
             AND m.date::date = %s
           LIMIT 1""",
        (home_team, away_team, match_date[:10])
    )
    return rows[0] if rows else None


def store_live_snapshots_batch(snapshots: list[dict]) -> int:
    """
    Bulk insert live match snapshots. Replaces store_live_snapshot() called N times.
    Each snapshot dict has: match_id, minute, score_home, score_away, + optional fields.
    """
    if not snapshots:
        return 0

    now = datetime.now(timezone.utc).isoformat()

    columns = [
        "match_id", "minute", "added_time", "score_home", "score_away", "captured_at",
        "shots_home", "shots_away", "shots_on_target_home", "shots_on_target_away",
        "xg_home", "xg_away", "possession_home", "corners_home", "corners_away",
        "attacks_home", "attacks_away",
        "live_ou_05_over", "live_ou_05_under",
        "live_ou_15_over", "live_ou_15_under",
        "live_ou_25_over", "live_ou_25_under",
        "live_ou_35_over", "live_ou_35_under",
        "live_ou_45_over", "live_ou_45_under",
        "live_1x2_home", "live_1x2_draw", "live_1x2_away",
        "model_xg_home", "model_xg_away", "model_ou25_prob",
    ]

    rows = []
    for s in snapshots:
        rows.append(tuple(
            s.get("match_id") if c == "match_id"
            else now if c == "captured_at"
            else s.get(c)
            for c in columns
        ))

    return bulk_insert("live_match_snapshots", columns, rows)


def store_live_odds_batch(odds_rows: list[dict]) -> int:
    """
    Bulk insert live in-play odds. Replaces store_live_odds() called per match.
    Each dict has: match_id, bookmaker, market, selection, odds, minute.
    """
    if not odds_rows:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    columns = ["match_id", "bookmaker", "market", "selection", "odds",
               "timestamp", "is_live", "is_closing", "minutes_to_kickoff"]

    rows = []
    for r in odds_rows:
        rows.append((
            r["match_id"],
            r.get("bookmaker", "api-football-live"),
            r["market"],
            r["selection"],
            r["odds"],
            now,
            True,
            False,
            r.get("minute"),
        ))

    return bulk_insert("odds_snapshots", columns, rows)


def store_match_events_batch(match_id: str, events: list[dict],
                              home_team_api_id: int = None) -> int:
    """
    Bulk upsert match events. Replaces store_match_events_af() one-at-a-time loop.
    """
    if not events:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    columns = ["match_id", "minute", "added_time", "event_type", "team",
               "player_name", "detail", "af_event_order", "created_at"]

    rows = []
    for ev in events:
        team_side = "unknown"
        if home_team_api_id and ev.get("team_api_id"):
            team_side = "home" if ev["team_api_id"] == home_team_api_id else "away"

        rows.append((
            match_id,
            ev.get("minute", 0),
            ev.get("added_time", 0),
            ev["event_type"],
            team_side,
            ev.get("player_name"),
            ev.get("detail"),
            ev.get("af_event_order"),
            now,
        ))

    return bulk_upsert(
        "match_events", columns, rows,
        conflict_columns=["match_id", "af_event_order"],
        update_columns=["minute", "added_time", "event_type", "team",
                        "player_name", "detail"]
    )


def update_match_status_sql(match_id: str, status: str):
    """Update match status. Replaces update_match_status()."""
    execute_write(
        "UPDATE matches SET status = %s WHERE id = %s",
        (status, match_id)
    )
