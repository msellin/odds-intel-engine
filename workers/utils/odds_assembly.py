"""Assemble a book's complete market from `odds_snapshots` rows — correctly.

WHY THIS EXISTS (2026-09-17). Every analysis that reads a book's prices has to
turn a stream of per-selection rows into a market (a 1x2 triple, an over/under
pair). Two independent traps sit in that step, and this project walked into both:

1. **Legs arrive together for most books, separately for Coolbet.** 99.8-100% of
   fetch-rounds write all three 1x2 legs at ONE timestamp for every book except
   Coolbet, which writes one leg per sub-second timestamp. So a reader must
   assemble from a window, not from a single timestamp — and must not take
   `DISTINCT ON (match, selection) ORDER BY timestamp DESC`, which pulls each leg
   from whichever round was latest FOR THAT LEG and manufactures a time-smear
   (ANALYSIS_GOTCHAS §62).

2. **A scrape pass can write the same selection twice, seconds apart, and the
   second write can be worse.** Measured against The Odds API's live Coolbet
   quote on 8 fixtures matched one-to-one:

       reading the LATEST row per selection   -> +1.87pp worse than the live quote
       reading the first complete BURST       -> +0.00pp (reproduces it exactly)
       reading the BEST price in that burst   -> +0.00pp

   `own_path_kill_criterion.assemble()` looked immune because it assembles from a
   window — but its callers take the LAST assembled triple, which starts at the
   latest timestamp, so it measured +1.86pp: exactly as wrong as the naive read.

THE RULE. Within `burst_s` of a start point, take the BEST price per selection;
beyond that, the first seen. Best rather than first because it is the price a
bettor could actually have taken, and the two measured identically.

`burst_s` is deliberately small. It must cover the double-write (observed
0.5-10s apart) without reaching back far enough to surface a price the market has
since moved away from — showing a better price than the book currently offers is
the best-of-books mirage (§52/§55) in a new place. `window_s` is the separate,
much longer allowance for a book whose legs genuinely trickle in.
"""
from __future__ import annotations

BURST_S = 15.0
WINDOW_S = 15 * 60.0


def assemble(obs, sides, window_s: float = WINDOW_S, burst_s: float = BURST_S):
    """[(anchor_ts, {selection: odds})] — every complete market in `obs`.

    `obs` is an iterable of (timestamp, selection, odds). `sides` is the full
    complement the market needs (e.g. ("home", "draw", "away")); a group missing
    any of them is not a market and is skipped, because the overround of a
    partial market is not an overround at all.
    """
    obs = sorted(obs, key=lambda x: x[0])
    out = []
    for i, (t0, _, _) in enumerate(obs):
        picked: dict[str, float] = {}
        for t, sel, o in obs[i:]:
            dt = (t - t0).total_seconds()
            if dt > window_s:
                break
            if dt <= burst_s:
                # inside the burst: keep the best price seen for this selection
                if o > picked.get(sel, 0.0):
                    picked[sel] = o
            else:
                picked.setdefault(sel, o)
        if all(s in picked for s in sides):
            out.append((t0, {s: picked[s] for s in sides}))
    return out


def latest_market(obs, sides, window_s: float = WINDOW_S, burst_s: float = BURST_S):
    """The most recent complete market, or None.

    Use this instead of `assemble(...)[-1]`. The last element of `assemble` is the
    triple ANCHORED at the latest row, which is the worse half of a double-write;
    this anchors at the latest BURST and takes the best price within it.
    """
    tri = assemble(obs, sides, window_s=window_s, burst_s=burst_s)
    if not tri:
        return None
    newest = tri[-1][0]
    # every triple whose anchor is inside the final burst, best price per leg
    tail = [q for t, q in tri if (newest - t).total_seconds() <= burst_s]
    if not tail:
        return tri[-1][1]
    return {s: max(q[s] for q in tail) for s in sides}


CROSS_BOOK_MAX_GAP_S = 15 * 60.0


def best_across_books(per_book, sides, max_gap_s: float = CROSS_BOOK_MAX_GAP_S,
                      window_s: float = WINDOW_S, burst_s: float = BURST_S):
    """Best price per selection across books — or None if they are not contemporaneous.

    WHY THIS IS NOT `max(latest_market(b) for b in books)` (2026-09-17). That
    obvious one-liner is wrong, and wrong in the direction that flatters us.
    `latest_market` is correct WITHIN a book, but each book's latest lands at a
    different wall-clock time: measured over 30 days of stored 1x2, the median gap
    between two books' own latest quotes is **7.2 hours**. Combining them mixes two
    different states of the world, and the apparent overround falls monotonically
    with the gap:

        books  <15 min apart   6.55%      <- contemporaneous, the real number
        books   15m-2h apart   6.07%
        books    2-12h apart   5.16%
        books     12h+ apart   4.37%      <- 2.18pp of pure artefact

    That is the best-of-books mirage (ANALYSIS_GOTCHAS 52/55) displaced from books
    into time: a market no book ever offered, assembled from a live quote and a
    stale one. It is the same failure this module was written to fix, one level up.

    So: assemble each book independently, then REFUSE the combination unless every
    contributing book's anchor sits inside `max_gap_s`. Returning None for a
    fixture is correct; a number built from stale legs is not.

    `per_book` maps bookmaker -> iterable of (timestamp, selection, odds).
    Returns (quote, anchor_span_seconds) or None.
    """
    last = {}
    for book, obs in per_book.items():
        tri = assemble(obs, sides, window_s=window_s, burst_s=burst_s)
        if tri:
            last[book] = tri[-1]
    if len(last) < 2:
        return None
    anchors = [t for t, _ in last.values()]
    span = (max(anchors) - min(anchors)).total_seconds()
    if span > max_gap_s:
        return None
    return {s: max(q[s] for _, q in last.values()) for s in sides}, span
