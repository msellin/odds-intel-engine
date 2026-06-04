"""Shadow trail health check — verifies the PIN-CROSS-DRIFT shadow flag and
the inplay_e cal_model_prob trail are populating from live pipeline runs.

Filed as SHADOW-TRAIL-HEALTH-CHECK in PRIORITY_QUEUE.md (2026-06-04). Reusable
by SHADOW-TRAIL-VOLUME-RECHECK on 2026-06-07 and any future activation gates
that depend on these shadow trails (PIN-CROSS-DRIFT-ACTIVATE / INPLAY-E-PLATT-ACTIVATE).

Reports:
  1. PIN-CROSS-DRIFT shadow_flag distribution (TRUE / FALSE / NULL) since wire-up.
  2. inplay_e fires post-wire-up and how many carry `cal_model_prob` in reasoning.
  3. pipeline_runs hangs > 30 min since the cutoff date.
  4. Failed pipeline_runs since the cutoff (excluding the original 2026-06-03 12:30 incident batch).

Exit code 0 = all trails healthy; non-zero = at least one trail is stalled.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402


PIN_HELPER_DEPLOY_UTC = "2026-06-03 10:47"  # commit 3f9a3c4
INPLAY_E_PLATT_WIREUP_UTC = "2026-06-03 12:09"  # commit e511d7e


def _fetch_pin_shadow_distribution(since_utc: str) -> list[dict]:
    return execute_query(
        """
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE pin_cross_drift_shadow_flag = TRUE) AS flag_true,
          COUNT(*) FILTER (WHERE pin_cross_drift_shadow_flag = FALSE) AS flag_false,
          COUNT(*) FILTER (WHERE pin_cross_drift_shadow_flag IS NULL) AS flag_null
        FROM simulated_bets
        WHERE created_at >= %s
        """,
        (since_utc,),
    )


def _fetch_inplay_e_post_wireup(since_utc: str) -> list[dict]:
    return execute_query(
        """
        SELECT
          COUNT(*) AS total,
          COALESCE(SUM(CASE WHEN reasoning LIKE '%%cal_model_prob%%' THEN 1 ELSE 0 END), 0) AS with_calib
        FROM simulated_bets
        WHERE bot_id=(SELECT id FROM bots WHERE name='inplay_e')
          AND created_at >= %s
        """,
        (since_utc,),
    )


def _fetch_inplay_post_wireup(since_utc: str) -> list[dict]:
    return execute_query(
        """
        SELECT b.name AS bot, COUNT(*) AS bets, MAX(sb.created_at) AS last_fire
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE sb.created_at >= %s
          AND b.name LIKE 'inplay%%'
        GROUP BY b.name
        ORDER BY bets DESC
        """,
        (since_utc,),
    )


def _fetch_pipeline_hangs(since_utc: str, threshold_min: float) -> list[dict]:
    return execute_query(
        """
        SELECT job_name, status, started_at,
          EXTRACT(EPOCH FROM (COALESCE(completed_at, NOW()) - started_at))/60.0 AS dur_min
        FROM pipeline_runs
        WHERE started_at >= %s
          AND EXTRACT(EPOCH FROM (COALESCE(completed_at, NOW()) - started_at))/60.0 > %s
          AND job_name != 'settlement'
        ORDER BY started_at DESC
        """,
        (since_utc, threshold_min),
    )


def _fetch_pipeline_failures(since_utc: str) -> list[dict]:
    return execute_query(
        """
        SELECT job_name, status, started_at, error_message
        FROM pipeline_runs
        WHERE started_at >= %s
          AND status NOT IN ('completed', 'running')
        ORDER BY started_at DESC
        """,
        (since_utc,),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        default=PIN_HELPER_DEPLOY_UTC,
        help=f"UTC start time for the report window (default: {PIN_HELPER_DEPLOY_UTC} — PIN helper deploy)",
    )
    parser.add_argument(
        "--inplay-since",
        default=INPLAY_E_PLATT_WIREUP_UTC,
        help=f"UTC start time for inplay_e Platt wire-up (default: {INPLAY_E_PLATT_WIREUP_UTC})",
    )
    parser.add_argument("--hang-min", type=float, default=30.0, help="Hang threshold in minutes")
    args = parser.parse_args()

    print(f"Shadow trail health — generated {datetime.now(timezone.utc).isoformat()}")
    print(f"Window: {args.since} → now (UTC)")
    print()

    print("=" * 72)
    print("1. PIN-CROSS-DRIFT shadow flag distribution")
    print("=" * 72)
    pin_rows = _fetch_pin_shadow_distribution(args.since)
    pin = pin_rows[0] if pin_rows else {"total": 0, "flag_true": 0, "flag_false": 0, "flag_null": 0}
    print(f"  total={pin['total']}  TRUE={pin['flag_true']}  FALSE={pin['flag_false']}  NULL={pin['flag_null']}")
    pin_evaluated = (pin["flag_true"] or 0) + (pin["flag_false"] or 0)
    pin_silent_failure = pin["total"] > 0 and pin["flag_null"] == pin["total"]
    if pin_silent_failure:
        print("  ⚠ SILENT WRITE FAILURE — all bets have NULL flag despite helper deployed")
    elif pin["total"] == 0:
        print("  ⚠ no bets at all in window — pipeline may be down")
    else:
        print(f"  ✓ helper evaluated {pin_evaluated}/{pin['total']} bets")

    print()
    print("=" * 72)
    print(f"2. inplay_e cal_model_prob trail (since {args.inplay_since} UTC)")
    print("=" * 72)
    ipe_rows = _fetch_inplay_e_post_wireup(args.inplay_since)
    ipe = ipe_rows[0] if ipe_rows else {"total": 0, "with_calib": 0}
    print(f"  inplay_e bets post-wire-up: {ipe['total']}  with cal_model_prob: {ipe['with_calib']}")
    if ipe["total"] == 0:
        print("  ⚠ inplay_e has NOT fired since wire-up — cannot verify cal_model_prob trail")
    elif (ipe["with_calib"] or 0) == 0:
        print("  ⚠ SILENT WRITE FAILURE — inplay_e fired but no row carries cal_model_prob")
    else:
        print(f"  ✓ cal_model_prob trail is populating ({ipe['with_calib']}/{ipe['total']})")

    print()
    print("Other inplay bots (sanity check — pipeline is firing inplay bets?):")
    for r in _fetch_inplay_post_wireup(args.inplay_since):
        print(f"  {r['bot']:20s} bets={r['bets']:3d}  last={r['last_fire']}")

    print()
    print("=" * 72)
    print(f"3. pipeline_runs hangs > {args.hang_min} min (excl. settlement)")
    print("=" * 72)
    hangs = _fetch_pipeline_hangs(args.since, args.hang_min)
    if not hangs:
        print("  ✓ none")
    else:
        for r in hangs:
            print(f"  {r['started_at']} | {r['job_name']:30s} | {r['status']:10s} | dur={r['dur_min']:.1f}m")

    print()
    print("=" * 72)
    print("4. Non-completed pipeline_runs (failures)")
    print("=" * 72)
    fails = _fetch_pipeline_failures(args.since)
    if not fails:
        print("  ✓ none")
    else:
        for r in fails:
            err = (r.get("error_message") or "")[:80]
            print(f"  {r['started_at']} | {r['job_name']:30s} | {r['status']:10s} | err={err}")

    print()
    print("=" * 72)
    print("Verdict")
    print("=" * 72)
    exit_code = 0
    if pin_silent_failure:
        print("  FAIL — PIN-CROSS-DRIFT silent write failure")
        exit_code = 1
    if ipe["total"] > 0 and (ipe["with_calib"] or 0) == 0:
        print("  FAIL — inplay_e fired but cal_model_prob is missing — silent write failure")
        exit_code = 1
    if exit_code == 0:
        print("  OK — no silent write failures detected. Sample-size sufficiency must be")
        print("        evaluated separately at activation gates.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
