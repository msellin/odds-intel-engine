"""#140 BOT-SLICE-ANALYSIS — can a pre-defined slice turn a losing bot into a positive-CLV one?

READ-ONLY. Implements the pre-registration in dev/active/bot-slice-analysis-prereg.md exactly
(bots, designated metric per family, discovery = older half / holdout = newer half of each bot's
metric-bearing picks, match-clustered one-sided t, ONE Holm family over every test, survival rule).

    python3 scripts/analysis/bot_slice_analysis.py            # prints tables, writes JSON
    python3 scripts/analysis/bot_slice_analysis.py --json out.json

Nothing here writes to the DB. See the prereg for why each metric was chosen (§5) — in short:
sharp bots are judged on fresh own-book margin-corrected CLV (Pinnacle CLV is their entry rule),
model bots on that AND fresh de-vigged Pinnacle CLV at the executable price, the sim model bot on
Pinnacle-devig CLV re-priced at the executable price, the in-play bot on hit rate minus the book's
de-vigged probability.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from workers.api_clients.db import execute_query  # noqa: E402

CAPABLE = [
    "bot_coolbet_1x2_model_v1", "bot_coolbet_ou_model_v1", "bot_coolbet_trigger_sharp_1x2_v1",
    "bot_coolbet_trigger_sharp_ou_v1", "bot_ou35_model_v1", "bot_trigger_1x2_sharp_tight_v1",
    "bot_trigger_1x2_sharp_v1", "bot_trigger_ou_sharp_v1", "bot_unibet_trigger_sharp_1x2_v1",
    "bot_unibet_trigger_sharp_ou_v1", "bot_unified_gate_1x2_paper_v1",
]
EXTRA_SHADOW = ["bot_inplay_slowstate_v1"]
EXTRA_SIM = ["bot_v10_1x2"]

MODEL_SHADOW = {"bot_ou35_model_v1", "bot_coolbet_1x2_model_v1", "bot_coolbet_ou_model_v1",
                "bot_unified_gate_1x2_paper_v1"}
INPLAY = {"bot_inplay_slowstate_v1"}

MIN_TOTAL = 60        # designated-metric picks needed before a bot is sliced at all
MIN_DISC = 30         # matches in a discovery cell
MIN_HOLD = 20         # matches in a holdout cell
ALPHA = 0.05
HOLD_MIN_ABS = 0.01   # +1.0 pt
HOLD_MIN_FRAC = 0.5   # >= 50% of the discovery mean

EUROPE = {
    "England", "Scotland", "Wales", "Northern-Ireland", "Ireland", "France", "Germany", "Italy",
    "Spain", "Portugal", "Netherlands", "Belgium", "Luxembourg", "Switzerland", "Austria",
    "Denmark", "Norway", "Sweden", "Finland", "Iceland", "Faroe-Islands", "Estonia", "Latvia",
    "Lithuania", "Poland", "Czech-Republic", "Slovakia", "Hungary", "Romania", "Bulgaria",
    "Greece", "Cyprus", "Malta", "Turkey", "Israel", "Ukraine", "Belarus", "Moldova", "Russia",
    "Serbia", "Croatia", "Slovenia", "Bosnia", "Montenegro", "Macedonia", "Albania", "Kosovo",
    "Georgia", "Armenia", "Azerbaijan", "Kazakhstan", "Andorra", "San-Marino", "Gibraltar",
    "Liechtenstein",
}

# ── data ─────────────────────────────────────────────────────────────────────────────────────

_EXCL = """
  EXISTS (SELECT 1 FROM odds_snapshots_quarantined q
           WHERE q.match_id = {a}.match_id AND q.bookmaker = {a}.recommended_bookmaker)
  OR EXISTS (SELECT 1 FROM data_quality_findings f
           WHERE f.match_id = {a}.match_id AND f.bookmaker = {a}.recommended_bookmaker)
"""

SHADOW_SQL = f"""
SELECT u.id, u.bot_name, u.match_id, m.date AS kickoff, u.pick_time, u.market, u.selection,
       u.odds_at_pick, u.odds_at_pick_live, u.recommended_bookmaker AS book, u.result,
       u.calibrated_prob, u.clv_margin_corrected, u.closing_fresh, u.clv_pinnacle,
       u.decision_quote_age_min, u.inplay_minute, u.inplay_score_home, u.inplay_score_away,
       c.clv_sharp, c.clv_cons, l.priority AS league_priority, l.country, l.name AS league,
       ({_EXCL.format(a='u')}) AS data_fault
  FROM shadow_bets_unique u
  JOIN matches m ON m.id = u.match_id
  LEFT JOIN leagues l ON l.id = m.league_id
  LEFT JOIN leg_clv_sharp c ON c.ledger = 'shadow_bets' AND c.leg_id = u.id
 WHERE u.bot_name = ANY(%s) AND u.result IN ('won','lost')
"""

SIM_SQL = f"""
SELECT s.id, b.name AS bot_name, s.match_id, m.date AS kickoff, s.pick_time, s.market,
       s.selection, s.odds_at_pick, s.odds_at_pick_live, s.recommended_bookmaker AS book,
       s.result::text AS result, s.calibrated_prob, s.clv_pinnacle_devig,
       c.clv_sharp, c.clv_cons, l.priority AS league_priority, l.country, l.name AS league,
       ({_EXCL.format(a='s')}) AS data_fault
  FROM simulated_bets s
  JOIN bots b ON b.id = s.bot_id
  JOIN matches m ON m.id = s.match_id
  LEFT JOIN leagues l ON l.id = m.league_id
  LEFT JOIN leg_clv_sharp c ON c.ledger = 'simulated_bets' AND c.leg_id = s.id
 WHERE b.name = ANY(%s) AND s.result IN ('won','lost') AND s.combo_legs IS NULL
"""


def load() -> pd.DataFrame:
    sh = pd.DataFrame(execute_query(SHADOW_SQL, [CAPABLE + EXTRA_SHADOW]))
    sh["ledger"] = "shadow"
    sm = pd.DataFrame(execute_query(SIM_SQL, [EXTRA_SIM]))
    sm["ledger"] = "sim"
    df = pd.concat([sh, sm], ignore_index=True)
    num = ["odds_at_pick", "odds_at_pick_live", "calibrated_prob", "clv_margin_corrected",
           "clv_pinnacle", "clv_sharp", "clv_cons", "clv_pinnacle_devig",
           "decision_quote_age_min", "inplay_minute", "inplay_score_home", "inplay_score_away"]
    for c in num:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["pick_time"] = pd.to_datetime(df["pick_time"], utc=True)
    df["kickoff"] = pd.to_datetime(df["kickoff"], utc=True)
    df["odds_exec"] = df["odds_at_pick_live"].fillna(df["odds_at_pick"])
    df["won"] = (df["result"] == "won").astype(float)
    df["roi_unit"] = np.where(df["won"] == 1, df["odds_exec"] - 1.0, -1.0)
    df["ev_exec"] = df["calibrated_prob"] * df["odds_exec"] - 1.0
    # designated metrics
    df["m_clv_mc_fresh"] = np.where(df.get("closing_fresh", False) == True,  # noqa: E712
                                    df["clv_margin_corrected"], np.nan)
    df["m_clv_sharp"] = df["clv_sharp"]
    p_close = (1.0 + df["clv_pinnacle_devig"]) / df["odds_at_pick"]
    df["m_clv_pin_live"] = df["odds_exec"] * p_close - 1.0
    df["m_hit_minus_p"] = df["won"] - df["calibrated_prob"]
    return df


def designated(bot: str) -> list[str]:
    if bot in INPLAY:
        return ["m_hit_minus_p"]
    if bot in EXTRA_SIM:
        return ["m_clv_pin_live"]
    if bot in MODEL_SHADOW:
        return ["m_clv_mc_fresh", "m_clv_sharp"]
    return ["m_clv_mc_fresh"]


# ── slice families ───────────────────────────────────────────────────────────────────────────

def region(country) -> str:
    if country == "World":
        return "international"
    return "europe" if country in EUROPE else "rest_of_world"


def odds_band(o) -> str:
    if o < 1.80:
        return "<1.80"
    if o < 2.50:
        return "1.80-2.49"
    if o < 3.50:
        return "2.50-3.49"
    return ">=3.50"


def side(market, sel) -> str:
    s = str(sel).lower()
    for k in ("home", "draw", "away", "over", "under"):
        if s.startswith(k):
            return k
    return s


def families(bot: str, d: pd.DataFrame, disc_mask: pd.Series) -> dict[str, pd.Series]:
    """Return {family: cell-label Series} for this bot (cells fixed by the prereg)."""
    out: dict[str, pd.Series] = {}
    out["F1_featured_league"] = d["league_priority"].notna().map({True: "featured", False: "other"})
    out["F2_region"] = d["country"].map(region)
    out["F3_odds_band"] = d["odds_exec"].map(odds_band)
    out["F4_side"] = [side(m, s) for m, s in zip(d["market"], d["selection"])]
    out["F4_side"] = pd.Series(out["F4_side"], index=d.index)
    out["F5_market"] = d["market"].astype(str)
    out["F6_book"] = d["book"].astype(str)
    if bot in INPLAY:
        mn = d["inplay_minute"]
        out["F7p_inplay_minute"] = pd.Series(np.where(mn < 30, "<30", np.where(mn < 60, "30-59", ">=60")),
                                             index=d.index).where(mn.notna())
        g = d["inplay_score_home"] + d["inplay_score_away"]
        out["F9p_goals_on_board"] = pd.Series(np.where(g <= 0, "0", np.where(g == 1, "1", ">=2")),
                                              index=d.index).where(g.notna())
    else:
        h = (d["kickoff"] - d["pick_time"]).dt.total_seconds() / 3600.0
        out["F7_time_to_ko"] = pd.Series(np.where(h < 3, "<3h", np.where(h < 12, "3-12h", ">=12h")),
                                         index=d.index)
        qa = d["decision_quote_age_min"]
        if qa.notna().mean() >= 0.5:
            out["F9_quote_age"] = pd.Series(np.where(qa <= 10, "<=10m", np.where(qa <= 60, "10-60m", ">60m")),
                                            index=d.index).where(qa.notna())
    ev = d["ev_exec"]
    q = ev[disc_mask].dropna().quantile([1 / 3, 2 / 3]).values
    if len(q) == 2 and not np.isnan(q).any():
        lab = np.where(ev <= q[0], f"low(<={q[0]:+.3f})",
                       np.where(ev <= q[1], f"mid", f"high(>{q[1]:+.3f})"))
        out["F8_edge_band"] = pd.Series(lab, index=d.index).where(ev.notna())
    # drop single-cell families
    return {k: v for k, v in out.items() if v.dropna().nunique() >= 2}


# ── statistics ───────────────────────────────────────────────────────────────────────────────

def cluster_stats(sub: pd.DataFrame, metric: str) -> dict:
    g = sub.groupby("match_id")[metric].mean().dropna()
    n = len(g)
    if n == 0:
        return {"n": 0, "mean": None, "se": None, "t": None, "p": None}
    mean = float(g.mean())
    if n < 2 or g.std(ddof=1) == 0:
        return {"n": n, "mean": mean, "se": None, "t": None, "p": None}
    se = float(g.std(ddof=1) / math.sqrt(n))
    t = mean / se
    p = float(stats.t.sf(t, df=n - 1))  # one-sided, H1: mean > 0
    return {"n": n, "mean": mean, "se": se, "t": t, "p": p}


def roi(sub: pd.DataFrame) -> float | None:
    return float(sub["roi_unit"].mean()) if len(sub) else None


def holm(pvals: list[float]) -> list[float]:
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = running
    return adj


# ── main ─────────────────────────────────────────────────────────────────────────────────────

def run() -> dict:
    df = load()
    report = {"exclusions": {}, "bots": {}, "tests": []}
    for bot in CAPABLE + EXTRA_SHADOW + EXTRA_SIM:
        b = df[df["bot_name"] == bot].copy()
        n_all = len(b)
        n_fault = int(b["data_fault"].sum()) if n_all else 0
        b = b[~b["data_fault"].astype(bool)]
        binfo = {"settled": n_all, "data_fault_excluded": n_fault, "roi_exec_all": roi(b),
                 "metrics": {}}
        for metric in designated(bot):
            d = b[b[metric].notna()].copy()
            n_out = int((d[metric].abs() > 1).sum())
            d = d[d[metric].abs() <= 1]
            minfo = {"n": len(d), "outliers_dropped": n_out}
            if len(d) < MIN_TOTAL:
                minfo["status"] = f"too few picks ({len(d)} < {MIN_TOTAL})"
                binfo["metrics"][metric] = minfo
                continue
            med = d["pick_time"].median()
            disc = d["pick_time"] <= med
            minfo.update(status="sliced", split_at=str(med),
                         overall=cluster_stats(d, metric),
                         discovery=cluster_stats(d[disc], metric),
                         holdout=cluster_stats(d[~disc], metric),
                         roi_disc=roi(d[disc]), roi_hold=roi(d[~disc]))
            fams = families(bot, d, disc)
            minfo["families"] = list(fams)
            bot_disc_mean = minfo["discovery"]["mean"]
            for fam, lab in fams.items():
                for cell in sorted(lab.dropna().unique()):
                    in_cell = lab == cell
                    ds = d[disc & in_cell]
                    hs = d[~disc & in_cell]
                    s_d = cluster_stats(ds, metric)
                    s_h = cluster_stats(hs, metric)
                    rec = {"bot": bot, "metric": metric, "family": fam, "cell": str(cell),
                           "disc": s_d, "hold": s_h, "roi_disc": roi(ds), "roi_hold": roi(hs),
                           "picks_disc": len(ds), "picks_hold": len(hs),
                           "disc_vs_bot_mean": (s_d["mean"] - bot_disc_mean)
                           if s_d["mean"] is not None else None,
                           "tested": s_d["n"] >= MIN_DISC and s_h["n"] >= MIN_HOLD
                           and s_d["p"] is not None}
                    report["tests"].append(rec)
            binfo["metrics"][metric] = minfo
        report["bots"][bot] = binfo

    tested = [r for r in report["tests"] if r["tested"]]
    adj = holm([r["disc"]["p"] for r in tested])
    for r, a in zip(tested, adj):
        r["p_holm"] = a
    for r in report["tests"]:
        pass_holm = r.get("p_holm") is not None and r["p_holm"] < ALPHA
        h, dm = r["hold"]["mean"], r["disc"]["mean"]
        hold_ok = (h is not None and dm is not None and h > 0 and h >= HOLD_MIN_ABS
                   and h >= HOLD_MIN_FRAC * dm)
        r["pass_holm"] = pass_holm
        r["hold_ok"] = bool(hold_ok)
        r["survives"] = bool(pass_holm and hold_ok)
        r["near_miss"] = bool(r["tested"] and r["disc"]["p"] is not None and r["disc"]["p"] < 0.05
                              and not r["survives"])
    report["n_tests"] = len(tested)
    report["n_survivors"] = sum(r["survives"] for r in report["tests"])
    return report


def fmt(x, pct=True, nd=1):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x * 100:+.{nd}f}" if pct else f"{x:.{nd}f}"


def print_report(rep: dict) -> None:
    print(f"\nTESTS in the Holm family: {rep['n_tests']}   SURVIVORS: {rep['n_survivors']}\n")
    print("== per bot ==")
    for bot, bi in rep["bots"].items():
        print(f"\n{bot}  settled={bi['settled']}  data-fault excluded={bi['data_fault_excluded']}"
              f"  ROI(exec, all)={fmt(bi['roi_exec_all'])}%")
        for m, mi in bi["metrics"].items():
            if mi.get("status") != "sliced":
                print(f"   {m}: {mi.get('status')}  (outliers dropped {mi.get('outliers_dropped')})")
                continue
            o, dsc, ho = mi["overall"], mi["discovery"], mi["holdout"]
            print(f"   {m}: n={mi['n']} matches={o['n']} mean={fmt(o['mean'])} t={fmt(o['t'], False, 2)}"
                  f" | disc {dsc['n']}m {fmt(dsc['mean'])} | hold {ho['n']}m {fmt(ho['mean'])}"
                  f" | ROI disc {fmt(mi['roi_disc'])} hold {fmt(mi['roi_hold'])} | split {mi['split_at'][:16]}"
                  f" | outliers {mi['outliers_dropped']}")
    print("\n== best discovery cells (tested), sorted by raw p ==")
    tested = sorted([r for r in rep["tests"] if r["tested"]], key=lambda r: r["disc"]["p"])
    hdr = f"{'bot':34} {'metric':15} {'family':20} {'cell':18} {'d_n':>4} {'d_mean':>7} {'d_t':>6} {'p':>7} {'p_holm':>7} {'h_n':>4} {'h_mean':>7} {'ROId':>7} {'ROIh':>7} flag"
    print(hdr)
    for r in tested[:40]:
        flag = "SURVIVES" if r["survives"] else ("near-miss" if r["near_miss"] else "")
        print(f"{r['bot'][:34]:34} {r['metric'][2:17]:15} {r['family'][:20]:20} {r['cell'][:18]:18} "
              f"{r['disc']['n']:>4} {fmt(r['disc']['mean']):>7} {fmt(r['disc']['t'], False, 2):>6} "
              f"{r['disc']['p']:>7.4f} {r['p_holm']:>7.3f} {r['hold']['n']:>4} {fmt(r['hold']['mean']):>7} "
              f"{fmt(r['roi_disc']):>7} {fmt(r['roi_hold']):>7} {flag}")
    pos = [r for r in tested if r["disc"]["mean"] > 0]
    print(f"\ntested cells with a positive discovery mean: {len(pos)} of {len(tested)}; "
          f"of those positive on holdout too: {sum(1 for r in pos if (r['hold']['mean'] or 0) > 0)}")
    untested = [r for r in rep["tests"] if not r["tested"]]
    print(f"cells below the size minimum (listed in JSON, not tested): {len(untested)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None, help="write the full report as JSON")
    a = ap.parse_args()
    rep = run()
    print_report(rep)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(rep, f, indent=1, default=str)
        print(f"\nJSON -> {a.json}")


if __name__ == "__main__":
    main()
