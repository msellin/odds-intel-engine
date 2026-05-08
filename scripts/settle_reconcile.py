"""
OddsIntel — Settlement Reconciliation (MONEY-SETTLE-RECON)

After the 21:00 UTC settlement job runs, checks that all finished matches
from the current settlement window have their bets settled (no stuck 'pending').

A match is considered "drift" if:
  - Its status = 'finished'
  - It has 1+ simulated_bets still in result = 'pending'

Threshold: alert if stale_pending > 2 (allows for genuine edge cases —
  odd timezone-boundary matches, matches that started during a deploy window).

Runs daily at 21:30 UTC (after the 21:00 settlement job has had time to complete).

Usage:
    venv/bin/python scripts/settle_reconcile.py              # today+yesterday
    venv/bin/python scripts/settle_reconcile.py --date 2026-05-07  # specific date
    venv/bin/python scripts/settle_reconcile.py --dry-run    # print only, no email
"""

import os
import sys
import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("DIGEST_FROM_EMAIL", "OddsIntel <digest@oddsintel.app>")
ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL", "")

DRIFT_THRESHOLD = 2  # alert if more than this many matches have unsettled pending bets


def fetch_stale_pending(target_date: date) -> list[dict]:
    """
    Return matches that are 'finished' but still have pending bets.
    Looks at target_date and the day before (settlement window).
    """
    from workers.api_clients.db import execute_query

    yesterday = (target_date - timedelta(days=1)).isoformat()
    today = target_date.isoformat()

    rows = execute_query("""
        SELECT
            m.id,
            m.date,
            m.status,
            m.settlement_status,
            COUNT(sb.id) AS pending_count,
            MIN(ht.name)  AS home_team,
            MIN(ta.name)  AS away_team
        FROM matches m
        JOIN simulated_bets sb ON sb.match_id = m.id AND sb.result = 'pending'
        LEFT JOIN teams ht ON m.home_team_id = ht.id
        LEFT JOIN teams ta ON m.away_team_id = ta.id
        WHERE m.status = 'finished'
          AND m.date >= %s
          AND m.date <= %s
        GROUP BY m.id, m.date, m.status, m.settlement_status
        ORDER BY m.date
    """, [f"{yesterday}T00:00:00", f"{today}T23:59:59"])

    return rows


def fetch_settlement_summary(target_date: date) -> dict:
    """Return high-level settlement stats for the given date."""
    from workers.api_clients.db import execute_query

    yesterday = (target_date - timedelta(days=1)).isoformat()
    today = target_date.isoformat()

    totals = execute_query("""
        SELECT
            COUNT(*) FILTER (WHERE m.status = 'finished')                AS finished_matches,
            COUNT(*) FILTER (WHERE m.settlement_status = 'done')         AS settled_done,
            COUNT(*) FILTER (WHERE m.status = 'finished'
                              AND m.settlement_status IS DISTINCT FROM 'done') AS unsettled_matches,
            COUNT(sb.id) FILTER (WHERE sb.result = 'pending')            AS total_pending_bets,
            COUNT(sb.id) FILTER (WHERE sb.result != 'pending')           AS total_settled_bets
        FROM matches m
        LEFT JOIN simulated_bets sb ON sb.match_id = m.id
        WHERE m.date >= %s AND m.date <= %s
    """, [f"{yesterday}T00:00:00", f"{today}T23:59:59"])

    return totals[0] if totals else {}


def send_alert(subject: str, html: str, dry_run: bool) -> None:
    if dry_run:
        print(f"\n[DRY RUN] Would send: {subject}")
        return
    if not RESEND_API_KEY or not ADMIN_ALERT_EMAIL:
        print(f"[ALERT — no email configured] {subject}")
        return
    import httpx
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={"from": FROM_EMAIL, "to": [ADMIN_ALERT_EMAIL], "subject": subject, "html": html},
        timeout=15,
    )
    print(f"Alert sent ({resp.status_code}): {subject}")


def run(target_date: date = None, dry_run: bool = False) -> bool:
    """
    Run settlement reconciliation for target_date (default: today).
    Returns True if clean (no drift), False if drift found.
    """
    if target_date is None:
        target_date = date.today()

    print(f"\nSettlement reconciliation for {target_date.isoformat()}")
    print("─" * 50)

    summary = fetch_settlement_summary(target_date)
    stale = fetch_stale_pending(target_date)

    finished = int(summary.get("finished_matches") or 0)
    settled_done = int(summary.get("settled_done") or 0)
    unsettled = int(summary.get("unsettled_matches") or 0)
    total_pending = int(summary.get("total_pending_bets") or 0)
    total_settled = int(summary.get("total_settled_bets") or 0)

    print(f"Finished matches:         {finished}")
    print(f"  settlement_status=done: {settled_done}")
    print(f"  unsettled:              {unsettled}")
    print(f"Pending bets remaining:   {total_pending}")
    print(f"Settled bets:             {total_settled}")
    print(f"Matches with stuck bets:  {len(stale)}")

    if not stale:
        print("✅ Clean — all finished matches settled")
        return True

    if len(stale) <= DRIFT_THRESHOLD:
        print(f"⚠️  {len(stale)} match(es) with pending bets — within acceptable threshold ({DRIFT_THRESHOLD})")
        for r in stale:
            print(f"  {r['home_team']} vs {r['away_team']} — {r['pending_count']} pending bet(s) "
                  f"(settlement_status={r['settlement_status']})")
        return True

    # Drift exceeds threshold — send alert
    print(f"❌ {len(stale)} matches with unsettled pending bets — sending alert")

    lines = [
        f"<h2>Settlement drift: {target_date.isoformat()}</h2>",
        f"<p><strong>{len(stale)} finished matches</strong> still have pending bets "
        f"after the settlement job ran.</p>",
        "<p>Check Railway logs around 21:00 UTC for settlement errors.</p>",
        "<ul>",
    ]
    for r in stale:
        match_date = str(r.get("date", "?"))[:16]
        lines.append(
            f"<li><strong>{r['home_team']} vs {r['away_team']}</strong> ({match_date}) — "
            f"{r['pending_count']} pending bet(s), settlement_status={r['settlement_status']}</li>"
        )
    lines.append("</ul>")
    lines.append(
        "<p><strong>Action:</strong> Run <code>venv/bin/python -m workers.jobs.settlement settle_ready</code> "
        "manually, or trigger settlement via Railway deploy.</p>"
    )
    lines.append(f"<p>Summary: {finished} finished matches | {total_pending} total pending bets | "
                 f"{total_settled} settled bets</p>")

    subject = f"[OddsIntel] Settlement drift {target_date.isoformat()} — {len(stale)} unsettled"
    send_alert(subject, "".join(lines), dry_run)

    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Date to check (YYYY-MM-DD), default=today")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else None
    ok = run(target, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)
