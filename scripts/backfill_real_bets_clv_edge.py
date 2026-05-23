"""REAL-BETS-CLV-EDGE backfill (2026-05-23).

Fills edge_pct_taken and clv on existing real_bets rows, and refreshes
captured_odds from simulated_bets.odds_at_pick where it was wrongly set to
the live odds (placer key-name bug fixed in coolbet_placer.py:1201). The
DB-generated `slippage_pct` column updates automatically from captured_odds.

  edge_pct_taken    = model_probability × actual_odds − 1
  clv               = (actual_odds / closing_odds) − 1   (decimal fraction)

Reads model_probability from simulated_bets (via simulated_bet_id) and
closing_odds via get_closing_odds(). Idempotent — only writes when the
computed value differs from current.

Usage:
    venv/bin/python3 scripts/backfill_real_bets_clv_edge.py
    venv/bin/python3 scripts/backfill_real_bets_clv_edge.py --since 2026-05-20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_write
from workers.api_clients.supabase_client import execute_query
from workers.jobs.settlement import get_closing_odds


def _q(sql: str, params=()) -> list[dict]:
    return execute_query(sql, params) or []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None,
                    help="ISO date — only backfill rows placed on/after this date")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap rows processed (for testing)")
    args = ap.parse_args()

    where_clauses = []
    params: list = []
    if args.since:
        where_clauses.append("DATE(rb.placed_at) >= %s")
        params.append(args.since)
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    limit_sql = f" LIMIT {int(args.limit)}" if args.limit else ""

    rows = _q(
        f"""SELECT rb.id, rb.match_id, rb.market, rb.selection,
                   rb.captured_odds, rb.actual_odds, rb.simulated_bet_id,
                   rb.slippage_pct, rb.edge_pct_taken, rb.clv,
                   rb.result, rb.combo_legs,
                   sb.model_probability, sb.odds_at_pick
            FROM real_bets rb
            LEFT JOIN simulated_bets sb ON sb.id = rb.simulated_bet_id
            {where_sql}
            ORDER BY rb.placed_at
            {limit_sql}""",
        tuple(params),
    )

    print(f"scanning {len(rows)} real_bets row(s)")
    n_cap = n_edge = n_clv = 0
    for r in rows:
        updates: list[tuple[str, object]] = []

        # Fix captured_odds where the placer's old key-name bug stored the
        # live price instead of the bot's pick price. Use simulated_bet's
        # odds_at_pick as the truth source. slippage_pct is a GENERATED column
        # — it'll auto-recompute when captured_odds changes.
        cap = r.get("captured_odds")
        sim_cap = r.get("odds_at_pick")
        actual = float(r["actual_odds"])
        if sim_cap is not None:
            sim_cap_f = float(sim_cap)
            if cap is None or abs(float(cap) - sim_cap_f) > 1e-4:
                updates.append(("captured_odds", sim_cap_f))

        # edge_pct_taken
        mp = r.get("model_probability")
        if mp is not None and (r.get("edge_pct_taken") is None):
            new_edge = round(float(mp) * actual - 1, 5)
            updates.append(("edge_pct_taken", new_edge))

        # clv — only for settled singles where we can pull a closing line
        if r.get("clv") is None and r.get("combo_legs") is None and r.get("result") in ("won", "lost", "void"):
            try:
                closing = get_closing_odds(str(r["match_id"]), r["market"], r["selection"])
            except Exception:
                closing = None
            if closing and float(closing) > 1.0:
                new_clv = round(actual / float(closing) - 1, 5)
                updates.append(("clv", new_clv))

        if not updates:
            continue
        set_sql = ", ".join(f"{col}=%s" for col, _ in updates)
        execute_write(
            f"UPDATE real_bets SET {set_sql} WHERE id=%s",
            [val for _, val in updates] + [r["id"]],
        )
        for col, _ in updates:
            if col == "captured_odds": n_cap += 1
            elif col == "edge_pct_taken": n_edge += 1
            elif col == "clv": n_clv += 1

    print(f"updated captured_odds on {n_cap} row(s)  (slippage_pct auto-recomputes)")
    print(f"updated edge_pct_taken on {n_edge} row(s)")
    print(f"updated clv on {n_clv} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
