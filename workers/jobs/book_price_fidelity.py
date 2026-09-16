"""BOOK-PRICE-FIDELITY-MONITOR (2026-09-16) — is a book's recorded price real?

WHAT THIS ANSWERS, and why nothing else answers it.

A price we store is a claim: "this book offered this number". Every downstream
figure — edge %, ROI, CLV, the public track record — is computed on top of that
claim, and nothing verifies it. When a feed drifts, goes stale, or reports a line
the book does not honour, the arithmetic downstream stays perfectly consistent
and perfectly wrong. `OUTLIER-CEILING-CALIBRATED-2026-09-16` found exactly that:
`bot_v10_all` shows +5.83% ROI all-time, and the profit is carried by two books
whose recorded prices win far more often than those prices imply.

THE TEST. For a set of settled bets at one book, compare

    actual win rate        (did it win?)
    implied win rate       (1 / the price we recorded)

Bookmakers price with margin, so the implied rate OVERSTATES true probability.
An edgeless bettor therefore lands BELOW implied — roughly by the vig. A book
whose actual rate sits ABOVE implied, sustained over a real sample, was quoting
prices longer than the truth.

⚠️ THE SUBTLETY THAT MAKES A NAIVE VERSION OF THIS USELESS. A positive gap is
also what GENUINE LINE SHOPPING produces. If you take a price better than fair,
the probability implied by that price is lower than the real one, so you win more
often than it implies — by design. The calibration measured this baseline
directly: in the healthy 1.05–1.25 band the gap runs about +4 to +5 points in
every odds bucket. So an alert threshold of "gap > 0" would fire on precisely the
behaviour we want and be switched off within a week.

Hence the monitor is RELATIVE, not absolute. Each book is compared against the
FLEET MEDIAN gap for the same window. That self-calibrates: if line shopping gets
better across the board the baseline rises with it, and only a book that breaks
away from its peers is flagged. A fixed constant would need re-tuning every time
the selection rule changed, and would silently rot in between.

WHAT IT DELIBERATELY DOES NOT DO. It changes no price, no gate and no pick. It is
a smoke alarm, not a sprinkler. The defect it was built for ran from August to
September and was found by one ad-hoc query; the point is that somebody is now
running that query every week.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# Books whose rows are synthetic aggregates or CSV imports, not live quotes.
_NON_BOOKS = ("Max", "Avg", "betexplorer", "api-football", "api-football-live", "ub")

# A book needs this many settled bets before its gap means anything. At n=100 the
# standard error on a win rate near 40% is ~4.9 points, so a 6-point excess is
# barely over 1 sigma — deliberately loose, because this alarm is meant to start
# a query, not to conclude one.
# Recalibrated 2026-09-17 after the dedup fix. 100 was set against inflated
# rows; on deduped picks it left only 3 books qualifying and the monitor went
# blind. At n=60 a win-rate standard error near 40% is ~6.3 points, so the
# 6-point excess threshold is ~1 sigma -- loose on purpose, this alarm starts a
# query rather than concluding one.
MIN_BETS = int(os.getenv("FIDELITY_MIN_BETS", "60"))

# Points ABOVE the fleet median gap before a book is flagged. Set against the
# measured line-shop baseline (~+4 to +5), not against zero — see the module
# docstring. The books that triggered this work sat +12.1 and +11.7 against a
# fleet median near +2, i.e. roughly +10 excess, so 6 catches them with room.
ALERT_EXCESS_POINTS = float(os.getenv("FIDELITY_ALERT_EXCESS", "6.0"))

# FIDELITY-FALSE-POSITIVE-FIX-2026-09-16. The first live run flagged Bet365 at
# +16.8 on a 30d window (n=1,087) and the flag did not survive contact with the
# full sample: over 13,852 DC bets Bet365 reads +2.9, in line with Betano -3.6,
# 10Bet -2.0 and Coolbet -2.5. The 30d slice was 931 double_chance bets from
# three RETIRED bots, two of which (bot_dc_specialist, bot_dc_value) emit
# identical picks and so counted the same evidence twice.
#
# Three defects, all fixed below:
#   * 30d is too short -- one bot's burst owns a book's whole sample.
#   * ~~retired bots were included~~ -- REVERTED 2026-09-17. Excluding them was
#     an over-correction: the false positive's real cause was reading the RAW
#     shadow_bets table (91% of those dc-bot rows were re-emissions of the same
#     picks), not the bots being retired. A retired bot's historical picks are
#     still valid evidence about what a book was quoting at the time, and
#     excluding them left only 3 books above the threshold -- a blind monitor.
#     Dedup plus the breadth guard address the concentration properly.
#   * nothing reported CONCENTRATION, so a gap driven by one bot/market looked
#     identical to one spread across the fleet.
WINDOW_DAYS = int(os.getenv("FIDELITY_WINDOW_DAYS", "90"))

# A flagged book must show its gap across more than one bot AND more than one
# market. A price defect is a property of the BOOK, so it cannot be confined to
# a single strategy -- if it is, the strategy is the story, not the feed.
MIN_DISTINCT_BOTS = int(os.getenv("FIDELITY_MIN_BOTS", "2"))
MIN_DISTINCT_MARKETS = int(os.getenv("FIDELITY_MIN_MARKETS", "2"))

# DEDUP-CORRECTION-2026-09-17. This job originally read `shadow_bets` directly.
# That table stores one row per RE-EVALUATION, not per pick -- the refresh
# re-emits the same (bot, match, market, selection) on every pass and each lands
# as its own settled row. All-time that is 162,191 rows for 20,444 real picks
# (7.9x); it is still running at 1.4-2.2x on current days.
#
# Reading it raw inflated every n here by that factor and every t by its square
# root, which is precisely the error that produced this job's own first false
# positive (Bet365 flagged at +16.8 on what was really a few hundred picks from
# three retired bots). `shadow_bets_unique` is the canonical deduped view --
# DISTINCT ON the four keys, first emission -- and every aggregate must use it.
# Read the base table only for price-path work, where the re-emissions ARE the
# data.

_SQL = """
SELECT s.recommended_bookmaker AS book,
       COUNT(*)                                            AS bets,
       AVG(1.0 / s.odds_at_pick) * 100.0                   AS implied_pct,
       AVG(CASE WHEN s.result::text = 'won' THEN 1.0 ELSE 0.0 END) * 100.0 AS actual_pct,
       AVG(s.odds_at_pick)                                 AS avg_odds,
       COUNT(DISTINCT s.bot_id)                            AS n_bots,
       COUNT(DISTINCT s.market)                            AS n_markets
  FROM shadow_bets_unique s
  JOIN bots bo ON bo.id = s.bot_id
 WHERE s.result IS NOT NULL
   AND s.result::text <> 'pending'
   AND s.odds_at_pick IS NOT NULL
   AND s.odds_at_pick > 1
   AND s.recommended_bookmaker IS NOT NULL
   AND s.recommended_bookmaker <> ALL(%s)
   AND s.created_at > now() - (%s || ' days')::interval
 GROUP BY 1
HAVING COUNT(*) >= %s
"""


def measure(window_days: int = WINDOW_DAYS, min_bets: int = MIN_BETS) -> list[dict]:
    """Per-book implied-vs-actual gap, newest window. Pure read, no side effects.

    Returns one dict per qualifying book with `excess` = its gap minus the fleet
    median gap. Sorted worst-first so the caller can read the top row and stop.
    """
    from statistics import median

    from workers.api_clients.db import execute_query

    rows = execute_query(_SQL, (list(_NON_BOOKS), window_days, min_bets))
    if not rows:
        return []

    out = []
    for r in rows:
        implied = float(r["implied_pct"])
        actual = float(r["actual_pct"])
        out.append({
            "book": r["book"],
            "bets": int(r["bets"]),
            "implied_pct": round(implied, 1),
            "actual_pct": round(actual, 1),
            "avg_odds": round(float(r["avg_odds"]), 2),
            "gap": round(actual - implied, 1),
            "n_bots": int(r["n_bots"]),
            "n_markets": int(r["n_markets"]),
        })

    baseline = median(b["gap"] for b in out)
    for b in out:
        b["fleet_median_gap"] = round(baseline, 1)
        b["excess"] = round(b["gap"] - baseline, 1)

    out.sort(key=lambda b: -b["excess"])
    return out


def run_fidelity_check(
    window_days: int = WINDOW_DAYS,
    min_bets: int = MIN_BETS,
    alert_excess: float = ALERT_EXCESS_POINTS,
    notify: bool = True,
) -> dict:
    """Measure, and Telegram the books that broke away from their peers.

    Returns a summary dict. Raises nothing on a quiet week — no flagged book is
    the expected outcome and must not read as a failure.
    """
    books = measure(window_days=window_days, min_bets=min_bets)
    if not books:
        log.info("book-price-fidelity: no book reached n>=%d in %dd — nothing to judge",
                 min_bets, window_days)
        return {"books": 0, "flagged": 0, "detail": []}

    # BREADTH GUARD (FIDELITY-FALSE-POSITIVE-FIX). A price defect belongs to the
    # BOOK, so it cannot live inside one strategy or one market. The first live
    # run flagged Bet365 on 931 double_chance bets from three retired bots -- a
    # gap that vanished (+16.8 -> +2.9) on the book's full sample. If the gap is
    # confined, the strategy is the story and the alert would be misdirected.
    flagged = [b for b in books
               if b["excess"] > alert_excess
               and b["n_bots"] >= MIN_DISTINCT_BOTS
               and b["n_markets"] >= MIN_DISTINCT_MARKETS]
    narrow = [b for b in books
              if b["excess"] > alert_excess and b not in flagged]
    for b in narrow:
        log.info("book-price-fidelity: %s over threshold (%+.1f) but confined to "
                 "%d bot(s) / %d market(s) — not a book-level defect, not alerting",
                 b["book"], b["excess"], b["n_bots"], b["n_markets"])

    for b in books:
        log.info("book-price-fidelity: %-16s n=%-5d implied=%.1f%% actual=%.1f%% "
                 "gap=%+.1f excess=%+.1f", b["book"], b["bets"], b["implied_pct"],
                 b["actual_pct"], b["gap"], b["excess"])

    if flagged and notify:
        baseline = books[0]["fleet_median_gap"]
        lines = [
            "⚠️ <b>Book price fidelity</b>",
            f"<i>{window_days}d · fleet median gap {baseline:+.1f}pts · "
            f"flagging &gt; {alert_excess:+.1f} above it</i>",
            "",
        ]
        for b in flagged:
            lines.append(
                f"<b>{b['book']}</b> — won {b['actual_pct']:.1f}% where its price "
                f"implied {b['implied_pct']:.1f}%\n"
                f"   gap {b['gap']:+.1f}pts ({b['excess']:+.1f} vs fleet), "
                f"n={b['bets']} across {b['n_bots']} bots / {b['n_markets']} markets, "
                f"avg odds {b['avg_odds']:.2f}"
            )
        lines += [
            "",
            "A book can only beat its own implied rate by this much if the price "
            "we recorded was longer than the price it offered. Check the feed "
            "against the book's own site before trusting any edge, ROI or CLV "
            "computed on it.",
        ]
        try:
            from workers.notify.telegram import send_telegram
            send_telegram("\n".join(lines))
        except Exception as e:  # noqa: BLE001
            # Non-blocking by design: a broken notifier must not lose the
            # measurement, which is already in the log above.
            log.warning("book-price-fidelity: telegram notify failed: %s", e)

    return {
        "books": len(books),
        "flagged": len(flagged),
        "narrow": len(narrow),
        "fleet_median_gap": books[0]["fleet_median_gap"],
        "detail": books,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = run_fidelity_check(notify=False)
    print(f"\n{res['books']} books measured, fleet median gap "
          f"{res.get('fleet_median_gap')}, {res['flagged']} flagged")
