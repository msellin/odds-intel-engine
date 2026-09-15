#!/usr/bin/env python3
"""PROMO-EV CLI (2026-09-15, OWN Phase 2). Price a promotion BEFORE taking it,
and optionally record it in `promo_ledger` with the EV attached.

    # fair price for a selection (consensus, Shin de-vig, promo book excluded)
    python3 scripts/promo_ev.py fair --match <uuid> --market 1x2 --selection home --book Coolbet

    # EV of a catalogued promo on a selection at a price
    python3 scripts/promo_ev.py ev --terms <promo_terms uuid> --match <uuid> --market over_under_25 \
        --selection over --odds 1.95 --stake 10

    # …and record it (the ledger refuses a row without an EV — PROMO-LEDGER-EV-BEFORE-BET)
    python3 scripts/promo_ev.py ev ... --record

    # list active promos
    python3 scripts/promo_ev.py terms

    # add a promo (owner, from the book's T&C page)
    python3 scripts/promo_ev.py add-terms --book Coolbet --type odds_boost --title "Weekend boost" \
        --boost-pct 50 --applies-to profit --min-odds 1.5 --max-stake 20 --valid-to 2026-09-21 --url https://...

Read-only unless --record / add-terms.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query, execute_write  # noqa: E402
from workers.automation import promo_ev as pe  # noqa: E402


def cmd_fair(a) -> int:
    fp = pe.consensus_fair_prob(a.match, a.market, a.selection, exclude_book=a.book)
    if not fp:
        print("no consensus (fewer than two reference books with a full, fresh market)")
        return 1
    print(f"fair p {fp.prob:.4f}  fair odds {fp.fair_odds:.3f}  books {fp.books_used}")
    for b, p in sorted(fp.per_book.items(), key=lambda kv: -kv[1]):
        print(f"  {b:14s} {p:.4f}  ({1/p:.2f})")
    return 0


def _terms(tid: str) -> dict:
    rows = execute_query("SELECT * FROM promo_terms WHERE id = %s", (tid,))
    if not rows:
        raise SystemExit(f"promo_terms {tid} not found")
    return rows[0]


def cmd_ev(a) -> int:
    t = _terms(a.terms)
    fp = pe.consensus_fair_prob(a.match, a.market, a.selection, exclude_book=t["book"])
    if not fp:
        print("no consensus fair price — refusing to price the promo")
        return 1
    ev, note = pe.ev_for_terms(t, fair_p=fp.prob, odds=a.odds, stake=a.stake)
    plain = pe.ev_straight(fp.prob, a.odds, a.stake)
    print(f"[{t['book']}] {t['promo_type']} — {t['title']}")
    print(f"  fair p {fp.prob:.4f} ({fp.books_used} books), price {a.odds:.2f}, stake {a.stake:.2f}")
    print(f"  plain-bet EV {plain:+.2f}   promo EV {ev:+.2f}")
    print(f"  {note}")
    if a.record:
        if note.startswith("refused"):
            print("not recorded: the terms refuse this bet")
            return 1
        execute_write(
            """INSERT INTO promo_ledger (promo_terms_id, book, match_id, market, selection,
                                         fair_prob, fair_prob_books, price, stake_eur, ev_eur, ev_note, notes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (t["id"], t["book"], a.match, a.market, a.selection, round(fp.prob, 5), fp.books_used,
             a.odds, a.stake, round(ev, 2), note, a.notes))
        print("recorded in promo_ledger (EV before bet)")
    return 0


def cmd_terms(a) -> int:
    rows = execute_query(
        "SELECT id, book, promo_type, title, boost_pct, face_value_eur, min_odds, max_stake_eur, "
        "valid_to FROM promo_terms WHERE active ORDER BY book, valid_to NULLS LAST") or []
    if not rows:
        print("no active promo_terms — add them from each book's T&C page with add-terms")
        return 0
    for r in rows:
        print(f"{r['id']}  {r['book']:12s} {r['promo_type']:14s} {r['title'][:40]:40s} "
              f"boost {r['boost_pct'] or '-'}  face {r['face_value_eur'] or '-'}  "
              f"min_odds {r['min_odds'] or '-'}  max_stake {r['max_stake_eur'] or '-'}  to {r['valid_to'] or '-'}")
    return 0


def cmd_add_terms(a) -> int:
    execute_write(
        """INSERT INTO promo_terms (book, promo_type, title, boost_pct, boost_applies_to, face_value_eur,
                                    stake_returned, min_odds, max_stake_eur, min_legs, refund_eur, refund_cash,
                                    rollover_x, single_use, valid_to, source_url, terms_text)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (a.book, a.type, a.title, a.boost_pct, a.applies_to, a.face_value, a.stake_returned,
         a.min_odds, a.max_stake, a.min_legs, a.refund, a.refund_cash, a.rollover, not a.multi_use,
         a.valid_to, a.url, a.terms_text))
    print("added")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fair"); f.add_argument("--match", required=True); f.add_argument("--market", required=True)
    f.add_argument("--selection", required=True); f.add_argument("--book"); f.set_defaults(fn=cmd_fair)
    e = sub.add_parser("ev"); e.add_argument("--terms", required=True); e.add_argument("--match", required=True)
    e.add_argument("--market", required=True); e.add_argument("--selection", required=True)
    e.add_argument("--odds", type=float, required=True); e.add_argument("--stake", type=float, default=10.0)
    e.add_argument("--record", action="store_true"); e.add_argument("--notes"); e.set_defaults(fn=cmd_ev)
    t = sub.add_parser("terms"); t.set_defaults(fn=cmd_terms)
    d = sub.add_parser("add-terms"); d.add_argument("--book", required=True); d.add_argument("--type", required=True)
    d.add_argument("--title", required=True); d.add_argument("--boost-pct", type=float); d.add_argument("--applies-to", default="profit")
    d.add_argument("--face-value", type=float); d.add_argument("--stake-returned", action="store_true")
    d.add_argument("--min-odds", type=float); d.add_argument("--max-stake", type=float); d.add_argument("--min-legs", type=int)
    d.add_argument("--refund", type=float); d.add_argument("--refund-cash", action="store_true"); d.add_argument("--rollover", type=float)
    d.add_argument("--multi-use", action="store_true"); d.add_argument("--valid-to"); d.add_argument("--url"); d.add_argument("--terms-text")
    d.set_defaults(fn=cmd_add_terms)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
