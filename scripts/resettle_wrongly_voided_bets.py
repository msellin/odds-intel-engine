#!/usr/bin/env python3
"""BET-VOID-INTEGRITY-2026-08-24 — one-off repair of wrongly-voided bets.

The engine now runs `resettle_wrongly_voided_bets()` inside the 15-min
`settle_ready` sweep, so this script exists for two things the cron cannot do:
take a CSV backup before the first repair, and let a human see the diff via
`--dry-run` before anything is written.

    python3 scripts/resettle_wrongly_voided_bets.py --dry-run
    python3 scripts/resettle_wrongly_voided_bets.py --apply

Backup lands in dev/active/ and is the only way back — `void_reason` is cleared
on repair, so a second run cannot re-identify what it changed.
"""
import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.jobs.settlement import resettle_wrongly_voided_bets  # noqa: E402

BACKUP_DIR = Path(__file__).resolve().parent.parent / "dev" / "active"

_BACKUP_SQL = """
SELECT sb.id, sb.bot_id, b.name AS bot_name, sb.match_id, sb.market, sb.selection,
       sb.stake, sb.odds_at_pick, sb.result::text AS result, sb.pnl, sb.closing_odds,
       sb.clv, sb.void_reason, m.status::text AS match_status,
       m.score_home, m.score_away, ht.name AS home_team, ta.name AS away_team
FROM {table} sb
JOIN bots b ON b.id = sb.bot_id
JOIN matches m ON m.id = sb.match_id
LEFT JOIN teams ht ON ht.id = m.home_team_id
LEFT JOIN teams ta ON ta.id = m.away_team_id
WHERE sb.result = 'void'
  AND sb.void_reason IS DISTINCT FROM 'quarantine'
  AND m.status = 'finished'
  AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
"""


def backup() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = BACKUP_DIR / f"void-integrity-backup-{stamp}.csv"
    rows = []
    for table in ("shadow_bets", "simulated_bets"):
        for r in execute_query(_BACKUP_SQL.format(table=table)) or []:
            rows.append({"source_table": table, **r})
    if not rows:
        print("nothing eligible — no backup written")
        return path
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"backed up {len(rows)} row(s) → {path}")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="show what would change")
    g.add_argument("--apply", action="store_true", help="write the repairs")
    args = ap.parse_args()

    if args.apply:
        backup()

    stats = resettle_wrongly_voided_bets(dry_run=args.dry_run)
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
