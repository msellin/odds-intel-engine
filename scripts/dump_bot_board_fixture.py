"""Snapshot the /admin/bots data into a JSON file for LOCAL design work (#139).

The web app's /admin/bots reads the admin-only views bot_scoreboard / bot_config /
bot_capabilities / bot_ledger with the service-role key, which a laptop does not hold.
This script reads the same rows over the engine's direct DB connection and writes them
to odds-intel-web/.dev-fixtures/bot-board.json (gitignored). `next dev` started with
BOT_BOARD_FIXTURE pointing at that file renders the page from it, without the superadmin
check (src/lib/bot-board.ts isBotBoardDevPreview — development builds only).

    python3 scripts/dump_bot_board_fixture.py            # default output path
    python3 scripts/dump_bot_board_fixture.py --out /tmp/x.json

`overview` holds the non-bot reads of the /admin Overview (feeds, jobs, DQ, real bets per week).
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


# Same body as the pipeline_job_latest view (migration 417).
_JOB_LATEST_SQL = """
WITH r AS (
    SELECT job_name, status, started_at, error_message,
           max(started_at) FILTER (WHERE status = 'completed') OVER (PARTITION BY job_name) AS last_ok_at,
           row_number() OVER (PARTITION BY job_name ORDER BY started_at DESC)             AS rn
      FROM public.pipeline_runs
     WHERE started_at > now() - interval '35 days'
       AND job_name NOT IN ('hist_backfill', 'backfill_coaches', 'backfill_transfers')
), f AS (
    SELECT job_name,
           -- distinct minutes, not rows: some jobs write two rows per run (prune_anon_users)
           count(DISTINCT date_trunc('minute', started_at)) FILTER (WHERE status = 'failed' AND started_at > coalesce(last_ok_at, '-infinity')) AS fail_streak,
           min(started_at) FILTER (WHERE status = 'failed' AND started_at > coalesce(last_ok_at, '-infinity')) AS failing_since
      FROM r
     GROUP BY job_name
)
SELECT r.job_name, r.status, r.started_at, r.error_message, r.last_ok_at, f.fail_streak, f.failing_since
  FROM r JOIN f USING (job_name)
 WHERE r.rn = 1
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(_WEB / ".dev-fixtures" / "bot-board.json"))
    # 150 (was 30): the sheet's Picks tab pages through the ledger 50 at a time (IA move P7).
    ap.add_argument("--ledger-per-bot", type=int, default=150)
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
    # IA move P7 (retiring /admin/shadow-bots/[bot]): the sheet's Picks tab shows "Bet made"
    # (real_bets, placed_real IS NOT FALSE -- TRUE = money moved, NULL = legacy reconciled) and
    # the current price at our three books for PENDING pre-match picks. Same reads as
    # src/lib/bot-board.ts loadBotPicks(), in the same shapes.
    placed = {}
    for r in _rows(
        """SELECT b.name AS bot_name, r.match_id, r.market, r.selection, r.actual_odds, r.bookmaker,
                  r.stake, r.placed_real, r.result, r.pnl, r.shadow_bet_id, r.simulated_bet_id, r.placed_at
             FROM real_bets r JOIN bots b ON b.id = r.bot_id
            WHERE r.placed_real IS NOT FALSE"""
    ):
        placed.setdefault(r.pop("bot_name"), []).append(r)
    pend = [r for rows in ledger.values() for r in rows
            if r.get("result") == "pending" and not r.get("is_inplay") and r.get("match_id")]
    prices = []
    if pend:
        prices = _rows(
            """SELECT DISTINCT ON (match_id, lower(market), lower(selection), bookmaker)
                      match_id, market, selection, bookmaker, odds, "timestamp"
                 FROM odds_snapshots
                WHERE match_id = ANY(%s::uuid[]) AND lower(market) = ANY(%s)
                  AND bookmaker IN ('Coolbet', 'Unibet-Site', 'Epicbet')
                  AND is_live = false AND "timestamp" >= now() - interval '12 hours'
                ORDER BY match_id, lower(market), lower(selection), bookmaker, "timestamp" DESC""",
            [sorted({r["match_id"] for r in pend}), sorted({(r["market"] or "").lower() for r in pend})],
        )
    weekly = {}
    for r in _rows(_WEEKLY_SQL):
        weekly.setdefault(r["bot_name"], []).append(r)
    out = {
        "scoreboard": _rows("SELECT * FROM bot_scoreboard"),
        "config": _rows("SELECT * FROM bot_config"),
        "capabilities": _rows("SELECT * FROM bot_capabilities"),
        "retired": _rows("SELECT name, retired_at, retired_reason FROM bots WHERE retired_at IS NOT NULL"),
        "ledger": ledger,
        "placed": placed,
        "prices": prices,
        "weekly": weekly,
        "market_stats": _rows(_MARKET_STATS_SQL),
        # /admin Overview (#139, 2026-09-24): the non-bot reads src/lib/admin-overview.ts makes,
        # in the same shapes (feed_status rows, latest pipeline run per job, counts).
        "overview": {
            "feeds": _rows("SELECT * FROM feed_status"),
            # Same body as view pipeline_job_latest (migration 417), inline so it works pre-deploy.
            "jobs": _rows(_JOB_LATEST_SQL),
            "stale_pending": _rows(
                """SELECT count(*) AS n FROM simulated_bets b JOIN matches m ON m.id = b.match_id
                    WHERE b.result = 'pending' AND m.date < now() - interval '150 minutes'""")[0]["n"],
            "dq_24h": _rows(
                """SELECT check_name, count(*) AS n FROM data_quality_findings
                    WHERE found_at > now() - interval '24 hours' GROUP BY 1"""),
            "unconfirmed_manual": _rows(
                """SELECT count(*) AS n FROM real_bets
                    WHERE placed_real IS NULL AND placed_at >= '2026-09-10'
                      AND placed_at < now() - interval '24 hours'""")[0]["n"],
            "real_bets_weekly": _rows(
                """SELECT to_char(date_trunc('week', placed_at), 'YYYY-MM-DD') AS week, count(*) AS bets,
                          coalesce(sum(stake), 0) AS staked,
                          coalesce(sum(pnl) FILTER (WHERE result IS NOT NULL AND result <> 'pending'), 0) AS pnl
                     FROM real_bets
                    WHERE placed_real IS DISTINCT FROM false
                      AND placed_at >= date_trunc('week', now()) - interval '11 weeks'
                    GROUP BY 1 ORDER BY 1"""),
        },
    }
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out))
    print(f"wrote {p} — {len(out['scoreboard'])} bots, {sum(map(len, ledger.values()))} ledger rows, "
          f"{sum(map(len, weekly.values()))} weekly rows, {sum(map(len, placed.values()))} placed bets, "
          f"{len(prices)} current prices")


if __name__ == "__main__":
    main()
