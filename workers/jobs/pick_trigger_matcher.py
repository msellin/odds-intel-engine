"""
BOOK-AGNOSTIC-EDGE-ENGINE Stage B — the matcher (PAPER).
See docs/BOOK_AGNOSTIC_EDGE_ENGINE.md.

Joins each book's latest swept odds (`odds_snapshots`) against the Stage A trigger
windows (`pick_triggers`) and writes a `shadow_bet` for any price landing in
[min_odds, max_odds] — the CORRECT Coolbet-native selection (edge evaluated at the
book's OWN odds). Runs PAPER alongside the current simulated_bets-copy mirror so
the wider universe can be measured before it is ever trusted with money.

SAFETY: the emit bot (`bot_coolbet_trigger_v1`) is NOT in the placer's
PLACEABLE_BOTS whitelist and has no coolbet_placer_bots toggle — it can never
stake money; it only writes shadow_bets. No model call, no HTTP — a bounded DB
join. Idempotent upsert. Run:  python3 -m workers.jobs.pick_trigger_matcher
"""
from __future__ import annotations

import logging
import uuid

log = logging.getLogger(__name__)

STAKE_EUR = 10.0
TRIGGER_MARKETS = ("1x2", "over_under_25")
# book (odds_snapshots.bookmaker) → the paper bot its trigger matches emit into
BOOKS = {"Coolbet": "bot_coolbet_trigger_v1"}


def _bot_id(name: str) -> str | None:
    from workers.api_clients.db import execute_query
    r = execute_query("SELECT id::text AS id FROM bots WHERE name=%s", [name])
    return r[0]["id"] if r else None


def match_and_emit(book: str = "Coolbet") -> dict:
    """Emit shadow_bets for every upcoming fixture whose `book` price lands inside
    its Stage A trigger window. Never raises."""
    counters = {"book": book, "matched": 0, "written": 0}
    try:
        from workers.api_clients.db import execute_query, execute_write
        bot_name = BOOKS.get(book)
        bot_id = _bot_id(bot_name) if bot_name else None
        if not bot_id:
            log.warning("trigger matcher: bot for %s not registered (migration 320?)", book)
            return counters
        cohort = "coolbet_trigger"

        rows = execute_query(
            """
            WITH latest AS (  -- latest pre-match price per (match, market, selection) for this book
              SELECT DISTINCT ON (o.match_id, o.market, o.selection)
                     o.match_id::text AS mid, o.market, o.selection, o.odds::float AS odds
                FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
               WHERE o.bookmaker = %s AND o.market = ANY(%s)
                 AND o.timestamp <= m.date AND m.date > NOW()
               ORDER BY o.match_id, o.market, o.selection, o.timestamp DESC
            )
            SELECT t.match_id::text AS mid, t.market, t.selection,
                   t.cal_prob::float AS cal, t.min_odds::float AS mino,
                   t.max_odds::float AS maxo, l.odds AS book_odds
              FROM pick_triggers t
              JOIN latest l
                ON l.mid = t.match_id::text AND l.market = t.market AND l.selection = t.selection
             WHERE t.kickoff_at > NOW()
               AND l.odds >= t.min_odds
               AND (t.max_odds IS NULL OR l.odds <= t.max_odds)
            """,
            [book, list(TRIGGER_MARKETS)],
        )
        counters["matched"] = len(rows)
        run_id = str(uuid.uuid4())
        for r in rows:
            price = float(r["book_odds"])
            if price <= 1.0:
                continue
            edge = float(r["cal"]) - 1.0 / price   # edge at the book's OWN price
            execute_write(
                """INSERT INTO shadow_bets
                       (shadow_run_id, shadow_cohort, bot_id, match_id, market, selection,
                        odds_at_pick, odds_at_pick_live, pick_time, stake,
                        model_probability, calibrated_prob, edge_percent)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(), %s, %s,%s,%s)
                   ON CONFLICT (shadow_cohort, bot_id, match_id, market, selection)
                   DO UPDATE SET
                        odds_at_pick      = EXCLUDED.odds_at_pick,
                        odds_at_pick_live = EXCLUDED.odds_at_pick_live,
                        calibrated_prob   = EXCLUDED.calibrated_prob,
                        edge_percent      = EXCLUDED.edge_percent""",
                [run_id, cohort, bot_id, r["mid"], r["market"], r["selection"],
                 price, price, STAKE_EUR, r["cal"], r["cal"], edge],
            )
            counters["written"] += 1
        log.info("trigger matcher (%s): %s", book, counters)
    except Exception as e:  # noqa: BLE001
        log.warning("trigger matcher raised (non-fatal): %s", e)
    return counters


def run_all() -> dict:
    out = {}
    for book in BOOKS:
        out[book] = match_and_emit(book)
    return out


def main() -> int:
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(run_all(), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
