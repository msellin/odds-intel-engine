#!/usr/bin/env python3
"""#152 LANES — an odds split between the two NEW+ public twins.

Pre-registration (FIXED, written before the run): dev/active/model-bots-new-models-plan.md,
"Pre-registration — LANES". Implemented exactly.

Both twins: NEW+ (r1x2_comb_v1, walk-forward OPEN, as B2/B3), Pinnacle price required, all
books, OPEN quotes bounded timestamp < kickoff, one pick per match per bot (best EV), and
vip_exclude — never a candidate the VIP bot holds (B2 arm N2: NEW+ EV >= 5%, odds 1.30-6.00,
Pinnacle required, same match+selection). Every other gate = B2's (run_b2_arm is reused).
  * Match-result twin: EV >= 3%, all selections, odds 1.30 - s (s exclusive)
  * High-odds twin:    EV >= 2%, home/away only, odds s - 6.00
  * s in {2.00, 2.30, 2.50, 2.80, 3.00}; no-split reference: Match 1.30-4.50, High 1.60-6.00
Select s on kickoffs 08-31..09-12 = argmax min(CLV_match, CLV_high) among s where both twins
have >= 30 CLV picks; confirm ONCE on 09-13..09-24: each twin's mean CLV, one-sided bootstrap
(10k), Holm m = 2, PASS = both adjusted p < 0.05. CLV = odds x Pinnacle's POWER-de-vigged last
pre-kickoff probability, real closes only (a close that is Pinnacle's opening row is dropped).
Same window caveat as B2/B3: seen before, so the forward record decides.

Read-only. Output (gitignored): data/models/_research/1x2/backtest/lanes_*.

    python3 scripts/backtest_1x2_lanes.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import backtest_1x2_new_bots as BT  # noqa: E402
from backtest_model_bots_new_models import _pin_close_1x2  # noqa: E402  (power de-vig, real-close guard)

S_GRID = (2.00, 2.30, 2.50, 2.80, 3.00)
MATCH_EV, HIGH_EV = 0.03, 0.02
ODDS_LO, ODDS_HI = 1.30, 6.00
MATCH_SELS = ("home", "draw", "away")
HIGH_SELS = ("home", "away")
REF_MATCH_ODDS, REF_HIGH_ODDS = (1.30, 4.50), (1.60, 6.00)
SPLIT_EPOCH = 1789257600.0            # 2026-09-13T00:00Z: select 08-31..09-12, confirm 09-13..09-24
DAYS = {"select": 13, "confirm": 12}
MIN_CLV_PICKS = 30
HOLM_M = 2
ALPHA = 0.05
N_BOOT = 10000
SEED = 20260925
OUT_DIR = BT.OUT_DIR


def _arm(L, ev, odds, sels, vip, max_excl):
    cfg = {"ev_min": ev, "odds_range": odds, "max_exclusive": max_excl}
    picks, _ = BT.run_b2_arm("custom", L, cfg=cfg, sels=sels, exclude=vip)
    return pd.DataFrame(picks)


def _clv(df: pd.DataFrame, pc: dict) -> pd.DataFrame:
    if df.empty:
        return df.assign(clv=pd.Series(dtype=float), half=pd.Series(dtype=str))
    idx = {"home": 0, "draw": 1, "away": 2}
    df = df.copy()
    df["clv"] = [o * pc[m][idx[s]] - 1 if m in pc else np.nan for m, s, o in zip(df.match_id, df.selection, df.odds)]
    ko = pd.to_datetime(df.kickoff_utc, utc=True).map(lambda t: t.timestamp())
    df["half"] = np.where(ko < SPLIT_EPOCH, "select", "confirm")
    return df


def _boot(x: np.ndarray):
    if len(x) < 2:
        return None, None, None, None
    rng = np.random.default_rng(SEED)
    bs = x[rng.integers(0, len(x), (N_BOOT, len(x)))].mean(1)
    return float(x.mean()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5)), float((bs <= 0).mean())


def _summ(df: pd.DataFrame, half: str) -> dict:
    s = df[df.half == half] if len(df) else df
    c = s.clv.dropna().to_numpy(float) if len(s) else np.array([])
    m, lo, hi, p = _boot(c)
    r = _boot(s.pnl_flat.to_numpy(float)) if len(s) else (None, None, None, None)
    return {"n": int(len(s)), "picks_per_day": round(len(s) / DAYS[half], 2), "n_clv": int(len(c)),
            "clv": m, "clv_ci95": None if lo is None else [lo, hi], "p_one_sided": p,
            "roi": r[0], "roi_ci95": None if r[1] is None else [r[1], r[2]],
            "hit": float((s.result == "win").mean()) if len(s) else None}


def _overlap(a: pd.DataFrame, b: pd.DataFrame, half: str) -> dict:
    if a.empty or b.empty:
        return {"same_match_selection": 0, "same_match": 0, "shared_clv": None}
    a, b = a[a.half == half], b[b.half == half]
    ka, kb = set(zip(a.match_id, a.selection)), set(zip(b.match_id, b.selection))
    shared = a[[k in kb for k in zip(a.match_id, a.selection)]]
    c = shared.clv.dropna()
    return {"same_match_selection": len(ka & kb), "same_match": len(set(a.match_id) & set(b.match_id)),
            "shared_clv": float(c.mean()) if len(c) else None, "shared_n_clv": int(len(c))}


def main() -> int:
    L = BT.load_everything()
    pc, circ = _pin_close_1x2(list(L["matches"]))
    vip_df, _ = BT.run_b2_arm("N2", L)
    vip = {(p["match_id"], p["selection"]) for p in vip_df}
    print(f"  VIP (B2 N2) holdings: {len(vip):,}; Pinnacle 1X2 closes usable {len(pc):,} (circular dropped {circ})")
    grid = {}
    for s in S_GRID:
        mt = _clv(_arm(L, MATCH_EV, (ODDS_LO, s), MATCH_SELS, vip, True), pc)
        hi = _clv(_arm(L, HIGH_EV, (s, ODDS_HI), HIGH_SELS, vip, False), pc)
        grid[s] = (mt, hi)
    ref = (_clv(_arm(L, MATCH_EV, REF_MATCH_ODDS, MATCH_SELS, vip, False), pc),
           _clv(_arm(L, HIGH_EV, REF_HIGH_ODDS, HIGH_SELS, vip, False), pc))
    rows, out = [], {"grid": {}}
    for s, (mt, hi) in list(grid.items()) + [("ref", ref)]:
        rec = {}
        for half in ("select", "confirm"):
            rec[half] = {"match": _summ(mt, half), "high": _summ(hi, half), "twin_overlap": _overlap(mt, hi, half),
                         "vip_overlap": int(sum((m, x) in vip for m, x in zip(mt.match_id, mt.selection))
                                            + sum((m, x) in vip for m, x in zip(hi.match_id, hi.selection)))
                         if len(mt) or len(hi) else 0}
            for tw in ("match", "high"):
                r = rec[half][tw]
                rows.append({"s": s, "half": half, "twin": tw, **{k: r[k] for k in ("n", "picks_per_day", "n_clv",
                             "clv", "roi", "hit", "p_one_sided")},
                             "clv_lo": (r["clv_ci95"] or [None, None])[0], "clv_hi": (r["clv_ci95"] or [None, None])[1],
                             "roi_lo": (r["roi_ci95"] or [None, None])[0], "roi_hi": (r["roi_ci95"] or [None, None])[1],
                             "overlap_same_match_sel": rec[half]["twin_overlap"]["same_match_selection"],
                             "overlap_same_match": rec[half]["twin_overlap"]["same_match"],
                             "vip_overlap": rec[half]["vip_overlap"]})
        out["grid"][str(s)] = rec
    # selection
    elig = {s: min(out["grid"][str(s)]["select"]["match"]["clv"], out["grid"][str(s)]["select"]["high"]["clv"])
            for s in S_GRID
            if out["grid"][str(s)]["select"]["match"]["n_clv"] >= MIN_CLV_PICKS
            and out["grid"][str(s)]["select"]["high"]["n_clv"] >= MIN_CLV_PICKS}
    if not elig:
        verdict = {"selected_s": None, "verdict": "FAIL", "reason": f"no s with both twins >= {MIN_CLV_PICKS} CLV picks"}
    else:
        s_star = max(elig, key=elig.get)
        conf = out["grid"][str(s_star)]["confirm"]
        ps = {"match": conf["match"]["p_one_sided"], "high": conf["high"]["p_one_sided"]}
        order = sorted(ps, key=lambda k: 1.0 if ps[k] is None else ps[k])
        run, adj = 0.0, {}
        for i, k in enumerate(order):
            run = max(run, min(1.0, (HOLM_M - i) * (1.0 if ps[k] is None else ps[k]))); adj[k] = run
        ok = all(adj[k] < ALPHA and (conf[k]["clv"] or 0) > 0 for k in adj)
        verdict = {"selected_s": s_star, "select_min_clv": elig[s_star], "p_raw": ps, "p_holm": adj,
                   "verdict": "PASS" if ok else "FAIL"}
    out.update({"generated_at": datetime.now(timezone.utc).isoformat(),
                "label": "Backtest (simulated) — #152 LANES; seen window, the forward record decides",
                "preregistration": "dev/active/model-bots-new-models-plan.md — 'Pre-registration — LANES'",
                "design": {"s_grid": S_GRID, "match": {"ev": MATCH_EV, "sels": MATCH_SELS},
                           "high": {"ev": HIGH_EV, "sels": HIGH_SELS}, "min_clv_picks": MIN_CLV_PICKS,
                           "holm_m": HOLM_M, "n_boot": N_BOOT, "seed": SEED, "split": "2026-09-13"},
                "verdict": verdict})
    pd.DataFrame(rows).to_csv(OUT_DIR / "lanes_table.csv", index=False)
    (OUT_DIR / "lanes_summary.json").write_text(json.dumps(out, indent=1, default=float))
    pd.set_option("display.width", 250)
    print(pd.DataFrame(rows).to_string(index=False))
    for s in list(S_GRID) + ["ref"]:
        for half in ("select", "confirm"):
            print(s, half, "twin overlap", out["grid"][str(s)][half]["twin_overlap"])
    print(json.dumps(verdict, default=float))
    BT.CONN.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
