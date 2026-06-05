"""
GROWTH-ACCURACY-PICKS-LOG (2026-06-05) — daily publisher for the
public accuracy track-record.

Runs at 06:00 UTC daily via the Railway scheduler. For every match
kicking off in the next 24h that has an ensemble prediction, logs:
  - The highest-confidence 1X2 selection (home / draw / away)
  - The higher of over_15 / under_15 (OU 1.5 market)
  - The higher of over_25 / under_25 (OU 2.5 market)
  - The higher of btts_yes / btts_no (BTTS market)

Inserts into `published_picks` are idempotent via the UNIQUE constraint
on (match_id, market, model_version), so re-running the same day is safe.

The picked_at column is set to NOW() and never modified — that's the
credibility anchor for the public claim "we called these picks before
kickoff."

NOT a betting/value-bet job. This is a pure outcome-accuracy log that
powers the /accuracy marketing surface. Picks here ignore odds entirely;
even 1.01-odds heavy favourites get logged if the model is most
confident about them.

Run manually:
    python -m workers.jobs.publish_daily_picks
    python -m workers.jobs.publish_daily_picks --window-hours 48
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from rich.console import Console

from workers.api_clients.db import execute_query, execute_write

console = Console()


# Markets to publish per match. The order doesn't matter — the cron is idempotent.
MARKETS: list[dict] = [
    {"market": "1x2", "selections": ["1x2_home", "1x2_draw", "1x2_away"],
     "sel_map": {"1x2_home": "home", "1x2_draw": "draw", "1x2_away": "away"}},
    {"market": "over_under_15", "selections": ["over15", "under15"],
     "sel_map": {"over15": "over", "under15": "under"}},
    {"market": "over_under_25", "selections": ["over25", "under25"],
     "sel_map": {"over25": "over", "under25": "under"}},
    {"market": "btts", "selections": ["btts_yes", "btts_no"],
     "sel_map": {"btts_yes": "yes", "btts_no": "no"}},
]


def run_publish_daily_picks(window_hours: int = 24) -> int:
    """Publish the top model pick per (market) for every match kicking off
    in the next `window_hours` hours that has an ensemble prediction.

    Returns the number of new rows inserted (re-runs of already-published
    matches return 0 — UNIQUE constraint enforces idempotency).
    """
    now = datetime.now(timezone.utc)
    until = now + timedelta(hours=window_hours)

    # Pull all candidate matches with their ensemble predictions in one query.
    # Filters: status='scheduled' so we don't backfill finished matches here
    # (that's the separate backfill script's job), and kickoff in the
    # near future so we don't publish picks for matches a week away.
    sql = """
    SELECT
        m.id            AS match_id,
        m.date          AS kickoff_at,
        p.market        AS market,
        p.model_probability AS prob,
        p.model_version AS model_version
    FROM matches m
    JOIN predictions p ON p.match_id = m.id
    WHERE m.status = 'scheduled'
      AND m.date >= %s
      AND m.date <= %s
      AND p.source = 'ensemble'
      AND p.market IN (
          '1x2_home','1x2_draw','1x2_away',
          'over15','under15','over25','under25',
          'btts_yes','btts_no'
      )
    """
    rows = execute_query(sql, (now, until))

    # Group rows by (match_id, model_version) → {market_key: prob}
    by_match: dict[tuple[str, str], dict] = {}
    kickoff_map: dict[str, datetime] = {}
    for r in rows:
        key = (r["match_id"], r["model_version"])
        if key not in by_match:
            by_match[key] = {}
        by_match[key][r["market"]] = float(r["prob"])
        kickoff_map[r["match_id"]] = r["kickoff_at"]

    inserts = 0
    for (match_id, model_version), probs in by_match.items():
        kickoff_at = kickoff_map[match_id]
        for spec in MARKETS:
            # Find the highest-probability selection within this market that
            # has prediction data for this match
            available = {s: probs[s] for s in spec["selections"] if s in probs}
            if not available:
                continue  # market not covered for this match
            top_key, top_prob = max(available.items(), key=lambda kv: kv[1])
            selection = spec["sel_map"][top_key]
            inserted = _insert_pick(
                match_id=match_id,
                market=spec["market"],
                selection=selection,
                model_probability=top_prob,
                model_version=model_version,
                picked_at=datetime.now(timezone.utc),
                kickoff_at=kickoff_at,
            )
            if inserted:
                inserts += 1

    console.print(
        f"[green]publish_daily_picks: window={window_hours}h, "
        f"{len(by_match)} match×model pairs, {inserts} new rows[/green]"
    )
    return inserts


def _insert_pick(
    *,
    match_id: str,
    market: str,
    selection: str,
    model_probability: float,
    model_version: str,
    picked_at: datetime,
    kickoff_at: datetime,
) -> bool:
    """Insert a published pick row. Returns True if a new row was inserted,
    False if the row already existed (UNIQUE constraint kicked in).
    """
    try:
        result = execute_write(
            """
            INSERT INTO published_picks
                (match_id, market, selection, model_probability,
                 model_version, picked_at, kickoff_at, is_backfilled)
            VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE)
            ON CONFLICT (match_id, market, model_version) DO NOTHING
            """,
            (
                match_id, market, selection, model_probability,
                model_version, picked_at, kickoff_at,
            ),
        )
        # ON CONFLICT DO NOTHING returns affected rows = 0 if collision,
        # 1 if inserted. execute_write returns the rowcount.
        return result > 0
    except Exception as e:
        console.print(f"[yellow]publish_daily_picks: insert failed for "
                      f"{match_id} {market}: {e}[/yellow]")
        return False


def settle_published_picks_for_matches(match_ids: list[str]) -> int:
    """Mark `outcome` on every published_picks row for the given match IDs,
    based on the actual final score.

    Called from settle_finished_matches() in workers/jobs/settlement.py
    so settlement of published_picks happens on the same cadence as the
    paper-bet settlement.

    Returns the number of rows updated. Idempotent — re-running on an
    already-settled match is a no-op because the WHERE clause filters
    outcome IS NULL.
    """
    if not match_ids:
        return 0

    # Pull finished match data + every still-pending pick on those matches
    rows = execute_query(
        """
        SELECT
            pp.id            AS pick_id,
            pp.market        AS market,
            pp.selection     AS selection,
            m.score_home     AS sh,
            m.score_away     AS sa,
            m.status         AS match_status
        FROM published_picks pp
        JOIN matches m ON m.id = pp.match_id
        WHERE pp.match_id = ANY(%s::uuid[])
          AND pp.outcome IS NULL
        """,
        [match_ids],
    )
    if not rows:
        return 0

    updates: list[tuple[str, str]] = []  # (outcome, pick_id)
    for r in rows:
        sh, sa = r["sh"], r["sa"]
        status = r["match_status"]
        # Void picks on cancelled / postponed / suspended matches — the
        # outcome question doesn't apply
        if status in ("cancelled", "postponed", "suspended", "abandoned"):
            updates.append(("void", r["pick_id"]))
            continue
        if sh is None or sa is None:
            # Match marked finished but score is missing — skip; we'll
            # try again on the next settlement sweep when scores land
            continue
        hit = _is_hit(r["market"], r["selection"], sh, sa)
        updates.append(("hit" if hit else "miss", r["pick_id"]))

    if not updates:
        return 0

    # Bulk update
    n = 0
    for outcome, pick_id in updates:
        try:
            execute_write(
                """
                UPDATE published_picks
                SET outcome = %s, settled_at = NOW()
                WHERE id = %s AND outcome IS NULL
                """,
                (outcome, pick_id),
            )
            n += 1
        except Exception as e:
            console.print(f"[yellow]settle_published_picks: update failed for "
                          f"pick {pick_id}: {e}[/yellow]")
    console.print(
        f"[cyan]settle_published_picks: marked {n} picks "
        f"across {len(match_ids)} match(es)[/cyan]"
    )
    return n


def _is_hit(market: str, selection: str, score_home: int, score_away: int) -> bool:
    """Returns True if the picked selection occurred given the final score.
    Pure outcome check — does NOT consider odds, edge, or staking.
    """
    total = score_home + score_away
    if market == "1x2":
        if selection == "home":
            return score_home > score_away
        if selection == "draw":
            return score_home == score_away
        if selection == "away":
            return score_home < score_away
    elif market == "over_under_15":
        if selection == "over":
            return total >= 2
        if selection == "under":
            return total < 2
    elif market == "over_under_25":
        if selection == "over":
            return total >= 3
        if selection == "under":
            return total < 3
    elif market == "btts":
        both_scored = score_home >= 1 and score_away >= 1
        if selection == "yes":
            return both_scored
        if selection == "no":
            return not both_scored
    # Unknown market/selection combo — treat as miss (defensive; logs the issue
    # via the wrapping settle function's outcome write)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--window-hours", type=int, default=24,
        help="Publish picks for matches kicking off within this many hours (default 24)",
    )
    args = parser.parse_args()
    n = run_publish_daily_picks(window_hours=args.window_hours)
    return 0 if n >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
