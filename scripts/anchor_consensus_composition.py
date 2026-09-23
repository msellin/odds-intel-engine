"""ANCHOR-CONSENSUS-COMPOSITION ([[#113]], 2026-09-23) — does "any 5 books" make an anchor?

Read-only. Writes nothing.
    python3 scripts/anchor_consensus_composition.py --days 120

WHY
---
~41% of priced fixtures have no Pinnacle quote, so every check needing a fair price
goes dark there. The consensus arm (#068) already anchors on a de-vigged mean of
>=5 books, justified on n=11,419 fixtures where it predicted as well as AF-Pinnacle.
The owner's question is the right one: will ANY 5 books do? Books are not
independent — skins copy each other, soft books share the favourite-longshot bias,
and thin markets are priced off one supplier feed — so "5 books" can be 1 opinion.

WHAT IS MEASURED (1X2, latest pre-kickoff triple per book, Shin de-vig,
realised outcomes, log-loss; lower is better)
  T1  fixtures WITH Pinnacle — each consensus variant PAIRED against Pinnacle
      (per-fixture ΔLL, mean + median + t + win%), split by Pinnacle's own
      overround (tight <=4% = a real line; wide = a goodwill quote).
  T2  fixtures WITHOUT Pinnacle — the variants PAIRED against consensus_all
      (the only common yardstick there), plus calibration of consensus_all.
  T3  how many books / which panel books the no-Pinnacle fixtures actually have.

VARIANTS (Pinnacle is never a member)
  consensus_all   every book quoting (>=5 required)
  panel           mean of the books measured to TIE Pinnacle on outcomes
                  (anchor_book_sharpness_research.py §C: Marathonbet, 1xBet,
                  BetVictor, Betfair; + William Hill) — >=3 required
  random_k        k books drawn at random from those quoting (k=2,3,5,8),
                  averaged over draws — the literal "any k books" question
  softest_5       the 5 widest-margin books quoting — the worst case of "any 5"
  best_single     the single panel book present (first by list order)

GUARDS (ANALYSIS_GOTCHAS §9/§58, and the reason ours are optional here)
  * A book's quote is DROPPED from a fixture when any leg is >1.5625x away from
    the leave-one-out median of the other books (home/away inversions in our own
    scraped feeds were 92% of an earlier "finding").
  * Our own scraped books (Coolbet, Epicbet, Unibet-Site, Tonybet) are on a
    different clock (median quote age 15–144 min vs ~5 for AF books), so the
    AF-only variant is reported too.
  * Many simultaneous tests; |t| < 2.5 is read as a tie, not a finding.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.supabase_client import execute_query  # noqa: E402
from workers.model.devig import devig  # noqa: E402

SIDES = ("home", "draw", "away")
EXCLUDED = ("Unibet", "Unibet-Kambi", "Max", "Avg", "Betfair Exchange", "BetWin", "Betfred")
OUR_BOOKS = {"Coolbet", "Epicbet", "Unibet-Site", "Tonybet"}
PANEL = ("Marathonbet", "1xBet", "BetVictor", "Betfair", "William Hill")
GUARD = 1.5625
IDX = {"HOME_WIN": 0, "DRAW": 1, "AWAY_WIN": 2, "home": 0, "draw": 1, "away": 2,
       "H": 0, "D": 1, "A": 2, "1": 0, "X": 1, "2": 2}
DRAWS = 20


def load(days: int):
    rows = execute_query(
        """SELECT DISTINCT ON (o.bookmaker, o.match_id, o.selection)
                  o.bookmaker bk, o.match_id::text mid, lower(o.selection) sel,
                  o.odds::float odds, m.result res
             FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
            WHERE o.market = '1x2' AND o.is_live IS NOT TRUE AND o.odds > 1.01
              AND o.timestamp <= m.date AND m.date > now() - make_interval(days => %s)
              AND m.status = 'finished' AND m.result IS NOT NULL
              AND lower(o.selection) = ANY(%s) AND NOT (o.bookmaker = ANY(%s))
            ORDER BY o.bookmaker, o.match_id, o.selection, o.timestamp DESC""",
        (days, list(SIDES), list(EXCLUDED))) or []
    fx: dict = defaultdict(lambda: defaultdict(dict))
    res: dict = {}
    for r in rows:
        fx[r["mid"]][r["bk"]][r["sel"]] = r["odds"]
        res[r["mid"]] = r["res"]
    out = {}
    for mid, books in fx.items():
        k = IDX.get(str(res[mid]))
        if k is None:
            continue
        trip = {b: [q[s] for s in SIDES] for b, q in books.items() if len(q) == 3}
        if trip:
            out[mid] = (k, trip)
    return out


def guarded(trip: dict) -> dict:
    """Drop a book whose any leg is > GUARD away from the median of the OTHER books."""
    if len(trip) < 3:
        return trip
    keep = {}
    for b, q in trip.items():
        others = [v for o, v in trip.items() if o != b and o != "Pinnacle"]
        if len(others) < 2:
            keep[b] = q
            continue
        med = [median(o[i] for o in others) for i in range(3)]
        if max(max(q[i] / med[i], med[i] / q[i]) for i in range(3)) <= GUARD:
            keep[b] = q
    return keep


def probs(q):
    p = devig(q)
    return p if p and min(p) > 0 else None


def overround(q):
    return sum(1 / x for x in q) - 1


def cons(trip: dict, books) -> list | None:
    ps = [probs(trip[b]) for b in books if b in trip]
    ps = [p for p in ps if p]
    if not ps:
        return None
    m = [mean(p[i] for p in ps) for i in range(3)]
    s = sum(m)
    return [x / s for x in m]


def ll(p, k):
    return -math.log(max(p[k], 1e-9))


def variants(trip: dict, rng: random.Random) -> dict:
    """Every variant's log-loss-ready probability vector (None when not formable)."""
    others = [b for b in trip if b != "Pinnacle"]
    af = [b for b in others if b not in OUR_BOOKS]
    out = {}
    out["consensus_all"] = cons(trip, others) if len(others) >= 5 else None
    out["consensus_af_only"] = cons(trip, af) if len(af) >= 5 else None
    pan = [b for b in PANEL if b in trip]
    out["panel(>=3)"] = cons(trip, pan) if len(pan) >= 3 else None
    out["best_single_panel"] = probs(trip[pan[0]]) if pan else None
    for k in (2, 3, 5, 8):
        if len(others) >= k:
            draws = [cons(trip, rng.sample(others, k)) for _ in range(DRAWS)]
            draws = [d for d in draws if d]
            out[f"random_{k}"] = draws or None      # list of vectors, scored as mean LL
        else:
            out[f"random_{k}"] = None
    if len(others) >= 5:
        soft = sorted(others, key=lambda b: -overround(trip[b]))[:5]
        out["softest_5"] = cons(trip, soft)
    else:
        out["softest_5"] = None
    return out


def score(v, k):
    if v is None:
        return None
    if isinstance(v[0], list):
        return mean(ll(p, k) for p in v)
    return ll(v, k)


def paired(rows: list, name: str, ref: str):
    d = [r[name] - r[ref] for r in rows if r.get(name) is not None and r.get(ref) is not None]
    if len(d) < 50:
        return None
    n, mu = len(d), mean(d)
    var = sum((x - mu) ** 2 for x in d) / (n - 1)
    t = mu / math.sqrt(var / n) if var > 0 else 0.0
    return n, mu, median(d), sum(1 for x in d if x < 0) / n, t


def table(title: str, rows: list, ref: str):
    print(f"\n{title}\n  {'variant':22s} {'n':>6s} {'mean ΔLL':>9s} {'med ΔLL':>9s} "
          f"{'wins':>6s} {'t':>7s}   (Δ = variant − {ref}; negative = variant better)")
    names = [k for k in rows[0] if k not in (ref, "tight", "nbooks")] if rows else []
    for name in names:
        r = paired(rows, name, ref)
        if r:
            n, mu, md, w, t = r
            verdict = "tie" if abs(t) < 2.5 else ("BETTER" if mu < 0 else "worse")
            print(f"  {name:22s} {n:6d} {mu:+9.4f} {md:+9.4f} {100*w:5.1f}% {t:+7.2f}   {verdict}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--seed", type=int, default=113)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    data = load(a.days)
    with_p, without_p = [], []
    books_without = defaultdict(int)
    nb_without = []
    for mid, (k, trip) in data.items():
        trip = guarded(trip)
        v = variants(trip, rng)
        row = {n: score(x, k) for n, x in v.items()}
        others = [b for b in trip if b != "Pinnacle"]
        row["nbooks"] = len(others)
        if "Pinnacle" in trip and probs(trip["Pinnacle"]):
            row["Pinnacle"] = ll(probs(trip["Pinnacle"]), k)
            row["tight"] = overround(trip["Pinnacle"]) <= 0.04
            with_p.append(row)
        else:
            with_p_ = None  # noqa: F841
            without_p.append((row, v.get("consensus_all"), k))
            nb_without.append(len(others))
            for b in others:
                books_without[b] += 1
    print(f"Window {a.days} d — finished fixtures with >=1 complete 1X2 triple: {len(data)}; "
          f"with Pinnacle {len(with_p)}, without {len(without_p)}")

    table("T1 · WITH Pinnacle, all", with_p, "Pinnacle")
    tight = [r for r in with_p if r["tight"]]
    wide = [r for r in with_p if not r["tight"]]
    if tight:
        table(f"T1a · Pinnacle TIGHT (overround <=4%, a real line) — {len(tight)} fixtures", tight, "Pinnacle")
    table(f"T1b · Pinnacle WIDE (a goodwill quote) — {len(wide)} fixtures", wide, "Pinnacle")

    rows2 = [r for r, _, _ in without_p]
    table(f"T2 · WITHOUT Pinnacle — {len(rows2)} fixtures, vs consensus_all", rows2, "consensus_all")

    # calibration of consensus_all on the no-Pinnacle fixtures
    bins = defaultdict(lambda: [0, 0.0, 0])
    for _, p, k in without_p:
        if not p:
            continue
        for i in range(3):
            b = min(int(p[i] * 10), 9)
            bins[b][0] += 1
            bins[b][1] += p[i]
            bins[b][2] += int(i == k)
    print("\nT2b · consensus_all calibration WITHOUT Pinnacle (stated vs realised)")
    for b in sorted(bins):
        n, sp, hit = bins[b]
        if n >= 30:
            print(f"  {b/10:.1f}-{(b+1)/10:.1f}: n={n:5d} stated {sp/n:.3f} realised {hit/n:.3f} "
                  f"gap {hit/n - sp/n:+.3f}")

    print("\nT3 · WITHOUT Pinnacle — books quoting per fixture")
    for lo, hi in ((1, 1), (2, 2), (3, 4), (5, 7), (8, 30)):
        n = sum(1 for x in nb_without if lo <= x <= hi)
        print(f"  {lo}-{hi} books: {n:6d} ({100*n/max(len(nb_without),1):.1f}%)")
    pan_ok = sum(1 for r, _, _ in without_p if r.get("panel(>=3)") is not None)
    print(f"  panel(>=3) formable: {pan_ok} ({100*pan_ok/max(len(without_p),1):.1f}%)")
    print("  most frequent books there: " + ", ".join(
        f"{b} {n}" for b, n in sorted(books_without.items(), key=lambda x: -x[1])[:12]))


if __name__ == "__main__":
    main()
