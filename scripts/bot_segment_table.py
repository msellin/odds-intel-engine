#!/usr/bin/env python3
"""BOT-SEGMENT-TABLE — every bot, every segment, one table. No "best cell" summary.

    python3 scripts/bot_segment_table.py
    python3 scripts/bot_segment_table.py --bot bot_pin_1x2_home_v1
    python3 scripts/bot_segment_table.py --min-n 25 --csv out.csv

WHY THIS EXISTS
---------------
Owner, 2026-09-13: *"did you analyze each? i didnt see any table with the result
for each bot in every possible segment or configuration or filter they could
have."* Fair — `floor_grid_sweep.py` reports the BEST cell per group, which
answers "is there something here?" but not "show me everything". Reporting only
the winner is also how a wide scan flatters itself: the losing cells are the
context that tells you whether the winner is real.

So this prints EVERY segment above a volume gate, winners and losers together,
and sorts by CLV so the shape of each bot is visible at a glance.

HOW TO READ IT — in this order, or you will fool yourself
---------------------------------------------------------
1. **CLV first, ROI second.** CLV vs Pinnacle converges ~30x faster (~334
   settled bets for a useful read, against ~9,300 for +/-2% on ROI). At the
   volumes here almost every ROI figure is noise; the t-column says so.
2. **`t` is not decoration.** |t| < 2 means the number could easily be zero.
   Most segments in this table are in that band and should be read as "no
   information", not as a small effect.
3. **This is a WIDE SCAN.** Hundreds of segments are printed, so by construction
   some will show |t| > 2 by chance alone — roughly 5% of them. A lone
   significant cell in an otherwise flat bot is noise. What counts as evidence
   is a CONTIGUOUS RUN of positive segments, which is what `floor_grid_sweep`'s
   `#robust` column measures properly. Use this table to see shape; use that one
   to decide.
4. **Edge kinds are never comparable.** A 3% sharp edge and a 13% model edge are
   different quantities (ANALYSIS_GOTCHAS). Never rank a sharp segment against a
   model segment on the edge axis.
"""
from __future__ import annotations

import argparse
import math
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workers.api_clients.db import execute_query  # noqa: E402

ODDS_BANDS = [("<1.8", 0, 1.8), ("1.8-2.2", 1.8, 2.2), ("2.2-2.8", 2.2, 2.8),
              ("2.8-3.2", 2.8, 3.2), ("3.2-4.0", 3.2, 4.0), ("4.0+", 4.0, 99)]
EDGE_BANDS = [("<5%", -9, 0.05), ("5-8%", 0.05, 0.08), ("8-10%", 0.08, 0.10),
              ("10-13%", 0.10, 0.13), ("13-18%", 0.13, 0.18), ("18%+", 0.18, 9)]


def _rows(bot: str | None) -> list[dict]:
    where = "AND b.name = %s" if bot else ""
    return execute_query(
        f"""
        SELECT b.name AS bot, s.market, lower(s.selection) AS sel,
               COALESCE(s.odds_at_pick_live, s.odds_at_pick)::float AS odds,
               s.edge_percent::float AS edge,
               s.clv_pinnacle::float AS clv,
               (s.result = 'won') AS won
          FROM shadow_bets_unique s JOIN bots b ON b.id = s.bot_id
         WHERE s.result IN ('won','lost')
           AND COALESCE(s.odds_at_pick_live, s.odds_at_pick) > 1.0
           {where}
        """, [bot] if bot else None) or []


def _stat(sel: list[dict]) -> dict | None:
    if len(sel) < 3:
        return None
    rets = [(r["odds"] - 1) if r["won"] else -1.0 for r in sel]
    roi = 100 * st.mean(rets)
    roi_t = (st.mean(rets) / (st.stdev(rets) / math.sqrt(len(rets)))
             if st.stdev(rets) else 0.0)
    cl = [r["clv"] for r in sel if r["clv"] is not None]
    clv = clv_t = None
    if len(cl) >= 3 and st.stdev(cl):
        clv = 100 * st.mean(cl)
        clv_t = st.mean(cl) / (st.stdev(cl) / math.sqrt(len(cl)))
    return {"n": len(sel), "win": 100 * sum(1 for r in sel if r["won"]) / len(sel),
            "roi": roi, "roi_t": roi_t, "clv": clv, "clv_t": clv_t}


def _segments(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """Every segmentation worth cutting, each independently."""
    out: list[tuple[str, list[dict]]] = [("ALL", rows)]
    for m in sorted({r["market"] for r in rows}):
        out.append((f"market={m}", [r for r in rows if r["market"] == m]))
    for s in sorted({r["sel"] for r in rows}):
        out.append((f"selection={s}", [r for r in rows if r["sel"] == s]))
    for lbl, lo, hi in ODDS_BANDS:
        out.append((f"odds {lbl}", [r for r in rows if lo <= r["odds"] < hi]))
    for lbl, lo, hi in EDGE_BANDS:
        out.append((f"edge {lbl}",
                    [r for r in rows if r["edge"] is not None and lo <= r["edge"] < hi]))
    # Cumulative floors — the thing you would actually configure.
    for f in (0.05, 0.08, 0.10, 0.13, 0.15):
        out.append((f"edge >= {f:.0%}",
                    [r for r in rows if r["edge"] is not None and r["edge"] >= f]))
    for f in (1.8, 2.2, 2.8, 3.2):
        out.append((f"odds >= {f}", [r for r in rows if r["odds"] >= f]))
    return out


def run(bot: str | None, min_n: int, csv: str | None) -> int:
    rows = _rows(bot)
    if not rows:
        print("no settled picks found")
        return 1
    bots = sorted({r["bot"] for r in rows})
    lines: list[str] = []
    print(f"\n{len(rows)} settled picks across {len(bots)} bots   "
          f"(segments with n >= {min_n})")
    print("CLV first, ROI second. |t| < 2 = indistinguishable from zero.")
    print("WIDE SCAN: ~5% of segments will show |t|>2 by chance — a lone hit is "
          "noise;\nlook for a CONTIGUOUS RUN, and confirm with floor_grid_sweep's "
          "#robust column.\n")

    for b in bots:
        br = [r for r in rows if r["bot"] == b]
        segs = [(name, _stat(s)) for name, s in _segments(br)]
        segs = [(n, d) for n, d in segs if d and d["n"] >= min_n]
        if not segs:
            continue
        segs.sort(key=lambda kv: (kv[1]["clv"] if kv[1]["clv"] is not None else -99),
                  reverse=True)
        print(f"\n{'='*86}\n{b}   ({len(br)} settled)\n{'='*86}")
        print(f"  {'segment':22}{'n':>6}{'win%':>7}{'CLV%':>9}{'t':>7}"
              f"{'ROI%':>9}{'t':>7}   flag")
        for name, d in segs:
            clv = f"{d['clv']:+.2f}" if d["clv"] is not None else "—"
            clvt = f"{d['clv_t']:+.1f}" if d["clv_t"] is not None else "—"
            # Flag only what clears BOTH a real t and a real n. Everything else
            # is shown plain, so the eye is not drawn to noise.
            flag = ""
            if d["clv_t"] is not None and d["clv_t"] >= 2 and d["n"] >= 40:
                flag = "CLV+"
            elif d["clv_t"] is not None and d["clv_t"] <= -2 and d["n"] >= 40:
                flag = "clv-"
            print(f"  {name:22}{d['n']:>6}{d['win']:>7.1f}{clv:>9}{clvt:>7}"
                  f"{d['roi']:>+9.1f}{d['roi_t']:>+7.1f}   {flag}")
            clv_csv = "" if d["clv"] is None else f"{d['clv']:.2f}"
            lines.append(f"{b},{name},{d['n']},{d['win']:.1f},{clv_csv},{d['roi']:.1f}")
    if csv:
        Path(csv).write_text("bot,segment,n,win_pct,clv_pct,roi_pct\n" + "\n".join(lines))
        print(f"\nwrote {csv}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bot")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--csv")
    a = ap.parse_args()
    return run(a.bot, a.min_n, a.csv)


if __name__ == "__main__":
    raise SystemExit(main())
