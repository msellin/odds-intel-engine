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
import time
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
                    maxconn=20,
                    dsn=DATABASE_URL,
                    connect_timeout=10,
                )
                # NOTE on DB-STMT-TIMEOUT: Supavisor (Supabase's transaction-mode
                # pooler) strips startup `options=` parameters, so we can't set
                # statement_timeout per-connection from the client. Supabase
                # configures a role-level statement_timeout (~2min by default)
                # which acts as a runaway-query backstop. If we want a tighter
                # bound, do it via SQL migration: ALTER ROLE app SET
                # statement_timeout='60s'. Tracked under DB-STMT-TIMEOUT.
                console.print(
                    f"[dim]DB pool created ({_pool.minconn}-{_pool.maxconn} connections)[/dim]"
                )
    return _pool


def get_pool_status() -> dict:
    """Return pool utilization snapshot — used by /health and InplayBot heartbeat.

    psycopg2 ThreadedConnectionPool internals:
      _used  — dict of checked-out connections (in use right now)
      _pool  — list of idle connections waiting
    Returns zeroes if pool not yet initialised.
    """
    with _pool_lock:
        p = _pool
    if p is None:
        return {"used": 0, "idle": 0, "max": 20, "pct": 0}
    used = len(p._used)
    idle = len(p._pool)
    return {"used": used, "idle": idle, "max": p.maxconn, "pct": round(used / p.maxconn * 100)}


def _reset_pool():
    """Discard the current pool so the next call recreates it.
    Called when a connection is found to be dead (SSL drop / idle timeout).

    NOTE: We intentionally do NOT call closeall() here. Multiple jobs run
    concurrently in APScheduler threads sharing this pool. closeall() would
    kill connections held by sibling threads mid-query, causing InterfaceError
    in those threads. Instead we just discard the pool reference — existing
    connections finish normally against the old pool; new calls get a fresh pool.
    """
    global _pool
    with _pool_lock:
        _pool = None
    console.print("[yellow]DB pool reset — will reconnect on next query[/yellow]")


# 15s default: long enough to absorb transient saturation when several scheduler
# jobs grab conns at once, short enough to surface real problems fast. 60s of
# silently waiting on a stuck pool hides signal — the live poller would freeze
# for a full minute before the cycle errored. Override via env if you need to.
_POOL_WAIT_TIMEOUT = float(os.getenv("DB_POOL_WAIT_TIMEOUT", "15"))


def _acquire_conn(p: pool.ThreadedConnectionPool, timeout: float):
    """Get a connection from the pool, waiting up to `timeout` seconds when full.

    psycopg2's ThreadedConnectionPool.getconn() raises PoolError immediately when
    saturated — no built-in blocking. This wrapper polls with backoff so transient
    saturation (multiple APScheduler jobs hitting the DB at once) waits instead of
    crashing the caller. After `timeout` seconds with no slot, we re-raise so a
    genuinely deadlocked pool still surfaces loudly rather than hanging forever.
    """
    deadline = time.monotonic() + timeout
    backoff = 0.05
    warned = False
    while True:
        try:
            return p.getconn()
        except pool.PoolError:
            now = time.monotonic()
            if now >= deadline:
                console.print(
                    f"[red]DB pool exhausted for {timeout}s — giving up "
                    f"(used={len(p._used)}/{p.maxconn})[/red]"
                )
                raise
            if not warned and now - (deadline - timeout) > 1.0:
                console.print(
                    f"[yellow]DB pool saturated ({len(p._used)}/{p.maxconn}) — "
                    f"waiting up to {timeout:.0f}s[/yellow]"
                )
                warned = True
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 1.0)


@contextmanager
def get_conn(wait_timeout: float | None = None):
    """Context manager that gets a connection from the pool and returns it after use.

    Always returns the connection to the pool, even on exception — without this
    `finally`, any non-connection error (SQL syntax, integrity violation, KeyError
    on a malformed row, TypeError, etc.) leaks the conn permanently. With a small
    pool and InplayBot polling every 30s, ~maxconn such errors and the pool is
    dead — which took the entire pipeline down for 11h on 2026-05-08.

    When the pool is saturated, waits up to `wait_timeout` seconds (default 60s,
    overridable via DB_POOL_WAIT_TIMEOUT env) for a slot before raising. Prefer
    being slow over crashing the live poller mid-cycle.
    """
    p = get_pool()
    conn = _acquire_conn(p, wait_timeout if wait_timeout is not None else _POOL_WAIT_TIMEOUT)
    conn_dead = False
    try:
        yield conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        # Connection-level error (SSL drop, idle timeout, killed by pool reset).
        # Discard this conn and reset the pool so the next caller reconnects.
        conn_dead = True
        raise
    except Exception:
        # App-level error (bad SQL, integrity violation, unexpected exception
        # in caller). The transaction may be aborted — rollback so the conn
        # is reusable. If rollback itself fails, treat the conn as dead.
        try:
            conn.rollback()
        except Exception:
            conn_dead = True
        raise
    finally:
        try:
            p.putconn(conn, close=conn_dead)
        except Exception:
            pass
        if conn_dead:
            _reset_pool()


_CONN_ERRORS = (psycopg2.OperationalError, psycopg2.InterfaceError)


def execute_query(sql: str, params=None) -> list[dict]:
    """Execute a read query, return list of dicts. Retries once on connection drop."""
    for attempt in range(2):
        try:
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, params)
                    return [dict(row) for row in cur.fetchall()]
        except _CONN_ERRORS:
            if attempt == 1:
                raise


def execute_write(sql: str, params=None) -> int:
    """Execute a write query, return rows affected. Retries once on connection drop."""
    for attempt in range(2):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    conn.commit()
                    return cur.rowcount
        except _CONN_ERRORS:
            if attempt == 1:
                raise


def execute_write_returning(sql: str, params=None) -> list[dict]:
    """Execute a write query with RETURNING clause. Retries once on connection drop."""
    for attempt in range(2):
        try:
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(sql, params)
                    rows = [dict(row) for row in cur.fetchall()]
                    conn.commit()
                    return rows
        except _CONN_ERRORS:
            if attempt == 1:
                raise


def bulk_insert(table: str, columns: list[str], rows: list[tuple],
                on_conflict: str = "DO NOTHING",
                page_size: int = 500) -> int:
    """
    Bulk insert via execute_values — 10-50x faster than individual INSERTs.

    Args:
        table: Table name
        columns: List of column names
        rows: List of tuples, one per row
        on_conflict: Conflict clause (e.g. "DO NOTHING" or
                     "ON CONSTRAINT xyz DO UPDATE SET col = EXCLUDED.col")
        page_size: Rows per internal INSERT statement. Default 500 is safe for
                   most callers; raise to 5000 for very large bulk loads
                   (e.g. odds snapshots ~190k rows/run — empirical benchmark
                   showed page_size=500 → 41s, page_size=5000 → 14s for 100k rows).
    Returns:
        Number of rows inserted (note: psycopg2 execute_values reports rowcount
        of the LAST batch only when page_size < total rows, not the total).
    """
    if not rows:
        return 0

    cols = ", ".join(columns)
    sql = f"INSERT INTO {table} ({cols}) VALUES %s ON CONFLICT {on_conflict}"
    for attempt in range(2):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, sql, rows, page_size=page_size)
                    conn.commit()
                    return cur.rowcount
        except _CONN_ERRORS:
            if attempt == 1:
                raise


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

    cols = ", ".join(columns)
    conflict = ", ".join(conflict_columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_columns)
    sql = (f"INSERT INTO {table} ({cols}) VALUES %s "
           f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}")
    for attempt in range(2):
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
                    conn.commit()
                    return cur.rowcount
        except _CONN_ERRORS:
            if attempt == 1:
                raise


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
    Build {api_football_id: match_record} for today's AND yesterday's matches.
    Includes yesterday because late matches (e.g., 21:00+ UTC kickoffs)
    may still be live after midnight UTC when date.today() rolls over.
    """
    from datetime import timedelta
    today = target_date or date.today().isoformat()
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    rows = execute_query(
        """SELECT id, api_football_id, home_team_id, away_team_id,
                  date, status, lineups_fetched_at
           FROM matches
           WHERE date::date IN (%s, %s)
             AND api_football_id IS NOT NULL""",
        (today, yesterday)
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
        "fouls_home", "fouls_away", "offsides_home", "offsides_away",
        "saves_home", "saves_away", "blocked_shots_home", "blocked_shots_away",
        "pass_accuracy_home", "pass_accuracy_away",
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

    rows = []
    for ev in events:
        team_side = "home"  # Default to home (DB CHECK constraint requires home/away)
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

    # Bulk insert via execute_values — one round-trip instead of N. Releases the
    # pooled conn fast (was held for the full per-row loop, ~30 round-trips per
    # match, multiplied by ~30 live matches per LivePoller cycle). If the batch
    # fails (rare — bad row, e.g. NULL on a NOT NULL column), fall back to the
    # per-row loop so a single bad event doesn't poison the whole match.
    columns = ("match_id", "minute", "added_time", "event_type", "team",
               "player_name", "detail", "af_event_order", "created_at")
    cols = ", ".join(columns)
    bulk_sql = f"INSERT INTO match_events ({cols}) VALUES %s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            try:
                psycopg2.extras.execute_values(cur, bulk_sql, rows, page_size=500)
                stored = cur.rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                stored = 0
                for row in rows:
                    try:
                        cur.execute(
                            f"INSERT INTO match_events ({cols}) "
                            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            row,
                        )
                        stored += 1
                    except Exception:
                        conn.rollback()
                        continue
                conn.commit()
    return stored


def update_match_status_sql(match_id: str, status: str):
    """Update match status. Replaces update_match_status()."""
    execute_write(
        "UPDATE matches SET status = %s WHERE id = %s",
        (status, match_id)
    )


def finish_match_sql(match_id: str, score_home: int, score_away: int):
    """
    Mark a match as finished with final score + result.
    Called by the live poller when it detects FT/AET/PEN status.
    This ensures the matches table has correct scores immediately,
    not just after the 21:00 UTC settlement run.
    """
    result = "home" if score_home > score_away else "away" if score_away > score_home else "draw"
    execute_write(
        """UPDATE matches
           SET status = 'finished', score_home = %s, score_away = %s, result = %s,
               settlement_status = 'ready'
           WHERE id = %s AND status != 'finished'""",
        (score_home, score_away, result, match_id)
    )
