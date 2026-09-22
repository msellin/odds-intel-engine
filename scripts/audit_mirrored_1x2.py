#!/usr/bin/env python3
"""Find stored 1X2 triples whose home and away are transposed (1X2-HOME-AWAY-INVERSIONS).

Runs the PRODUCTION guard (`workers/utils/mirror_guard`) over history, so the
audit and the write-time gate can never drift into two different definitions of
"mirrored" — the same discipline as `anchor_sanity.OUTLIER_MAX_RATIO` vs the §9
research constant.

DRY-RUN BY DEFAULT, and the default does nothing but print. Historical rows are
deliberately NOT deleted or moved without `--quarantine`, and the decision is the
owner's:

  * These rows are EVIDENCE. `SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES-2026-09-20`
    kept its 31 bad picks as `result='void'` rows rather than deleting them, on
    the `KAMBI-CRITERION-CONTAMINATION-2026-09-05` precedent, precisely so the
    fault stays auditable after the fix.
  * The volume is tiny (33 triples in 120 days = 99 rows), so there is no
    storage or performance argument for removing them.
  * Model training reads this history. Removing 99 rows changes nothing
    statistically but does change a table other work has already measured.

What the row DOES want a decision on, and this script surfaces it:
`bot_unibet_trigger_1x2_v1` took one pick on an inverted leg (Birkirkara v
Hibernians, 2026-09-12, home @ 3.20 against a true ~2.05) and it is still
counted as a normal loss. The other three inverted-leg picks are already
`result='void'` from the phantom-fixture cleanup. Whether to void the fourth for
consistency is a call about a bot's published record, not a data-hygiene
question, so this script only reports it.

Usage:
    python3 scripts/audit_mirrored_1x2.py                 # report, 120 days
    python3 scripts/audit_mirrored_1x2.py --days 30
    python3 scripts/audit_mirrored_1x2.py --quarantine    # move rows (asks first)
"""
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from workers.api_clients.db import execute_query, execute_write  # noqa: E402
from workers.utils import mirror_guard as mg  # noqa: E402

LATEST_TRIPLES = """
WITH latest AS (
  SELECT DISTINCT ON (o.match_id, o.bookmaker, o.selection)
         o.match_id, o.bookmaker, o.selection, o.odds::float AS odds
  FROM odds_snapshots o
  JOIN matches m ON m.id = o.match_id
  WHERE o.market = '1x2'
    AND COALESCE(o.is_live, false) = false
    AND o.timestamp <= m.date
    AND m.date >= now() - (%s || ' days')::interval
    AND m.date <= now()
  ORDER BY o.match_id, o.bookmaker, o.selection, o.timestamp DESC
)
SELECT l.match_id::text AS match_id, l.bookmaker, l.selection, l.odds, m.date AS ko
FROM latest l JOIN matches m ON m.id = l.match_id
WHERE l.odds > 1.0
"""


def find(days: int):
    by_match = defaultdict(lambda: defaultdict(dict))
    kickoffs = {}
    for r in execute_query(LATEST_TRIPLES, (str(days),)):
        by_match[r["match_id"]][r["bookmaker"]][r["selection"]] = r["odds"]
        kickoffs[r["match_id"]] = r["ko"]

    hits, tested = [], 0
    for mid, bmap in by_match.items():
        full = {b: t for b, t in bmap.items() if {"home", "draw", "away"} <= t.keys()}
        for book, triple in full.items():
            peers = [(t["home"], t["draw"], t["away"]) for b, t in full.items() if b != book]
            tested += 1
            reason = mg.screen_1x2(mid, book, triple, reference=peers)
            if reason:
                hits.append({"match_id": mid, "bookmaker": book, "triple": triple,
                             "ko": kickoffs[mid], "reason": reason, "peers": len(peers)})
    return hits, tested


def picks_on(hits):
    """Picks struck ON an inverted leg — not merely on an affected fixture.

    The distinction is the whole reason this row was mis-scoped twice. A pick on
    an affected FIXTURE is nothing; a pick whose `odds_at_pick` IS the inverted
    number is the fault reaching money. The draw leg is excluded because a
    home<->away mirror leaves it untouched by definition — counting it inflated
    an earlier pass from 4 hits to 17.
    """
    bad = {(h["match_id"], h["bookmaker"]): h for h in hits}
    rows = execute_query(
        """SELECT sb.id, b.name AS bot, sb.match_id::text AS match_id, sb.selection,
                  sb.odds_at_pick::float AS odds, sb.recommended_bookmaker AS book,
                  sb.pick_time, sb.result, sb.pnl
             FROM shadow_bets sb LEFT JOIN bots b ON b.id = sb.bot_id
            WHERE sb.market = '1x2' AND sb.selection IN ('home','away')
              AND sb.match_id::text = ANY(%s)""",
        ([h["match_id"] for h in hits],),
    )
    out = []
    for r in rows:
        h = bad.get((r["match_id"], r["book"]))
        if not h:
            continue
        price = h["triple"][r["selection"]]
        if abs(r["odds"] - price) / max(price, 1e-9) <= 0.02:
            out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--quarantine", action="store_true",
                    help="move the offending rows into odds_snapshots_quarantined "
                         "(asks for confirmation; rows are COPIED then deleted)")
    args = ap.parse_args()

    hits, tested = find(args.days)
    print(f"tested {tested} (fixture, book) 1x2 triples over {args.days} days")
    print(f"MIRRORED: {len(hits)} ({100 * len(hits) / max(tested, 1):.4f}%)\n")
    print("by bookmaker:", Counter(h["bookmaker"] for h in hits).most_common())
    print("by ISO week :", sorted(Counter(h["ko"].strftime("%G-W%V") for h in hits).items()))
    print()
    for h in sorted(hits, key=lambda x: x["ko"]):
        t = h["triple"]
        print(f"  {h['ko']:%Y-%m-%d} {h['bookmaker']:14s} "
              f"{t['home']:>7.2f}/{t['draw']:>6.2f}/{t['away']:>7.2f}  "
              f"peers={h['peers']:2d}  {h['match_id']}")

    struck = picks_on(hits)
    print(f"\nPICKS STRUCK ON AN INVERTED LEG: {len(struck)}")
    for r in struck:
        print(f"  {r['pick_time']:%Y-%m-%d} {r['bot']} {r['selection']} @ {r['odds']} "
              f"from {r['book']} -> result={r['result']} pnl={r['pnl']}  id={r['id']}")
    live = [r for r in struck if r["result"] != "void"]
    if live:
        print(f"\n  {len(live)} of those are still counted as real results. Voiding them "
              f"is the owner's call (it changes a bot's published record) — this "
              f"script does not do it.")

    if not args.quarantine:
        print("\n(report only — pass --quarantine to move the odds rows; "
              "historical rows are kept as evidence by default, see the docstring)")
        return

    if input(f"\nMove the 1x2 rows of {len(hits)} (fixture, book) pairs to "
             f"odds_snapshots_quarantined? [y/N] ").strip().lower() != "y":
        print("aborted")
        return
    moved = 0
    for h in hits:
        moved += execute_write(
            """WITH moved AS (
                 DELETE FROM odds_snapshots
                  WHERE match_id = %s AND bookmaker = %s AND market = '1x2'
                    AND COALESCE(is_live, false) = false
                RETURNING *)
               INSERT INTO odds_snapshots_quarantined
                 (id, match_id, bookmaker, market, selection, odds, timestamp,
                  is_closing, minutes_to_kickoff, is_live, handicap_line,
                  quarantine_reason, quarantined_at)
               SELECT id, match_id, bookmaker, market, selection, odds, timestamp,
                      is_closing, minutes_to_kickoff, is_live, handicap_line,
                      %s, now()
                 FROM moved""",
            (h["match_id"], h["bookmaker"], h["reason"]),
        ) or 0
    print(f"moved {moved} rows")


if __name__ == "__main__":
    main()
