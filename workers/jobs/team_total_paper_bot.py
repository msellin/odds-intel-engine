"""
TEAM-TOTAL-PAPER-2026-09-10 — sharp-anchor paper bot on FULL-MATCH team goal
totals (USE-COLLECTED-MARKETS: a market we already collect prices for on every
book but never modelled). Run as a proper SHADOW bot (bot_team_total_paper_shadow_v1),
writes shadow_bets like the corners bot, tracked on /admin/shadow-bots, kept off
the public pages.

Why this one first (of the collected-but-unused markets): team totals are the
cleanest — a full-match `team_total_{side}_{line}` settles from the FINAL SCORE
(matches.score_home / score_away), which exists for EVERY finished match, so there
is NO settlement-coverage gap like corners/cards. And Pinnacle prices the .5 lines
(_05/_15/_25/_35), giving a real sharp anchor.

Strategy (line-shop vs sharp anchor, same shape as corners_paper_bot): for an
upcoming fixture take the best `team_total_{side}_{line}` over/under price among the
reachable books (Epicbet, Betano, Unibet) and record a paper pick when it beats the
de-vigged Pinnacle fair value (edge = price * devig_p - 1 >= floor). EUR 10 nominal.

Market encoding: team_total_home_15 = home team over/under 1.5 goals. The regex
excludes first-half team totals (`team_total_1h_*`) — those settle from HT, a later
bot. Subcommands: pick | settle | report. Never raises out of the scheduler wrappers.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from collections import defaultdict

log = logging.getLogger(__name__)

BOT_NAME = "bot_team_total_paper_shadow_v1"
SHADOW_COHORT = "team_total_paper"
# Reachable EMTA/accessible books that price team totals (the line-shop set). Epicbet
# is the richest; Pinnacle is the sharp anchor, never a bet book.
PLACEMENT_BOOKS = ("Epicbet", "Betano", "Unibet")
STAKE_EUR = 10.0
# Start at 0 (bet any positive edge) so the paper ledger captures the full edge
# distribution for a later multi-dimensional sweep (side × line × edge band × book),
# exactly the accumulate-then-sweep discipline BOOK-AGNOSTIC used.
EDGE_FLOOR = float(os.getenv("TEAM_TOTAL_PAPER_EDGE_FLOOR", "0.0"))

_MARKET_RE = re.compile(r"^team_total_(home|away)_(\d+)$")


def _decode(market: str) -> tuple[str, float] | None:
    """team_total_home_15 -> ('home', 1.5). None for first-half / malformed."""
    m = _MARKET_RE.match(market)
    if not m:
        return None
    return m.group(1), int(m.group(2)) / 10.0


def _devig_two_way(o_over: float, o_under: float) -> tuple[float, float]:
    """Proportional two-way de-vig -> (p_over, p_under)."""
    io, iu = 1.0 / o_over, 1.0 / o_under
    s = io + iu
    return io / s, iu / s


def _bot_id() -> str | None:
    from workers.api_clients.db import execute_query
    r = execute_query("SELECT id::text AS id FROM bots WHERE name=%s", [BOT_NAME])
    return r[0]["id"] if r else None


def generate_picks() -> dict:
    """Record paper picks (shadow_bets) for upcoming fixtures where the best reachable
    team-total price beats de-vigged Pinnacle. One row per (match, market, selection)
    via ON CONFLICT DO NOTHING. Never raises."""
    counters = {"scanned": 0, "picked": 0, "skipped_existing": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write

        bot_id = _bot_id()
        if not bot_id:
            log.warning("team-total paper: bot %s not registered (migration 327 unapplied?)", BOT_NAME)
            return counters

        rows = execute_query(
            """
            SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.bookmaker)
                   o.match_id::text AS match_id, o.market, o.selection,
                   o.bookmaker, o.odds
              FROM odds_snapshots o
              JOIN matches m ON m.id = o.match_id
             WHERE o.market ~ '^team_total_(home|away)_[0-9]+$'
               AND m.date > now()
               AND o.bookmaker IN ('Epicbet','Betano','Unibet','Pinnacle')
               AND o.selection IN ('over','under')
             ORDER BY o.match_id, o.market, o.selection, o.bookmaker, o."timestamp" DESC
            """
        )
        by_mkt: dict[tuple, dict] = defaultdict(lambda: {"over": {}, "under": {}})
        for r in rows:
            g = by_mkt[(r["match_id"], r["market"])]
            g[r["selection"]][r["bookmaker"]] = float(r["odds"])

        run_id = str(uuid.uuid4())
        picks = []
        for (match_id, market), g in by_mkt.items():
            if _decode(market) is None:
                continue
            po, pu = g["over"].get("Pinnacle"), g["under"].get("Pinnacle")
            if not po or not pu or po <= 1.0 or pu <= 1.0:
                continue  # need a Pinnacle two-way to de-vig against
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
                [run_id, SHADOW_COHORT, bot_id, match_id, market, sel,
                 price, price, STAKE_EUR, dp, dp, edge_pct, book],
            )
            if n:
                counters["picked"] += 1
            else:
                counters["skipped_existing"] += 1
        log.info("team-total paper: scanned %d (match,line), %d new picks, %d already had one",
                 counters["scanned"], counters["picked"], counters["skipped_existing"])
    except Exception as e:  # noqa: BLE001
        log.warning("team-total paper generate_picks raised (non-fatal): %s", e)
    return counters


def settle_picks() -> dict:
    """Grade pending team-total picks from the FINAL score. No settlement gap — every
    finished match carries a score, so no auto-void is needed. Never raises."""
    counters = {"settled": 0, "won": 0, "lost": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write
        bot_id = _bot_id()
        if not bot_id:
            return counters
        rows = execute_query(
            """
            SELECT sb.id, sb.market, sb.selection, sb.odds_at_pick,
                   m.score_home, m.score_away
              FROM shadow_bets sb
              JOIN matches m ON m.id = sb.match_id
             WHERE sb.bot_id = %s
               AND sb.result = 'pending'
               AND sb.market LIKE 'team_total_%%'
               AND m.status = 'finished'
               AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
            """,
            [bot_id],
        )
        for r in rows:
            dec = _decode(r["market"])
            if dec is None:
                continue
            side, line = dec
            actual = int(r["score_home"] if side == "home" else r["score_away"])
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
            log.info("team-total paper: settled %d (%dW/%dL)",
                     counters["settled"], counters["won"], counters["lost"])
    except Exception as e:  # noqa: BLE001
        log.warning("team-total paper settle_picks raised (non-fatal): %s", e)
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
              AND market LIKE 'team_total_%%'""",
        [STAKE_EUR, BOT_NAME],
    )[0]
    return {"summary": r}


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
