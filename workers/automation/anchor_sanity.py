"""ANCHOR-PRICE-SANITY — refuse a book price the sharp anchor contradicts.

WHY THIS EXISTS (SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES-2026-09-20).
`bot_trigger_1x2_sharp_v1` published 25 picks and **every one of them was
priced off a quote belonging to a DIFFERENT fixture**. The bot's fair value is
the Shin-de-vigged Pinnacle line, so a real overlay tops out around +6.6%
(`pick_generator._candidates_from_sharp` says so in its own docstring). Its 25
picks ran +10.9% to +52.2%, median +19.6%. Beitar Jerusalem were stored at
18.00 to win a match Pinnacle priced at 1.67; Southern District at 101.00
against a true 1.83. Those are not edges, they are other people's football
matches.

THE UPSTREAM FAULT is fixture matching. `coolbet_placer.fuzzy_match_event`
scores each of our two teams with `max()` over BOTH sides of a candidate event,
so nothing stops home and away from matching the SAME side — and the Unibet
bulk sweep (`unibet_odds_feed._async_run_bulk`) draws candidates from an entire
COUNTRY's lobby, where "Maccabi Netanya vs Maccabi Tel Aviv" sits beside a
dozen other Maccabi fixtures. That fault is being fixed at source separately;
this module is the LAST LINE, and it is here because the last line was missing
entirely.

WHY A GUARD AND NOT JUST THE SOURCE FIX. Every gate in this system is
`edge = p - 1/odds`. A price from the wrong fixture does not read as a broken
row — it reads as the LARGEST EDGE ON THE BOARD. So this fault is actively
SELECTED FOR by the very thing that decides what we stake, and any future
mis-mapping, from any feed, will be found by our bots before it is found by us.
A source fix removes one cause; this removes the consequence of all of them.

WHY IT DID NOT ALREADY EXIST. It did — in `scripts/anchor_book_sharpness_
research.py` as the "§9 outlier guard", and only there. The production pricing
paths (`best_price_router._latest_book_odds`, `jobs/pick_trigger_matcher`)
never consulted the anchor at all. That is this repo's most-repeated failure
shape, logged in `docs/RELIABILITY_LEDGER.md`: a second code path inheriting no
gates. `MAX_ODDS_RATIO` below is deliberately the SAME number as §9 so the
research script and production cannot drift into two different definitions of
"impossible price".

CALIBRATION — the threshold is measured, not guessed. Over 30 days and 7,457
fixtures with a paired Pinnacle 1x2 line, the max-over-selections relative
implied-probability deviation of Coolbet/Epicbet/Unibet-Site from Pinnacle is
p50 0.071, p90 0.196, p95 0.278, p99 0.856. `MAX_ODDS_RATIO` of 1.5625 is a
relative implied-prob deviation of 36%, i.e. just past p97 — comfortably above
ordinary book disagreement and roughly 5x the largest overlay this project has
ever observed against Pinnacle. It cannot reject a real edge: a +6.6% overlay
on a 2.00 shot is a price of ~2.30, a ratio of 1.15.

WHY THIS GUARD IS NOT SUFFICIENT ON ITS OWN, and what covers the rest. A ratio
test is only as sharp as the price range it works in. 1x2 prices span 1.02 to
101, so a wrong fixture usually blows straight past 1.56x — it caught 20 of the
25 bad 1x2 picks. **O/U prices are compressed into roughly 1.2-3.0, so even a
completely wrong fixture rarely moves the ratio past the threshold**: all six
`trigger_ou_sharp` picks passed this guard, while Unibet-Site's O/U 2.5 on
Hapoel Tel Aviv read over 2.20 / under 1.58 against Pinnacle's over 1.69 /
under 2.19 AND Coolbet's over 1.62 / under 2.15 — the two-way market inverted,
i.e. unmistakably another fixture. The complement is `BotConfig.edge_ceiling`,
which works in edge space instead of price space and therefore does not care
how compressed the market is. Neither gate subsumes the other; both are needed.

CONSENSUS FALLBACK (#113, 2026-09-23): with no Pinnacle quote the reference is the
median raw price of >= 4 other books (`consensus_median_quotes`).

FAIL-OPEN, DELIBERATELY. With no anchor quote AND no 4-book median there is no evidence, and
refusing every fixture Pinnacle does not price would silently delete most of
the obscure-league coverage these bots run on — trading a known fault for an
invisible one. Absent anchor => allowed, and counted so the blind spot is
measurable. This costs nothing on the sharp-anchored bots, which by
construction only raise picks on fixtures that HAVE a complete Pinnacle line.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# The reference line. Same book `pick_triggers._SHARP_ANCHOR_BOOK` de-vigs for
# fair value — a price that book contradicts this hard is not a price.
ANCHOR_BOOK = "Pinnacle"

# Same constant as `scripts/anchor_book_sharpness_research.OUTLIER_MAX_RATIO`,
# and the same squared comparison, so the two cannot drift apart. See the
# CALIBRATION note above for why 1.25**2 and not something tighter.
OUTLIER_MAX_RATIO = 1.25
MAX_ODDS_RATIO = OUTLIER_MAX_RATIO ** 2  # 1.5625
MIN_PROB_GAP = 0.04   # …AND this far apart in implied probability (board_guard's rule)
MAX_LONGSHOT_RATIO = 2.5   # hard ceiling whatever the probability gap

# The sharp WINDOW's upper bound: `max_odds = min_odds x OUTLIER_MULT`. A book
# price above that is a stale or mis-mapped quote, not a gift.
#
# IT LIVES HERE, NOT IN `pick_triggers`, AND THAT PLACEMENT IS LOAD-BEARING.
# Both sharp engines need it — `pick_triggers`/`pick_trigger_matcher` (which
# defined it) and `pick_generator` (the clone that dropped it, which is how the
# phantom picks got through). But `pick_generator.generate()` is forbidden by
# smoke `PICK-GENERATOR` from referencing `pick_triggers` at all, for a good
# reason: it must DERIVE min/max odds at decision time from `cal_prob`, never
# read the precomputed window rows — stored derivations are the shape behind a
# whole family of bugs in this repo. Importing the constant from a neutral
# module satisfies both invariants honestly: one definition, no window read,
# and no re-typed 1.6 (a copied constant is exactly how the two paths diverged).
OUTLIER_MULT = 1.6


def is_anchor_sane(book_odds: float | None, anchor_odds: float | None) -> bool:
    """True when `book_odds` is close enough to the anchor to be the same fixture.

    Checked in BOTH directions: a cross-matched or inverted triple makes one leg
    far too long AND its partner far too short, and only the too-long leg reads
    as an edge — but the too-short one is the same broken row and must not be
    trusted either (it can still clear a floor on another selection).

    No anchor => True (fail open, see module docstring).
    """
    if anchor_odds is None or book_odds is None:
        return True
    try:
        b, a = float(book_odds), float(anchor_odds)
    except (TypeError, ValueError):
        return True
    if b <= 1.0 or a <= 1.0:
        return True
    # #112 (8), 2026-09-24: a ratio alone is noise at long odds — 21 vs 12 is x1.75 but
    # 3.5 probability points, and correct 17–21 longshots (China v Maldives 91 vs a 51
    # median) were being dropped as "wrong fixture". A wrong-fixture / inverted leg is far
    # apart in PROBABILITY too, so require both — the same rule board_guard adopted
    # (MIN_PROB_GAP) after its first dry run. Price SIZE is capped elsewhere (the outlier
    # ceilings, pick_generator._own_outlier_ok); this is an identity check.
    r = max(b / a, a / b)
    if r <= MAX_ODDS_RATIO:
        return True
    # review 2026-09-24: above an anchor of ~25 the prob-gap test can never refuse, so cap
    # the ratio outright (largest genuine longshot flip seen in 7 days: x1.93).
    if r > MAX_LONGSHOT_RATIO:
        return False
    return abs(1.0 / b - 1.0 / a) <= MIN_PROB_GAP


def anchor_quotes(match_id: str, market: str) -> dict[str, float]:
    """Latest PRE-MATCH anchor price per selection for one (match, market).

    Deliberately NOT freshness-capped, unlike the placeable books it judges. A
    four-hour-old Pinnacle quote is a poor price to stake against but a perfectly
    good answer to "is this even the right match?", which is the only question
    asked here. Capping it would make the guard go blind exactly when a feed is
    lagging — which is when mis-mapped rows are most likely.
    """
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.selection) o.selection, o.odds::float AS odds
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = %s AND o.market = %s AND o.bookmaker = %s
           AND o.is_live IS NOT TRUE AND o.timestamp <= m.date
         ORDER BY o.selection, o.timestamp DESC
        """,
        (match_id, market, ANCHOR_BOOK),
    )
    pin = {r["selection"]: float(r["odds"]) for r in (rows or []) if r["odds"]}
    if pin:
        return pin
    # ANCHOR-WIDENING (#113, 2026-09-23). With no Pinnacle quote this guard used to be
    # blind (fail-open) on ~41% of priced fixtures — exactly the obscure leagues where
    # mis-mapped fixtures live. Fall back to the MEDIAN raw price across >=
    # CONSENSUS_QUORUM other books (the mirror_guard quorum): one wrong-fixture quote
    # cannot move a median, and a price 1.56x off it is a data fault, not an edge.
    # Still fail-open below the quorum. Same "no freshness cap" reasoning as above.
    return consensus_median_quotes(match_id, market)


CONSENSUS_QUORUM = 4
_NOT_A_REFERENCE = ("Pinnacle", "Max", "Avg", "Betfair Exchange", "BetWin", "Betfred",
                    "Unibet", "Unibet-Kambi", "Coolbet-OddsAPI")


def consensus_median_quotes(match_id: str, market: str) -> dict[str, float]:
    """Median of each book's latest pre-match price per selection, where at least
    CONSENSUS_QUORUM books quote that selection."""
    from statistics import median
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.bookmaker, o.selection) o.selection, o.odds::float AS odds
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = %s AND o.market = %s AND NOT (o.bookmaker = ANY(%s))
           AND o.is_live IS NOT TRUE AND o.timestamp <= m.date AND o.odds > 1.0
         ORDER BY o.bookmaker, o.selection, o.timestamp DESC
        """,
        (match_id, market, list(_NOT_A_REFERENCE)),
    ) or []
    by_sel: dict[str, list] = {}
    for r in rows:
        by_sel.setdefault(r["selection"], []).append(float(r["odds"]))
    return {s: median(v) for s, v in by_sel.items() if len(v) >= CONSENSUS_QUORUM}
