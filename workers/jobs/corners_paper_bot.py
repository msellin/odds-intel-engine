"""
CORNERS-PAPER-FORWARD-2026-09-07 — paper-forward validation of the corners
line-shop strategy, run as a proper SHADOW bot (bot_corners_paper_shadow_v1).

It writes to shadow_bets like every other experimental bot, so it tracks on
/admin/shadow-bots (index card + per-bot ledger + the "upcoming picks to place
real money on" panel) and is automatically kept OFF the public performance /
picks pages — those read simulated_bets / the calibrated cohort, never
shadow_bets. One ledger per bot (gotcha 18): shadow_bets, not a private table.

Strategy (from CORNERS-EDGE-TAIL / the NEW-MARKET audit): for an upcoming
fixture take the best price among the books we can actually place corners at
(Betano, Unibet) for a corners O/U selection and record a paper pick when that
price beats de-vigged Pinnacle fair value (edge = price * devig_p - 1 >= 0).
EUR 10 nominal. Settled from match_stats.corners_home + corners_away.

The generic goals-based shadow settler is taught to SKIP corners_ou_% markets
(settlement.py, _PENDING_SHADOW_BETS_SQL) — it grades on the goal score and
would silently VOID these. settle_picks() below grades them from corner counts.

Why paper, not real money: the historical +20.99% did NOT reproduce at
executable prices (audit z=+0.33..+3.62, edge Betano/Unibet-only, Epicbet
negative, no dose-response, one 9-day pre-collapse window). Prove it forward on
real executable prices first.

Subcommands: pick | settle | report. Never raises out of the scheduler wrappers.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from collections import defaultdict

log = logging.getLogger(__name__)

BOT_NAME = "bot_corners_paper_shadow_v1"
SHADOW_COHORT = "corners_paper"
PLACEMENT_BOOKS = ("Betano", "Unibet")   # the audit's edge books
STAKE_EUR = 10.0
EDGE_FLOOR = float(os.getenv("CORNERS_PAPER_EDGE_FLOOR", "0.0"))  # audit: 0 is the rule


def _decode_line(market: str) -> float | None:
    """corners_ou_105 -> 10.5 (0.5-stepped, encoded without the dot)."""
    m = re.match(r"^corners_ou_(\d+)$", market)
    if not m:
        return None
    return int(m.group(1)) / 10.0


def _devig_two_way(o_over: float, o_under: float) -> tuple[float, float]:
    """Proportional two-way de-vig -> (p_over, p_under)."""
    io, iu = 1.0 / o_over, 1.0 / o_under
    s = io + iu
    return io / s, iu / s


# CORNERS-SETTLEMENT-GATE (2026-09-10): AF publishes corner counts for only ~17-32%
# of finished fixtures — a hard ceiling that can't be grown (§56). Betting where a
# corners PRICE exists but no corner STAT ever arrives produces bets that can NEVER
# settle (6/73 settled before this), so the bot had no honest track record. Gate
# picks to leagues where corner stats reliably land, computed DYNAMICALLY on a
# trailing window so it self-maintains as coverage shifts. An unsettleable paper bet
# is worthless for measuring the market.
_SETTLEABLE_MIN_PCT = 80.0     # a league must settle >= this % of its finished games
_SETTLEABLE_MIN_FINISHED = 8   # ...over at least this many finished games
_SETTLEABLE_WINDOW_DAYS = 30


def _settleable_league_ids() -> list:
    """League ids whose finished games reliably carry AF corner stats (the only
    leagues where a corners paper bet can actually be graded). Empty list on any
    error → generate_picks then no-ops for the run (never bets blind)."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """
            SELECT m.league_id
              FROM matches m
              LEFT JOIN match_stats ms ON ms.match_id = m.id
             WHERE m.status = 'finished'
               AND m.date > now() - (%s || ' days')::interval
             GROUP BY m.league_id
            HAVING count(*) >= %s
               AND 100.0 * count(*) FILTER (WHERE ms.corners_home IS NOT NULL)
                       / count(*) >= %s
            """,
            [str(_SETTLEABLE_WINDOW_DAYS), _SETTLEABLE_MIN_FINISHED, _SETTLEABLE_MIN_PCT],
        )
        return [str(r["league_id"]) for r in rows if r.get("league_id") is not None]
    except Exception as e:  # noqa: BLE001
        log.warning("corners paper: _settleable_league_ids failed (%s) — betting nothing this run", e)
        return []


def _bot_id() -> str | None:
    from workers.api_clients.db import execute_query
    r = execute_query("SELECT id::text AS id FROM bots WHERE name=%s", [BOT_NAME])
    return r[0]["id"] if r else None


def generate_picks() -> dict:
    """Record paper picks (shadow_bets rows) for upcoming fixtures where the best
    Betano/Unibet corners price beats de-vigged Pinnacle. One row per
    (match, market, selection) via ON CONFLICT DO NOTHING. Never raises."""
    counters = {"scanned": 0, "picked": 0, "skipped_existing": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write

        bot_id = _bot_id()
        if not bot_id:
            log.warning("corners paper: bot %s not registered (migration 308 unapplied?)", BOT_NAME)
            return counters

        # latest price per (match, market, selection, bookmaker) for upcoming
        # CORNERS-SETTLEMENT-GATE: only bet leagues whose corner stats reliably land,
        # so every pick can be graded (see _settleable_league_ids). No settleable
        # leagues → bet nothing this run rather than place ungradeable paper.
        settleable = _settleable_league_ids()
        if not settleable:
            log.info("corners paper: no settleable leagues this run — 0 picks")
            return counters
        # fixtures with a corners O/U market, restricted to the books we need AND to
        # the corner-settleable leagues.
        rows = execute_query(
            """
            SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.bookmaker)
                   o.match_id::text AS match_id, o.market, o.selection,
                   o.bookmaker, o.odds, m.date AS kickoff
              FROM odds_snapshots o
              JOIN matches m ON m.id = o.match_id
             WHERE o.market ~ '^corners_ou_[0-9]+$'
               AND m.date > now()
               AND m.league_id::text = ANY(%s)
               AND o.bookmaker IN ('Betano','Unibet','Pinnacle')
               AND o.selection IN ('over','under')
             ORDER BY o.match_id, o.market, o.selection, o.bookmaker, o."timestamp" DESC
            """,
            [settleable],
        )
        # group by (match, market): {selection: {book: odds}}
        by_mkt: dict[tuple, dict] = defaultdict(lambda: {"over": {}, "under": {}})
        for r in rows:
            g = by_mkt[(r["match_id"], r["market"])]
            g[r["selection"]][r["bookmaker"]] = float(r["odds"])

        run_id = str(uuid.uuid4())
        picks = []
        for (match_id, market), g in by_mkt.items():
            line = _decode_line(market)
            if line is None:
                continue
            po, pu = g["over"].get("Pinnacle"), g["under"].get("Pinnacle")
            if not po or not pu or po <= 1.0 or pu <= 1.0:
                continue  # no Pinnacle two-way to de-vig against
            counters["scanned"] += 1
            dp_over, dp_under = _devig_two_way(po, pu)
            for sel, dp in (("over", dp_over), ("under", dp_under)):
                cands = {b: g[sel][b] for b in PLACEMENT_BOOKS if b in g[sel]}
                if not cands:
                    continue
                book = max(cands, key=cands.get)
                price = cands[book]
                edge = price * dp - 1.0
                if edge < EDGE_FLOOR:
                    continue
                picks.append((match_id, market, sel, book, round(price, 3),
                              round(dp, 6), round(edge * 100.0, 4)))

        for match_id, market, sel, book, price, dp, edge_pct in picks:
            n = execute_write(
                """INSERT INTO shadow_bets
                       (shadow_run_id, shadow_cohort, bot_id, match_id, market, selection,
                        odds_at_pick, odds_at_pick_live, pick_time, stake,
                        model_probability, calibrated_prob, edge_percent, recommended_bookmaker)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(), %s, %s,%s,%s,%s)
                   ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection) DO NOTHING""",
                # odds_at_pick == odds_at_pick_live: for a line-shop bot the price
                # we quote IS the executable price (no high-water inflation).
                # model_probability == calibrated_prob == de-vigged Pinnacle fair p.
                [run_id, SHADOW_COHORT, bot_id, match_id, market, sel,
                 price, price, STAKE_EUR, dp, dp, edge_pct, book],
            )
            if n:
                counters["picked"] += 1
            else:
                counters["skipped_existing"] += 1
        log.info("corners paper: scanned %d (match,line), %d new picks, %d already had one",
                 counters["scanned"], counters["picked"], counters["skipped_existing"])
    except Exception as e:
        log.warning("corners paper generate_picks raised (non-fatal): %s", e)
    return counters


def settle_picks() -> dict:
    """Grade this bot's pending corners picks whose match has a finished corner
    count. Writes result + pnl into shadow_bets (clv columns stay NULL — no
    corners closing anchor is wired). Never raises."""
    counters = {"settled": 0, "won": 0, "lost": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write
        bot_id = _bot_id()
        if not bot_id:
            return counters
        rows = execute_query(
            """
            SELECT sb.id, sb.market, sb.selection, sb.odds_at_pick,
                   (ms.corners_home + ms.corners_away) AS actual
              FROM shadow_bets sb
              JOIN match_stats ms ON ms.match_id = sb.match_id
             WHERE sb.bot_id = %s
               AND sb.result = 'pending'
               AND sb.market LIKE 'corners_ou_%%'
               AND ms.corners_home IS NOT NULL AND ms.corners_away IS NOT NULL
            """,
            [bot_id],
        )
        for r in rows:
            line = _decode_line(r["market"])
            if line is None:
                continue
            actual = int(r["actual"])
            over = actual > line          # .5 lines -> never a push
            won = (over and r["selection"] == "over") or (not over and r["selection"] == "under")
            pnl = round(STAKE_EUR * (float(r["odds_at_pick"]) - 1.0), 2) if won else -STAKE_EUR
            execute_write(
                "UPDATE shadow_bets SET result=%s, pnl=%s WHERE id=%s",
                ["won" if won else "lost", pnl, r["id"]],
            )
            counters["settled"] += 1
            counters["won" if won else "lost"] += 1
        if counters["settled"]:
            log.info("corners paper: settled %d (%dW/%dL)",
                     counters["settled"], counters["won"], counters["lost"])
    except Exception as e:
        log.warning("corners paper settle_picks raised (non-fatal): %s", e)
    return counters


def report() -> dict:
    from workers.api_clients.db import execute_query
    r = execute_query(
        """SELECT count(*) FILTER (WHERE result='pending') pending,
                  count(*) FILTER (WHERE result IN ('won','lost')) settled,
                  round(100.0 * sum(pnl) FILTER (WHERE result IN ('won','lost'))
                        / nullif(count(*) FILTER (WHERE result IN ('won','lost')) * %s, 0), 2) roi_pct,
                  round(sum(pnl) FILTER (WHERE result IN ('won','lost'))::numeric, 2) pnl
             FROM shadow_bets
            WHERE bot_id = (SELECT id FROM bots WHERE name=%s)
              AND market LIKE 'corners_ou_%%'""",
        [STAKE_EUR, BOT_NAME],
    )[0]
    perbook = execute_query(
        """SELECT recommended_bookmaker AS bookmaker,
                  count(*) FILTER (WHERE result IN ('won','lost')) n,
                  round(100.0 * sum(pnl) FILTER (WHERE result IN ('won','lost'))
                        / nullif(count(*) FILTER (WHERE result IN ('won','lost')) * %s, 0), 2) roi
             FROM shadow_bets
            WHERE bot_id = (SELECT id FROM bots WHERE name=%s)
              AND market LIKE 'corners_ou_%%'
            GROUP BY 1 ORDER BY 2 DESC NULLS LAST""",
        [STAKE_EUR, BOT_NAME],
    )
    return {"summary": r, "per_book": perbook}


def main() -> int:
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("pick", "settle", "report"))
    a = ap.parse_args()
    if a.cmd == "pick":
        print(json.dumps(generate_picks(), default=str, indent=2))
    elif a.cmd == "settle":
        print(json.dumps(settle_picks(), default=str, indent=2))
    else:
        print(json.dumps(report(), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
