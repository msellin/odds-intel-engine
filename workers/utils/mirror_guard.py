"""MIRROR-GUARD — refuse a 1X2 triple whose home and away are transposed.

WHY THIS EXISTS (1X2-HOME-AWAY-INVERSIONS-2026-09-19, re-measured 2026-09-22).
A small number of stored pre-kickoff 1X2 triples are the MIRROR IMAGE of every
other book on the same fixture: our `home` price is the market's `away` price and
vice versa. Measured over 120 days against a 4+-book consensus: **29 (fixture,
book) pairs out of 159,764 testable — 0.018%** — concentrated in the feeds we
scrape ourselves (Coolbet 9, Epicbet 5, Unibet-Site 5, Unibet-Kambi 2 = 72%, at
0.13-0.26% per book) against 0.008-0.021% on the API-Football-fed books.

**FOUR PAPER PICKS WERE STRUCK ON AN INVERTED LEG**, which corrects this row's
earlier "exposure = ZERO" finding: `bot_trigger_1x2_sharp_v1` x3 (all since
voided by SHARP-BOT-PRICED-OFF-PHANTOM-FIXTURES) and `bot_unibet_trigger_1x2_v1`
x1 on Birkirkara v Hibernians, 2026-09-12, which lost. No `real_bets` and no
`picks_forward_test` row ever took one.

WHY A MIRROR IS THE WORST SHAPE OF BAD DATA. Every gate in this system is
`edge = p - 1/odds`, a LOWER bound. A wrong price only ever INFLATES the edge, so
a mirrored triple does not read as a broken row — it reads as the largest edge on
the board, and the bots are a search procedure for exactly that. It clears every
floor we own and can never trip one.

WHY THE EXISTING GUARD DOES NOT COVER THIS, and this is the load-bearing part.
`workers/automation/anchor_sanity.is_anchor_sane` is a RATIO test against
Pinnacle, applied at READ time. It has two blind spots a mirror walks through:

  1. **It fails open with no anchor — deliberately — and that is exactly what
     happened.** Birkirkara v Hibernians has NO Pinnacle 1x2 line at all, so the
     one inverted pick that was never voided is the one the anchor could not see.
     A consensus of the 7 books that DID price it catches it immediately.
  2. **A ratio test has little power on a moderate mirror.** Balzan v Sliema
     stored away at 3.40 against a true 2.12 — ratio 1.604 against a 1.5625
     threshold, i.e. it passed by 2.5%. The mirror is obvious in STRUCTURE
     (swapping the sides reconciles the whole triple) and marginal in ratio.

So this is a different test, not a tighter threshold, and it runs at WRITE time
rather than read time. That placement is the point: `odds_snapshots` has EIGHT
production INSERT sites and no shared choke point, and "a second code path
inheriting no gates" is this repo's most-repeated failure shape
(`docs/RELIABILITY_LEDGER.md`). A row that never lands cannot be inherited by a
consumer that forgot to ask.

THE MECHANISM IS UPSTREAM AND IS **NOT** FIXED HERE — see PRIORITY_QUEUE #001
FIXTURE-MATCHING-ORIENTATION. The matcher deliberately accepts a flipped event
and then throws away the fact that it was flipped:
  * `workers/automation/coolbet_placer.py:1535-1545` scores `home_score` and
    `away_score` each with `max()` over BOTH sides of the candidate event and
    returns the event at :1628 with no orientation flag;
  * `workers/automation/coolbet_matching.py:180-182` computes `direct` and
    `swapped` pair scores, takes `max(...)`, and returns only the score — the
    winning orientation is discarded. **This is the matcher behind
    `run_board_sweep`, the MAIN Coolbet feed, and #001 does not name it**;
and every side-mapper then writes the book's own `1`/`X`/`2` straight into our
`home`/`draw`/`away` with no re-orientation against our fixture:
`unibet_odds_feed.parse_contest:154-163`, `unibet_kambi.parse_betoffers:258-261`,
`epicbet_explorer.parse_event_markets:680-691` (keys off the BOOK event's
`homeTeamName`), `coolbet_explorer.parse_market:727-740`. Consistent with that,
the mirror is stable for a fixture's whole life rather than flickering: the
Unibet-Site case ran 24 of 24 snapshots mirrored, the two Epicbet cases 60/60 and
61/61. Three of the affected fixtures were matched at a name score of 98-100,
i.e. the EVENT was identified perfectly and only the SIDES were wrong.

CALIBRATION — measured, not guessed. Over the same 120 days, of 159,764 testable
(fixture, book) pairs only 231 fit the swapped consensus better than the straight
one at all, and their `d_swap` distribution thins steadily: 33 pairs at <= 0.06,
42 at <= 0.08, 50 at <= 0.10. Every pair inside 0.06 is visibly reversed on
inspection (e.g. Coolbet 1.01/17.00/35.00 where the consensus says p_home=0.025).
`MIRROR_TOL = 0.06` is therefore 1 in ~4,800 writes. These are the SAME numbers
the detection script used to produce the evidence above, so the guard and the
measurement cannot drift into two definitions of "mirrored".

WHY THE CONSENSUS IS THE RIGHT REFERENCE FOR *OUR* ORIENTATION, which is the
soundness argument the whole guard rests on. The consensus is dominated by the
API-Football books, and those rows are keyed to the fixture by
`matches.api_football_id` — the same AF fixture `matches.home_team_id` was
populated from. So AF's notion of "home" and ours are consistent BY
CONSTRUCTION, and the books that can drift from it are exactly the fuzzy-matched
ones. That asymmetry is what makes "the market says the other way round" mean
"this book is transposed" rather than "our fixture is". On one fixture
(Al-Rayyan v Qatar SC, 2026-09-20) Epicbet AND Unibet-Site were mirrored
together — two scrapers agreeing with each other and not with nine AF books —
which is the same fault twice, not a vote against the reference.

Note the corollary, and it is a real limit: if a fixture's own home/away were
wrong in `matches`, every book would look mirrored at once and the guard would
correctly decline to single anyone out. That case is [[#001]]'s, not this
module's.

FAIL-OPEN, like `anchor_sanity`, and for the same reason: with fewer than
`MIN_REFERENCE_BOOKS` other books there is no consensus, and refusing every
thinly-priced fixture would trade a known fault for an invisible one. In practice
the API-Football sweep writes ~13 books every 30 minutes, so our own sweeps
always have a quorum by the time they run — all 29 observed cases had 5-15 books.

WHAT IS AND IS NOT COVERED. Installed on the four PRE-MATCH writers that hold a
whole triple at once: `supabase_client.store_odds`,
`supabase_client.store_book_odds_snapshots`,
`coolbet_explorer.store_coolbet_snapshots_for_match` and
`jobs/fetch_odds.fetch_af_odds`. NOT installed on the in-play writers
(`store_live_odds`, `store_live_odds_batch`) — in-play betting was retired
2026-08-21 and a live triple has no stable consensus — nor on
`daily_pipeline_v2._store_parsed_odds` or `jobs/closing_snap`, which re-walk the
same API-Football rows the guarded `fetch_af_odds` already screens.
"""
from __future__ import annotations

import logging
from statistics import median

log = logging.getLogger(__name__)

# Summed |deviation| of (p_home, p_away) from the SWAPPED consensus, in
# normalised (sum-to-1) implied probability. See CALIBRATION above.
MIRROR_TOL = 0.06

# ...and the straight orientation must be decisively worse, or the triple simply
# agrees with the market and nothing is wrong with it.
#
# HONESTLY: on 120 days of real data this is NOT the binding constraint —
# refusals are 33 at 0.00, 33 at 0.20, 30 at 0.30, 18 at 0.40. It is a belt
# against a degenerate consensus rather than a live filter, and it is kept
# because it makes the definition of "mirrored" complete: a triple that fits
# BOTH orientations has not been shown to be transposed.
MIN_STRAIGHT = 0.20

# A pick'em fixture mirrors itself, so the test has no power there. The consensus
# must separate the two sides by at least this much before we judge anything.
#
# This one IS binding, and it is the single most sensitive number in the module:
# over the same 120 days, refusals run 67 at 0.05, 56 at 0.10, 33 at 0.15, 21 at
# 0.20. Relaxing it does not find more mirrors, it starts judging fixtures where
# a mirror and a disagreement are indistinguishable.
MIN_SEPARATION = 0.15

# A consensus needs a quorum. Below this we fail open.
MIN_REFERENCE_BOOKS = 4


def normalise(home: float, draw: float, away: float) -> tuple[float, float] | None:
    """(p_home, p_away) as sum-to-1 implied probabilities, or None if unusable.

    Normalising removes the book's margin, which is what makes a 4% -margin
    exchange comparable with a 12% -margin retail book on the same fixture.
    """
    try:
        h, d, a = float(home), float(draw), float(away)
    except (TypeError, ValueError):
        return None
    if h <= 1.0 or d <= 1.0 or a <= 1.0:
        return None
    total = 1.0 / h + 1.0 / d + 1.0 / a
    if total <= 0:
        return None
    return (1.0 / h) / total, (1.0 / a) / total


def consensus(triples) -> tuple[float, float] | None:
    """Median (p_home, p_away) over other books' triples, or None without quorum.

    Median rather than mean deliberately: if the mirrored book is itself in the
    pool (it never is here — callers pass leave-one-out) or if one more book is
    wrong, a median of 4+ is unmoved where a mean would be dragged.
    """
    pts = [p for p in (normalise(*t) for t in triples) if p]
    if len(pts) < MIN_REFERENCE_BOOKS:
        return None
    return median(p[0] for p in pts), median(p[1] for p in pts)


def is_mirrored(home, draw, away, cons: tuple[float, float] | None) -> bool:
    """True when this triple is the market's triple with home and away swapped."""
    if cons is None:
        return False  # fail open — no evidence
    ph_c, pa_c = cons
    if abs(ph_c - pa_c) < MIN_SEPARATION:
        return False  # pick'em: the test has no power, do not guess
    p = normalise(home, draw, away)
    if p is None:
        return False
    ph, pa = p
    d_swap = abs(ph - pa_c) + abs(pa - ph_c)
    d_straight = abs(ph - ph_c) + abs(pa - pa_c)
    return d_swap <= MIRROR_TOL and d_straight >= MIN_STRAIGHT


def extract_1x2(rows, market_of, selection_of, odds_of) -> dict[str, float]:
    """Pull {home, draw, away} out of a heterogeneous row list.

    The four install points carry three different row shapes (dicts from the AF
    parser, dicts from `store_odds`, 4-tuples from the explorers), so the
    accessors are passed in rather than the module guessing.
    """
    out: dict[str, float] = {}
    for r in rows:
        if market_of(r) != "1x2":
            continue
        sel = selection_of(r)
        if sel in ("home", "draw", "away"):
            out[sel] = odds_of(r)
    return out


def peer_triples(match_id: str, bookmaker: str) -> list[tuple[float, float, float]]:
    """Latest pre-match 1x2 triple per OTHER book on this fixture, from the DB.

    Used by the single-book writers, which see only their own prices. The
    API-Football path does not need this — it holds every book's rows for the
    fixture in one payload and passes them straight to `consensus`.

    Never raises: a guard that can fail a write is worse than the fault it
    prevents, so any DB trouble degrades to "no consensus" i.e. fail open.
    """
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """
            SELECT DISTINCT ON (bookmaker, selection) bookmaker, selection, odds::float AS odds
              FROM odds_snapshots
             WHERE match_id = %s AND market = '1x2'
               AND bookmaker <> %s
               AND COALESCE(is_live, false) = false
               AND timestamp > now() - interval '3 days'
             ORDER BY bookmaker, selection, timestamp DESC
            """,
            (match_id, bookmaker),
        ) or []
    except Exception as e:  # pragma: no cover - degradation path
        log.debug("mirror-guard: peer lookup failed for %s: %s", match_id, e)
        return []
    by_book: dict[str, dict[str, float]] = {}
    for r in rows:
        by_book.setdefault(r["bookmaker"], {})[r["selection"]] = r["odds"]
    return [
        (t["home"], t["draw"], t["away"])
        for t in by_book.values()
        if {"home", "draw", "away"} <= t.keys()
    ]


def screen_1x2(match_id: str, bookmaker: str, triple: dict[str, float],
               reference: list[tuple[float, float, float]] | None = None) -> str | None:
    """Return a quarantine reason when this triple mirrors the market, else None.

    `reference` lets a caller that already holds every other book's prices (the
    API-Football bulk path) avoid a DB round trip; everything else passes None
    and the peers are read from `odds_snapshots`.
    """
    if not {"home", "draw", "away"} <= triple.keys():
        return None  # partial triple — nothing to compare, fail open
    peers = reference if reference is not None else peer_triples(match_id, bookmaker)
    cons = consensus(peers)
    if not is_mirrored(triple["home"], triple["draw"], triple["away"], cons):
        return None
    ph_c, pa_c = cons
    return (f"mirror-guard 2026-09-22: 1x2 home/away transposed vs {len(peers)}-book "
            f"consensus (stored {triple['home']}/{triple['draw']}/{triple['away']}, "
            f"consensus p_home={ph_c:.3f} p_away={pa_c:.3f})")


def quarantine_1x2(match_id: str, bookmaker: str, triple: dict[str, float],
                   reason: str, minutes_to_kickoff: int | None = None) -> None:
    """Park a refused triple in `odds_snapshots_quarantined` instead of dropping it.

    Never delete silently. The table already exists (it is where
    `scripts/cleanup_ou_odds_garbage.py` parks rows) and carries
    `quarantine_reason` / `quarantined_at`, so a refused price stays auditable
    and a mis-calibrated threshold is recoverable rather than lost.

    Never raises, for the same reason as `peer_triples`.
    """
    try:
        from workers.api_clients.db import get_conn
        rows = [(match_id, bookmaker, "1x2", sel, triple[sel], minutes_to_kickoff, reason)
                for sel in ("home", "draw", "away") if sel in triple]
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO odds_snapshots_quarantined
                       (match_id, bookmaker, market, selection, odds, timestamp,
                        is_live, minutes_to_kickoff, quarantine_reason, quarantined_at)
                       VALUES (%s, %s, %s, %s, %s, now(), false, %s, %s, now())""",
                    rows,
                )
                conn.commit()
    except Exception as e:  # pragma: no cover - degradation path
        log.warning("mirror-guard: quarantine write failed for %s/%s: %s",
                    match_id, bookmaker, e)


def drop_mirrored_1x2(match_id: str, bookmaker: str, rows, market_of, selection_of,
                      odds_of, minutes_to_kickoff: int | None = None,
                      reference: list[tuple[float, float, float]] | None = None):
    """Screen `rows` for one (fixture, book) and return them without a mirrored 1x2.

    Only the three 1x2 legs are removed. The book's other markets are untouched:
    a transposition is a 1x2-shaped fault (O/U and BTTS have no home/away to
    swap), and dropping a book's whole board on this evidence would cost far
    more coverage than the fault costs accuracy.
    """
    triple = extract_1x2(rows, market_of, selection_of, odds_of)
    reason = screen_1x2(match_id, bookmaker, triple, reference=reference)
    if not reason:
        return rows
    log.warning("mirror-guard: refusing %s 1x2 on %s — %s", bookmaker, match_id, reason)
    quarantine_1x2(match_id, bookmaker, triple, reason, minutes_to_kickoff)
    return [r for r in rows if market_of(r) != "1x2"]


def drop_mirrored_1x2_multibook(match_id: str, rows, bookmaker_of, market_of,
                                selection_of, odds_of,
                                minutes_to_kickoff: int | None = None):
    """Same screen for a payload that already holds EVERY book on one fixture.

    This is the API-Football bulk path, which parses ~13 books for a fixture in
    one go. The consensus is taken leave-one-out from the payload itself, so the
    whole screen costs no database reads at all — and it is the only path where
    the reference is guaranteed complete rather than whatever happens to be
    stored at the moment we write.
    """
    by_book: dict[str, dict[str, float]] = {}
    for r in rows:
        if market_of(r) == "1x2":
            sel = selection_of(r)
            if sel in ("home", "draw", "away"):
                by_book.setdefault(bookmaker_of(r), {})[sel] = odds_of(r)
    full = {b: t for b, t in by_book.items() if {"home", "draw", "away"} <= t.keys()}
    if len(full) <= MIN_REFERENCE_BOOKS:
        return rows
    refused: dict[str, str] = {}
    for book, triple in full.items():
        peers = [(t["home"], t["draw"], t["away"]) for b, t in full.items() if b != book]
        reason = screen_1x2(match_id, book, triple, reference=peers)
        if reason:
            refused[book] = reason
    if not refused:
        return rows
    for book, reason in refused.items():
        log.warning("mirror-guard: refusing %s 1x2 on %s — %s", book, match_id, reason)
        quarantine_1x2(match_id, book, full[book], reason, minutes_to_kickoff)
    return [r for r in rows
            if not (market_of(r) == "1x2" and bookmaker_of(r) in refused)]
