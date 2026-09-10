"""
FIRST-HALF-1X2-PAPER-2026-09-10 — sharp-anchor paper bot on the FIRST-HALF result
(1X2 at half time) (USE-COLLECTED-MARKETS #2). A three-way market we already sweep
on every book but never modelled. Settles from the HALF-TIME score
(`matches.ht_score_home/away`, 98% covered) — no settlement-coverage gap.

Strategy = line-shop vs sharp anchor, three-way variant of team_total_paper_bot:
for an upcoming fixture, de-vig the Pinnacle 1H home/draw/away triple with the
shared `workers.model.devig.devig` (Shin-de-vig — the SAME sharp anchor the trigger
engine uses), then for each selection take the best reachable book price (Epicbet/
Betano/Unibet) and record a paper pick when price*P_sharp - 1 >= floor. EUR 10
nominal, shadow_bets only. Subcommands: pick | settle | report.
"""
from __future__ import annotations

import logging
import os
import uuid
from collections import defaultdict

log = logging.getLogger(__name__)

BOT_NAME = "bot_1h_1x2_paper_shadow_v1"
SHADOW_COHORT = "fh_1x2_paper"
PLACEMENT_BOOKS = ("Epicbet", "Betano", "Unibet")
STAKE_EUR = 10.0
EDGE_FLOOR = float(os.getenv("FH_1X2_PAPER_EDGE_FLOOR", "0.0"))
_SEL_IDX = {"home": 0, "draw": 1, "away": 2}


def _bot_id() -> str | None:
    from workers.api_clients.db import execute_query
    r = execute_query("SELECT id::text AS id FROM bots WHERE name=%s", [BOT_NAME])
    return r[0]["id"] if r else None


def generate_picks() -> dict:
    counters = {"scanned": 0, "picked": 0, "skipped_existing": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write
        from workers.model.devig import devig
        bot_id = _bot_id()
        if not bot_id:
            log.warning("fh-1x2 paper: bot %s not registered (migration 328 unapplied?)", BOT_NAME)
            return counters
        rows = execute_query(
            """
            SELECT DISTINCT ON (o.match_id, o.selection, o.bookmaker)
                   o.match_id::text AS match_id, o.selection, o.bookmaker, o.odds
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.market = '1x2_1h'
               AND m.date > now()
               AND o.bookmaker IN ('Epicbet','Betano','Unibet','Pinnacle')
               AND o.selection IN ('home','draw','away')
             ORDER BY o.match_id, o.selection, o.bookmaker, o."timestamp" DESC
            """
        )
        by_match: dict[str, dict] = defaultdict(lambda: {"home": {}, "draw": {}, "away": {}})
        for r in rows:
            by_match[r["match_id"]][r["selection"]][r["bookmaker"]] = float(r["odds"])

        run_id = str(uuid.uuid4())
        picks = []
        for match_id, g in by_match.items():
            ph, pd, pa = (g["home"].get("Pinnacle"), g["draw"].get("Pinnacle"),
                          g["away"].get("Pinnacle"))
            if not (ph and pd and pa) or min(ph, pd, pa) <= 1.0:
                continue  # need the full Pinnacle triple to de-vig
            p_sharp = devig([ph, pd, pa])   # [p_home, p_draw, p_away], Shin-de-vig
            if not p_sharp:
                continue
            counters["scanned"] += 1
            for sel in ("home", "draw", "away"):
                dp = p_sharp[_SEL_IDX[sel]]
                cands = {b: g[sel][b] for b in PLACEMENT_BOOKS if b in g[sel]}
                if not cands:
                    continue
                book = max(cands, key=cands.get)
                price = cands[book]
                edge = price * dp - 1.0
                if edge < EDGE_FLOOR:
                    continue
                picks.append((match_id, sel, book, round(price, 3),
                              round(dp, 6), round(edge * 100.0, 4)))

        for match_id, sel, book, price, dp, edge_pct in picks:
            n = execute_write(
                """INSERT INTO shadow_bets
                       (shadow_run_id, shadow_cohort, bot_id, match_id, market, selection,
                        odds_at_pick, odds_at_pick_live, pick_time, stake,
                        model_probability, calibrated_prob, edge_percent, recommended_bookmaker)
                   VALUES (%s,%s,%s,%s,'1x2_1h',%s,%s,%s, now(), %s, %s,%s,%s,%s)
                   ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection) DO NOTHING""",
                [run_id, SHADOW_COHORT, bot_id, match_id, sel,
                 price, price, STAKE_EUR, dp, dp, edge_pct, book],
            )
            counters["picked" if n else "skipped_existing"] += 1
        log.info("fh-1x2 paper: scanned %d, %d new picks, %d existing",
                 counters["scanned"], counters["picked"], counters["skipped_existing"])
    except Exception as e:  # noqa: BLE001
        log.warning("fh-1x2 paper generate_picks raised (non-fatal): %s", e)
    return counters


def settle_picks() -> dict:
    """Grade pending 1H-1X2 picks from the HALF-TIME result. No settlement gap."""
    counters = {"settled": 0, "won": 0, "lost": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write
        bot_id = _bot_id()
        if not bot_id:
            return counters
        rows = execute_query(
            """
            SELECT sb.id, sb.selection, sb.odds_at_pick, m.ht_score_home, m.ht_score_away
              FROM shadow_bets sb JOIN matches m ON m.id = sb.match_id
             WHERE sb.bot_id = %s AND sb.result = 'pending' AND sb.market = '1x2_1h'
               AND m.status = 'finished'
               AND m.ht_score_home IS NOT NULL AND m.ht_score_away IS NOT NULL
            """,
            [bot_id],
        )
        for r in rows:
            hh, ha = int(r["ht_score_home"]), int(r["ht_score_away"])
            result = "home" if hh > ha else ("away" if ha > hh else "draw")
            won = r["selection"] == result
            pnl = round(STAKE_EUR * (float(r["odds_at_pick"]) - 1.0), 2) if won else -STAKE_EUR
            execute_write("UPDATE shadow_bets SET result=%s, pnl=%s WHERE id=%s",
                          ["won" if won else "lost", pnl, r["id"]])
            counters["settled"] += 1
            counters["won" if won else "lost"] += 1
        if counters["settled"]:
            log.info("fh-1x2 paper: settled %d (%dW/%dL)",
                     counters["settled"], counters["won"], counters["lost"])
    except Exception as e:  # noqa: BLE001
        log.warning("fh-1x2 paper settle_picks raised (non-fatal): %s", e)
    return counters


def report() -> dict:
    from workers.api_clients.db import execute_query
    r = execute_query(
        """SELECT count(*) FILTER (WHERE result='pending') pending,
                  count(*) FILTER (WHERE result IN ('won','lost')) settled,
                  round(100.0 * sum(pnl) FILTER (WHERE result IN ('won','lost'))
                        / nullif(count(*) FILTER (WHERE result IN ('won','lost')) * %s, 0), 2) roi_pct
             FROM shadow_bets WHERE bot_id=(SELECT id FROM bots WHERE name=%s) AND market='1x2_1h'""",
        [STAKE_EUR, BOT_NAME],
    )[0]
    return {"summary": r}


def main() -> int:
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("pick", "settle", "report"))
    a = ap.parse_args()
    fn = {"pick": generate_picks, "settle": settle_picks, "report": report}[a.cmd]
    print(json.dumps(fn(), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
