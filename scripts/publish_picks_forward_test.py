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

from workers.api_clients.db import (
    execute_query, execute_write, execute_write_returning,
)
from workers.model.devig import devig
from workers.notify.telegram import send_telegram_public

log = logging.getLogger("picks_forward")

RULE_VERSION = "sharp_edge_v4_2026_09_15"

# ── the locked rule ──────────────────────────────────────────────────────────
MIN_EDGE   = 0.03   # sharp edge floor
MAX_ODDS   = 4.0    # uncapped, the edge collapses into longshot noise
ALIGN_MIN  = 60.0   # anchor and bet quote within 60 min — see note below
TOP_N      = 8      # per CALENDAR DAY (UTC), counted in the DB — see daily_room()

# RULE-V4-2026-09-15 — cadence. v1-v3 published in ONE batch at 10:00 UTC. The
# candidate window is `now+45min .. now+14h`, so a single 10:00 run can never see
# a kickoff before ~10:45 and can never see 00:00-03:00 kickoffs at all: measured,
# 47% of qualifying legs were structurally unreachable, and an independent replay
# had the 10:00 slot catching 5 of 16 legs on a two-day sample.
#
# v4 runs every 30 minutes. Three things had to be true first, and now are:
#   1. claim-before-send (PUBLISH-CLAIM-BEFORE-SEND) — a leg re-qualifies in a
#      median of 6 consecutive runs, so send-then-record would have posted the
#      same pick ~6 times to 62 subscribers.
#   2. a DB-backed daily cap — `select()` alone caps per CALL, which is
#      meaningless when there are 48 calls a day.
#   3. price persistence measured — 94% of qualifying prices still clear the
#      floor at the same book 30 minutes later (62% at 60), so a 30-minute
#      cadence publishes prices a reader can still get.
#
# WHY THIS IS v4 AND NOT AN EDIT TO v3: it changes WHICH bets are selected, not
# just when they are looked at. The pre-registration is explicit that this starts
# a new test with a new start date. The cost is zero — v2 and v3 published
# nothing at all, so no accumulated n is being discarded.
MAX_RATIO  = 0.20   # book price may not exceed the anchor by more than this
MAX_ANCHOR_OVERROUND = 0.04   # [v3] the anchor must actually BE a sharp line

# MAX_ANCHOR_OVERROUND is v3's one change (PICKS-ANCHOR-QUALITY-GATE-2026-09-14).
#
# `anchor_overround` was already COMPUTED on every leg (see load_candidates) and
# then thrown away. This is the project's dominant failure shape — a number
# computed but never surfaced — reproduced on a PUBLIC, live, Telegram-published
# feed whose own HEADER below claims the picks are "priced directly against the
# sharpest line in the market". On a third of the slate that is false.
#
# Measured 2026-09-14, this rule's own population, 90d, Shin de-vig, aligned
# <=60 min, the three bettable books (n=326, ROI -13.54% overall):
#
#     anchor overround band          n     ROI
#     <4%   sharp-grade anchor      72    -3.36%
#     4-6%                          76   -22.07%
#     6-9%                          58   -16.00%
#     >=9%  goodwill quote         120   -13.06%   <- 34.1% of the slate
#
# And a paired live-Pinnacle test the same day (n=92, 39 leagues) confirms the
# >=9% band is NOT a feed artefact: where our AF row says 9.28%, real Pinnacle
# says 9.26% (+0.00pp). Pinnacle genuinely charges 9%+ there. A price with no
# size behind it is a quote, not a line, and an "edge" against it is two soft
# prices disagreeing. 4% is the same threshold docs/ANCHOR_IS_NOT_SHARP_2026_09_14.md
# uses for "plausibly a real line", and it is the band where our stored anchor
# is verified accurate (paired delta +0.10pp vs +1.05pp in the 4-6% band).
#
# ⚠️ THIS IS AN HONESTY FIX, NOT AN ALPHA FIX — state it plainly whenever the
# rule is described. Gating does NOT make the rule profitable: the retained band
# is still -3.36% at n=72. It stops us publishing an edge computed against a
# quote with no size behind it. If the honest answer stays "this rule has no
# demonstrable edge at any anchor quality", that belongs on /performance and in
# the Telegram feed — publishing +X% while the honest number is negative is the
# exact pattern CLAUDE.md was written to prevent.
#
# 👥 PICKS — it changes what readers are told, not what we stake.
#
# VOLUME COST, stated up front because it is the owner's call and not an
# implementation detail (CLAUDE.md, "when the two directions conflict, say so"):
# the <4% band was 72 of 326 legs (22%) in the ROI split and 11 of 92 fixtures
# (12%) in the paired test. At TOP_N=8/day this will often publish fewer than 8
# picks. Loosening to 0.06 roughly doubles volume and admits the worst-measured
# band (-22.07%). That trade-off is the owner's to make; the constant is here.

# MAX_RATIO is v2's one change, and it is the ONLY change (RULE-V2-2026-09-15).
#
# Production has carried ODDS-OUTLIER-FILTER-2026-08-18 since August — >35 pct
# over anchor for 1x2 — because AF's Bet365 quotes run ~26.6 pct above
# contemporaneous Pinnacle: stale or shell prices nobody can take. This
# standalone publisher never applied it. Six of the eight picks published on
# 2026-09-14 under v1 sat above 20 pct, and the top one at +35.7 pct.
#
# Measured on the time-aligned backtest, ROI by book/anchor ratio band:
#     0-10 pct   n=202  +11.75 pct
#    10-20 pct   n=763   +6.24 pct
#    20-35 pct   n=225  -12.13 pct   <-- the leak
#    35+  pct    n= 44   +7.32 pct   (n too small to read)
#
# So the loss lives in 20-35 pct, BELOW the existing 35 pct filter — exactly what
# the BET365-EXECUTION-AUDIT note predicted ("the ~20-30 pct band still leaks
# through and generates -20 pct ROI picks"). Capping at 20 pct moves the rule
# from +3.83 pct to +7.39 pct (n 1234 -> 965).
#
# WHY THIS IS A NEW TEST, NOT A PATCH. The pre-registration says changing the
# rule after the first publication invalidates the test and starts a new one
# with a new start date. It does. v1 is 8 picks on 2026-09-14 and is CLOSED at
# that n; v2 starts fresh. Quietly tightening a running pre-registered rule and
# carrying the n forward is precisely the discipline failure the whole document
# exists to prevent.
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


def load_candidates() -> tuple[list[dict], list[dict]]:
    """Returns (live picks, full candidate pool).

    The POOL is every leg that clears the odds cap and the alignment window,
    with the edge floor NOT yet applied. The live arm is the pool filtered at
    MIN_EDGE and cut to TOP_N. The junk arm needs the unfiltered pool because it
    re-runs the SAME rule — floor and all — on a shuffled anchor; selecting from
    the live picks instead would make the control a relabelling of the live arm
    rather than an independent draw. See junk_anchor_arm().
    """
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
            if overround > MAX_ANCHOR_OVERROUND:
                continue          # [v3] not a sharp line — see MAX_ANCHOR_OVERROUND.
                                  # Applied to the POOL, not in select(), so the
                                  # junk arm is gated identically: whatever test
                                  # the live arm gets, every control arm gets.
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
                if odds / anchor_odds[s] - 1.0 > MAX_RATIO:
                    continue          # phantom/stale price — see MAX_RATIO
                edge = p_sharp * odds - 1.0
                # NOTE: the MIN_EDGE floor is applied by select() below, not
                # here — the junk arm must see the same unfiltered pool.
                m = meta[mid]
                out.append({
                    "match_id": mid, "market": market, "selection": s,
                    "odds": odds, "bookmaker": book, "edge": edge,
                    "p_sharp": p_sharp, "anchor_odds": anchor_odds,
                    "anchor_overround": overround, "anchor_quoted_at": anchor_ts,
                    "price_ratio": odds / anchor_odds[s] - 1.0,
                    "odds_quoted_at": ts,
                    "alignment_gap_minutes":
                        abs((ts - anchor_ts).total_seconds()) / 60.0,
                    "kickoff_at": m["kickoff"], "home_team": m["home_team"],
                    "away_team": m["away_team"], "league": m["league"] or "",
                })

    return select(out), out


def daily_room() -> int:
    """How many more live picks may publish today. TOP_N minus what is already out.

    RULE-V4: `select()` caps per CALL. Under a 30-minute cadence that is 48 calls
    a day and the cap stops meaning anything, so the count has to come from the
    database.

    Counted on `published_at::date` in UTC, deliberately, NOT on kickoff date:
    with LOOKAHEAD_H=14 a single run spans two kickoff dates, which makes a
    kickoff-date cap unenforceable. The visible consequence is that 00:00-03:00
    kickoffs are only ever in window from ~10:00-13:00 the previous day and so
    consume the PREVIOUS day's allowance. That is a real quirk and it is written
    down rather than discovered later.

    Falls CLOSED (returns 0) if the count cannot be read. A cap that fails open
    is not a cap, and the surface it protects is a public channel."""
    try:
        rows = execute_query(
            """SELECT count(*) AS n FROM picks_forward_test
                WHERE arm = 'live' AND published_at::date = (now() AT TIME ZONE 'utc')::date"""
        )
        return max(0, TOP_N - int(rows[0]["n"]))
    except Exception as e:
        log.warning("daily_room unreadable — publishing nothing this pass: %s", e)
        return 0


def select(cands: list[dict], room: int | None = None) -> list[dict]:
    """Edge floor, then the best `room` by edge.

    `room` defaults to TOP_N so the junk-anchor control and any offline caller
    keep the per-call behaviour they were measured with; the live path passes
    `daily_room()`."""
    keep = [c for c in cands if c["edge"] >= MIN_EDGE]
    keep.sort(key=lambda c: -c["edge"])
    return keep[: (TOP_N if room is None else room)]


def junk_anchor_arm(pool: list[dict]) -> list[dict]:
    """Negative control: the SAME rule, anchor shuffled to a DIFFERENT fixture.

    Expected to lose roughly the vig. If this arm makes money the harness is
    broken and the live arm means nothing. Not published — recorded only.

    JUNK-ARM-DEGENERATE-2026-09-14 — what this used to do, and why it could not
    work. The first version took the eight LIVE picks, overwrote each one's
    `p_sharp` with a donor's, and recorded them. Selection never changed: the
    junk rows were the same eight fixtures, markets, selections and prices as
    the live arm, so they were guaranteed to settle to identical outcomes. The
    control could not disagree with the live arm about anything. Verified on the
    day-one rows — all eight junk rows match a live row on
    (match_id, market, selection, odds, bookmaker).

    A junk anchor has to change WHICH BETS ARE CHOSEN, because that is the only
    thing the anchor does in this rule. So: assign every candidate in the pool a
    donor `p_sharp` from a different fixture in the same market, recompute the
    edge, and run the same floor-and-top-N selection over the result. The picks
    that come out are a different set, chosen by a number that carries no
    information — which is exactly the null this test needs.
    """
    if len(pool) < 2:
        return []
    # RULE-V4: seed per (date, run) rather than with a fixed constant. Under one
    # run a day a constant seed was merely reproducible; under 48 runs it makes
    # every run's draw IDENTICAL, so the control stops being an independent
    # sample and starts being the same draw counted 48 times.
    rng = random.Random(int(datetime.now(timezone.utc).timestamp()) // 1800)
    junk: list[dict] = []
    for c in pool:
        others = [o for o in pool if o["match_id"] != c["match_id"]
                  and o["market"] == c["market"]]
        if not others:
            continue
        donor = rng.choice(others)
        d = dict(c)
        d["p_sharp"] = donor["p_sharp"]
        d["edge"] = donor["p_sharp"] * c["odds"] - 1.0
        d["arm"] = "junk_anchor"
        junk.append(d)
    return select(junk)


def claim(c: dict, arm: str) -> str | None:
    """Reserve this leg BEFORE sending, returning its new row id — or None if it
    was already published.

    PUBLISH-CLAIM-BEFORE-SEND (2026-09-15). `record()` used to run AFTER the
    Telegram send, and `ON CONFLICT ... DO NOTHING` then suppressed the duplicate
    ROW while the duplicate MESSAGE had already gone out to 62 subscribers. That
    was survivable at one run a day and is not survivable at any other cadence: a
    qualifying leg re-qualifies in a median of 6 consecutive runs (mean 6.8, max
    13), so 13 picks would have produced ~88 channel messages.

    It also broke the invariant the read path states in as many words — that the
    published set and the recorded ledger are the same set. With send-then-record
    the ledger kept the FIRST row and the channel showed the LAST message, and on
    a measured day those disagreed on price by 3.6% (2.75 -> 2.85, edge +4.3% ->
    +8.1%).

    The database is the only thing that can arbitrate this. `_LAST_SENT` in
    workers/notify/telegram.py cannot: `send_telegram_public` has no dedup window
    at all, and `_LAST_SENT` is an in-process dict wiped on every scheduler
    restart — which is exactly how RELIABILITY_LEDGER #13 happened, four restarts
    in ninety minutes.

    So: INSERT ... RETURNING id. A returned id means WE created the row and may
    send. An empty result means somebody already did, and we must not.
    """
    rows = execute_write_returning(
        """
        INSERT INTO picks_forward_test
            (match_id, market, selection, odds, bookmaker, edge, p_sharp,
             anchor_odds, anchor_overround, anchor_quoted_at, odds_quoted_at,
             alignment_gap_minutes, arm, rule_version, kickoff_at,
             telegram_message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (match_id, market, selection, arm) DO NOTHING
        RETURNING id
        """,
        (c["match_id"], c["market"], c["selection"], c["odds"], c["bookmaker"],
         c["edge"], c["p_sharp"], json.dumps(c["anchor_odds"]),
         c["anchor_overround"], c["anchor_quoted_at"], c["odds_quoted_at"],
         c["alignment_gap_minutes"], arm, RULE_VERSION, c["kickoff_at"],
         None),
    )
    return str(rows[0]["id"]) if rows else None


def attach_message_id(pick_id: str, message_id: int | None) -> None:
    """Stamp the Telegram message id onto a row we already claimed. Separate from
    `claim` because the send sits between them — and a send that fails must leave
    the row in place, recorded and unpublished, rather than rolling back a claim
    another run would then re-send."""
    if message_id is None:
        return
    execute_write(
        "UPDATE picks_forward_test SET telegram_message_id = %s WHERE id = %s",
        (message_id, pick_id),
    )


GRADE_B_EDGE = 0.03   # == MIN_EDGE: the price at which a leg becomes a pick
GRADE_A_EDGE = 0.05


def required_odds(p_sharp: float) -> tuple[float, float, float]:
    """(break-even, grade-B price, grade-A price) for a given sharp probability.

    edge = p_sharp * odds - 1, so the price needed for a target edge t is
    (1 + t) / p_sharp. Exact arithmetic off the sharp line — it states what the
    pick is worth taking at, and predicts nothing about whether it wins.
    """
    return 1.0 / p_sharp, (1.0 + GRADE_B_EDGE) / p_sharp, (1.0 + GRADE_A_EDGE) / p_sharp


def write_board(pool: list[dict]) -> int:
    """Replace the live candidate board that /picks renders as a watchlist.

    PICKS-BOARD-WATCHLIST (2026-09-15). The rule publishes only legs over the
    floor, and on a flat day that is nothing at all — 0 of 31 on the day this
    was written. Rather than lower the floor, show the board with the price each
    leg would need. A reader who finds 2.18 has a pick; one who only finds 2.15
    knows to leave it.

    ⚠️ This writes `picks_board`, NEVER `picks_forward_test`. A row here did not
    qualify; counting it in the pre-registered ledger would inflate n with bets
    nobody was told to take. Separate table, separate view, no `arm` column.

    Only legs at or above break-even are kept: below that the sharp line says
    the price is bad at any grade, and listing it would be noise.
    """
    rows = []
    for c in pool:
        be, b3, a5 = required_odds(c["p_sharp"])
        if c["odds"] < be:
            continue
        rows.append((c["match_id"], c["market"], c["selection"], c["odds"],
                     c["bookmaker"], c["p_sharp"], c["edge"], be, b3, a5,
                     c["anchor_overround"], c["kickoff_at"]))
    if not rows:
        return 0
    try:
        # Replace, not append: this is "the board as it stands", not history.
        execute_write("DELETE FROM picks_board WHERE kickoff_at < NOW()")
        for r in rows:
            execute_write(
                """INSERT INTO picks_board
                     (match_id, market, selection, odds, bookmaker, p_sharp,
                      edge, odds_breakeven, odds_grade_b, odds_grade_a,
                      anchor_overround, kickoff_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
                   ON CONFLICT (match_id, market, selection) DO UPDATE SET
                     odds = EXCLUDED.odds, bookmaker = EXCLUDED.bookmaker,
                     p_sharp = EXCLUDED.p_sharp, edge = EXCLUDED.edge,
                     odds_breakeven = EXCLUDED.odds_breakeven,
                     odds_grade_b = EXCLUDED.odds_grade_b,
                     odds_grade_a = EXCLUDED.odds_grade_a,
                     anchor_overround = EXCLUDED.anchor_overround,
                     updated_at = NOW()""", r)
    except Exception as e:
        log.warning("write_board failed (non-fatal — publishing is unaffected): %s", e)
        return 0
    return len(rows)


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

    picks, pool = load_candidates()
    # RULE-V4: re-select against the DB-backed daily allowance. load_candidates()
    # caps per call (correct for offline analysis); the live path must cap per DAY.
    room = daily_room()
    picks = select(pool, room)
    if args.send:
        print(f"board refreshed: {write_board(pool)} legs at/above break-even")
    if not picks:
        print(f"no qualifying picks — nothing to publish (valid outcome; "
              f"{room} of {TOP_N} slots free today)")
        return 0

    print(f"{len(picks)} picks under {RULE_VERSION}\n")
    for c in picks:
        print(f"  +{c['edge']*100:5.2f}%  {c['kickoff_at']:%H:%M}  "
              f"{c['home_team']} v {c['away_team']}  "
              f"{c['market']}/{c['selection']} @{c['odds']:.2f} {c['bookmaker']}  "
              f"gap={c['alignment_gap_minutes']:.0f}m "
              f"ratio={c['price_ratio']*100:+.0f}%")

    if not args.send:
        print("\n(dry run — pass --send to publish)")
        return 0

    if not args.no_header:
        send_telegram_public(HEADER.format(n=len(picks)))

    sent = 0
    for c in picks:
        pick_id = claim(c, "live")
        if pick_id is None:
            log.info("already published, not re-sending: %s v %s",
                     c["home_team"], c["away_team"])
            continue
        mid = send_telegram_public(render(c))
        if mid is None:
            log.warning("send FAILED: %s v %s — row kept, unpublished",
                        c["home_team"], c["away_team"])
        else:
            sent += 1
            attach_message_id(pick_id, mid)

    # Negative control — recorded, never published. Same cadence AND same daily
    # room as the live arm: an uncapped control accumulates the union of every
    # run's draw while live accumulates one capped set, and the two arms stop
    # being the same rule.
    for c in junk_anchor_arm(pool)[:max(0, room)]:
        claim(c, "junk_anchor")

    print(f"\npublished {sent}/{len(picks)} to the channel, all recorded")
    return 0 if sent == len(picks) else 1


if __name__ == "__main__":
    sys.exit(main())
