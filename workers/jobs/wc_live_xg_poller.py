"""
OddsIntel — WC2026 Live xG Poller (WC-D2)

One tick captures live xG + shots + possession for every in-progress WC match
and writes one row per match to live_xg_snapshots. Drives the win-probability
curve + goal-probability widgets shipped in Wave 3.

Gates
-----
1. WC window (2026-06-11 → 2026-07-19) — enforced at the scheduler layer via
   _WC_LIVE_WINDOW_START/END. The job-level early-return below is a defence
   in depth so manual invocations during quiet weeks still no-op cleanly.
2. WC league only — leagues.api_football_id = 1 (FIFA World Cup).
3. In-progress matches only — matches.status = 'live'. We rely on the existing
   live_tracker / live_poller writes to flip status to 'live' when AF reports
   1H/2H/HT/ET/BT/P. Once status flips to 'finished' the row stops being
   polled.

Cadence
-------
Scheduler fires this every 60s. xG doesn't move fast enough to need the 30s
tier the main live_poller uses, and per-match /fixtures/statistics is one
AF call per live WC match. Budget: ~5 concurrent WC matches × 2h × 60 ticks
= ~600 calls/day during the tournament, comfortably under the Ultra budget.

Source data
-----------
GET /fixtures/statistics?fixture=ID returns shots, shots on goal, possession,
expected_goals (when AF publishes it for that fixture). Reuses
api_football.parse_fixture_stats which already handles the xG field.
"""

from datetime import date
from rich.console import Console

from workers.api_clients.api_football import (
    get_fixture_statistics,
    parse_fixture_stats,
    budget,
)
from workers.api_clients.db import execute_query, bulk_insert

console = Console()


# WC league on API-Football = id 1 (FIFA World Cup).
WC_LEAGUE_AF_ID = 1

# WC-window gate (defence-in-depth — primary gate is in the scheduler).
_WC_LIVE_WINDOW_START = date(2026, 6, 11)
_WC_LIVE_WINDOW_END = date(2026, 7, 19)


def _in_wc_window(today: date | None = None) -> bool:
    today = today or date.today()
    return _WC_LIVE_WINDOW_START <= today <= _WC_LIVE_WINDOW_END


def _fetch_live_wc_matches() -> list[dict]:
    """Return live WC matches that have an AF fixture id.

    Joined against leagues so we can hard-filter to api_football_id = 1
    (FIFA World Cup) at the SQL level — avoids polluting the poller with
    other live matches even if some accidentally inherit the wrong status.
    """
    return execute_query(
        """
        SELECT m.id           AS match_id,
               m.api_football_id AS af_id
        FROM   matches m
        JOIN   leagues l ON l.id = m.league_id
        WHERE  l.api_football_id = %s
          AND  m.status = 'live'
          AND  m.api_football_id IS NOT NULL
        """,
        [WC_LEAGUE_AF_ID],
    )


def _build_snapshot(match_id: str, raw_stats: list[dict]) -> tuple | None:
    """Convert one /fixtures/statistics response into a live_xg_snapshots row.

    Returns None when the stats payload is empty (kick-off seconds where AF
    hasn't published any stats yet) — caller skips empty payloads to avoid
    spamming the table with all-null rows.
    """
    if not raw_stats:
        return None
    parsed = parse_fixture_stats(raw_stats)
    if not parsed:
        return None

    # /fixtures/statistics does not carry the minute — we read it from the
    # first team's nested fixture meta if present, otherwise 0. The matching
    # live_match_snapshots row written by the main live_poller carries the
    # authoritative minute, so consumers can join on (match_id, captured_at).
    minute = 0
    try:
        minute = raw_stats[0].get("fixture", {}).get("status", {}).get("elapsed") or 0
    except (AttributeError, KeyError, IndexError):
        minute = 0

    home_xg = parsed.get("xg_home")
    away_xg = parsed.get("xg_away")
    home_shots = parsed.get("shots_home")
    away_shots = parsed.get("shots_away")
    home_shots_on = parsed.get("shots_on_target_home")
    away_shots_on = parsed.get("shots_on_target_away")
    home_poss = parsed.get("possession_home")
    away_poss = parsed.get("possession_away")

    # Skip when literally every stat is null — saves a row of pure nulls
    # during the first 60-90s of a match when AF hasn't populated anything.
    if all(v is None for v in (
        home_xg, away_xg, home_shots, away_shots,
        home_shots_on, away_shots_on, home_poss, away_poss,
    )):
        return None

    return (
        match_id,
        minute,
        home_xg, away_xg,
        home_shots, home_shots_on,
        away_shots, away_shots_on,
        home_poss, away_poss,
    )


def run_wc_live_xg_poll() -> int:
    """Run one tick. Returns the number of snapshots written.

    Designed to be safe to call from APScheduler every 60s — fully idempotent
    in the sense that two ticks 60s apart simply append two rows, and the
    table has no uniqueness constraint other than the surrogate id.
    """
    if not _in_wc_window():
        return 0

    live = _fetch_live_wc_matches()
    if not live:
        return 0

    rows: list[tuple] = []
    for m in live:
        if not budget.can_call():
            console.print(
                "[yellow]wc_live_xg_poller: AF budget low — stopping cycle early[/yellow]"
            )
            break
        af_id = m["af_id"]
        try:
            raw = get_fixture_statistics(af_id)
        except Exception as e:
            console.print(f"[yellow]wc_live_xg AF {af_id} error: {e}[/yellow]")
            continue
        snap = _build_snapshot(m["match_id"], raw)
        if snap:
            rows.append(snap)

    if not rows:
        return 0

    columns = [
        "match_id", "minute",
        "home_xg", "away_xg",
        "home_shots", "home_shots_on",
        "away_shots", "away_shots_on",
        "home_possession_pct", "away_possession_pct",
    ]
    bulk_insert("live_xg_snapshots", columns, rows, on_conflict="DO NOTHING")
    console.print(
        f"[dim]wc_live_xg_poller: wrote {len(rows)} live xG snapshot(s)[/dim]"
    )
    return len(rows)


if __name__ == "__main__":
    n = run_wc_live_xg_poll()
    console.print(f"[green]wc_live_xg_poller: {n} snapshot(s) written[/green]")
