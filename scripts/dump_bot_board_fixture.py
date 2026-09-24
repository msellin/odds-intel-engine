"""Snapshot the /admin/bots data into a JSON file for LOCAL design work (#139).

The web app's /admin/bots reads the admin-only views bot_scoreboard / bot_config /
bot_capabilities / bot_ledger with the service-role key, which a laptop does not hold.
This script reads the same rows over the engine's direct DB connection and writes them
to odds-intel-web/.dev-fixtures/bot-board.json (gitignored). `next dev` started with
BOT_BOARD_FIXTURE pointing at that file renders the page from it, without the superadmin
check (src/lib/bot-board.ts isBotBoardDevPreview — development builds only).

    python3 scripts/dump_bot_board_fixture.py            # default output path
    python3 scripts/dump_bot_board_fixture.py --out /tmp/x.json
"""
import argparse
import datetime as dt
import decimal
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402

_WEB = Path(__file__).resolve().parent.parent.parent / "odds-intel-web"


def _plain(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v if v is None or isinstance(v, (int, float, str, bool)) else str(v)


def _rows(sql, params=None):
    return [{k: _plain(v) for k, v in r.items()} for r in execute_query(sql, params)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(_WEB / ".dev-fixtures" / "bot-board.json"))
    ap.add_argument("--ledger-per-bot", type=int, default=30)
    a = ap.parse_args()
    ledger = {}
    for r in _rows(
        """SELECT * FROM (
             SELECT l.*, row_number() OVER (PARTITION BY bot_name ORDER BY pick_time DESC) rn
               FROM bot_ledger l) x
            WHERE rn <= %s""",
        [a.ledger_per_bot],
    ):
        r.pop("rn", None)
        ledger.setdefault(r["bot_name"], []).append(r)
    for rows in ledger.values():
        rows.sort(key=lambda r: r["pick_time"] or "", reverse=True)
    out = {
        "scoreboard": _rows("SELECT * FROM bot_scoreboard"),
        "config": _rows("SELECT * FROM bot_config"),
        "capabilities": _rows("SELECT * FROM bot_capabilities"),
        "retired": _rows("SELECT name, retired_at, retired_reason FROM bots WHERE retired_at IS NOT NULL"),
        "ledger": ledger,
    }
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out))
    print(f"wrote {p} — {len(out['scoreboard'])} bots, {sum(map(len, ledger.values()))} ledger rows")


if __name__ == "__main__":
    main()
