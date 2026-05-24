"""AH-AWAY-MODEL-AUDIT — live-data follow-up (2026-05-24).

The original audit (scripts/ah_away_model_audit.py) ran against the 601-bet
backtest CSV from the May 12-24 silent period and found a strong asymmetry:
bot_ah_away_dog -31.8% ROI vs bot_ah_home_fav +3.1%. The conclusion that
shipped was AH-AWAY-LINE-FILTER (restrict bot_ah_away_dog to handicap_line
>= +0.5) plus the temporary retirement of the bot pending this audit.

This follow-up checks whether the asymmetry holds in *real settled* data
after AH-CAL-BYPASS (2026-05-24, 09:36 UTC) which fixed the double-shrinkage
bug that was silently killing AH bets. Real-money settled bets after
AH-CAL-BYPASS are the ground truth for whether the asymmetry is structural
or a backtest artifact.

Slices:
  1) Overall ROI per bot — has the asymmetry resolved post-fix?
  2) ROI by (selection_side × handicap_line) — which lines still misbehave?
  3) +0 line deep-dive — both bots show heavy losses there
  4) Recommendation — apply line filters symmetrically

Run: python3 scripts/ah_model_audit_live.py
"""
from __future__ import annotations
import sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workers.api_clients.db import execute_query
from rich.console import Console
from rich.table import Table

console = Console()


def _parse_line(selection: str) -> float | None:
    parts = selection.split()
    if len(parts) >= 2:
        try:
            return float(parts[1])
        except ValueError:
            return None
    return 0.0


def main():
    rows = execute_query("""
        SELECT
          b.name AS bot_name,
          sb.selection, sb.result,
          sb.model_probability AS raw_prob,
          sb.calibrated_prob   AS cal_prob,
          sb.odds_at_pick, sb.edge_percent, sb.pick_time,
          sb.stake, sb.pnl,
          l.tier
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        JOIN matches m ON m.id = sb.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE b.name IN ('bot_ah_home_fav','bot_ah_away_dog')
          AND sb.market = 'asian_handicap'
          AND sb.result IN ('won','lost','void')
        ORDER BY sb.pick_time
    """)
    console.print(f"\n[bold]Settled AH bets in simulated_bets: {len(rows)}[/bold]\n")

    # ── Slice 1: Overall ROI per bot ─────────────────────────────────────
    per_bot = defaultdict(lambda: {"n":0,"won":0,"lost":0,"void":0,
                                    "stake":0.0,"pnl":0.0,
                                    "sum_cal":0.0,"sum_raw":0.0})
    for r in rows:
        a = per_bot[r["bot_name"]]
        a["n"] += 1
        a[r["result"]] += 1
        a["stake"] += float(r["stake"] or 0)
        a["pnl"]   += float(r["pnl"] or 0)
        a["sum_cal"] += float(r["cal_prob"] or 0)
        a["sum_raw"] += float(r["raw_prob"] or 0)

    t = Table(title="Slice 1 — Overall ROI per bot (post-AH-CAL-BYPASS)")
    for col in ("bot", "n", "W", "L", "V", "hit%", "avg_cal%", "avg_raw%", "ROI%"):
        t.add_column(col)
    for bot, a in sorted(per_bot.items()):
        decided = a["won"] + a["lost"]
        hit = a["won"]/decided*100 if decided else 0
        roi = a["pnl"]/a["stake"]*100 if a["stake"]>0 else 0
        avg_cal = a["sum_cal"]/a["n"]*100
        avg_raw = a["sum_raw"]/a["n"]*100
        t.add_row(bot, str(a["n"]), str(a["won"]), str(a["lost"]), str(a["void"]),
                  f"{hit:.1f}", f"{avg_cal:.1f}", f"{avg_raw:.1f}", f"{roi:+.1f}")
    console.print(t)

    # ── Slice 2: ROI by (selection_side × handicap_line) ─────────────────
    by_line = defaultdict(lambda: {"n":0,"won":0,"lost":0,"void":0,
                                    "stake":0.0,"pnl":0.0,"sum_cal":0.0})
    for r in rows:
        side = "home" if r["selection"].startswith("home") else "away"
        line = _parse_line(r["selection"])
        if line is None: continue
        key = (side, line)
        a = by_line[key]
        a["n"] += 1
        a[r["result"]] += 1
        a["stake"] += float(r["stake"] or 0)
        a["pnl"]   += float(r["pnl"] or 0)
        a["sum_cal"] += float(r["cal_prob"] or 0)

    t = Table(title="Slice 2 — ROI by (side × handicap_line)")
    for col in ("side", "line", "n", "W", "L", "V", "hit%", "cal%", "Δ(cal-hit)pp", "ROI%"):
        t.add_column(col)
    for (side, line), a in sorted(by_line.items()):
        decided = a["won"]+a["lost"]
        hit = a["won"]/decided*100 if decided else 0
        avg_cal = a["sum_cal"]/a["n"]*100
        delta = avg_cal - hit
        roi = a["pnl"]/a["stake"]*100 if a["stake"]>0 else 0
        flag = " ⚠" if abs(delta) > 15 and a["n"] >= 3 else ""
        t.add_row(side+flag, f"{line:+.2f}", str(a["n"]), str(a["won"]),
                  str(a["lost"]), str(a["void"]),
                  f"{hit:.1f}", f"{avg_cal:.1f}", f"{delta:+.1f}", f"{roi:+.1f}")
    console.print(t)

    # ── Slice 3: +0 line deep dive ───────────────────────────────────────
    zero_line_bets = [r for r in rows if _parse_line(r["selection"]) == 0.0]
    if zero_line_bets:
        console.print(f"\n[bold yellow]Slice 3 — +0 line deep dive (n={len(zero_line_bets)})[/bold yellow]")
        t = Table()
        for col in ("bot", "selection", "pick_time", "result", "odds", "cal%", "edge%", "pnl"):
            t.add_column(col)
        for r in zero_line_bets:
            t.add_row(r["bot_name"], r["selection"], str(r["pick_time"])[:19],
                      r["result"], f"{float(r['odds_at_pick']):.2f}",
                      f"{float(r['cal_prob'])*100:.1f}",
                      f"{float(r['edge_percent']):.1f}",
                      f"{float(r['pnl']):+.2f}")
        console.print(t)

    # ── Headline conclusion ──────────────────────────────────────────────
    console.print()
    console.print("[bold]Headline:[/bold]")
    home_fav = per_bot.get("bot_ah_home_fav", {})
    away_dog = per_bot.get("bot_ah_away_dog", {})
    hf_roi = (home_fav.get("pnl",0)/home_fav.get("stake",1)*100) if home_fav.get("stake",0) else 0
    ad_roi = (away_dog.get("pnl",0)/away_dog.get("stake",1)*100) if away_dog.get("stake",0) else 0
    console.print(f"  bot_ah_home_fav: ROI {hf_roi:+.1f}% (n={home_fav.get('n',0)})")
    console.print(f"  bot_ah_away_dog: ROI {ad_roi:+.1f}% (n={away_dog.get('n',0)})")
    console.print()
    if zero_line_bets:
        zero_pnl = sum(float(r["pnl"] or 0) for r in zero_line_bets)
        zero_stake = sum(float(r["stake"] or 0) for r in zero_line_bets)
        zero_roi = zero_pnl/zero_stake*100 if zero_stake else 0
        console.print(f"  +0 line subset (both bots): ROI {zero_roi:+.1f}% on n={len(zero_line_bets)}")
        console.print(f"  → Excluding +0 line would have changed portfolio outcome by "
                      f"{zero_pnl:+.2f} on stake {zero_stake:.0f}")


if __name__ == "__main__":
    main()
