"""OUR-STATS — the OddsIntel side of every competitor comparison, in one place.

Six audit scripts each carried their own near-identical copy of this query.
That was survivable while they only summed `pnl`; it stopped being survivable
with STALE-ODDS-HISTORY-RESTATE, because the honest figure now has to be
computed from `odds_at_pick_live` and any script still summing `pnl` would
quietly keep publishing the inflated one.

WHAT CHANGED AND WHY
--------------------
`simulated_bets.pnl` is settled from `odds_at_pick`, and STALE-BEST-ODDS
showed `odds_at_pick` is a high-water mark rather than a price on offer — the
pipeline aggregated a fixture's whole snapshot history and took max(). On the
cohort published to the landing that is worth **+5.34pp**: +15.99% recorded
against +10.65% at the best price actually live at pick time, over the 76% of
bets that can be repriced.

So `roi_pct` here is the LIVE-priced figure. The recorded one is still
published, clearly labelled, as `roi_pct_recorded` — same treatment we give
Forebet, and for the same reason: repricing a record without showing what was
originally claimed is not transparency.

COVERAGE IS NOT OPTIONAL
------------------------
`odds_at_pick_live` is NULL where no accessible book quoted that selection at
or before `pick_time` (~24% of the cohort). Publishing a restated ROI without
its `n` and coverage invites exactly the "your data is thin" dismissal that
FOREBET-ODDS-CROSS-SOURCE existed to remove — see ANALYSIS_GOTCHAS #29.
"""
from __future__ import annotations

# DUPLICATED-RULES-REMAINING-2026-09-06. THE canonical flat stake for PUBLISHED
# and COMPARATIVE figures — the €10-per-pick basis that matches how WinnerOdds,
# Tipstrr, SignalOdds and Forebet publish, so head-to-head comparison is
# apples-to-apples. Every competitor audit imports this rather than re-typing
# `STAKE = 10.0`, which is how the four copies came to exist.
#
# ⚠️ DO NOT couple the other two 10.0s in this repo to it. Three different
# concepts happen to share a value today, and unifying them would be the same
# duplication bug in reverse — coupling things that are only accidentally equal:
#
#   * `place_coolbet_ui.DEFAULT_STAKE` is REAL MONEY actually placed. It must be
#     free to change without moving the published methodology.
#   * `daily_pipeline_v2.STAKE` is the simulated per-bot stake basis; the bots
#     stake proportional to divergence (Kelly) internally.
#
# Only the PUBLICATION basis lives here.
PUBLICATION_FLAT_STAKE_EUR = 10.0

# Back-compat alias for callers that already read `STAKE` from this module.
STAKE = PUBLICATION_FLAT_STAKE_EUR

# The public cohort: production maturities, the markets we compare on,
# settled, excluding the in-play bots (different game, different venue).
_SQL = """
    SELECT sb.result::text            AS result,
           sb.odds_at_pick::float     AS odds,
           sb.odds_at_pick_live::float AS odds_live,
           sb.stake::float            AS stake,
           sb.pnl::float              AS pnl
      FROM simulated_bets sb
      JOIN bots b ON b.id = sb.bot_id
     WHERE sb.created_at >= %s::date
       AND sb.created_at <  %s::date
       AND sb.result::text IN ('won','lost')
       AND sb.market IN ('1x2','over_under_25','o/u')
       AND b.maturity_label IN ('calibrated','beta','active')
       AND b.name NOT LIKE 'inplay_%%'
"""


def _roi(rows, price) -> tuple[int, float, float]:
    """(n, roi_pct, hit_rate_pct) at a flat stake, priced by `price`."""
    n = won = 0
    pnl = 0.0
    for r in rows:
        o = price(r)
        if not o or o <= 1:
            continue
        n += 1
        if r["result"] == "won":
            won += 1
            pnl += (o - 1) * STAKE
        else:
            pnl -= STAKE
    if not n:
        return 0, 0.0, 0.0
    return n, 100.0 * pnl / (n * STAKE), 100.0 * won / n


def our_stats(start: str, end: str) -> dict:
    """Our side of the comparison, priced honestly.

    `roi_pct` is computed from `odds_at_pick_live` — the best price across
    accessible books that was actually live when the bet was raised.
    """
    from workers.api_clients.db import execute_query

    rows = execute_query(_SQL, (start, end))
    if not rows:
        return {"n": 0}

    priced = [r for r in rows if r.get("odds_live")]
    n_live, roi_live, hit_live = _roi(priced, lambda r: r["odds_live"])
    # Recorded ROI on the SAME subset, so the two are like-for-like. Comparing
    # a live-priced subset against a recorded figure computed over all rows
    # would mix a pricing change with a population change.
    _, roi_rec_sub, _ = _roi(priced, lambda r: r["odds"])
    n_all, roi_rec_all, hit_all = _roi(rows, lambda r: r["odds"])

    odds_vals = [r["odds_live"] for r in priced if r.get("odds_live")]
    return {
        "n": n_live,
        "stake_total": round(n_live * STAKE, 2),
        "pnl_total": round(n_live * STAKE * roi_live / 100.0, 2),
        "roi_pct": round(roi_live, 2),
        "hit_rate_pct": round(hit_live, 2),
        "avg_odds": round(sum(odds_vals) / len(odds_vals), 3) if odds_vals else None,
        "priced_at": "best price live at pick time",
        # Everything needed to audit the restatement.
        "n_total_settled": n_all,
        "coverage_pct": round(100.0 * n_live / n_all, 1) if n_all else 0.0,
        "roi_pct_recorded": round(roi_rec_sub, 2),
        "roi_pct_recorded_all_rows": round(roi_rec_all, 2),
        "hit_rate_pct_recorded_all_rows": round(hit_all, 2),
        "restatement_note": (
            "roi_pct is priced at odds that were live at pick time. "
            "roi_pct_recorded is the same bets at the stored odds_at_pick, "
            "which STALE-BEST-ODDS-2026-09-02 showed to be a high-water mark "
            "across the whole snapshot history rather than a price on offer."
        ),
    }
