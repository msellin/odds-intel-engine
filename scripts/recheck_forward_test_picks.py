#!/usr/bin/env python3
"""Re-check SENT forward-test picks of an earlier rule_version against the arm's
CURRENT rule, using PICK-TIME data only ([[#158]], 2026-09-25, owner-approved).

    python3 -m scripts.recheck_forward_test_picks            # dry run, prints
    python3 -m scripts.recheck_forward_test_picks --write    # records the verdicts

WHY. Since [[#156]] /performance scores each forward-test bot on its CURRENT
rule_version only. The consensus arm moved v1 -> CONSENSUS_RULE_VERSION (v2), whose
ONLY change is the credible-method gate (edge >= MIN_EDGE under ALL of
CREDIBLE_DEVIG_METHODS). A v1 pick that would ALSO have been published under v2 is a
v2 pick in every respect but its label, so the owner decided it counts in the bot's
current record — provided the verdict is reached from what was known when the pick
was published, never from how it turned out.

WHAT IT READS — and what it must never read. Per pick: the stored pick-time fields
(match, market, selection, published odds and book, stored edge / p_sharp / anchor
fields, published_at). For consensus picks it rebuilds the consensus from
`odds_snapshots` AS OF `published_at` — the same filters as the publisher's own
`load_candidates` (pre-match rows, EXCLUDED_BOOKS out, latest quote per book within
the 6 h before), then the publisher's own `_consensus_anchor` / `devig_by` — so the
arithmetic is identical to live. It never reads outcome, pnl, closing odds, CLV or
settled_at (smoke RECHECK-FORWARD-TEST-PICK-TIME-ONLY pins this).

odds_snapshots keeps intraday history for ~7 days, so run this within a week of a
rule change. A pick whose pick-time consensus cannot be rebuilt FAILS (reason
`no_pick_time_consensus`) — an unverifiable pick does not count.

IDEMPOTENT. One verdict per (pick_id, checked_rule_version), inserted with
ON CONFLICT DO NOTHING: a verdict, once reached from pick-time data, is frozen.
Re-running after snapshots age out can never overwrite it with a worse rebuild.

The sharp (live) arm is recorded the same way: its v1 picks are judged against the
current rule from their STORED pick-time anchor fields (all 8 fail the v3+
anchor-overround <= 4% gate). The PRE-REGISTERED test's own counts are not changed
by any of this — its checkpoints read rule_version as published.
"""
from __future__ import annotations

import argparse
import json
import sys

from workers.api_clients.db import execute_query, execute_write
from workers.model.devig import devig_by
from scripts.publish_picks_forward_test import (
    ALIGN_MIN, CONSENSUS_ARM, CONSENSUS_MAX_EDGE, CONSENSUS_MIN_BOOKS,
    CONSENSUS_RULE_VERSION, CREDIBLE_DEVIG_METHODS, EXCLUDED_BOOKS, MARKETS,
    MAX_ANCHOR_OVERROUND, MAX_ODDS, MAX_RATIO, MIN_EDGE, RULE_VERSION,
    _consensus_anchor,
)

CURRENT_RULE = {"live": RULE_VERSION, CONSENSUS_ARM: CONSENSUS_RULE_VERSION}

# Pick-time columns only — never a result column (see the module docstring).
PICKS_SQL = """
    SELECT id::text AS id, arm, rule_version, match_id::text AS match_id, market,
           selection, odds::float AS odds, bookmaker, edge::float AS edge,
           p_sharp::float AS p_sharp, anchor_odds, anchor_overround::float AS anchor_overround,
           alignment_gap_minutes::float AS alignment_gap_minutes, published_at, grade
      FROM picks_forward_test
     WHERE arm = ANY(%s)
       AND telegram_message_id IS NOT NULL
       AND rule_version <> ALL(%s)
     ORDER BY published_at
"""

# The publisher's load_candidates() query, pinned to one leg's market and to
# `published_at` instead of now(): latest pre-match quote per (selection, book)
# inside the 6 h before publication.
QUOTES_SQL = """
    SELECT DISTINCT ON (o.selection, o.bookmaker)
           o.selection, o.bookmaker, o.odds::float AS odds, o.timestamp
      FROM odds_snapshots o
     WHERE o.match_id = %s AND o.market = %s
       AND o.is_live IS NOT TRUE
       AND NOT (o.bookmaker = ANY(%s))
       AND o.timestamp <= %s
       AND o.timestamp > %s - interval '6 hours'
     ORDER BY o.selection, o.bookmaker, o.timestamp DESC
"""


def evaluate_consensus_leg(side_q: dict, sides: list[str], selection: str,
                           odds: float, bookmaker: str,
                           stored_p: float | None = None) -> tuple[bool, dict]:
    """The consensus arm's CURRENT rule for one published leg, from pick-time quotes.

    Mirrors load_candidates(anchor="consensus") + select(credible_gate=True,
    max_edge=CONSENSUS_MAX_EDGE) for the leg that WAS published (its book and odds):
    >= CONSENSUS_MIN_BOOKS complete books, the published quote aligned within ALIGN_MIN
    of the anchor, odds <= MAX_ODDS, price ratio <= MAX_RATIO, MIN_EDGE <= Shin edge
    <= CONSENSUS_MAX_EDGE, and the edge >= MIN_EDGE under every credible method.

    `stored_p` is the Shin consensus probability the publisher RECORDED at pick time.
    The rebuild must reproduce it: odds_snapshots is thinned after the fact (some
    intraday :30 rows are gone), so a rebuild can see a slightly different book state
    from the one the publisher saw. When it does not reproduce it, the Shin edge is taken
    from the recorded value (exact), and the credible-method gate passes only if the
    rebuilt worst-method edge clears MIN_EDGE by MORE than the discrepancy's size on the
    edge (|p_rebuild - p_stored| * odds) — i.e. only if the verdict cannot be an artefact
    of the missing rows. Otherwise it fails as `rebuild_mismatch_inconclusive`."""
    got = _consensus_anchor(sides, side_q)
    if got is None:
        return False, {"reason": "no_pick_time_consensus",
                       "books_with_quotes": sorted({b for s in sides for b in (side_q.get(s) or {})})}
    prob_by_sel, anchor_ts, n_books = got
    p = prob_by_sel[selection]
    edge = p * odds - 1.0
    edges = {"shin": edge}
    for meth in CREDIBLE_DEVIG_METHODS:
        if meth == "shin":
            continue
        ga = _consensus_anchor(sides, side_q, lambda o, meth=meth: devig_by(meth, o))
        if ga is not None:
            edges[meth] = ga[0][selection] * odds - 1.0
    credible_min = min(edges.values())
    mismatch = stored_p is not None and abs(p - stored_p) > 1e-9
    slack = abs(p - stored_p) * odds if mismatch else 0.0
    shin_edge = stored_p * odds - 1.0 if mismatch else edge
    own = (side_q.get(selection) or {}).get(bookmaker)
    gap = abs((own[1] - anchor_ts).total_seconds()) / 60.0 if own else None
    ratio = odds * p - 1.0            # odds / (1/p) - 1 == the edge vs the fair line
    checks = {
        "min_books": n_books >= CONSENSUS_MIN_BOOKS,
        "aligned": gap is not None and gap <= ALIGN_MIN,
        "max_odds": odds <= MAX_ODDS,
        "max_ratio": ratio <= MAX_RATIO,
        "min_edge": shin_edge >= MIN_EDGE,
        "max_edge": shin_edge <= CONSENSUS_MAX_EDGE,
        "credible_methods": credible_min - slack >= MIN_EDGE,
    }
    passes = all(checks.values())
    failed = [k for k, v in checks.items() if not v]
    reason = "passes" if passes else ",".join(failed)
    if mismatch and failed == ["credible_methods"]:
        reason = "rebuild_mismatch_inconclusive"
    return passes, {
        "reason": reason,
        "rebuild_reproduces_stored_p": not mismatch,
        "mismatch_slack_on_edge": round(slack, 6),
        "shin_edge_used": round(shin_edge, 6),
        "n_books": n_books, "p_shin": p,
        "edges": {k: round(v, 6) for k, v in edges.items()},
        "edge_credible_min": round(credible_min, 6),
        "alignment_gap_minutes": None if gap is None else round(gap, 2),
        "checks": checks,
    }


def evaluate_sharp_leg(pick: dict) -> tuple[bool, dict]:
    """The live arm's CURRENT rule for one published leg, from its STORED pick-time
    anchor fields (a Pinnacle anchor is not rebuilt: v1's 2026-09-14 snapshots are past
    the intraday retention, and the stored fields are what the publisher computed)."""
    anchor_odds = pick["anchor_odds"] or {}
    if isinstance(anchor_odds, str):
        anchor_odds = json.loads(anchor_odds)
    a = anchor_odds.get(pick["selection"])
    ratio = pick["odds"] / float(a) - 1.0 if a else None
    checks = {
        "anchor_overround": pick["anchor_overround"] is not None
                            and pick["anchor_overround"] <= MAX_ANCHOR_OVERROUND,
        "max_ratio": ratio is not None and ratio <= MAX_RATIO,
        "max_odds": pick["odds"] <= MAX_ODDS,
        "min_edge": pick["edge"] is not None and pick["edge"] >= MIN_EDGE,
        "aligned": pick["alignment_gap_minutes"] is not None
                   and pick["alignment_gap_minutes"] <= ALIGN_MIN,
    }
    passes = all(checks.values())
    return passes, {
        "reason": "passes" if passes else ",".join(k for k, v in checks.items() if not v),
        "anchor_overround": pick["anchor_overround"],
        "price_ratio": None if ratio is None else round(ratio, 6),
        "checks": checks, "source": "stored pick-time anchor fields",
    }


def recheck(pick: dict) -> tuple[bool, dict]:
    if pick["arm"] != CONSENSUS_ARM:
        return evaluate_sharp_leg(pick)
    sides = MARKETS[pick["market"]]
    rows = execute_query(QUOTES_SQL, (pick["match_id"], pick["market"], list(EXCLUDED_BOOKS),
                                      pick["published_at"], pick["published_at"]))
    side_q: dict = {}
    for r in rows:
        side_q.setdefault(r["selection"], {})[r["bookmaker"]] = (r["odds"], r["timestamp"])
    passes, d = evaluate_consensus_leg(side_q, sides, pick["selection"], pick["odds"],
                                       pick["bookmaker"], stored_p=pick["p_sharp"])
    # Consistency with what the publisher recorded at the time (same data, same code
    # should give the same number; a gap means the rebuild saw a different book set).
    if "p_shin" in d and pick["p_sharp"] is not None:
        d["stored_p_sharp"] = pick["p_sharp"]
        d["p_rebuild_diff"] = round(d["p_shin"] - pick["p_sharp"], 6)
    d["source"] = "odds_snapshots as of published_at"
    return passes, d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="record the verdicts")
    args = ap.parse_args()

    picks = execute_query(PICKS_SQL, (list(CURRENT_RULE), list(CURRENT_RULE.values())))
    try:
        done = {(r["pick_id"], r["checked_rule_version"]) for r in execute_query(
            "SELECT pick_id::text AS pick_id, checked_rule_version FROM pick_rule_recheck")}
    except Exception:
        if args.write:
            raise                                   # migration 431 not applied — nowhere to write
        done = set()
    tally: dict = {}
    for p in picks:
        target = CURRENT_RULE[p["arm"]]
        if (p["id"], target) in done:
            continue                                # frozen verdict — never re-derived
        passes, d = recheck(p)
        d["published_rule_version"] = p["rule_version"]
        key = (p["arm"], p["grade"], p["rule_version"])
        t = tally.setdefault(key, [0, 0])
        t[0 if passes else 1] += 1
        print(f"{'PASS' if passes else 'fail'}  {p['arm']:16} {p['grade'] or '-'}  "
              f"{p['market']}/{p['selection']} @{p['odds']:.2f} {p['bookmaker']:12} "
              f"{d['reason']}  {d.get('edges', '')}  diff={d.get('p_rebuild_diff', '')}")
        if args.write:
            execute_write(
                """INSERT INTO pick_rule_recheck (pick_id, checked_rule_version, passes, details)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (pick_id, checked_rule_version) DO NOTHING""",
                (p["id"], target, passes, json.dumps(d, default=str)))
    for (arm, grade, rv), (ok, bad) in sorted(tally.items(), key=str):
        print(f"{arm} grade={grade} {rv} -> {CURRENT_RULE[arm]}: {ok} pass, {bad} fail")
    if not tally:
        print("nothing to re-check (every sent earlier-rule pick already has a verdict)")
    if not args.write:
        print("(dry run — pass --write to record)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
