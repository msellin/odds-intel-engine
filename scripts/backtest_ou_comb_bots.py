#!/usr/bin/env python3
"""O/U COMBINED-MODEL BOTS — honest backtest vs every old O/U bot ([[#149]] round O1).

Pre-registration: dev/active/market2-model-plan.md §4 (fixed before results).
  * NEW bots bot_ou_comb_ev{5,8}: EV = p_comb x odds - 1 >= 5% / 8% (flat), Pinnacle price
    required, odds 1.30-6.00, one pick per (match, line) = best EV, lines 1.5/2.5/3.5, every
    book's OPEN (earliest pre-kickoff) quote, window 2026-08-31..2026-09-24.
  * p_comb = the round-O1 combined model scored on OPEN prices (scripts/ab_ou_combined.py).
  * CLV = odds x p_pin_close - 1, p_pin_close = Pinnacle's power-de-vigged LAST pre-kickoff
    price on the same line and side. Recomputed the SAME way for every old bot's real picks
    (bot_ledger, pre-match, same window), so the comparison is like for like.
  * Null = every side at its best open price, same odds range.

    python3 scripts/backtest_ou_comb_bots.py [--ev 0.05 0.08] [--tag o1]
Read-only against the database. Outputs gitignored under data/models/_research/market2/.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")
import ab_ou_combined as OU  # noqa: E402

OUT = OU.OUT
ODDS_LO, ODDS_HI = 1.30, 6.00
DIRECT = {"Coolbet", "Unibet-Site", "Epicbet", "Tonybet"}


def pin_close() -> pd.DataFrame:
    legs = pd.read_parquet(OUT / "ou_legs_close.parquet")
    c = OU.consensus(legs)
    return c[["match_id", "market", "pin_over"]].rename(columns={"pin_over": "pin_close_over"}).dropna()


def open_quotes() -> pd.DataFrame:
    legs = pd.read_parquet(OUT / "ou_legs_open.parquet")
    return legs[legs.selection.isin(["over", "under"])]


def boot(x, n=10000, seed=11):
    rng = np.random.default_rng(seed)
    x = np.asarray(x, float)
    if len(x) < 5:
        return (float(x.mean()) if len(x) else float("nan")), float("nan"), float("nan"), float("nan")
    m = np.array([x[rng.integers(0, len(x), len(x))].mean() for _ in range(n)])
    return float(x.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)), float((m <= 0).mean())


def summarise(df: pd.DataFrame) -> dict:
    c = df.clv.dropna()
    mu, lo, hi, p = boot(c)
    r = boot(df.pnl.dropna())
    return {"n": int(len(df)), "n_clv": int(len(c)), "clv": mu, "clv_ci": [lo, hi], "p": p,
            "roi": r[0], "roi_ci": [r[1], r[2]], "hit": float((df.pnl > 0).mean()) if len(df) else float("nan"),
            "median_odds": float(df.odds.median()) if len(df) else float("nan")}


def new_bots(evs: list[float], tag: str) -> tuple[dict, pd.DataFrame]:
    pred = pd.read_parquet(OUT / "ou_comb_test_open.parquet")
    q = open_quotes().merge(pred[["match_id", "market", "line", "y", "p_comb", "pin_over"]],
                            on=["match_id", "market"])
    q = q[(q.odds >= ODDS_LO) & (q.odds <= ODDS_HI)]
    q["p_side"] = np.where(q.selection == "over", q.p_comb, 1 - q.p_comb)
    q["ev"] = q.p_side * q.odds - 1
    pc = pin_close()
    q = q.merge(pc, on=["match_id", "market"], how="left")
    q["p_close_side"] = np.where(q.selection == "over", q.pin_close_over, 1 - q.pin_close_over)
    q["clv"] = q.odds * q.p_close_side - 1
    win = np.where(q.selection == "over", q.y == 1, q.y == 0)
    q["pnl"] = np.where(win, q.odds - 1, -1.0)
    q["src"] = np.where(q.bookmaker.isin(DIRECT), "direct", "AF")
    out, picks = {}, []
    # null: every side at its best open price
    best = q.sort_values("odds", ascending=False).drop_duplicates(["match_id", "market", "selection"])
    out["null_best_open"] = summarise(best)
    for ev in evs:
        s = q[(q.ev >= ev) & q.pin_over.notna()].sort_values("ev", ascending=False)
        s = s.drop_duplicates(["match_id", "market"])
        name = f"bot_ou_comb_ev{int(round(ev * 100))}"
        out[name] = summarise(s)
        out[name]["by_line"] = {mk: summarise(s[s.market == mk]) for mk in OU.LINES}
        out[name]["by_src"] = {k: summarise(s[s.src == k]) for k in ("AF", "direct")}
        out[name]["by_side"] = {k: summarise(s[s.selection == k]) for k in ("over", "under")}
        out[name]["per_week"] = float(len(s) / (25 / 7))
        s = s.assign(bot=name, tag=tag)
        picks.append(s)
    return out, pd.concat(picks) if picks else pd.DataFrame()


def old_bots() -> dict:
    import psycopg2
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    q = """SELECT source, bot_name, match_id::text, market, selection, odds::float8, result, bookmaker
             FROM bot_ledger
            WHERE market = ANY(%s) AND NOT is_inplay
              AND kickoff >= '2026-08-31' AND kickoff < '2026-09-25' AND odds > 1.01"""
    with conn.cursor() as cur:
        cur.execute(q, (list(OU.LINES),))
        df = pd.DataFrame(cur.fetchall(), columns=["source", "bot", "match_id", "market", "selection",
                                                   "odds", "result", "bookmaker"])
    df["selection"] = df.selection.str.lower().str.extract(r"(over|under)")[0]
    df = df.dropna(subset=["selection"])
    df = df.merge(pin_close(), on=["match_id", "market"], how="left")
    df["p_close_side"] = np.where(df.selection == "over", df.pin_close_over, 1 - df.pin_close_over)
    df["clv"] = df.odds * df.p_close_side - 1
    r = df.result.astype(str).str.lower()
    df["pnl"] = np.where(r.isin(["won", "win"]), df.odds - 1, np.where(r.isin(["lost", "loss", "lose"]), -1.0, np.nan))
    return {f"{src}:{b}": summarise(g) for (src, b), g in df.groupby(["source", "bot"]) if len(g) >= 10}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ev", nargs="+", type=float, default=[0.05, 0.08])
    ap.add_argument("--tag", default="o1")
    a = ap.parse_args()
    new, picks = new_bots(a.ev, a.tag)
    ps = {k: v["p"] for k, v in new.items() if k.startswith("bot_ou_comb")}
    adj = OU.holm(ps)
    for k in ps:
        new[k]["holm"] = adj[k]
        new[k]["PASS"] = bool(new[k]["clv"] > 0 and adj[k] < 0.05)
    old = old_bots()
    res = {"window": "2026-08-31..2026-09-24", "new": new, "old": old}
    (OUT / f"backtest_ou_{a.tag}.json").write_text(json.dumps(res, indent=1, default=float))
    if len(picks):
        picks.to_csv(OUT / f"backtest_ou_{a.tag}_picks.csv", index=False)
    f = lambda r: (f"n={r['n']:5d} clv {r['clv']*100:+.2f}% [{r['clv_ci'][0]*100:+.2f},{r['clv_ci'][1]*100:+.2f}] "
                   f"(n_clv {r['n_clv']}) roi {r['roi']*100:+.1f}% hit {r['hit']:.3f} odds {r['median_odds']:.2f}")
    print("NEW")
    for k, r in new.items():
        print(f"  {k:24s} {f(r)}" + (f"  Holm {r['holm']:.4f} {'PASS' if r['PASS'] else 'FAIL'}" if "holm" in r else ""))
        for sub in ("by_line", "by_src", "by_side"):
            for kk, rr in r.get(sub, {}).items():
                print(f"      {sub[3:]}:{kk:14s} {f(rr)}")
    print("OLD (bot_ledger, same window, same CLV)")
    for k, r in sorted(old.items(), key=lambda kv: -kv[1]["n"]):
        print(f"  {k:48s} {f(r)}")
    return 0


if __name__ == "__main__" and "--o2" not in sys.argv and "--o3" not in sys.argv:
    raise SystemExit(main())


# ── ROUND O2 — sharp-anchored arms (pre-registered, dev/active/market2-model-plan.md) ──
O2_ARMS = {"S1": ("all", 0.03), "S2": ("all", 0.05), "S3": ("direct", 0.03), "S4": ("direct", 0.05)}
O2_CAP = 0.15
O2_CONFIRM = ("2026-05-01", "2026-08-31")
O1_WINDOW = ("2026-08-31", "2026-09-25")


def o2_quotes() -> pd.DataFrame:
    legs = open_quotes()
    c_open = OU.consensus(pd.read_parquet(OUT / "ou_legs_open.parquet"))[["match_id", "market", "pin_over"]]
    f = pd.read_parquet(OU.CACHE_1X2 / "features_hist_full.parquet", columns=["match_id", "kickoff", "gh", "ga"])
    f["ks"] = (pd.to_datetime(f.kickoff, utc=True) - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    f = f.drop(columns="kickoff").dropna(subset=["gh", "ga"]).astype({"match_id": str})
    q = legs.merge(c_open.dropna(), on=["match_id", "market"]).merge(f, on="match_id")
    q = q[(q.odds >= ODDS_LO) & (q.odds <= ODDS_HI) & (q.bookmaker != "Pinnacle")]
    q["line"] = q.market.map(OU.LINES)
    q["p_pin"] = np.where(q.selection == "over", q.pin_over, 1 - q.pin_over)
    q["ev"] = q.odds * q.p_pin - 1
    q = q.merge(pin_close(), on=["match_id", "market"], how="left")
    q["clv"] = q.odds * np.where(q.selection == "over", q.pin_close_over, 1 - q.pin_close_over) - 1
    over = (q.gh + q.ga) > q.line
    win = np.where(q.selection == "over", over, ~over)
    q["pnl"] = np.where(win, q.odds - 1, -1.0)
    q["src"] = np.where(q.bookmaker.isin(DIRECT), "direct", "AF")
    q["h_before"] = (q.ks - q.ts) / 3600
    return q


def o2(tag: str = "o2") -> None:
    q = o2_quotes()
    res = {}
    for wname, (lo, hi) in (("confirm_2026-05-01..08-30", O2_CONFIRM), ("o1_window_2026-08-31..09-24", O1_WINDOW)):
        w = q[(q.ks >= OU.ep(lo)) & (q.ks < OU.ep(hi))]
        out, ps, picks = {}, {}, []
        best = w.sort_values("odds", ascending=False).drop_duplicates(["match_id", "market", "selection"])
        out["null_best_open"] = summarise(best)
        for arm, (books, thr) in O2_ARMS.items():
            s = w[(w.ev >= thr) & (w.ev <= O2_CAP)]
            if books == "direct":
                s = s[s.src == "direct"]
            s = s.sort_values("ev", ascending=False).drop_duplicates(["match_id", "market"])
            r = summarise(s)
            r["by_line"] = {mk: summarise(s[s.market == mk]) for mk in OU.LINES}
            r["by_side"] = {k: summarise(s[s.selection == k]) for k in ("over", "under")}
            r["by_book"] = {b: summarise(g) for b, g in s.groupby("bookmaker") if len(g) >= 20}
            r["by_hours"] = {str(k): summarise(g) for k, g in s.groupby(pd.cut(s.h_before, [0, 2, 6, 12, 24, 1e4]), observed=True)}
            out[arm] = r; ps[arm] = r["p"]
            picks.append(s.assign(arm=arm, window=wname))
        adj = OU.holm(ps)
        for arm in ps:
            out[arm]["holm"] = adj[arm]
            out[arm]["PASS"] = bool(out[arm]["clv"] > 0 and adj[arm] < 0.05)
        res[wname] = out
        pd.concat(picks).to_csv(OUT / f"backtest_ou_{tag}_{wname[:7]}_picks.csv", index=False)
        f = lambda r: (f"n={r['n']:5d} clv {r['clv']*100:+.2f}% [{r['clv_ci'][0]*100:+.2f},{r['clv_ci'][1]*100:+.2f}] "
                       f"(n_clv {r['n_clv']}) roi {r['roi']*100:+.1f}% [{r['roi_ci'][0]*100:+.1f},{r['roi_ci'][1]*100:+.1f}] "
                       f"hit {r['hit']:.3f} odds {r['median_odds']:.2f}")
        print(f"\n== O2 {wname}")
        for k, r in out.items():
            print(f"  {k:16s} {f(r)}" + (f"  Holm {r['holm']:.4f} {'PASS' if r['PASS'] else 'FAIL'}" if "holm" in r else ""))
            for sub in ("by_line", "by_side", "by_book", "by_hours"):
                for kk, rr in r.get(sub, {}).items():
                    print(f"      {sub[3:]}:{kk:16s} {f(rr)}")
    (OUT / f"backtest_ou_{tag}.json").write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__" and "--o2" in sys.argv:
    o2()


# ── ROUND O3 — filters on top of S2 (pre-registered) ──
def o3(tag: str = "o3") -> None:
    q = o2_quotes()
    q = q[(q.ev >= 0.05) & (q.ev <= O2_CAP)].copy()
    # Pinnacle open quote time per (match, market)
    legs = open_quotes()
    pt = legs[legs.bookmaker == "Pinnacle"].groupby(["match_id", "market"]).ts.max().rename("ts_pin")
    q = q.merge(pt, on=["match_id", "market"], how="left")
    # leave-one-out consensus of the OTHER books at open (power de-vig, mean logit)
    w = legs[legs.bookmaker.isin(OU.CONSENSUS_BOOKS)].pivot_table(
        index=["match_id", "market", "bookmaker"], columns="selection", values="odds", aggfunc="first").dropna().reset_index()
    ovr = 1 / w.over + 1 / w.under
    w = w[(ovr > 0.98) & (ovr < 1.30)].copy()
    w["l"] = OU.logit(OU.power_devig(w.over.to_numpy(), w.under.to_numpy()))
    g = w.groupby(["match_id", "market"]).l.agg(["sum", "count"]).reset_index()
    q = q.merge(g, on=["match_id", "market"], how="left").merge(
        w[["match_id", "market", "bookmaker", "l"]], on=["match_id", "market", "bookmaker"], how="left")
    own = q.l.fillna(0.0); n_oth = q["count"] - q.l.notna().astype(int)
    lo_ = (q["sum"] - own) / n_oth
    p_cons_over = 1 / (1 + np.exp(-lo_))
    q["ev_cons"] = np.where(n_oth >= 3, q.odds * np.where(q.selection == "over", p_cons_over, 1 - p_cons_over) - 1, np.nan)
    arms = {
        "S2": np.ones(len(q), bool),
        "T1": (q.ts - q.ts_pin).abs().to_numpy() <= 3 * 3600,
        "T2": (q.ev_cons >= 0.02).to_numpy(),
        "T3": (q.h_before >= 12).to_numpy(),
    }
    arms["T4"] = arms["T1"] & arms["T2"]
    res = {}
    for wname, (lo, hi) in (("primary_2026-08-01..08-30", ("2026-08-01", "2026-08-31")),
                            ("secondary_2026-08-31..09-24", O1_WINDOW)):
        inw = ((q.ks >= OU.ep(lo)) & (q.ks < OU.ep(hi))).to_numpy()
        base = q[inw].sort_values("ev", ascending=False).drop_duplicates(["match_id", "market"])
        out, ps = {"S2": summarise(base)}, {}
        for arm in ("T1", "T2", "T3", "T4"):
            s = q[inw & arms[arm]].sort_values("ev", ascending=False).drop_duplicates(["match_id", "market"])
            r = summarise(s)
            r["by_src"] = {k: summarise(s[s.src == k]) for k in ("AF", "direct")}
            # one-sided test: filtered mean CLV > base mean CLV (bootstrap of the difference)
            rng = np.random.default_rng(5); a, b = s.clv.dropna().to_numpy(), base.clv.dropna().to_numpy()
            d = np.array([a[rng.integers(0, len(a), len(a))].mean() - b[rng.integers(0, len(b), len(b))].mean()
                          for _ in range(5000)]) if len(a) > 5 else np.array([np.nan])
            r["delta_vs_S2"] = float(a.mean() - b.mean()) if len(a) else float("nan")
            ps[arm] = float((d <= 0).mean()); r["p_vs_S2"] = ps[arm]
            out[arm] = r
        adj = OU.holm(ps)
        for arm in ps:
            out[arm]["holm"] = adj[arm]; out[arm]["PASS"] = bool(out[arm]["delta_vs_S2"] > 0 and adj[arm] < 0.05)
        res[wname] = out
        print(f"\n== O3 {wname}")
        for k, r in out.items():
            print(f"  {k}: n={r['n']:5d} clv {r['clv']*100:+.2f}% [{r['clv_ci'][0]*100:+.2f},{r['clv_ci'][1]*100:+.2f}] "
                  f"roi {r['roi']*100:+.1f}% [{r['roi_ci'][0]*100:+.1f},{r['roi_ci'][1]*100:+.1f}]"
                  + (f"  Δ vs S2 {r['delta_vs_S2']*100:+.2f}pp Holm {r['holm']:.3f} {'PASS' if r['PASS'] else 'FAIL'}" if "holm" in r else "")
                  + ("".join(f"  | {kk} n={rr['n']} clv {rr['clv']*100:+.2f}%" for kk, rr in r.get("by_src", {}).items())))
    (OUT / f"backtest_ou_{tag}.json").write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__" and "--o3" in sys.argv:
    o3()
