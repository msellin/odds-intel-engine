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

FAIL-OPEN, DELIBERATELY. With no anchor quote there is no evidence, and
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
    return max(b / a, a / b) <= MAX_ODDS_RATIO


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
    return {r["selection"]: float(r["odds"]) for r in (rows or []) if r["odds"]}
