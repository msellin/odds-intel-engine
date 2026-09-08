#!/usr/bin/env python3
"""BOT-2D-AUDIT — Step 1 of SHADOW-BOT-CONSOLIDATION, out-of-sample honest.

For EVERY bot, search the 2D (edge floor x odds floor) matrix for a profitable
frame — but the ONLY thing that counts is a frame that survives HELD-OUT
out-of-sample validation, not in-sample selection:

    1. split each bot's settled picks chronologically: TRAIN (earlier) / TEST (held out)
    2. on TRAIN, grid-search and pick the best frame (max profit, min-n gate)
    3. apply THAT train-selected frame to the untouched TEST window
    4. a bot "has a real frame" only if the train-selected frame is ALSO
       profitable on TEST

This defeats the matrix-mining problem (searching ~40 cells x N bots finds
spurious "robust" cells): the frame is chosen without ever seeing TEST, so a
cell that only worked by chance in-sample is exposed. A time-fold "positive in
every fold" check is still in-sample selection and is NOT enough — hence the
strict held-out split here.

Verdict:
  RETIRE      — profitable NOWHERE on TRAIN, or the train-best frame LOSES on TEST
  KEEP/FRAME  — train-best frame is profitable on TEST with enough n
  KEEP/BASE   — the whole bot (no gate) is already profitable on TEST

Read-only. Usage:
    python3 scripts/bot_2d_audit.py                 # all bots
    python3 scripts/bot_2d_audit.py --min-total 120 --split 0.7
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import execute_query  # noqa: E402

EDGES = [0.03, 0.05, 0.06, 0.08, 0.10, 0.12, 0.13, 0.15, 0.18, 0.20]
ODDS = [1.0, 1.6, 1.8, 2.0, 2.2, 2.6, 2.8, 3.2, 3.5]


def _mfam(market: str) -> str:
    """Normalise a market to a family so a bot is audited PER MARKET — a bot can be
    great in one and terrible in another (line-shop 1x2 +13% vs its O/U -17%), and a
    blended per-bot verdict hides that. This was a real bug caught 2026-09-08."""
    m = (market or "").lower()
    if m.startswith("over_under") or m in ("o/u", "ou"):
        return "o/u"
    if m.startswith("corners"):
        return "corners"
    if m in ("1x2",):
        return "1x2"
    if m in ("btts", "both_teams_score"):
        return "btts"
    if "handicap" in m or m.startswith("ah"):
        return "ah"
    if "double" in m or m == "dc":
        return "double_chance"
    return m


def _load() -> dict[tuple, list[tuple]]:
    """Every (bot, market-family)'s settled picks, from BOTH ledgers, deduped per
    (bot, match, market, selection). Each pick = (edge_frac, exec_odds, ret, pick_time)."""
    rows = execute_query(
        """
        SELECT bot, match_id, market, selection, ep, odds, result, pick_time FROM (
          SELECT b.name bot, sb.match_id::text match_id, sb.market, sb.selection,
                 sb.edge_percent::float ep,
                 COALESCE(sb.odds_at_pick_live, sb.odds_at_pick)::float odds,
                 sb.result, sb.pick_time,
                 ROW_NUMBER() OVER (PARTITION BY b.name, sb.match_id, sb.market, sb.selection
                                    ORDER BY sb.pick_time) rn
            FROM shadow_bets sb JOIN bots b ON b.id = sb.bot_id
           WHERE sb.result IN ('won','lost') AND sb.edge_percent IS NOT NULL
             AND COALESCE(sb.odds_at_pick_live, sb.odds_at_pick) IS NOT NULL
          UNION ALL
          SELECT b.name bot, sb.match_id::text, sb.market, sb.selection,
                 sb.edge_percent::float,
                 COALESCE(sb.odds_at_pick_live, sb.odds_at_pick)::float,
                 sb.result, sb.pick_time,
                 ROW_NUMBER() OVER (PARTITION BY b.name, sb.match_id, sb.market, sb.selection
                                    ORDER BY sb.pick_time)
            FROM simulated_bets sb JOIN bots b ON b.id = sb.bot_id
           WHERE sb.result IN ('won','lost') AND sb.edge_percent IS NOT NULL
             AND COALESCE(sb.odds_at_pick_live, sb.odds_at_pick) IS NOT NULL
        ) q
        WHERE rn = 1
        """
    )
    from collections import defaultdict
    seen: dict = defaultdict(dict)
    for r in rows:
        gkey = (r["bot"], _mfam(r["market"]))
        dkey = (r["match_id"], r["market"], r["selection"])
        seen[gkey].setdefault(dkey, r)  # first occurrence wins the dedup
    out: dict[tuple, list[tuple]] = {}
    for gkey, d in seen.items():
        picks = []
        for r in d.values():
            ret = (r["odds"] - 1) if r["result"] == "won" else -1.0
            picks.append((r["ep"], r["odds"], ret, r["pick_time"]))
        picks.sort(key=lambda x: x[3])
        out[gkey] = picks
    return out


def _roi(rows) -> float:
    return 100 * st.mean([r[2] for r in rows]) if rows else 0.0


def _profit(rows, stake=10.0) -> float:
    return sum(r[2] for r in rows) * stake


def audit_bot(picks: list[tuple], split: float, min_train_cell: int, min_test: int):
    """Return dict with base_test_roi, the train-selected best frame, and its TEST result."""
    n = len(picks)
    cut = int(n * split)
    train, test = picks[:cut], picks[cut:]
    base_test = (_roi(test), len(test))
    if len(train) < min_train_cell or len(test) < min_test:
        return {"base_test": base_test, "frame": None, "test": None, "n_train": len(train), "n_test": len(test)}
    # pick the best frame on TRAIN by profit, gated on a minimum train cell size
    best = None
    for ef in EDGES:
        for of in ODDS:
            k = [r for r in train if r[0] >= ef and r[1] >= of]
            if len(k) < min_train_cell:
                continue
            pf = _profit(k)
            if best is None or pf > best[2]:
                best = (ef, of, pf, _roi(k), len(k))
    if best is None:
        return {"base_test": base_test, "frame": None, "test": None, "n_train": len(train), "n_test": len(test)}
    ef, of, _, train_roi, train_n = best
    # apply the TRAIN-selected frame to the untouched TEST window
    tk = [r for r in test if r[0] >= ef and r[1] >= of]
    test_res = (_roi(tk), len(tk), _profit(tk))
    return {
        "base_test": base_test,
        "frame": (ef, of, train_roi, train_n),
        "test": test_res,
        "n_train": len(train), "n_test": len(test),
    }


def verdict(a, min_test: int) -> str:
    base_roi, base_n = a["base_test"]
    if a["frame"] is None:
        return "RETIRE (too few picks or no train frame)"
    test_roi, test_n, _ = a["test"]
    if base_roi > 0 and base_n >= min_test:
        return "KEEP/BASE (whole bot +OOS)"
    if test_n >= min_test and test_roi > 0:
        return f"KEEP/FRAME (train-frame holds OOS +{test_roi:.0f}% n={test_n})"
    return "RETIRE (train-frame LOSES out-of-sample)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", type=float, default=0.7, help="train fraction (chronological)")
    ap.add_argument("--min-total", type=int, default=100, help="min settled picks to audit a bot")
    ap.add_argument("--min-train-cell", type=int, default=40)
    ap.add_argument("--min-test", type=int, default=25)
    a = ap.parse_args()

    groups = _load()
    print(f"BOT-2D-AUDIT — per (bot × market), held-out OOS (train {a.split:.0%} / "
          f"test {1-a.split:.0%}), min {a.min_total} picks\n")
    print(f"{'bot × market':<38}{'n':>6}{'baseTestROI':>12}   train-best frame → TEST      verdict")
    print("-" * 116)
    rows = []
    for (bot, mfam), picks in sorted(groups.items(), key=lambda x: -len(x[1])):
        if len(picks) < a.min_total:
            continue
        au = audit_bot(picks, a.split, a.min_train_cell, a.min_test)
        v = verdict(au, a.min_test)
        base_roi, base_n = au["base_test"]
        if au["frame"]:
            ef, of, tr_roi, tr_n = au["frame"]
            tro, tn, _ = au["test"]
            frame = f"e≥{ef:.0%}/o≥{of:.1f} (train {tr_roi:+.0f}%) → test {tro:+.0f}%/{tn}"
        else:
            frame = "—"
        label = f"{bot} · {mfam}"
        print(f"{label:<38}{len(picks):>6}{base_roi:>11.1f}%   {frame:<34} {v}")
        rows.append((label, v))
    retire = [b for b, v in rows if v.startswith("RETIRE")]
    keep = [b for b, v in rows if v.startswith("KEEP")]
    print(f"\nSUMMARY: {len(keep)} KEEP, {len(retire)} RETIRE (of {len(rows)} audited bot×market cells)")
    print("RETIRE:", ", ".join(retire) or "—")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
