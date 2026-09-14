#!/usr/bin/env python3
"""Publish the pre-registered sharp-edge PICKS forward test.

THE RULE IS LOCKED. It lives in dev/active/picks-forward-test-preregistration.md
and is restated in RULE below. Changing any constant here invalidates the test
and starts a new one with a new start date — that is the entire point of a
pre-registration, so if you are about to edit a number, edit the doc first and
say why.

    python3 scripts/publish_picks_forward_test.py            # dry run, prints
    python3 scripts/publish_picks_forward_test.py --send     # records + posts

WHY THIS IS NOT PART OF THE PIPELINE (yet): the pipeline's signaler path reads
`simulated_bets` and applies the MODEL edge floors. This rule uses no model and
a different anchor. Wiring it into the pipeline before the forward test resolves
would put an unproven rule behind the same machinery that published manufactured
edge for months. It runs standalone until n=800 decides it.
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from datetime import datetime, timezone

from workers.api_clients.db import execute_query, execute_write
from workers.model.devig import devig
from workers.notify.telegram import send_telegram_public

log = logging.getLogger("picks_forward")

RULE_VERSION = "sharp_edge_v1_2026_09_14"

# ── the locked rule ──────────────────────────────────────────────────────────
MIN_EDGE   = 0.03   # sharp edge floor
MAX_ODDS   = 4.0    # uncapped, the edge collapses into longshot noise
ALIGN_MIN  = 60.0   # anchor and bet quote within 60 min — see note below
TOP_N      = 8      # per day, by edge
LOOKAHEAD_H = 14    # publish for fixtures kicking off inside this window
MIN_LEAD_MIN = 45   # never publish a price a reader cannot reach in time

# ALIGNMENT is the load-bearing constant. Unaligned, this rule backtested
# +8.47pct; aligned it reads +5.5pct. The selection was picking STALE soft-book
# quotes — median gap on selected legs was 360 minutes. Pinnacle refreshes ~5
# min before kickoff; Coolbet is frozen 57pct of the time. Comparing a fresh
# anchor against a stale price measures drift, not mispricing.

# Feeds that quote prices the book does not honour. Measured against the books'
# own sites: AF Unibet 33.1pct phantom-high (and dead since 2026-09-12),
# Unibet-Kambi 38pct. Max/Avg are synthetic aggregates and the rest are
# football-data.co.uk CSV imports, not live books.
EXCLUDED_BOOKS = ("Max", "Avg", "Betfair Exchange", "BetWin", "Betfred",
                  "Unibet-Kambi", "Unibet")

MARKETS = {"1x2": ["home", "draw", "away"],
           "over_under_25": ["over", "under"]}

PICK_LABEL = {
    ("1x2", "home"): "Home win",
    ("1x2", "away"): "Away win",
    ("1x2", "draw"): "Draw",
    ("over_under_25", "over"): "Over 2.5 goals",
    ("over_under_25", "under"): "Under 2.5 goals",
}

HEADER = (
    "📊 <b>New selection method — starting today</b>\n\n"
    "We've rebuilt how these picks are chosen. They're now priced directly "
    "against the sharpest line in the market rather than against our own model."
    "\n\n<b>No past performance is claimed for this method.</b> It starts today "
    "at zero, and we'll publish the result — win or lose — as it accumulates."
    "\n\nToday: {n} picks."
)


def load_candidates() -> list[dict]:
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.bookmaker)
               o.match_id, o.market, o.selection, o.bookmaker,
               o.odds::float AS odds, o.timestamp,
               m.date AS kickoff,
               ht.name AS home_team, at.name AS away_team, l.name AS league
          FROM odds_snapshots o
          JOIN matches m  ON m.id  = o.match_id
          JOIN teams   ht ON ht.id = m.home_team_id
          JOIN teams   at ON at.id = m.away_team_id
          LEFT JOIN leagues l ON l.id = m.league_id
         WHERE o.market = ANY(%s)
           AND o.is_live IS NOT TRUE
           AND NOT (o.bookmaker = ANY(%s))
           AND m.date > now() + (%s || ' minutes')::interval
           AND m.date < now() + (%s || ' hours')::interval
           AND o.timestamp > now() - interval '6 hours'
         ORDER BY o.match_id, o.market, o.selection, o.bookmaker,
                  o.timestamp DESC
        """,
        (list(MARKETS), list(EXCLUDED_BOOKS), str(MIN_LEAD_MIN), str(LOOKAHEAD_H)),
    )

    quotes: dict = {}
    meta: dict = {}
    for r in rows:
        meta[r["match_id"]] = r
        quotes.setdefault(r["match_id"], {}).setdefault(r["market"], {}) \
              .setdefault(r["selection"], {})[r["bookmaker"]] = (
                  r["odds"], r["timestamp"])

    out: list[dict] = []
    for mid, by_market in quotes.items():
        for market, sides in MARKETS.items():
            side_q = by_market.get(market) or {}
            pin = {s: side_q.get(s, {}).get("Pinnacle") for s in sides}
            if any(pin[s] is None for s in sides):
                continue
            anchor_ts = max(pin[s][1] for s in sides)
            anchor_odds = {s: pin[s][0] for s in sides}
            overround = sum(1.0 / o for o in anchor_odds.values()) - 1.0
            probs = devig([anchor_odds[s] for s in sides])
            if probs is None:
                continue
            for s, p_sharp in zip(sides, probs):
                aligned = {
                    b: (o, t) for b, (o, t) in (side_q.get(s) or {}).items()
                    if abs((t - anchor_ts).total_seconds()) / 60.0 <= ALIGN_MIN
                }
                if not aligned:
                    continue
                book, (odds, ts) = max(aligned.items(), key=lambda kv: kv[1][0])
                if odds > MAX_ODDS:
                    continue
                edge = p_sharp * odds - 1.0
                if edge < MIN_EDGE:
                    continue
                m = meta[mid]
                out.append({
                    "match_id": mid, "market": market, "selection": s,
                    "odds": odds, "bookmaker": book, "edge": edge,
                    "p_sharp": p_sharp, "anchor_odds": anchor_odds,
                    "anchor_overround": overround, "anchor_quoted_at": anchor_ts,
                    "odds_quoted_at": ts,
                    "alignment_gap_minutes":
                        abs((ts - anchor_ts).total_seconds()) / 60.0,
                    "kickoff_at": m["kickoff"], "home_team": m["home_team"],
                    "away_team": m["away_team"], "league": m["league"] or "",
                })

    out.sort(key=lambda c: -c["edge"])
    return out[:TOP_N]


def junk_anchor_arm(live: list[dict], pool: list[dict]) -> list[dict]:
    """Negative control: same rule, anchor shuffled to a DIFFERENT fixture.

    Expected to lose roughly the vig. If this arm makes money the harness is
    broken and the live arm means nothing. Not published — recorded only.
    """
    if len(pool) < 2:
        return []
    rng = random.Random(20260914)
    arm = []
    for c in live:
        others = [o for o in pool if o["match_id"] != c["match_id"]
                  and o["market"] == c["market"]]
        if not others:
            continue
        donor = rng.choice(others)
        d = dict(c)
        d["p_sharp"] = donor["p_sharp"]
        d["edge"] = donor["p_sharp"] * c["odds"] - 1.0
        d["arm"] = "junk_anchor"
        arm.append(d)
    return arm


def record(c: dict, arm: str, message_id: int | None) -> None:
    execute_write(
        """
        INSERT INTO picks_forward_test
            (match_id, market, selection, odds, bookmaker, edge, p_sharp,
             anchor_odds, anchor_overround, anchor_quoted_at, odds_quoted_at,
             alignment_gap_minutes, arm, rule_version, kickoff_at,
             telegram_message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (match_id, market, selection, arm) DO NOTHING
        """,
        (c["match_id"], c["market"], c["selection"], c["odds"], c["bookmaker"],
         c["edge"], c["p_sharp"], json.dumps(c["anchor_odds"]),
         c["anchor_overround"], c["anchor_quoted_at"], c["odds_quoted_at"],
         c["alignment_gap_minutes"], arm, RULE_VERSION, c["kickoff_at"],
         message_id),
    )


def render(c: dict) -> str:
    pick = PICK_LABEL.get((c["market"], c["selection"]),
                          f"{c['market']} {c['selection']}")
    ko = c["kickoff_at"].astimezone(timezone.utc)
    return (
        f"⚽ <b>{c['home_team']} vs {c['away_team']}</b>\n"
        f"{c['league']} · {ko:%a %d %b · %H:%M UTC}\n\n"
        f"✅ Pick: <b>{pick}</b> @ <b>{c['odds']:.2f}</b> "
        f"at <b>{c['bookmaker']}</b>\n"
        f"📈 Edge vs sharp line: <b>+{c['edge'] * 100:.1f}%</b>\n\n"
        f"<a href='https://oddsintel.app/picks'>Live picks</a>"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true",
                    help="actually post to the public channel and record")
    ap.add_argument("--no-header", action="store_true",
                    help="skip the method-change header (use after day 1)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    picks = load_candidates()
    if not picks:
        print("no qualifying picks — nothing to publish (this is a valid outcome)")
        return 0

    print(f"{len(picks)} picks under {RULE_VERSION}\n")
    for c in picks:
        print(f"  +{c['edge']*100:5.2f}%  {c['kickoff_at']:%H:%M}  "
              f"{c['home_team']} v {c['away_team']}  "
              f"{c['market']}/{c['selection']} @{c['odds']:.2f} {c['bookmaker']}  "
              f"gap={c['alignment_gap_minutes']:.0f}m")

    if not args.send:
        print("\n(dry run — pass --send to publish)")
        return 0

    if not args.no_header:
        send_telegram_public(HEADER.format(n=len(picks)))

    sent = 0
    for c in picks:
        mid = send_telegram_public(render(c))
        if mid is None:
            log.warning("send FAILED: %s v %s — recording anyway, unpublished",
                        c["home_team"], c["away_team"])
        else:
            sent += 1
        record(c, "live", mid)

    # negative control — recorded, never published
    for c in junk_anchor_arm(picks, picks):
        record(c, "junk_anchor", None)

    print(f"\npublished {sent}/{len(picks)} to the channel, all recorded")
    return 0 if sent == len(picks) else 1


if __name__ == "__main__":
    sys.exit(main())
