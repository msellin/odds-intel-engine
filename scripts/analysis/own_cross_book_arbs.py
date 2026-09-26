#!/usr/bin/env python3
"""[[#191]] OWN research idea 4 — cross-book arbitrage / near-arbs among the books WE can bet.

QUESTION. How often do Coolbet, Unibet-Site, Epicbet and Tonybet (pre-match) jointly offer a
book whose combined overround is < 0 (a true arb) or <= 1% (a near-arb)? And separately: how
often does one of those books BACK above what the Betfair Exchange LAYS (not placeable from
Estonia — reported on its own, never mixed into the OWN numbers)?

METHOD (fixed before the first run).
  Window      kickoffs 2026-09-20 00:00 UTC .. now. Inside the 7-day full-resolution retention
              window (§59/§64), so the price PATH is intact. Tonybet exists from 2026-09-23 14:00.
  Rows        odds_snapshots, is_live not true, timestamp < kickoff, odds > 1, the four books.
              Rows board_audit / board_guard already moved to odds_snapshots_quarantined are gone
              (§79); (match, book) pairs named in data_quality_findings are dropped as well.
  Markets     1x2 · btts · draw_no_bet · asian_handicap (key = market + handicap_line, stored
              home-perspective for both sides, §53) · over_under_<line> full-time only (no _1h/_2h;
              over_under_05 dropped — §80). Same key = same line = same settlement; a quarter line
              only ever meets the same quarter line. A per-(book, line) convention check (home
              implied prob. vs the other books' median on the same line) is printed first.
  Assembly    per (match, book, key) with the shared `workers.utils.odds_assembly.assemble`
              (window 120 s — Coolbet writes legs sub-second apart, §62/§63).
  Simultaneity event-driven: at every instant any of the books writes a complete market for the
              key, each book's latest assembled quote counts only if it is <= N min old (N = 15
              primary — the repo's CROSS_BOOK_MAX_GAP_S, §65 — and N = 60 sensitivity, because
              Coolbet / Unibet-Site re-write a given fixture only every ~60-77 min). Best price per
              side across the live books; the legs must come from >= 2 books.
  Episode     consecutive instants with the same side->book assignment and overround <= 1%.
              Duration = first instant .. first instant where it no longer holds (i.e. "until the
              next scrape that kills it"); which book's write killed it is recorded.
  Opportunity episodes are merged into one OPPORTUNITY per (match, key, leg books, leg prices):
              a quote that drops out of the N-min allowance between two scrapes and comes back at
              the same prices is the same bet, placeable once. All counts and € are opportunities.
  Phantoms    fair price per episode start: median de-vigged quote of the AF-fed peer books
              (Pinnacle, Bet365, 1xBet, Marathonbet, ... — >= 2 peers, each <= 180 min old; the
              AF value can be ~2 h stale per §88, which is fine for a phantom guard) else the liquid
              Betfair Exchange mid (<= 30 min) else "unverified". NOT the median of the live
              Estonian books — that hid Epicbet's flat ~1.87/1.87 template boards (see load_peers).
              edge_leg = odds * p_fair - 1. The leg
              with the largest edge is the OFF leg. Classes: clean (off edge <= 5%), stale-leg
              (5-15%), phantom (> 15% — wrong board / market split, excluded from €).
  Stale leg   for the OFF leg: its fetch age at the instant, and how long its VALUE had been
              unchanged (§88: fetch time is not liveness); whether the episode was ended by the
              OFF book re-pricing.
  €           true arbs only, clean + stale-leg classes, per leg cap C in {50, 200}:
              payout P = C * min(odds), profit = -P * overround. Scaled to 30 days by the
              observed kickoff days.
  Exchange    back at book O vs exchange LAY L (liquid: spread <= 5%, market matched >= €1k),
              commission c in {2%, 5%}: arb iff O*(1-c) > L - c; margin = (L - c)/(O*(1-c)) - 1.

Read-only. Writes CSV to data/models/_research/own191_arbs/ (gitignored) and prints the tables
dev/active/own-research-cross-book-arbs.md is built from.

    PYTHONPATH=. python3 scripts/analysis/own_cross_book_arbs.py
"""
from __future__ import annotations

import re
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from workers.api_clients.db import get_conn  # noqa: E402
from workers.utils.odds_assembly import assemble  # noqa: E402

BOOKS = ("Coolbet", "Unibet-Site", "Epicbet", "Tonybet")
START = datetime(2026, 9, 20, tzinfo=timezone.utc)
ASSEMBLE_WINDOW_S = 120.0
N_PRIMARY, N_SENS = 15, 60
NEAR = 0.01
CLEAN_EDGE, PHANTOM_EDGE = 0.05, 0.15
CAPS = (50, 200)
EXCH_COMMISSIONS = (0.02, 0.05)
EXCH_MAX_AGE_MIN = 30
PIN_MAX_AGE_MIN = 120
OUT = ROOT / "data" / "models" / "_research" / "own191_arbs"

SIDES = {"1x2": ("home", "draw", "away"), "btts": ("yes", "no"),
         "draw_no_bet": ("home", "away"), "asian_handicap": ("home", "away")}
OU_RE = re.compile(r"^over_under_(\d+)$")
EXCH_MARKETS = {"1x2", "btts", "over_under_15", "over_under_25", "over_under_35"}


def sides_of(market: str):
    if market in SIDES:
        return SIDES[market]
    return ("over", "under") if OU_RE.match(market) else None


def fetch(sql, params):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def load_day(d0, d1):
    rows = fetch("""
        SELECT o.match_id::text, o.bookmaker, o.market, coalesce(o.handicap_line, 0)::float,
               o.selection, o.odds::float, o.timestamp, m.date
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE m.date >= %s AND m.date < %s AND m.date < now()
           AND o.bookmaker = ANY(%s) AND o.is_live IS NOT TRUE AND o.odds > 1
           AND o.timestamp < m.date AND o.timestamp > m.date - interval '3 days'
           AND (o.market IN ('1x2','btts','draw_no_bet','asian_handicap')
                OR (o.market ~ '^over_under_[0-9]+$' AND o.market <> 'over_under_05'))
    """, (d0, d1, list(BOOKS)))
    return rows


def load_exchange(d0, d1):
    return fetch("""
        SELECT e.match_id::text, e.market, e.selection, e.back::float, e.lay::float,
               e.lay_size::float, e.market_matched::float, e.captured_at
          FROM exchange_quotes e JOIN matches m ON m.id = e.match_id
         WHERE m.date >= %s AND m.date < %s AND m.date < now() AND e.captured_at < m.date
           AND e.market = ANY(%s)
    """, (d0, d1, list(EXCH_MARKETS)))


def dq_pairs():
    rows = fetch("SELECT DISTINCT match_id::text, bookmaker FROM data_quality_findings "
                 "WHERE found_at > %s AND match_id IS NOT NULL AND bookmaker IS NOT NULL",
                 (START - timedelta(days=2),))
    return {(m, b) for m, b in rows}


def devig(q, sides):
    inv = [1.0 / q[s] for s in sides]
    t = sum(inv)
    return {s: i / t for s, i in zip(sides, inv)}


def build_series(rows, dq):
    """{(match, key): {book: [(t, quote), ...]}}, key = (market, line). Pinnacle kept apart."""
    raw = defaultdict(list)
    kickoff = {}
    for mid, book, market, line, sel, odds, ts, ko in rows:
        if (mid, book) in dq:
            continue
        sides = sides_of(market)
        if not sides or sel not in sides:
            continue
        key = (market, round(line, 2) if market == "asian_handicap" else 0.0)
        raw[(mid, key, book)].append((ts, sel, odds))
        kickoff[mid] = ko
    series = defaultdict(dict)
    for (mid, key, book), obs in raw.items():
        tri = assemble(obs, sides_of(key[0]), window_s=ASSEMBLE_WINDOW_S)
        if not tri:
            continue
        # one quote per anchor second (a same-timestamp triple yields one entry per row)
        seen, out = set(), []
        for t, q in tri:
            k = t.replace(microsecond=0)
            if k in seen:
                continue
            seen.add(k)
            out.append((t, q))
        series[(mid, key)][book] = out
    return series, kickoff


def latest_before(lst, t, times):
    i = bisect_right(times, t) - 1
    return lst[i] if i >= 0 else None


def since_change(lst, idx):
    """Time the value at lst[idx] was first seen unchanged (walk back while equal)."""
    q = lst[idx][1]
    j = idx
    while j > 0 and all(abs(lst[j - 1][1][s] - q[s]) < 1e-9 for s in q):
        j -= 1
    return lst[j][0]


def exch_index(exrows):
    """{(match, market): [(t, {sel: (back, lay, lay_size, matched)})]} liquid full markets only."""
    g = defaultdict(dict)
    for mid, market, sel, back, lay, lsz, mm, ts in exrows:
        g[(mid, market, ts)][sel] = (back, lay, lsz, mm)
    idx = defaultdict(list)
    for (mid, market, ts), d in g.items():
        sides = sides_of(market)
        if not all(s in d for s in sides):
            continue
        ok = all(b and l and b > 1 and l >= b and l / b - 1 <= 0.05 and (d[s][3] or 0) >= 1000
                 for s, (b, l, _, _) in d.items() if s in sides)
        if ok:
            idx[(mid, market)].append((ts, d))
    for v in idx.values():
        v.sort(key=lambda x: x[0])
    return idx


PEER_EXCLUDE = ("Max", "Avg", "Betfair Exchange", "BetWin", "Betfred", "Unibet",
                "Unibet-Kambi", "Coolbet-OddsAPI", "Optibet") + BOOKS   # board_guard._NOT_PEERS + ours
PEER_MAX_AGE_MIN = 180
MIN_PEERS = 2


def load_peers(match_ids):
    """{(match, key): {peer_book: [(t, quote)]}} from the AF-fed books (Pinnacle included).

    WHY a broad peer consensus and not the median of the live Estonian books: the first run
    used the latter and it hid the commonest phantom — Epicbet quoting a flat ~1.87/1.87
    template on BTTS / O/U 2.5 for a lower-league match (Landvetter v Torslanda, 2026-09-25)
    where eight other books had yes at ~1.52. With only two live books the median sits halfway
    and both legs look like modest value. AF books write a complete market per timestamp
    (§62), so grouping by timestamp is the right assembly for them."""
    if not match_ids:
        return {}
    rows = fetch("""
        SELECT o.match_id::text, o.bookmaker, o.market, coalesce(o.handicap_line, 0)::float,
               o.selection, o.odds::float, o.timestamp
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%s::uuid[]) AND NOT (o.bookmaker = ANY(%s))
           AND o.is_live IS NOT TRUE AND o.odds > 1 AND o.timestamp < m.date
           AND o.timestamp > m.date - interval '3 days'
           AND (o.market IN ('1x2','btts','draw_no_bet','asian_handicap')
                OR o.market ~ '^over_under_[0-9]+$')
    """, (list(match_ids), list(PEER_EXCLUDE)))
    g = defaultdict(dict)
    for mid, book, market, line, sel, odds, ts in rows:
        key = (market, round(line, 2) if market == "asian_handicap" else 0.0)
        g[(mid, key, book, ts)][sel] = odds
    out = defaultdict(lambda: defaultdict(list))
    for (mid, key, book, ts), q in g.items():
        sides = sides_of(key[0])
        if sides and all(s in q for s in sides):
            out[(mid, key)][book].append((ts, {s: q[s] for s in sides}))
    for d in out.values():
        for v in d.values():
            v.sort(key=lambda x: x[0])
    return out


def fair_at(mid, key, t, peers, exidx):
    """Fair probabilities at instant t: median de-vigged AF-peer quote (>= 2 peers, each
    <= 180 min old) else the liquid Betfair Exchange mid (<= 30 min) else None."""
    sides = sides_of(key[0])
    probs = []
    for book, lst in peers.get((mid, key), {}).items():
        p = latest_before(lst, t, [x[0] for x in lst])
        if p and (t - p[0]).total_seconds() <= PEER_MAX_AGE_MIN * 60:
            probs.append(devig(p[1], sides))
    if len(probs) >= MIN_PEERS:
        return {s: median(p[s] for p in probs) for s in sides}, f"peers({len(probs)})"
    if key[0] in EXCH_MARKETS and key[1] == 0.0:
        lst = exidx.get((mid, key[0]))
        if lst:
            e = latest_before(lst, t, [x[0] for x in lst])
            if e and (t - e[0]).total_seconds() <= EXCH_MAX_AGE_MIN * 60:
                mids = {s: 2.0 / (1 / e[1][s][0] + 1 / e[1][s][1]) for s in sides}
                return devig(mids, sides), "exchange"
    return None, "none"


def scan(series, kickoff, n_min):
    """Every near-arb episode for one staleness allowance (raw; classified by `finalize`)."""
    eps = []
    for (mid, key), per_book in series.items():
        books = {b: v for b, v in per_book.items() if b in BOOKS}
        if len(books) < 2:
            continue
        sides = sides_of(key[0])
        times = {b: [x[0] for x in v] for b, v in books.items()}
        instants = sorted({t for v in times.values() for t in v})
        cur = None
        for t in instants:
            live = {}
            for b, v in books.items():
                i = bisect_right(times[b], t) - 1
                if i >= 0 and (t - v[i][0]).total_seconds() <= n_min * 60:
                    live[b] = i
            state = None
            if len(live) >= 2:
                best = {}
                for s in sides:
                    b = max(live, key=lambda bb: books[bb][live[bb]][1][s])
                    best[s] = (b, books[b][live[b]][1][s])
                if len({b for b, _ in best.values()}) >= 2:
                    m = sum(1 / o for _, o in best.values()) - 1
                    if m <= NEAR:
                        state = (tuple((s, best[s][0]) for s in sides), m, best, dict(live))
            if cur and (state is None or state[0] != cur["sig"]):
                cur["end"] = t
                cur["killer"] = [b for b, v in books.items() if t in set(times[b])]
                eps.append(cur)
                cur = None
            if state and cur is None:
                sig, m, best, live_idx = state
                cur = dict(match_id=mid, market=key[0], line=key[1], sig=sig, start=t, end=None,
                           killer=None, min_m=m, start_m=m, best=best, live_idx=live_idx,
                           kickoff=kickoff[mid], books=books)
            elif state and cur:
                cur["min_m"] = min(cur["min_m"], state[1])
        if cur:
            cur["end"] = kickoff[mid]
            cur["killer"] = ["kickoff"]
            eps.append(cur)
    return eps


def finalize(eps, peers, exidx):
    out = []
    for e in eps:
        books, sides = e["books"], sides_of(e["market"])
        fair, src = fair_at(e["match_id"], (e["market"], e["line"]), e["start"], peers, exidx)
        legs = []
        for s in sides:
            b, o = e["best"][s]
            i = e["live_idx"][b]
            legs.append(dict(side=s, book=b, odds=o, edge=(o * fair[s] - 1) if fair else float("nan"),
                             age_min=(e["start"] - books[b][i][0]).total_seconds() / 60,
                             unchanged_min=(e["start"] - since_change(books[b], i)).total_seconds() / 60))
        if fair:
            off = max(legs, key=lambda x: x["edge"])
            cls = "clean" if off["edge"] <= CLEAN_EDGE else ("stale-leg" if off["edge"] <= PHANTOM_EDGE else "phantom")
        else:  # no reference: the OFF leg is the one furthest above the other books' price
            off = max(legs, key=lambda x: x["age_min"])
            cls = "unverified"
        pair = "+".join(sorted({l["book"] for l in legs}))
        out.append(dict(
            match_id=e["match_id"], market=e["market"], line=e["line"],
            mkt_family=("over_under" if OU_RE.match(e["market"]) else e["market"]),
            day=e["kickoff"].date().isoformat(), start=e["start"], end=e["end"],
            dur_min=(e["end"] - e["start"]).total_seconds() / 60,
            mins_to_ko=(e["kickoff"] - e["start"]).total_seconds() / 60,
            margin=e["start_m"], min_margin=e["min_m"], is_arb=e["start_m"] < 0,
            books=pair, n_books=len({l["book"] for l in legs}), fair_src=src, cls=cls,
            off_book=off["book"], off_side=off["side"], off_edge=off["edge"], off_odds=off["odds"],
            off_age=off["age_min"], off_unchanged=off["unchanged_min"],
            off_oldest=off["age_min"] >= max(l["age_min"] for l in legs) - 1e-6,
            killed_by_off=off["book"] in (e["killer"] or []),
            killer="+".join(e["killer"] or []), min_odds=min(l["odds"] for l in legs),
            price_sig=";".join(f'{l["side"]}@{l["book"]}:{l["odds"]:.4f}' for l in legs),
            legs=";".join(f'{l["side"]}@{l["book"]}:{l["odds"]:.3f}({l["edge"]:+.3f},age{l["age_min"]:.0f})'
                          for l in legs)))
    return pd.DataFrame(out)


def scan_exchange(series, kickoff, exidx, n_min):
    """Book BACK vs exchange LAY on the same selection, both <= n_min old at the instant."""
    out = []
    for (mid, market), elist in exidx.items():
        per_book = series.get((mid, (market, 0.0)), {})
        etimes = [x[0] for x in elist]
        for b in BOOKS:
            lst = per_book.get(b)
            if not lst:
                continue
            btimes = [x[0] for x in lst]
            instants = sorted(set(btimes) | set(etimes))
            best_by_sel = {}
            for t in instants:
                i = bisect_right(btimes, t) - 1
                j = bisect_right(etimes, t) - 1
                if i < 0 or j < 0:
                    continue
                if (t - lst[i][0]).total_seconds() > n_min * 60 or (t - elist[j][0]).total_seconds() > n_min * 60:
                    continue
                for s, o in lst[i][1].items():
                    back, lay, lsz, _ = elist[j][1][s]
                    for c in EXCH_COMMISSIONS:
                        m = (lay - c) / (o * (1 - c)) - 1
                        if m <= NEAR:
                            k = (s, c)
                            if k not in best_by_sel or m < best_by_sel[k]["margin"]:
                                best_by_sel[k] = dict(match_id=mid, market=market, book=b, side=s,
                                                      commission=c, margin=m, odds=o, lay=lay,
                                                      lay_size=lsz, t=t,
                                                      day=kickoff[mid].date().isoformat(),
                                                      book_age=(t - lst[i][0]).total_seconds() / 60)
            out.extend(best_by_sel.values())
    return pd.DataFrame(out)


def convention_check(series):
    """Home implied probability per (book, AH line) vs the other books' median on the same line."""
    diffs = defaultdict(list)
    for (mid, key), per_book in series.items():
        if key[0] not in ("asian_handicap", "draw_no_bet"):
            continue
        last = {b: devig(v[-1][1], ("home", "away"))["home"] for b, v in per_book.items()}
        if len(last) < 3:
            continue
        for b, p in last.items():
            others = [q for bb, q in last.items() if bb != b]
            diffs[(key[0], b)].append(p - median(others))
    print("\n## convention check — home prob. minus peers' median, same key (|median| ~0 = same convention)")
    for (mk, b), v in sorted(diffs.items()):
        v = sorted(v)
        print(f"  {mk:15s} {b:12s} n={len(v):6d} median={median(v):+.4f} "
              f"share|d|>0.15={sum(abs(x) > 0.15 for x in v) / len(v):.3%}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    dq = dq_pairs()
    now = datetime.now(timezone.utc)
    frames = {N_PRIMARY: [], N_SENS: []}
    exframes = {N_PRIMARY: [], N_SENS: []}
    stats = []
    d = START
    while d < now:
        d1 = d + timedelta(days=1)
        rows = load_day(d, d1)
        exrows = load_exchange(d, d1)
        series, kickoff = build_series(rows, dq)
        exidx = exch_index(exrows)
        cov = defaultdict(int)
        for (mid, key), pb in series.items():
            nb = len([b for b in pb if b in BOOKS])
            if nb >= 2:
                cov[("over_under" if OU_RE.match(key[0]) else key[0])] += 1
        stats.append(dict(day=d.date().isoformat(), rows=len(rows), matches=len(kickoff),
                          **{f"keys2+_{k}": v for k, v in cov.items()}))
        if d.date().isoformat() == "2026-09-25":
            convention_check(series)
        raw = {n: scan(series, kickoff, n) for n in frames}
        peers = load_peers({e["match_id"] for v in raw.values() for e in v})
        for n in frames:
            frames[n].append(finalize(raw[n], peers, exidx))
            exframes[n].append(scan_exchange(series, kickoff, exidx, n))
        print(f"{d.date()} rows={len(rows)} matches={len(kickoff)} exch_rows={len(exrows)}", flush=True)
        d = d1
    days = len(stats)
    print("\n## coverage (market-keys with >= 2 of our books quoting)")
    print(pd.DataFrame(stats).fillna(0).to_string(index=False))
    for n, fl in frames.items():
        df = pd.concat([f for f in fl if len(f)], ignore_index=True)
        df.to_csv(OUT / f"episodes_N{n}.csv", index=False)
        report(df, n, days)
    for n, fl in exframes.items():
        ex = pd.concat([f for f in fl if len(f)], ignore_index=True) if any(len(f) for f in fl) else pd.DataFrame()
        ex.to_csv(OUT / f"exchange_N{n}.csv", index=False)
        report_exchange(ex, n)


def q(s, ps=(0.1, 0.5, 0.9)):
    return " / ".join(f"{s.quantile(p):.2f}" for p in ps) if len(s) else "-"


def report(df, n, days):
    print(f"\n\n######## OUR 4 BOOKS — staleness allowance N = {n} min ({days} kickoff days) ########")
    if df.empty:
        print("no episodes")
        return
    n_ep = len(df)
    df = df.sort_values("start")
    agg = df.groupby(["match_id", "market", "line", "price_sig"], as_index=False)
    first = agg.first()
    span = agg.agg(first_start=("start", "min"), last_end=("end", "max"), n_episodes=("start", "size"),
                   held_min=("dur_min", "sum"), any_killed_by_off=("killed_by_off", "max"))
    df = first.merge(span, on=["match_id", "market", "line", "price_sig"])
    df["span_min"] = (pd.to_datetime(df.last_end) - pd.to_datetime(df.first_start)).dt.total_seconds() / 60
    print(f"{n_ep} raw episodes -> {len(df)} distinct opportunities (same match, line, books, prices)")
    df["kind"] = df["is_arb"].map({True: "arb<0", False: "near 0-1%"})
    print("\n## episodes by class x kind")
    print(pd.crosstab(df["cls"], df["kind"], margins=True).to_string())
    print("\n## per day (all classes | clean+stale-leg only)")
    ok = df[df.cls.isin(["clean", "stale-leg"])]
    t = pd.DataFrame({
        "arbs_all": df[df.is_arb].groupby("day").size(),
        "near_all": df[~df.is_arb].groupby("day").size(),
        "arbs_ok": ok[ok.is_arb].groupby("day").size(),
        "near_ok": ok[~ok.is_arb].groupby("day").size(),
        "arbs_clean": ok[ok.is_arb & (ok.cls == "clean")].groupby("day").size(),
    }).fillna(0).astype(int)
    print(t.to_string())
    for name, sub in (("true arbs, non-phantom", ok[ok.is_arb]), ("near-arbs 0-1%, non-phantom", ok[~ok.is_arb])):
        print(f"\n## {name}: n={len(sub)}")
        if sub.empty:
            continue
        print(f"  margin p10/p50/p90 (%)  : {q(sub.margin * 100)}")
        print(f"  first episode duration min p10/50/90 (until the scrape that broke it): {q(sub.dur_min)}")
        print(f"  summed time held min p10/50/90: {q(sub.held_min)};  first-seen..last-seen span min: {q(sub.span_min)}")
        print(f"  mins to KO p10/50/90    : {q(sub.mins_to_ko)}")
        print(f"  OFF-leg edge vs fair (%) p10/50/90: {q(sub.off_edge * 100)}")
        print(f"  OFF-leg fetch age min   : {q(sub.off_age)}   unchanged-for min: {q(sub.off_unchanged)}")
        print(f"  OFF leg is the oldest-fetched leg: {sub.off_oldest.mean():.0%};  "
              f"ended by the OFF book re-pricing: {sub.killed_by_off.mean():.0%};  ended by kickoff: "
              f"{(sub.killer == 'kickoff').mean():.0%}")
        print("  fair source:", sub.fair_src.value_counts().to_dict())
        print("  by market:", sub.mkt_family.value_counts().to_dict())
        print("  by book combination:", sub.books.value_counts().to_dict())
        print("  OFF book:", sub.off_book.value_counts().to_dict())
    arbs = ok[ok.is_arb]
    print("\n## € estimate — true arbs, non-phantom (profit = -overround x cap x min odds)")
    for cap in CAPS:
        prof = (-arbs.margin * cap * arbs.min_odds)
        clean = arbs.cls == "clean"
        lasting = arbs.held_min >= 5
        print(f"  cap €{cap}/leg: total €{prof.sum():.0f} over {days} d → €{prof.sum() / days * 30:.0f}/month;"
              f"  clean only €{prof[clean].sum() / days * 30:.0f}/month;"
              f"  clean & lasting >= 5 min €{prof[clean & lasting].sum() / days * 30:.0f}/month;"
              f"  stake turnover/month €{(cap * arbs.min_odds * (1 + arbs.margin)).sum() / days * 30:.0f}")
    ph = df[df.cls == "phantom"]
    if len(ph):
        print(f"\n## phantom episodes (OFF-leg edge > {PHANTOM_EDGE:.0%}): n={len(ph)}, "
              f"OFF book {ph.off_book.value_counts().to_dict()}, markets {ph.mkt_family.value_counts().to_dict()}")
    print("\n## top 15 non-phantom true arbs")
    cols = ["day", "market", "line", "margin", "held_min", "mins_to_ko", "cls", "fair_src", "killer", "legs"]
    print(arbs.sort_values("margin").head(15)[cols].to_string(index=False))


def report_exchange(ex, n):
    print(f"\n\n######## BOOK BACK vs BETFAIR EXCHANGE LAY (not placeable from EE) — N = {n} min ########")
    if ex.empty:
        print("none")
        return
    for c in EXCH_COMMISSIONS:
        sub = ex[ex.commission == c]
        arb = sub[sub.margin < 0]
        print(f"  commission {c:.0%}: selections with arb {len(arb)}, near 0-1% {len(sub) - len(arb)}; "
              f"arb margin p10/50/90 % {q(arb.margin * 100)}; by book {arb.book.value_counts().to_dict()}; "
              f"by market {arb.market.value_counts().to_dict()}; lay size p50 €{arb.lay_size.median() if len(arb) else 0:.0f}; "
              f"book age min p50 {arb.book_age.median() if len(arb) else 0:.0f}")
        if len(arb):
            print("   per day:", arb.groupby("day").size().to_dict())


if __name__ == "__main__":
    main()
