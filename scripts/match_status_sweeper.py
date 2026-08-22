"""
MATCH-STATUS-SWEEPER — standalone catch-up for matches stuck at
`status IN ('scheduled', 'live')` past kickoff.

Runs independently of workers/scheduler.py. The scheduler-based
`fix_stale_live_matches()` in workers/jobs/settlement.py does the same
job every 15 min, but when the systemd scheduler stalls (as it did
2026-08-22, ~9h without a status refresh, leaving 439 matches stuck),
nothing else picks up the slack. This script is called from the
match_status_sweeper.yml GitHub Actions cron every 30 min so a
scheduler outage no longer poisons the shadow ledger.

Filter:
  * status IN ('scheduled', 'live')
  * date < NOW() - 95 minutes  (min match length + buffer)
  * date > NOW() - 3 days      (older matches are almost certainly
                                 abandoned upstream — hitting AF again
                                 wastes quota; the daily nightly
                                 settlement catches those separately)
  * api_football_id IS NOT NULL

For each stuck fixture:
  * FT/AET/PEN/ABD/WO with scores → mark finished + trigger settlement
    downstream (bet result columns transition when settlement job runs).
  * PST/CANC/SUSP/AWD/INT           → mark postponed AND void every
                                       pending simulated_bets + shadow_bets
                                       on the match. The scheduler's
                                       version voids simulated_bets only —
                                       shadow_bets on postponed fixtures
                                       have been leaking pending for weeks
                                       (~64 rows as of 2026-08-22).
  * Anything else (NS, 1H, HT, 2H, ET, BT, LIVE) → leave alone.

Batch-fetches via api_football.get_fixtures_batch (20 per call) so the
quota impact stays flat regardless of backlog size.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from workers.api_clients.db import execute_query, execute_write
from workers.api_clients.api_football import get_fixtures_batch


STALE_MINUTES = 95
LOOKBACK_DAYS = 3


def _load_stuck_matches() -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff_recent = now - timedelta(minutes=STALE_MINUTES)
    cutoff_far = now - timedelta(days=LOOKBACK_DAYS)
    return execute_query(
        """SELECT m.id, m.api_football_id, m.status::text AS status_text, m.date
           FROM matches m
           WHERE m.status IN ('scheduled', 'live')
             AND m.date < %s
             AND m.date > %s
             AND m.api_football_id IS NOT NULL
           ORDER BY m.date DESC""",
        (cutoff_recent.isoformat(), cutoff_far.isoformat()),
    )


def _mark_finished(match_id: str, home_goals: int, away_goals: int) -> None:
    result = (
        "home" if home_goals > away_goals
        else "away" if away_goals > home_goals
        else "draw"
    )
    execute_write(
        """UPDATE matches
              SET status = 'finished',
                  score_home = %s,
                  score_away = %s,
                  result = %s,
                  settlement_status = 'ready'
            WHERE id = %s
              AND status != 'finished'""",
        (home_goals, away_goals, result, match_id),
    )


def _mark_postponed_and_void(match_id: str) -> tuple[int, int]:
    execute_write(
        "UPDATE matches SET status = 'postponed' WHERE id = %s AND status != 'postponed'",
        (match_id,),
    )
    voided_sim = execute_write(
        """UPDATE simulated_bets
              SET result = 'void', pnl = 0
            WHERE match_id = %s AND result = 'pending'""",
        (match_id,),
    ) or 0
    voided_shadow = execute_write(
        """UPDATE shadow_bets
              SET result = 'void', pnl = 0
            WHERE match_id = %s AND result = 'pending'""",
        (match_id,),
    ) or 0
    return voided_sim, voided_shadow


def _record_health_ping(fixed: int, postponed: int, seen: int) -> None:
    """Emit a heartbeat row into pipeline_health_state so silence is visible."""
    reason = f"seen={seen} fixed={fixed} postponed={postponed}"
    execute_write(
        """INSERT INTO pipeline_health_state (pipeline_name, last_alert_at, last_alert_reason, updated_at)
           VALUES ('match_status_sweeper', NULL, %s, NOW())
           ON CONFLICT (pipeline_name)
           DO UPDATE SET last_alert_reason = EXCLUDED.last_alert_reason,
                         updated_at = NOW()""",
        (reason,),
    )


def sweep() -> dict:
    stuck = _load_stuck_matches()
    if not stuck:
        print("no stuck matches — nothing to do")
        _record_health_ping(0, 0, 0)
        return {"seen": 0, "fixed": 0, "postponed": 0, "voided_sim": 0, "voided_shadow": 0}

    print(f"sweeping {len(stuck)} stuck match(es)")

    af_ids = [int(m["api_football_id"]) for m in stuck]
    fixtures = get_fixtures_batch(af_ids)
    print(f"AF returned {len(fixtures)} / {len(af_ids)} requested")

    fixed = postponed = voided_sim = voided_shadow = 0
    for m in stuck:
        af_id = int(m["api_football_id"])
        fixture = fixtures.get(af_id)
        if not fixture:
            continue
        try:
            status_short = (
                fixture.get("fixture", {}).get("status", {}).get("short", "")
            )
            if status_short in ("FT", "AET", "PEN", "ABD", "WO"):
                goals = fixture.get("goals", {})
                home_goals = goals.get("home")
                away_goals = goals.get("away")
                if home_goals is None or away_goals is None:
                    if status_short in ("ABD", "WO"):
                        home_goals, away_goals = 0, 0
                    else:
                        continue
                _mark_finished(m["id"], int(home_goals), int(away_goals))
                fixed += 1
                print(
                    f"  finished {m['id']} af={af_id} "
                    f"{status_short} {home_goals}-{away_goals}"
                )
            elif status_short in ("PST", "CANC", "SUSP", "AWD", "INT"):
                v_sim, v_shadow = _mark_postponed_and_void(m["id"])
                postponed += 1
                voided_sim += v_sim
                voided_shadow += v_shadow
                print(
                    f"  postponed {m['id']} af={af_id} "
                    f"{status_short} · voided sim={v_sim} shadow={v_shadow}"
                )
        except Exception as e:
            print(f"  ERROR match={m['id']} af={af_id}: {e}")

    print(
        f"summary: seen={len(stuck)} fixed={fixed} postponed={postponed} "
        f"voided_sim={voided_sim} voided_shadow={voided_shadow}"
    )
    _record_health_ping(fixed, postponed, len(stuck))
    return {
        "seen": len(stuck),
        "fixed": fixed,
        "postponed": postponed,
        "voided_sim": voided_sim,
        "voided_shadow": voided_shadow,
    }


if __name__ == "__main__":
    sweep()
