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
nominal, shadow_bets only. Subcommands: pick | report (settlement is the generic
shadow settler's — #162 W1.3).
"""
from __future__ import annotations

import logging
import os
import uuid
from collections import defaultdict

# SHADOW-PICKS-UNATTRIBUTABLE (2026-09-18). This bot has NO model — its
# probability is de-vigged Pinnacle, so there is no model bundle to point at.
# But the picks still need to say WHICH RULE produced them: retired bots keep
# writing (owner's call 2026-09-18, the rows are near-free and genuinely
# out-of-sample), and the day this rule changes, old and new rows become
# indistinguishable. That cannot be backfilled, unlike ROI or CLV.
#
# So this is a RULE version, deliberately not a model version string. Do NOT
# join it against `model_versions` — bump it whenever the selection rule,
# the edge floor or the fair-price basis changes.
# v2 (2026-09-24, sweeper-odds audit): v1 listed "Unibet" while the SQL loads
# 'Unibet-Site', so Unibet could never win the line-shop; it also used Betano (not
# accessible to us since 2026-09-23) and omitted Coolbet and Tonybet. v2 line-shops
# across all four OWN books. A rule change, hence the version bump — v1 and v2 rows
# stay distinguishable (SHADOW-PICKS-UNATTRIBUTABLE).
RULE_VERSION = "fh_1x2_paper_devig_v2"


log = logging.getLogger(__name__)

BOT_NAME = "bot_1h_1x2_paper_shadow_v1"
SHADOW_COHORT = "fh_1x2_paper"
PLACEMENT_BOOKS = ("Coolbet", "Epicbet", "Unibet-Site", "Tonybet")
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
        # #162: a retired bot records no new picks (its existing ones still settle)
        from workers.utils.bot_status import bot_is_live
        if not bot_is_live(BOT_NAME):
            counters["retired"] = True
            return counters
        rows = execute_query(
            """
            SELECT DISTINCT ON (o.match_id, o.selection, o.bookmaker)
                   o.match_id::text AS match_id, o.selection, o.bookmaker, o.odds
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.market = '1x2_1h'
               AND m.date > now()
               AND o.bookmaker IN ('Coolbet','Epicbet','Unibet-Site','Tonybet','Pinnacle')
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
                              # EDGE-PERCENT-UNIT-FIX-2026-09-13: store the
                              # FRACTION, not percentage points. Every other
                              # writer of shadow_bets.edge_percent stores the
                              # fraction (0.127 = 12.7%) and every reader
                              # multiplies by 100 to display it. These three
                              # paper bots wrote `edge * 100`, so their stored
                              # edges were 100x everyone else's — median 2.83
                              # ("283%") against 0.127 for a normal bot. The
                              # live gate above is unaffected (it compares the
                              # raw `edge` to EDGE_FLOOR before this line), but
                              # every downstream edge floor silently passed
                              # ~97% of their picks instead of filtering.
                              round(dp, 6), round(edge, 6)))

        for match_id, sel, book, price, dp, edge_pct in picks:
            n = execute_write(
                """INSERT INTO shadow_bets
                       (shadow_run_id, shadow_cohort, bot_id, match_id, market, selection,
                        odds_at_pick, odds_at_pick_live, pick_time, stake,
                        model_probability, calibrated_prob, edge_percent, recommended_bookmaker,
                        model_version)
                   VALUES (%s,%s,%s,%s,'1x2_1h',%s,%s,%s, now(), %s, %s,%s,%s,%s,%s)
                   ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection) DO NOTHING""",
                [run_id, SHADOW_COHORT, bot_id, match_id, sel,
                 price, price, STAKE_EUR, dp, dp, edge_pct, book, RULE_VERSION],
            )
            counters["picked" if n else "skipped_existing"] += 1
        log.info("fh-1x2 paper: scanned %d, %d new picks, %d existing",
                 counters["scanned"], counters["picked"], counters["skipped_existing"])
    except Exception as e:  # noqa: BLE001
        log.warning("fh-1x2 paper generate_picks raised (non-fatal): %s", e)
    return counters


# ── LIVE PRICE VERIFICATION ([[#103]], 2026-09-23) ─────────────────────────
# Owner: "instead of I doing it manually, can we set up an automated action?"
# For every NEW Epicbet pick with edge >= VERIFY_MIN_EDGE, re-fetch that one
# fixture straight from Epicbet (same fetch as near_kickoff_capture — by the
# event id the sweep stored in book_event_map) and record what the site shows
# right now in price_verifications (migration 382). The pick itself is never
# changed: this only measures whether the recorded price was really there.
VERIFY_MIN_EDGE = 0.03
VERIFY_WINDOW_MIN = 30        # only picks made in the last half hour


def classify_live(recorded: float, live: float | None) -> str:
    """'confirmed' | 'moved_up' | 'moved_down' | 'missing' for one live look."""
    if live is None:
        return "missing"
    if abs(live - recorded) < 0.005:
        return "confirmed"
    return "moved_up" if live > recorded else "moved_down"


def verify_epicbet_picks() -> dict:
    c = {"checked": 0, "confirmed": 0, "moved_up": 0, "moved_down": 0,
         "missing": 0, "no_mapping": 0, "fetch_failed": 0, "kicked_off": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write
        bot_id = _bot_id()
        if not bot_id:
            return c
        todo = execute_query(
            """SELECT sb.id::text AS id, sb.match_id::text AS match_id, sb.selection,
                      sb.odds_at_pick::float AS odds, sb.calibrated_prob::float AS p,
                      bem.book_event_id, m.date
                 FROM shadow_bets sb
                 JOIN matches m ON m.id = sb.match_id
                 LEFT JOIN book_event_map bem
                        ON bem.match_id = sb.match_id AND bem.bookmaker = 'Epicbet'
                 LEFT JOIN price_verifications pv ON pv.shadow_bet_id = sb.id
                WHERE sb.bot_id = %s AND sb.market = '1x2_1h'
                  AND sb.recommended_bookmaker = 'Epicbet'
                  AND sb.calibrated_prob * sb.odds_at_pick - 1 >= %s
                  AND sb.pick_time > now() - (%s || ' minutes')::interval
                  AND pv.id IS NULL""",
            [bot_id, VERIFY_MIN_EDGE, str(VERIFY_WINDOW_MIN)])
        if not todo:
            return c
        from datetime import datetime, timezone
        from workers.automation import epicbet_explorer as ex
        sess = ex._session()
        cache: dict = {}
        try:
            for t in todo:
                status, live, detail = None, None, None
                if not t["book_event_id"]:
                    status = "no_mapping"
                elif t["date"] <= datetime.now(timezone.utc):
                    status = "kicked_off"
                else:
                    ev_id = int(t["book_event_id"])
                    if ev_id not in cache:
                        raw = ex.fetch_sidebets(sess, ev_id)
                        if not raw:
                            cache[ev_id] = None
                        else:
                            ev = {"id": ev_id, "raw": raw}
                            ids = ex.collect_market_ids(ev)
                            odds_map = ex.fetch_odds(sess, ids) if ids else {}
                            cache[ev_id] = {(mk, sel): od for mk, sel, od, _ln
                                            in ex.parse_event_markets(ev, odds_map)}
                    board = cache[ev_id]
                    if board is None:
                        status, detail = "fetch_failed", "sidebets returned nothing"
                    else:
                        live = board.get(("1x2_1h", t["selection"]))
                        status = classify_live(t["odds"], live)
                execute_write(
                    """INSERT INTO price_verifications
                           (shadow_bet_id, bookmaker, market, selection, recorded_odds,
                            live_odds, fair_prob, status, detail)
                       VALUES (%s,'Epicbet','1x2_1h',%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (shadow_bet_id) DO NOTHING""",
                    [t["id"], t["selection"], t["odds"], live, t["p"], status, detail])
                c["checked"] += 1
                c[status] += 1
        finally:
            ex.fs_close(sess)
        log.info("fh-1x2 verify: %s", c)
    except Exception as e:  # noqa: BLE001
        log.warning("fh-1x2 verify_epicbet_picks raised (non-fatal): %s", e)
    return c


# #162 W1.3 (2026-09-26): settle_picks() is DELETED. The generic shadow settler
# (settlement._settle_pending_shadow_bets) grades 1x2_1h legs from the half-time score
# through its resolver registry (_r_1x2_1h, which also fetches a missing HT score from
# AF) and writes closes + CLV, which this self-settler never did. One shadow
# settlement path; the 'fh_1x2_paper_settle' schedule is gone too.


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
    ap.add_argument("cmd", choices=("pick", "report"))
    a = ap.parse_args()
    fn = {"pick": generate_picks, "report": report}[a.cmd]
    print(json.dumps(fn(), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
