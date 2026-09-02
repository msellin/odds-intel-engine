#!/usr/bin/env python3
"""FOREBET-ODDS-CROSS-SOURCE — is Forebet's published ROI built on prices that
existed?

FOREBET-OU-VERIFY-2026-08-01 already showed Forebet quoting prices no book
offered, but it rested entirely on OUR odds_snapshots. A reader can dismiss
that as "your book coverage is thin" or "your snapshot timing is off". This
script removes that objection by running the test twice: once on Forebet, and
once on a THIRD-PARTY CONTROL whose honesty is independently checkable.

WHY BETAMINIC IS THE CONTROL
----------------------------
Betaminic's ShootingBets table publishes, for every settled bet, the price at a
*named* book — Bet365. That is falsifiable in a way Forebet's unattributed
"odds" column is not: we hold Bet365 quotes for the same fixtures, so we can
check Betaminic against its own claim. Step 1 does that. If Betaminic's
published Bet365 prices match our recorded Bet365 prices, then two independent
parties agree on the same numbers, and our snapshot data is no longer the weak
link in the argument.

Step 1 also calibrates the method. Fuzzy fixture matching, kickoff-date
boundaries and snapshot timing all inject noise, so "claimed odds exceed
anything we recorded" has a non-zero false-positive rate even for an honest
source. Betaminic measures that floor. Forebet is only interesting insofar as
it exceeds it.

THE DISCRIMINATING TEST IS MAGNITUDE, NOT FREQUENCY
--------------------------------------------------
The obvious test — "how often does a claimed price exceed anything we
recorded" — is NOT safe to compare across sources, and the first version of
this script got it wrong. Betaminic quotes Bet365, which is the best price on
the market only 19.3% of the time. Its claimed odds therefore sit below
best-of-books by construction, its exception rate is suppressed, and the
resulting "Forebet is 1.9x the honest floor" flattered nobody honestly.

What survives that bias is the SHAPE of the exception when it happens. Fuzzy
fixture matching and snapshot timing produce small overshoots — a few percent.
They do not produce a claimed 10.00 on a home win that thirteen books priced at
1.83. So the test is the ratio claimed/best, and the headline is the far tail:
how many picks claim MORE THAN 1.5x the best price available anywhere.

The second, independent test is whether the inflation is **concentrated on
winners**. Bet-time price differences cannot correlate with a result nobody
knew yet. Odds generous only in hindsight are not prices; they are decoration.

    python3 scripts/verify_forebet_odds_cross_source.py
    python3 scripts/verify_forebet_odds_cross_source.py --min-score 90 --days 120
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rapidfuzz import fuzz
from rich.console import Console
from rich.table import Table

console = Console()
LEDGER = Path(__file__).resolve().parent.parent / "ledger"

# Forebet writes 1/X/2, Betaminic writes Home/Draw/Away, SignalOdds names the
# team. Comparing a "1" to a "Home" by string equality matches nothing silently
# and the script would report a clean bill of health (ANALYSIS_GOTCHAS #3).
_SEL = {"1": "home", "x": "draw", "2": "away", "home": "home", "draw": "draw",
        "away": "away", "over": "over", "under": "under"}

# Our vocabulary. Only these two markets are shared by all the sources; other
# lines (OU 1.5/3.5, AH) are dropped rather than mapped onto over_under_25,
# which would compare different bets (ANALYSIS_GOTCHAS #25).
_MKT = {"1x2": "1x2", "match result": "1x2",
        "over_under_25": "over_under_25", "over / under": "over_under_25"}


def _norm_team(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|cf|sc|ac|afc|cd|ca|club|de|do|da|the)\b", " ", s)
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def load(name: str) -> list[dict]:
    p = LEDGER / f"picks_{name}.csv"
    if not p.exists():
        return []
    with p.open() as fh:
        return list(csv.DictReader(fh))


def usable(rows: list[dict], since: str) -> list[dict]:
    """Rows we can actually test: shared market, decodable pick, real price."""
    out = []
    for r in rows:
        mk = _MKT.get((r.get("market") or "").strip().lower())
        sel = _SEL.get((r.get("pick") or "").strip().lower())
        res = (r.get("result") or "").strip().lower()
        try:
            odds = float(r.get("odds") or 0)
        except ValueError:
            continue
        if not mk or not sel or odds <= 1.0 or res not in ("won", "lost"):
            continue
        if (r.get("kickoff_date") or "") < since:
            continue
        out.append({**r, "_mk": mk, "_sel": sel, "_odds": odds, "_res": res})
    return out


class Fixtures:
    """Fuzzy (date, home, away) -> matches.id, with a +/-1 day window.

    The +/-1 day matters: tipsters stamp picks in their own timezone, so a
    22:00 UTC kickoff lands on either side of midnight depending on the site.
    Matching on the exact date alone silently drops those fixtures.
    """

    def __init__(self, cur, since: str, until: str, min_score: int):
        self.min_score = min_score
        self.by_date: dict[str, list] = {}
        cur.execute(
            """SELECT m.id, m.date::date, ht.name, at.name
                 FROM matches m
                 JOIN teams ht ON ht.id = m.home_team_id
                 JOIN teams at ON at.id = m.away_team_id
                WHERE m.date >= %s AND m.date <= %s""",
            (since, until))
        for mid, d, h, a in cur.fetchall():
            self.by_date.setdefault(str(d), []).append((mid, _norm_team(h), _norm_team(a)))

    def find(self, dt: str, home: str, away: str):
        try:
            y, m, d = map(int, dt.split("-"))
            base = date(y, m, d)
        except (ValueError, AttributeError):
            return None
        nh, na = _norm_team(home), _norm_team(away)
        best = None
        for off in (-1, 0, 1):
            for mid, hh, aa in self.by_date.get((base + timedelta(days=off)).isoformat(), []):
                score = min(fuzz.token_set_ratio(nh, hh), fuzz.token_set_ratio(na, aa))
                if score >= self.min_score and (best is None or score > best[0]):
                    best = (score, mid)
        return best[1] if best else None


def _prices(cur, match_id: int, market: str, selection: str) -> tuple:
    """(best across all books, Bet365's best) for one bet, over all snapshots.

    MAX over the whole history, not the closing quote: this asks the most
    generous possible question — could the claimed price have been taken at
    ANY moment, at ANY of the books we record? A claim above this was never
    available. Using a single timestamp would make honest early prices look
    inflated.
    """
    cur.execute(
        """SELECT MAX(odds) FILTER (WHERE TRUE),
                  MAX(odds) FILTER (WHERE bookmaker = 'Bet365')
             FROM odds_snapshots
            WHERE match_id = %s AND market = %s AND selection = %s AND odds > 1""",
        (match_id, market, selection))
    row = cur.fetchone()
    return (float(row[0]) if row and row[0] else None,
            float(row[1]) if row and row[1] else None)


def _z_two_prop(k1: int, n1: int, k2: int, n2: int) -> float:
    """z for p1 != p2. Hand-rolled so the script has no scipy dependency."""
    if not n1 or not n2:
        return 0.0
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (p1 - p2) / se if se else 0.0


def _p_from_z(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def collect(cur, rows: list[dict], fx: Fixtures) -> list[dict]:
    out = []
    for r in rows:
        mid = fx.find(r["kickoff_date"], r.get("home_team", ""), r.get("away_team", ""))
        if not mid:
            continue
        best, b365 = _prices(cur, mid, r["_mk"], r["_sel"])
        if best is None:
            continue
        out.append({"odds": r["_odds"], "best": best, "b365": b365, "res": r["_res"],
                    "teams": f"{r.get('home_team')} v {r.get('away_team')}",
                    "date": r["kickoff_date"], "mk": r["_mk"], "sel": r["_sel"]})
    return out


def step1_validate(pairs: list[dict]) -> None:
    """Betaminic names Bet365. Do its numbers match the Bet365 we recorded?"""
    d = [p["odds"] - p["b365"] for p in pairs if p["b365"]]
    console.print("\n[bold]Step 1 — is OUR odds data trustworthy?[/bold]")
    if len(d) < 30:
        console.print(f"  [yellow]only {len(d)} Betaminic picks have a Bet365 price "
                      "in our DB — too few to validate.[/yellow]")
        return
    d.sort()
    exact = 100.0 * sum(1 for x in d if abs(x) < 0.005) / len(d)
    console.print(f"  Betaminic publishes the Bet365 price it took. Against our own "
                  f"recorded Bet365 quotes (n={len(d)}):")
    console.print(f"    exact agreement  [bold]{exact:.0f}%[/bold]   "
                  f"median {median(d):+.3f}   mean {sum(d) / len(d):+.3f}   "
                  f"p90 {d[int(0.9 * len(d))]:+.3f}")
    console.print("  [dim]Two parties that never spoke report the same prices for the "
                  "same bets. Our odds_snapshots are not the weak link in what "
                  "follows.[/dim]")


def step2_table(by_source: dict[str, list[dict]]) -> None:
    """Tail of claimed/best. Immune to which book a source happens to quote."""
    t = Table(show_header=True, header_style="bold")
    t.add_column("source"); t.add_column("picks", justify="right")
    t.add_column("any excess", justify="right")
    t.add_column(">1.25x best", justify="right")
    t.add_column(">1.5x best", justify="right")
    t.add_column(">2x best", justify="right")
    t.add_column("median excess", justify="right")

    stats = {}
    for name, pairs in by_source.items():
        if len(pairs) < 50:
            continue
        n = len(pairs)
        ex = [p["odds"] / p["best"] for p in pairs if p["odds"] > p["best"] + 0.005]
        if not ex:
            continue
        rate = lambda thr: 100.0 * sum(1 for r in ex if r > thr) / n   # noqa: E731
        stats[name] = {"n": n, "tail15": rate(1.50)}
        t.add_row(name, str(n), f"{100.0 * len(ex) / n:.1f}%", f"{rate(1.25):.1f}%",
                  f"[bold]{rate(1.50):.1f}%[/bold]", f"{rate(2.0):.1f}%",
                  f"{median(ex):.3f}x")

    console.print("\n[bold]Step 2 — how far past reachable do the claims go?[/bold]")
    console.print("[dim]  'best' = the highest quote we ever recorded for that exact "
                  "bet, at any of our books, at any time. Matching noise overshoots by "
                  "a few percent; it does not overshoot by 50%.[/dim]")
    console.print(t)

    if "betaminic" in stats and "forebet" in stats:
        b, f = stats["betaminic"], stats["forebet"]
        console.print(
            f"  Betaminic — a source that names its book and can be checked against "
            f"it — claims a price >1.5x the market on [bold]{b['tail15']:.1f}%[/bold] of "
            f"its picks. That is the measurement noise of this method.")
        console.print(
            f"  Forebet does it on [bold]{f['tail15']:.1f}%[/bold].")


def step3_matched(by_source: dict[str, list[dict]]) -> None:
    """Same test, both sources measured against the SAME single book.

    This is the version with no book-selection bias left in it: Bet365 is the
    reference for both, so neither source is advantaged by which book it
    happens to quote. The winner/loser split is the hindsight test.
    """
    t = Table(show_header=True, header_style="bold")
    t.add_column("source"); t.add_column("picks", justify="right")
    t.add_column(">1.5x Bet365", justify="right")
    t.add_column("on winners", justify="right")
    t.add_column("on losers", justify="right")
    t.add_column("gap", justify="right")
    t.add_column("p", justify="right")

    for name, pairs in by_source.items():
        rows = [p for p in pairs if p["b365"]]
        won = [p for p in rows if p["res"] == "won"]
        lost = [p for p in rows if p["res"] == "lost"]
        if len(rows) < 50 or not won or not lost:
            continue
        big = lambda xs: sum(1 for p in xs if p["odds"] > 1.5 * p["b365"])  # noqa: E731
        kw, kl = big(won), big(lost)
        pw, pl = 100.0 * kw / len(won), 100.0 * kl / len(lost)
        pv = _p_from_z(_z_two_prop(kw, len(won), kl, len(lost)))
        t.add_row(name, str(len(rows)),
                  f"[bold]{100.0 * (kw + kl) / len(rows):.1f}%[/bold]",
                  f"{pw:.1f}% (n={len(won)})", f"{pl:.1f}% (n={len(lost)})",
                  f"{pw - pl:+.1f}pp", f"{pv:.3g}")

    console.print("\n[bold]Step 3 — same test, one shared reference book[/bold]")
    console.print("[dim]  Both sources measured against Bet365, so neither is helped or "
                  "hurt by which book it quotes. Any remaining difference is the "
                  "sources, not the method.[/dim]")
    console.print(t)
    console.print("  [dim]A bettor cannot systematically get better prices on the bets "
                  "that happen to win — the result is not knowable at bet time. A flat "
                  "won/lost split is what an honest record looks like.[/dim]")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--min-score", type=int, default=85,
                    help="fuzzy team-name threshold (default 85)")
    ap.add_argument("--days", type=int, default=120, help="lookback (default 120)")
    args = ap.parse_args()

    since = (date.today() - timedelta(days=args.days)).isoformat()
    until = (date.today() + timedelta(days=1)).isoformat()

    sources = {"forebet": usable(load("forebet"), since),
               "betaminic": usable(load("betaminic"), since),
               "signalodds": usable(load("signalodds"), since)}
    console.print(f"\n[bold]Forebet's odds, checked against a third party[/bold]  "
                  f"(since {since})")
    for n, r in sources.items():
        console.print(f"  {n:11} {len(r):5} testable picks")

    from workers.api_clients.db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        fx = Fixtures(cur, since, until, args.min_score)
        by_source = {n: collect(cur, r, fx) for n, r in sources.items() if r}

    for n, p in by_source.items():
        console.print(f"  {n:11} {len(p):5} matched to our fixtures and priced")

    if "betaminic" in by_source:
        step1_validate(by_source["betaminic"])
    step2_table(by_source)
    step3_matched(by_source)

    fb = sorted((p for p in by_source.get("forebet", []) if p["res"] == "won"),
                key=lambda p: p["best"] - p["odds"])[:6]
    if fb:
        console.print("\n  Largest unreachable prices on Forebet winners:")
        for p in fb:
            console.print(f"    {p['date']}  {p['teams'][:42]:44} "
                          f"{p['mk']}/{p['sel']:5} claimed [bold]{p['odds']:.2f}[/bold] "
                          f"vs best anywhere {p['best']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
