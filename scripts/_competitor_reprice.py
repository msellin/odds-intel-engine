"""COMPETITOR-REPRICE — re-settle a competitor's picks at prices that existed.

A tipster's published ROI is computed from the odds it *says* it took. When
those odds are real, that is fine. When they are not, the published ROI is
fiction and republishing it — even with a caveat attached — presents fiction as
a measured number.

FOREBET-ODDS-CROSS-SOURCE (2026-09-02) established, against a third-party
control, that Forebet's quoted prices are frequently unobtainable: 9.5% of its
picks claim more than 1.5x the best price available anywhere, against 0.7% for
a source that names its book, and the inflation is concentrated on winners
(18.1% vs 8.4%, p=2.4e-06). This module answers the follow-on question — what
would those picks actually have returned at prices a person could have taken?

METHOD, AND WHY THE OBVIOUS VERSION IS WRONG
--------------------------------------------
The verification script prices each bet at MAX(odds) over every snapshot we
ever recorded, because there the goal is to be maximally generous: a claim
above even that was never available. Re-using MAX-ever as an EXECUTION price
is a category error — nobody systematically catches the all-time high of every
line. Doing so rates Forebet at +37.66% ROI, better than their own claim, which
is the tell that the metric is wrong.

So execution prices come from the CLOSING line: the last quote each bookmaker
published at or before kickoff. Three are computed, cheapest assumption last:

  best   — highest closing price across all books. An aggressive line shopper
           with an account everywhere. This is the number to publish, because
           it is the most favourable realistic assumption for the competitor.
  median — the middle closing price. What one ordinary account gets.
  bet365 — a single named book, as a sanity check on the other two.

Coverage is always partial (fuzzy fixture matching, and our odds are thinner in
obscure leagues), so every result carries `n_repriced` and `n_total`. Publish
both — a recomputed figure whose sample is not stated invites exactly the
"your data is thin" dismissal this whole line of work exists to remove.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

from rapidfuzz import fuzz

# DUPLICATED-RULES-REMAINING-2026-09-06: was a local `STAKE = 10.0`.
# The publication flat stake has one definition; see _our_stats.
from scripts._our_stats import PUBLICATION_FLAT_STAKE_EUR as STAKE  # noqa: E402
# Shared across sources: Forebet writes 1/X/2, Betaminic Home/Draw/Away.
# Comparing a "1" to a "Home" by equality matches nothing SILENTLY and every
# downstream number then looks clean (ANALYSIS_GOTCHAS #3).
SEL = {"1": "home", "x": "draw", "2": "away", "home": "home", "draw": "draw",
       "away": "away", "over": "over", "under": "under"}
MKT = {"1x2": "1x2", "match result": "1x2",
       "over_under_25": "over_under_25", "over / under": "over_under_25"}


def norm_team(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|cf|sc|ac|afc|cd|ca|club|de|do|da|the)\b", " ", s)
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


class FixtureIndex:
    """Fuzzy (date, home, away) -> matches.id, with a +/-1 day window.

    The +/-1 day matters: tipsters stamp picks in their own timezone, so a
    22:00 UTC kickoff lands either side of midnight depending on the site.
    Matching the exact date alone silently drops those fixtures.
    """

    def __init__(self, cur, since: str, until: str, min_score: int = 85):
        self.min_score = min_score
        self.by_date: dict[str, list] = {}
        cur.execute(
            """SELECT m.id, m.date::date, ht.name, at.name
                 FROM matches m
                 JOIN teams ht ON ht.id = m.home_team_id
                 JOIN teams at ON at.id = m.away_team_id
                WHERE m.date >= %s AND m.date <= %s""", (since, until))
        for mid, d, h, a in cur.fetchall():
            self.by_date.setdefault(str(d), []).append((mid, norm_team(h), norm_team(a)))

    def find(self, dt: str, home: str, away: str):
        try:
            y, m, d = map(int, (dt or "").split("-"))
            base = date(y, m, d)
        except (ValueError, AttributeError):
            return None
        nh, na = norm_team(home), norm_team(away)
        best = None
        for off in (-1, 0, 1):
            for mid, hh, aa in self.by_date.get((base + timedelta(days=off)).isoformat(), []):
                score = min(fuzz.token_set_ratio(nh, hh), fuzz.token_set_ratio(na, aa))
                if score >= self.min_score and (best is None or score > best[0]):
                    best = (score, mid)
        return best[1] if best else None


_CLOSING_SQL = """
    WITH closing AS (
        SELECT DISTINCT ON (o.bookmaker) o.bookmaker, o.odds
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = %s AND o.market = %s AND o.selection = %s
           AND o.odds > 1
           -- last quote at or before kickoff: the closing line, not a
           -- post-kickoff in-play price, which would be a different bet.
           AND o.timestamp <= m.date
         ORDER BY o.bookmaker, o.timestamp DESC
    )
    SELECT MAX(odds),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY odds),
           MAX(odds) FILTER (WHERE bookmaker = 'Bet365'),
           COUNT(*)
      FROM closing
"""


def closing_prices(cur, match_id, market: str, selection: str):
    """(best, median, bet365, n_books) at the closing line, or None."""
    cur.execute(_CLOSING_SQL, (match_id, market, selection))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return (float(row[0]), float(row[1]),
            float(row[2]) if row[2] is not None else None, int(row[3]))


def usable(rows: list[dict], since: str) -> list[dict]:
    """Rows we can reprice: shared market, decodable pick, settled, in window."""
    out = []
    for r in rows:
        mk = MKT.get((r.get("market") or "").strip().lower())
        sel = SEL.get((r.get("pick") or "").strip().lower())
        res = (r.get("result") or "").strip().lower()
        try:
            odds = float(r.get("odds") or 0)
        except (TypeError, ValueError):
            continue
        if not mk or not sel or odds <= 1.0 or res not in ("won", "lost"):
            continue
        if (r.get("kickoff_date") or "") < since:
            continue
        out.append({**r, "_mk": mk, "_sel": sel, "_odds": odds, "_res": res})
    return out


def _roi(pairs, pricer) -> dict:
    n = won = 0
    pnl = 0.0
    for p in pairs:
        o = pricer(p)
        if not o or o <= 1:
            continue
        n += 1
        if p["res"] == "won":
            won += 1
            pnl += (o - 1) * STAKE
        else:
            pnl -= STAKE
    return {
        "n": n,
        "roi_pct": round(100 * pnl / (n * STAKE), 2) if n else 0,
        "hit_rate_pct": round(100 * won / n, 2) if n else 0,
        "pnl_total": round(pnl, 2),
        "stake_total": round(n * STAKE, 2),
    }


def reprice(cur, rows: list[dict], since: str, until: str,
            min_score: int = 85) -> dict:
    """Re-settle `rows` (a picks-CSV list) at closing market prices."""
    picks = usable(rows, since)
    idx = FixtureIndex(cur, since, until, min_score)
    pairs = []
    for r in picks:
        mid = idx.find(r["kickoff_date"], r.get("home_team", ""), r.get("away_team", ""))
        if not mid:
            continue
        pr = closing_prices(cur, mid, r["_mk"], r["_sel"])
        if pr is None:
            continue
        best, med, b365, nb = pr
        pairs.append({"claimed": r["_odds"], "res": r["_res"], "best": best,
                      "median": med, "b365": b365, "nbooks": nb})
    books = sorted(p["nbooks"] for p in pairs)
    return {
        "n_total": len(picks),
        "n_repriced": len(pairs),
        "coverage_pct": round(100 * len(pairs) / len(picks), 1) if picks else 0,
        "median_books_at_close": books[len(books) // 2] if books else 0,
        "at_claimed_odds": _roi(pairs, lambda p: p["claimed"]),
        "at_best_close": _roi(pairs, lambda p: p["best"]),
        "at_median_close": _roi(pairs, lambda p: p["median"]),
        "at_bet365_close": _roi(pairs, lambda p: p["b365"]),
    }
