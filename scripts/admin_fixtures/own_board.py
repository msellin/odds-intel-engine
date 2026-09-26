"""/admin/shadow-bots OWN board preview snapshot ([[#182]]). The same shape src/lib/own-board.ts reads:
rows of own_bet_board (computed here with the job's own code when the table is not there yet) and the
already-placed selection keys."""


def snapshot(rows) -> dict:
    import json
    try:
        board = rows("SELECT * FROM own_bet_board WHERE anchor_source IS NOT NULL ORDER BY clears DESC, best_edge DESC NULLS LAST")
    except Exception:  # noqa: BLE001 — migrations 470/471 not applied yet
        board = []
    if not board:  # table missing or not filled yet: compute with the job's own code
        from datetime import datetime, timezone
        from workers.jobs import own_bet_board as ob
        books = ob.accessible_books()
        picks = ob.load_picks()
        lines, now = ob.load_lines(tuple(sorted({p["market"] for p in picks})), books)
        at = datetime.fromtimestamp(now, tz=timezone.utc)
        anchors = {k: ob.line_anchors(k[0], k[1], books, at) for k in {(p["match_id"], p["market"]) for p in picks}}
        board = json.loads(json.dumps(ob.build(picks, lines, now, books, anchors), default=str))
        for r in board:
            r["computed_at"] = at.isoformat()
    ids = sorted({r["match_id"] for r in board})
    placed = rows("SELECT match_id::text AS match_id, market, lower(selection) AS selection FROM real_bets "
                  "WHERE match_id = ANY(%s::uuid[]) AND placed_real IS NOT FALSE", (ids,)) if ids else []
    return {"rows": board, "placed": [f"{p['match_id']}|{p['market']}|{p['selection']}" for p in placed],
            "error": None}
