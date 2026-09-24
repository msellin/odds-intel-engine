"""Snapshot the /admin/bots data into a JSON file for LOCAL design work (#139).

The web app's /admin/bots reads the admin-only views bot_scoreboard / bot_config /
bot_capabilities / bot_ledger with the service-role key, which a laptop does not hold.
This script reads the same rows over the engine's direct DB connection and writes them
to odds-intel-web/.dev-fixtures/bot-board.json (gitignored). `next dev` started with
BOT_BOARD_FIXTURE pointing at that file renders the page from it, without the superadmin
check (src/lib/bot-board.ts isBotBoardDevPreview — development builds only).

    python3 scripts/dump_bot_board_fixture.py            # default output path
    python3 scripts/dump_bot_board_fixture.py --out /tmp/x.json

`weekly` (the 12-week strip), `market_stats` and the ledger's home_team / away_team come from
migration 411's views bot_weekly / bot_market_stats / bot_ledger_display. They are computed here with the SAME SQL inline (below)
rather than read from the views, so the fixture works before 411 is applied. Keep the two in
step with supabase/migrations/411_bot_weekly_view.sql.
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


# Same body as the bot_weekly view (migration 411).
_WEEKLY_SQL = """
WITH cur AS (
    SELECT DISTINCT ON (bot_name) bot_name, rule_version
      FROM public.bot_ledger
     WHERE source = 'forward_test'
     ORDER BY bot_name, pick_time DESC
)
SELECT l.bot_name,
       date_trunc('week', l.pick_time)                                                      AS week,
       count(*)                                                                             AS picks,
       count(*) FILTER (WHERE l.result IN ('won', 'lost'))                                  AS settled,
       count(l.clv_mc)       FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)       AS clv_mc_n,
       avg(l.clv_mc)         FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)       AS clv_mc_mean,
       count(l.clv_pinnacle) FILTER (WHERE l.result <> 'void' AND abs(l.clv_pinnacle) <= 1) AS clv_pin_n,
       avg(l.clv_pinnacle)   FILTER (WHERE l.result <> 'void' AND abs(l.clv_pinnacle) <= 1) AS clv_pin_mean,
       sum(l.pnl_unit)                                                                      AS pnl_unit
  FROM public.bot_ledger l
  LEFT JOIN cur c ON c.bot_name = l.bot_name
 WHERE l.pick_time >= date_trunc('week', now()) - interval '11 weeks'
   AND (l.source <> 'forward_test' OR l.rule_version = c.rule_version)
 GROUP BY l.bot_name, date_trunc('week', l.pick_time)
 ORDER BY 1, 2
"""


# Same body as the bot_market_stats view (migration 411).
_MARKET_STATS_SQL = """
WITH cur AS (
    SELECT DISTINCT ON (bot_name) bot_name, rule_version
      FROM public.bot_ledger
     WHERE source = 'forward_test'
     ORDER BY bot_name, pick_time DESC
)
SELECT l.bot_name,
       l.market,
       count(*) FILTER (WHERE l.result IN ('won', 'lost'))                                  AS settled,
       count(*) FILTER (WHERE l.result = 'won')                                             AS won,
       count(*) FILTER (WHERE l.result IN ('won', 'lost') AND l.odds > 1)                   AS odds_n,
       sum(1 / l.odds) FILTER (WHERE l.result IN ('won', 'lost') AND l.odds > 1)            AS sum_inv_odds,
       count(l.clv_mc)       FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)       AS clv_mc_n,
       avg(l.clv_mc)         FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)       AS clv_mc_mean,
       stddev_samp(l.clv_mc) FILTER (WHERE l.result <> 'void' AND abs(l.clv_mc) <= 1)       AS clv_mc_sd
  FROM public.bot_ledger l
  LEFT JOIN cur c ON c.bot_name = l.bot_name
 WHERE (l.source <> 'forward_test' OR l.rule_version = c.rule_version)
 GROUP BY l.bot_name, l.market
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(_WEB / ".dev-fixtures" / "bot-board.json"))
    ap.add_argument("--ledger-per-bot", type=int, default=30)
    a = ap.parse_args()
    ledger = {}
    for r in _rows(
        """SELECT * FROM (
             SELECT l.*, ht.name AS home_team, at.name AS away_team,
                    row_number() OVER (PARTITION BY l.bot_name ORDER BY l.pick_time DESC) rn
               FROM bot_ledger l
               LEFT JOIN matches m ON m.id = l.match_id
               LEFT JOIN teams ht  ON ht.id = m.home_team_id
               LEFT JOIN teams at  ON at.id = m.away_team_id) x
            WHERE rn <= %s""",
        [a.ledger_per_bot],
    ):
        r.pop("rn", None)
        ledger.setdefault(r["bot_name"], []).append(r)
    for rows in ledger.values():
        rows.sort(key=lambda r: r["pick_time"] or "", reverse=True)
    weekly = {}
    for r in _rows(_WEEKLY_SQL):
        weekly.setdefault(r["bot_name"], []).append(r)
    out = {
        "scoreboard": _rows("SELECT * FROM bot_scoreboard"),
        "config": _rows("SELECT * FROM bot_config"),
        "capabilities": _rows("SELECT * FROM bot_capabilities"),
        "retired": _rows("SELECT name, retired_at, retired_reason FROM bots WHERE retired_at IS NOT NULL"),
        "ledger": ledger,
        "weekly": weekly,
        "market_stats": _rows(_MARKET_STATS_SQL),
    }
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out))
    print(f"wrote {p} — {len(out['scoreboard'])} bots, {sum(map(len, ledger.values()))} ledger rows, "
          f"{sum(map(len, weekly.values()))} weekly rows")


if __name__ == "__main__":
    main()
