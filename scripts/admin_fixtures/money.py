"""Fixture for /admin/real-bets — the Real bets money ledger (#139 IA move P5, 2026-09-24).

Same shape as odds-intel-web src/lib/admin-money.ts `MoneyData` (loadMoney): real-money rows
only (placed_real IS NOT FALSE — paper rows excluded, as the page does), newest first, plus the
active promo terms with their ledger aggregates. Read-only SQL.
"""


def _num(v):
    return None if v is None else float(v)


def snapshot(rows) -> dict:
    import datetime as dt

    bets = rows(
        """SELECT rb.id::text, rb.match_id::text, rb.market, rb.selection, rb.bookmaker, rb.captured_odds,
                  rb.actual_odds, rb.slippage_pct, rb.edge_pct_taken, rb.clv, rb.clv_pinnacle,
                  rb.closing_bookmaker, rb.closing_minutes_before_ko, rb.stake, rb.placed_at, rb.result,
                  rb.pnl, rb.resolved_at, rb.notes, rb.placed_real, b.name AS bot, b.display_name AS bot_display,
                  sb.stake AS paper_stake, sb.pnl AS paper_pnl, sb.result AS paper_result,
                  ht.name AS home, at.name AS away, l.name AS league, l.country
             FROM real_bets rb
             LEFT JOIN bots b ON b.id = rb.bot_id
             LEFT JOIN simulated_bets sb ON sb.id = rb.simulated_bet_id
             LEFT JOIN matches m ON m.id = rb.match_id
             LEFT JOIN teams ht ON ht.id = m.home_team_id
             LEFT JOIN teams at ON at.id = m.away_team_id
             LEFT JOIN leagues l ON l.id = m.league_id
            WHERE rb.placed_real IS NOT FALSE
            ORDER BY rb.placed_at DESC LIMIT 3000"""
    )
    out = []
    for r in bets:
        out.append({
            "id": r["id"], "matchId": r["match_id"],
            "match": f"{r['home']} vs {r['away']}" if r["home"] and r["away"] else "Unknown",
            "league": f"{r['country']} / {r['league']}" if r["league"] else "Unknown",
            "bot": r["bot"], "market": r["market"], "selection": r["selection"], "bookmaker": r["bookmaker"],
            "capturedOdds": _num(r["captured_odds"]), "actualOdds": float(r["actual_odds"]),
            "slippagePct": _num(r["slippage_pct"]), "edgePctTaken": _num(r["edge_pct_taken"]),
            "clv": _num(r["clv"]), "clvPinnacle": _num(r["clv_pinnacle"]),
            "closingBookmaker": r["closing_bookmaker"], "closingMinutesBeforeKo": _num(r["closing_minutes_before_ko"]),
            "stake": float(r["stake"]), "placedAt": r["placed_at"], "result": r["result"], "pnl": _num(r["pnl"]),
            "resolvedAt": r["resolved_at"], "notes": r["notes"], "placedReal": r["placed_real"],
            "botDisplayName": r["bot_display"],
            "paper": (
                {"stake": float(r["paper_stake"] or 0), "pnl": _num(r["paper_pnl"]), "result": r["paper_result"]}
                if r["paper_result"] is not None else None
            ),
        })

    terms = rows(
        """SELECT id::text, book, promo_type, title, boost_pct, boost_applies_to, face_value_eur, stake_returned,
                  min_odds, max_stake_eur, min_legs, refund_eur, refund_cash, rollover_x, deposit_eur, single_use,
                  valid_to, source_url FROM promo_terms WHERE active ORDER BY valid_to NULLS LAST LIMIT 200"""
    )
    legs = rows("SELECT promo_terms_id::text AS tid, ev_eur, realised_pnl_eur, settled_at FROM promo_ledger")
    promos = []
    for t in terms:
        mine = [x for x in legs if x["tid"] == t["id"]]
        settled = [x for x in mine if x["settled_at"] is not None]
        promos.append({
            **t,
            "taken": len(mine),
            "evSum": sum(float(x["ev_eur"] or 0) for x in mine),
            "realisedSum": sum(float(x["realised_pnl_eur"] or 0) for x in settled),
            "settled": len(settled),
            "evSumSettled": sum(float(x["ev_eur"] or 0) for x in settled),
        })
    return {
        "bets": out,
        "betsError": None,
        "promos": promos,
        "promoError": None,
        "loadedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
