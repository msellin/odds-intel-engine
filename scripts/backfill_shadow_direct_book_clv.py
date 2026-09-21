"""DIRECT-BOOK-CLV-SHADOW-BACKFILL-2026-09-18 — port the real_bets backfill to
the shadow and simulated ledgers.

THE DEFECT. The forward fix shipped in `de7858c` (shadow) and on 2026-09-14
(simulated): CLV must be measured against the close at the book the pick was
actually priced at, never a substitute. Neither backfill ran. Measured
2026-09-21:

    shadow_bets     106,726 of 161,236 rows with a clv have NO closing_bookmaker  (66.2%)
    simulated_bets    3,124 of   3,124                                            (100%)

So two thirds of the shadow ledger's CLV — the column that gates every
promote/retire decision — and ALL of the public ledger's CLV is computed against
whichever book happened to close. The bias is not random: the substitute book is
chosen by having a close at all, which correlates with being a big liquid book
whose closing price is sharper, so the picked-vs-close gap reads better than it
was. Measured correction on the recoverable shadow rows: mean CLV +5.35% -> +2.30%.

WHY IT CALLS THE SETTLEMENT HELPERS. `get_closing_odds` and `closing_book_margin`
are the exact functions `_settle_pending_shadow_bets` calls. A backfill that
re-implements the lookup in SQL is how the two silently diverge — the same clone
gap that let SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES through. Slower, and correct.

WHAT IT WRITES, AND WHAT IT DELIBERATELY DOES NOT.
  writes   closing_odds, closing_bookmaker, clv, clv_live, closing_margin,
           clv_margin_corrected, closing_minutes_before_ko
  NEVER    clv_pinnacle / clv_pinnacle_live. Those are `odds_at_pick x
           devig(Pinnacle close) - 1`; closing_bookmaker is not an input, so
           rewriting them would be an unrelated change riding along.

ROWS WITH NO CLOSE AT THEIR OWN BOOK ARE SET EXPLICITLY NULL. Keeping the old
arbitrary-book value would leave a structurally-positive number indistinguishable
from a real one. ~4.6% of shadow rows land here; that is the honest outcome.

FRESHNESS. Migration 363 adds `closing_minutes_before_ko` because the shadow path
has no DIRECT_CLOSE_MAX_MIN bound: ~40% of historical closes are >3h pre-kickoff
and ~75% of those give clv=0 by construction. Recording the age is what stops
this trading a known bias for an unmeasurable one.

    python3 scripts/backfill_shadow_direct_book_clv.py --dry-run
    python3 scripts/backfill_shadow_direct_book_clv.py --table simulated_bets
    python3 scripts/backfill_shadow_direct_book_clv.py --batch 2000
    python3 scripts/backfill_shadow_direct_book_clv.py --exclude-bots a,b,c
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write  # noqa: E402
from workers.jobs.settlement import (  # noqa: E402
    _normalize_bet_market,
    _normalize_bet_selection,
    closing_book_margin,
    get_closing_odds,
)

# SHARP-ANCHOR-SWEEP is a LOCKED pre-registration whose primary metric is
# direct-book CLV. Backfilling its bots mid-measurement changes the population
# under a running test — substantively tiny (65 rows, same sign, third decimal)
# but procedurally exactly what a pre-registration exists to prevent. Excluded by
# default; pass --include-sharp once the sweep has reported.
SHARP_PREREG_BOTS = (
    "bot_coolbet_trigger_sharp_1x2_v1",
    "bot_unibet_trigger_sharp_1x2_v1",
    "bot_trigger_1x2_sharp_tight_v1",
)


def _has_col(table: str, col: str) -> bool:
    return bool(execute_query(
        """SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s""", (table, col)))


def _rows(table: str, exclude: list[str], limit: int, offset: int):
    """Settled rows carrying a clv but no closing_bookmaker — the defect, exactly.

    `closing_bookmaker IS NULL` is also the idempotency predicate: it shrinks
    monotonically. Recovered rows drop out because they gain a book; rows we
    deliberately NULL drop out because they lose their clv. So a re-run does not
    churn either, and there is no loop.

    The three ledgers do NOT share a schema — verified 2026-09-21 the hard way,
    twice: simulated_bets has no closing_margin/clv_margin_corrected, and
    shadow_bets has no combo_legs. A combo has no single closing price, so
    exclude combos where the concept exists and skip the clause where it does
    not, rather than assuming a shared shape.
    """
    combo = "AND t.combo_legs IS NULL" if _has_col(table, "combo_legs") else ""
    return execute_query(
        f"""SELECT t.id, t.match_id::text AS match_id, t.market, t.selection,
                   t.odds_at_pick, t.odds_at_pick_live, t.recommended_bookmaker,
                   t.clv AS old_clv, b.name AS bot_name, m.date AS kickoff
              FROM {table} t
              JOIN bots b ON b.id = t.bot_id
              JOIN matches m ON m.id = t.match_id
             WHERE t.clv IS NOT NULL
               AND t.closing_bookmaker IS NULL
               AND t.result IN ('won', 'lost', 'void')
               AND b.name NOT LIKE 'inplay%%'
               {combo}
               AND (%s::text[] IS NULL OR b.name <> ALL(%s::text[]))
             ORDER BY t.id
             LIMIT %s OFFSET %s""",
        (exclude or None, exclude or None, limit, offset),
    ) or []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="shadow_bets",
                    choices=["shadow_bets", "simulated_bets"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=2000)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--include-sharp", action="store_true",
                    help="also backfill the SHARP-ANCHOR-SWEEP pre-registration bots")
    ap.add_argument("--exclude-bots", default="")
    a = ap.parse_args()

    exclude = [s for s in a.exclude_bots.split(",") if s]
    if not a.include_sharp:
        exclude += list(SHARP_PREREG_BOTS)

    # memoised: ~7.2k distinct lookups back ~106k rows (14.6x duplication)
    close_memo: dict = {}
    margin_memo: dict = {}

    def close_of(mid, mkt, sel, book):
        k = (mid, mkt, sel, book)
        if k not in close_memo:
            close_memo[k] = get_closing_odds(mid, mkt, sel, book)
        return close_memo[k]

    def margin_of(mid, mkt, book):
        k = (mid, mkt, book)
        if k not in margin_memo:
            margin_memo[k] = closing_book_margin(mid, mkt, book)
        return margin_memo[k]

    ts_memo: dict = {}

    def ts_of(mid, mkt, sel, book, kickoff):
        # Memoised on the SAME key as the close. Unmemoised this was one query
        # per row (~106k) instead of one per distinct market (~7.2k), which is
        # what made the first dry run crawl.
        k = (mid, mkt, sel, book)
        if k not in ts_memo:
            rows = execute_query(
                """SELECT os.timestamp FROM odds_snapshots os
                    WHERE os.match_id = %s AND os.market = %s AND os.selection = %s
                      AND os.bookmaker = %s AND os.timestamp <= %s
                    ORDER BY os.is_closing DESC, os.timestamp DESC LIMIT 1""",
                (mid, mkt, sel, book, kickoff))
            ts_memo[k] = rows[0]["timestamp"] if rows else None
        return ts_memo[k]

    has_margin_cols = bool(execute_query(
        """SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = 'clv_margin_corrected'""",
        (a.table,)))
    if not has_margin_cols:
        print(f"note: {a.table} has no closing_margin / clv_margin_corrected "
              f"columns — writing the rest, margin correction is not available there")

    stats = defaultdict(int)
    by_bot: dict = defaultdict(lambda: {"n": 0, "old": 0.0, "new": 0.0, "nulled": 0})
    offset = 0
    total = 0

    while True:
        batch = _rows(a.table, exclude, a.batch, offset)
        if not batch:
            break
        writes = []
        for r in batch:
            mkt = _normalize_bet_market(r["market"], r["selection"])
            sel = _normalize_bet_selection(r["selection"])
            book = r["recommended_bookmaker"]
            close = close_of(r["match_id"], mkt, sel, book) if book else None

            if close and float(close) > 1.0:
                clv = round(float(r["odds_at_pick"]) / float(close) - 1, 4)
                clv_live = (round(float(r["odds_at_pick_live"]) / float(close) - 1, 4)
                            if r["odds_at_pick_live"] else None)
                m = margin_of(r["match_id"], mkt, book)
                margin = round(m, 5) if m is not None else None
                clv_mc = (round((1.0 + clv) / (1.0 + m) - 1.0, 5)
                          if m is not None else None)
                cts = ts_of(r["match_id"], mkt, sel, book, r["kickoff"])
                mins = int((r["kickoff"] - cts).total_seconds() // 60) if cts else None
                stats["recovered"] += 1
                by_bot[r["bot_name"]]["n"] += 1
                by_bot[r["bot_name"]]["old"] += float(r["old_clv"])
                by_bot[r["bot_name"]]["new"] += clv
                if m is not None:
                    stats["with_margin"] += 1
            else:
                clv = clv_live = book = margin = clv_mc = mins = None
                stats["nulled"] += 1
                by_bot[r["bot_name"]]["nulled"] += 1

            writes.append((close if clv is not None else None, clv, clv_live,
                           book, margin, clv_mc, mins, r["id"], book, clv))

        if not a.dry_run:
            for w in writes:
                # The two ledgers do NOT share a schema: simulated_bets has no
                # closing_margin / clv_margin_corrected (verified 2026-09-21), so
                # margin-correcting it is not merely unset but impossible. Write
                # the columns each table actually has rather than assuming they
                # match — the first run failed loudly here, which is the right
                # failure, but it should not need to fail to find that out.
                if has_margin_cols:
                    execute_write(
                        f"""UPDATE {a.table}
                               SET closing_odds = %s, clv = %s, clv_live = %s,
                                   closing_bookmaker = %s, closing_margin = %s,
                                   clv_margin_corrected = %s,
                                   closing_minutes_before_ko = %s
                             WHERE id = %s
                               AND (closing_bookmaker IS DISTINCT FROM %s
                                    OR clv IS DISTINCT FROM %s)""", w)
                else:
                    execute_write(
                        f"""UPDATE {a.table}
                               SET closing_odds = %s, clv = %s, clv_live = %s,
                                   closing_bookmaker = %s,
                                   closing_minutes_before_ko = %s
                             WHERE id = %s
                               AND (closing_bookmaker IS DISTINCT FROM %s
                                    OR clv IS DISTINCT FROM %s)""",
                        (w[0], w[1], w[2], w[3], w[6], w[7], w[8], w[9]))

        total += len(batch)
        print(f"  {total:,} rows processed "
              f"(recovered {stats['recovered']:,}, nulled {stats['nulled']:,})")
        if a.dry_run:
            offset += len(batch)     # dry-run must advance; no rows change
        if a.limit and total >= a.limit:
            break
        if not a.dry_run and len(batch) < a.batch:
            break

    print(f"\n{'DRY RUN — nothing written' if a.dry_run else 'WROTE'} · table={a.table}")
    print(f"  rows           {total:,}")
    print(f"  recovered      {stats['recovered']:,}")
    print(f"  set to NULL    {stats['nulled']:,}  (no close at the pick's own book)")
    print(f"  with margin    {stats['with_margin']:,}")
    print(f"  distinct close lookups {len(close_memo):,}")
    if exclude:
        print(f"  excluded bots  {', '.join(exclude)}")

    print(f"\n{'bot':<40s}{'n':>7s}{'old CLV':>10s}{'new CLV':>10s}{'delta':>9s}{'nulled':>8s}")
    for name, d in sorted(by_bot.items(), key=lambda kv: -kv[1]["n"])[:20]:
        if not d["n"]:
            print(f"{name[:40]:<40s}{0:>7d}{'—':>10s}{'—':>10s}{'—':>9s}{d['nulled']:>8d}")
            continue
        o, n2 = 100 * d["old"] / d["n"], 100 * d["new"] / d["n"]
        print(f"{name[:40]:<40s}{d['n']:>7d}{o:>9.2f}%{n2:>9.2f}%{n2 - o:>8.2f}%{d['nulled']:>8d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
