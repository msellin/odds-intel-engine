#!/usr/bin/env python3
"""
PER-BOT-SWEEP-2026-08-24 — point-in-time replay sweep for the 8 shadow bots.

Early execution of PER-BOT-SWEEP-N500-2026-10-01, pulled forward after the
2026-08-22→24 real-money loss.

Unlike CONFIG-SWEEP-2026-08-19 this harness:
  * replays odds strictly as of `kickoff - 3h` (no look-ahead best-of-day)
  * covers the LINE-SHOP mechanism (Pinnacle anchor, no model) that the
    original sweep never tested — 5 of 8 deployed bots use it
  * reports raw AND de-vigged edge side by side

Inputs (built by the SQL in dev/active/per-bot-sweep-2026-08-24-context.md):
  /tmp/sel.csv    per (match, market, selection) point-in-time prices + outcome
  /tmp/preds.csv  latest pre-kickoff ensemble probability per (match, market)

Usage:
    python3 scripts/per_bot_backtest_sweep.py
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict

SEL_CSV = "/tmp/sel.csv"
PRED_CSV = "/tmp/preds.csv"

ACCESSIBLE_SOFT = 1  # n_soft must be >= this for a line-shop pick

PRED_MARKET_MAP = {
    "1x2_home": ("1x2", "home"),
    "1x2_draw": ("1x2", "draw"),
    "1x2_away": ("1x2", "away"),
    "over25": ("over_under_25", "over"),
    "under25": ("over_under_25", "under"),
    "over35": ("over_under_35", "over"),
    "under35": ("over_under_35", "under"),
    "btts_yes": ("btts", "yes"),
    "btts_no": ("btts", "no"),
}

# Walk-forward windows. W4 is the live period the bots actually ran in.
WINDOWS = [
    ("W1", "2026-05-01", "2026-06-15"),
    ("W2", "2026-06-16", "2026-07-31"),
    ("W3", "2026-08-01", "2026-08-21"),
]


def load():
    rows = []
    for r in csv.DictReader(open(SEL_CSV)):
        if r["won"] == "":
            continue
        try:
            r["tier"] = int(r["tier"])
            r["won"] = int(r["won"])
            r["pin"] = float(r["pin"]) if r["pin"] else None
            r["best_soft"] = float(r["best_soft"]) if r["best_soft"] else None
            r["best_all"] = float(r["best_all"]) if r["best_all"] else None
            r["n_soft"] = int(r["n_soft"])
            r["median_odds"] = float(r["median_odds"]) if r["median_odds"] else None
            r["overround"] = float(r["overround"]) if r["overround"] else None
            r["pin_sels"] = int(r["pin_sels"]) if r["pin_sels"] else 0
        except ValueError:
            continue
        rows.append(r)
    preds = {}
    for r in csv.DictReader(open(PRED_CSV)):
        key = PRED_MARKET_MAP.get(r["market"])
        if key:
            preds[(r["match_id"], key[0], key[1])] = float(r["prob"])
    for r in rows:
        r["prob"] = preds.get((r["match_id"], r["market"], r["selection"]))
    return rows


def roi_stats(picks):
    """Flat-stake ROI + t-stat on per-bet return."""
    n = len(picks)
    if n == 0:
        return 0, 0.0, 0.0
    rets = [(o - 1.0) if w else -1.0 for o, w in picks]
    m = sum(rets) / n
    if n > 1:
        var = sum((x - m) ** 2 for x in rets) / (n - 1)
        se = math.sqrt(var / n)
    else:
        se = float("inf")
    return n, 100 * m, (m / se if se else 0.0)


def lineshop_picks(rows, market, selection, tiers, edge_min, devig, odds_min, odds_max,
                   outlier_mult, start, end, max_vig=None):
    out = []
    for r in rows:
        if r["market"] != market or r["selection"] != selection:
            continue
        if r["tier"] not in tiers or not (start <= r["d"] < end):
            continue
        pin, best = r["pin"], r["best_soft"]
        if pin is None or best is None or r["n_soft"] < ACCESSIBLE_SOFT:
            continue
        if not (odds_min <= best <= odds_max):
            continue
        if best > pin * outlier_mult:
            continue
        orr = r["overround"]
        need_sels = 2 if market != "1x2" else 3
        if orr is None or r["pin_sels"] < need_sels:
            continue
        if max_vig is not None and orr > max_vig:
            continue
        prob = (1.0 / pin) / orr if devig else (1.0 / pin)
        if best * prob - 1.0 < edge_min:
            continue
        out.append((best, r["won"]))
    return out


def model_picks(rows, market, selection, tiers, edge_min, odds_min, odds_max,
                min_prob, require_pin, no_pin, start, end, outlier_mult=None):
    out = []
    for r in rows:
        if r["market"] != market or r["selection"] != selection:
            continue
        if r["tier"] not in tiers or not (start <= r["d"] < end):
            continue
        p = r["prob"]
        if p is None or p < min_prob:
            continue
        if require_pin and r["pin"] is None:
            continue
        if no_pin:
            if r["pin"] is not None or r["n_soft"] < 3:
                continue
            best = r["best_soft"]
        else:
            best = r["best_all"]
        if best is None or not (odds_min <= best <= odds_max):
            continue
        if outlier_mult and r["median_odds"] and best > r["median_odds"] * outlier_mult:
            continue
        if best * p - 1.0 < edge_min:
            continue
        out.append((best, r["won"]))
    return out


# --- Live (as-deployed) config for each of the 8 bots --------------------
# Sourced from workers/jobs/daily_pipeline_v2.py :4122-4157, :4343-4360,
# :4557-4576, :3959-4048.

BOTS = {
    "bot_sweep_1x2_home_v1":     dict(kind="model", market="1x2", sel="home",
                                      tiers=(2, 3), edge=0.10, omin=2.00, omax=5.00,
                                      minp=0.25, reqpin=True),
    "bot_sweep_1x2_draw_v1":     dict(kind="model", market="1x2", sel="draw",
                                      tiers=(2, 3), edge=0.05, omin=1.30, omax=3.50,
                                      minp=0.25, reqpin=True),
    "bot_sweep_btts_yes_v1":     dict(kind="model", market="btts", sel="yes",
                                      tiers=(2, 3), edge=0.05, omin=2.00, omax=2.50,
                                      minp=0.25, reqpin=False),
    "bot_no_pin_home_v1":        dict(kind="model", market="1x2", sel="home",
                                      tiers=(1, 2, 3, 4), edge=0.08, omin=1.30, omax=6.00,
                                      minp=0.25, reqpin=False, nopin=True, outlier=1.35),
    "bot_sweep_ou25_v1":         dict(kind="lineshop", market="over_under_25", sel="both",
                                      tiers=(1, 2, 3, 4), edge=0.08, omin=1.30, omax=5.00,
                                      outlier=1.30, maxvig=1.10),
    "bot_sweep_ou35_v1":         dict(kind="lineshop", market="over_under_35", sel="both",
                                      tiers=(1, 2, 3, 4), edge=0.08, omin=1.30, omax=5.00,
                                      outlier=1.30, maxvig=1.10),
    "bot_pin_1x2_home_v1":       dict(kind="lineshop", market="1x2", sel="home",
                                      tiers=(1, 2), edge=0.12, omin=1.30, omax=6.00,
                                      outlier=1.35),
    "bot_pin_1x2_draw_tier4_v1": dict(kind="lineshop", market="1x2", sel="draw",
                                      tiers=(4,), edge=0.05, omin=1.30, omax=6.00,
                                      outlier=1.35),
}

# Live results measured from shadow_bets (deduped, 2026-08-19→24).
LIVE = {
    "bot_sweep_1x2_home_v1": (78, 11.0), "bot_sweep_1x2_draw_v1": (41, -0.3),
    "bot_sweep_btts_yes_v1": (30, 0.6), "bot_no_pin_home_v1": (66, -10.6),
    "bot_sweep_ou25_v1": (99, -1.9), "bot_sweep_ou35_v1": (72, 0.6),
    "bot_pin_1x2_home_v1": (62, 16.2), "bot_pin_1x2_draw_tier4_v1": (27, -40.8),
}

EDGE_GRID = [0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20]
TIER_GRID = [(1,), (2,), (3,), (4,), (1, 2), (2, 3), (1, 2, 3), (1, 2, 3, 4)]


def run_bot(rows, cfg, edge, tiers, start, end, devig):
    sels = ["over", "under"] if cfg["sel"] == "both" else [cfg["sel"]]
    picks = []
    for s in sels:
        if cfg["kind"] == "lineshop":
            picks += lineshop_picks(rows, cfg["market"], s, tiers, edge, devig,
                                    cfg["omin"], cfg["omax"], cfg["outlier"],
                                    start, end, cfg.get("maxvig"))
        else:
            picks += model_picks(rows, cfg["market"], s, tiers, edge, cfg["omin"],
                                 cfg["omax"], cfg["minp"], cfg.get("reqpin", False),
                                 cfg.get("nopin", False), start, end, cfg.get("outlier"))
    return picks


def main():
    rows = load()
    print(f"loaded {len(rows)} selection-rows, "
          f"{len({r['match_id'] for r in rows})} matches\n")

    print("=" * 104)
    print("PART 1 — AS-DEPLOYED CONFIG, replayed per walk-forward window "
          "(raw edge, exactly as the live code computes it)")
    print("=" * 104)
    hdr = f"{'bot':<28}" + "".join(f"{w[0]+' n/ROI':>17}" for w in WINDOWS) + \
          f"{'ALL n/ROI/t':>22}{'LIVE n/ROI':>16}"
    print(hdr)
    for name, cfg in BOTS.items():
        line = f"{name:<28}"
        for _, s, e in WINDOWS:
            n, r, _ = roi_stats(run_bot(rows, cfg, cfg["edge"], cfg["tiers"], s, e, False))
            line += f"{n:>8}/{r:>7.1f}%"
        n, r, t = roi_stats(run_bot(rows, cfg, cfg["edge"], cfg["tiers"],
                                    "2026-05-01", "2026-08-22", False))
        ln, lr = LIVE[name]
        line += f"{n:>8}/{r:>6.1f}%/{t:>5.2f}" + f"{ln:>7}/{lr:>7.1f}%"
        print(line)


if __name__ == "__main__":
    main()
