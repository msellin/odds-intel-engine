#!/usr/bin/env python3
"""
CS2 paper-trading activity report — monitor the recent diversification stack.

Surfaces what /admin/cs2 doesn't: per-bot 7d/30d activity, the n_books
distribution per pick (validates the MIN-BOOKS-RELAX supply unlock), the
supply funnel (matches in pool → eligible → fired), and a recent-picks log
for spot-checking. Designed as a tactical tool for the 3-5 day validation
window after CS2-BOT-MULTI-CONFIG, CS2-HLTV-ODDS-24H, CS2-BOT-SHRINKAGE,
and CS2-MIN-BOOKS-RELAX shipped.

Usage:
    python3 scripts/esports/cs2_bot_activity_report.py
    python3 scripts/esports/cs2_bot_activity_report.py --days 14
    python3 scripts/esports/cs2_bot_activity_report.py --recent 50
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query
from scripts.esports.cs2_bot import BOTS_CONFIG


def _hr(title: str = "") -> str:
    if not title:
        return "─" * 76
    return f"── {title} " + "─" * max(0, 73 - len(title))


def _fmt_pct(x: float | None, places: int = 1) -> str:
    if x is None:
        return "    —"
    return f"{x*100:+.{places}f}%"


def _fmt_roi(pnl_eur: float | None, staked_eur: float | None) -> str:
    if staked_eur is None or staked_eur <= 0 or pnl_eur is None:
        return "    —"
    return _fmt_pct(pnl_eur / staked_eur)


def _per_bot_activity(days: int) -> list[dict]:
    """Per-bot fires, settlement, ROI, average edge, avg n_books for a window."""
    rows = execute_query(f"""
        SELECT bot_name,
               COUNT(*) AS fires,
               COUNT(*) FILTER (WHERE result IS NOT NULL) AS settled,
               COUNT(*) FILTER (WHERE result = 'won') AS wins,
               COUNT(*) FILTER (WHERE result = 'lost') AS losses,
               COALESCE(SUM(pnl_eur), 0)::float AS pnl_eur,
               COALESCE(SUM(stake_eur) FILTER (WHERE result IS NOT NULL), 0)::float
                   AS staked_settled_eur,
               AVG(edge)::float AS avg_edge,
               AVG(n_books_at_pick)::float AS avg_books,
               SUM(CASE WHEN n_books_at_pick = 1 THEN 1 ELSE 0 END) AS single_book_fires,
               SUM(CASE WHEN market = 'match_winner' THEN 1 ELSE 0 END) AS mw_fires,
               SUM(CASE WHEN market = 'atleast1map'  THEN 1 ELSE 0 END) AS a1m_fires
        FROM cs2_simulated_bets
        WHERE placed_at >= NOW() - INTERVAL '{int(days)} days'
        GROUP BY bot_name
        ORDER BY fires DESC
    """, ())
    return list(rows)


def _supply_funnel(days: int) -> dict:
    """The MIN-BOOKS-RELAX validation funnel for an N-day window."""
    rows = execute_query(f"""
        SELECT
          COUNT(*) AS pool,
          COUNT(*) FILTER (WHERE threshold_odds1 IS NOT NULL) AS w_thr,
          COUNT(*) FILTER (
            WHERE threshold_odds1 IS NOT NULL AND
                  ((bookie_odds1   IS NOT NULL)::int +
                   (coolbet_odds1  IS NOT NULL)::int +
                   (pinnacle_odds1 IS NOT NULL)::int) >= 1
          ) AS eligible_min1,
          COUNT(*) FILTER (
            WHERE threshold_odds1 IS NOT NULL AND
                  ((bookie_odds1   IS NOT NULL)::int +
                   (coolbet_odds1  IS NOT NULL)::int +
                   (pinnacle_odds1 IS NOT NULL)::int) >= 2
          ) AS eligible_min2
        FROM cs2_upcoming_matches
        WHERE kickoff_time >= NOW() - INTERVAL '{int(days)} days'
          AND kickoff_time <  NOW() + INTERVAL '3 days'
    """, ())
    return dict(rows[0]) if rows else {}


def _recent_picks(limit: int) -> list[dict]:
    return list(execute_query("""
        SELECT b.placed_at, b.bot_name, b.team1, b.team2, b.market, b.pick,
               b.bookie, b.odds_at_pick, b.edge, b.stake_eur,
               b.n_books_at_pick, b.result, b.pnl_eur, b.kickoff_time
        FROM cs2_simulated_bets b
        ORDER BY b.placed_at DESC
        LIMIT %s
    """, (int(limit),)))


def _print_bot_table(rows: list[dict], days: int) -> None:
    print(_hr(f"per-bot activity (last {days}d)"))
    if not rows:
        print("  no fires in window\n")
        return
    cols = ("bot", "fires", "set", "W-L", "ROI", "avg edge",
            "avg #bk", "1bk", "MW", "A1M")
    print(f"  {cols[0]:<24}{cols[1]:>6} {cols[2]:>4} {cols[3]:>5} "
          f"{cols[4]:>7} {cols[5]:>9} {cols[6]:>7} {cols[7]:>4} "
          f"{cols[8]:>4} {cols[9]:>4}")
    for r in rows:
        wl = f"{r['wins']}-{r['losses']}" if r['settled'] else "  —"
        roi = _fmt_roi(r["pnl_eur"], r["staked_settled_eur"])
        avg_edge = _fmt_pct(r["avg_edge"]) if r["avg_edge"] is not None else "    —"
        avg_books = f"{r['avg_books']:.2f}" if r["avg_books"] is not None else "  —"
        print(f"  {r['bot_name']:<24}{r['fires']:>6} {r['settled']:>4} "
              f"{wl:>5} {roi:>7} {avg_edge:>9} {avg_books:>7} "
              f"{r['single_book_fires']:>4} {r['mw_fires']:>4} {r['a1m_fires']:>4}")
    print()


def _print_silent_check(active_7d: list[dict]) -> None:
    """List bots in BOTS_CONFIG with zero fires in the 7d window."""
    fired = {r["bot_name"] for r in active_7d}
    silent = [name for name, cfg in BOTS_CONFIG.items()
              if cfg.get("enabled", True) and name not in fired]
    print(_hr("silent-bot check (enabled but 0 fires in 7d)"))
    if not silent:
        print("  all enabled bots have fired in the last 7d\n")
        return
    for name in silent:
        cfg = BOTS_CONFIG[name]
        srcs = ",".join(cfg.get("sources", ()))
        mb = cfg.get("min_books_for_pick", "?")
        mkts = ",".join(cfg.get("markets", ()))
        print(f"  ⚠  {name:<24}  sources=[{srcs}] min_books={mb} markets=[{mkts}]")
    print(f"  → check supply funnel / per-bot edge floors / sources")
    print()


def _print_funnel(window: dict, days: int) -> None:
    print(_hr(f"supply funnel ({days}d kickoff window, today→+3d)"))
    pool = window.get("pool", 0) or 0
    w_thr = window.get("w_thr", 0) or 0
    e1 = window.get("eligible_min1", 0) or 0
    e2 = window.get("eligible_min2", 0) or 0
    def pct(n): return f"({100*n/pool:>4.1f}%)" if pool else "    —"
    print(f"  pool                       {pool:>5d}  {pct(pool)}")
    print(f"    + model coverage (thr)   {w_thr:>5d}  {pct(w_thr)}")
    print(f"    + ≥1 book (relaxed)      {e1:>5d}  {pct(e1)}  ← new bots eligible")
    print(f"    + ≥2 books (canonical)   {e2:>5d}  {pct(e2)}  ← value/v8/v7/hltv_v1 eligible")
    if e1 and e2:
        print(f"  supply unlock multiplier: {e1/max(e2,1):.2f}× more matches firable")
    print()


def _print_recent(rows: list[dict], limit: int) -> None:
    print(_hr(f"recent picks (last {limit})"))
    if not rows:
        print("  no picks ever placed\n")
        return
    header = f"  {'placed':<16} {'bot':<22} {'market':<12} {'pick':<18} {'bk':<8} {'odds':>5} {'edge':>6} {'eur':>6} {'#bk':>3} {'result':<6} {'pnl':>7}"
    print(header)
    for r in rows:
        placed = r["placed_at"].strftime("%m-%d %H:%M") if r["placed_at"] else "       —"
        bot = (r["bot_name"] or "")[:22]
        market = (r["market"] or "")[:12]
        pick = (r["pick"] or "")[:18]
        bookie = (r["bookie"] or "")[:8]
        odds = f"{float(r['odds_at_pick']):.2f}" if r["odds_at_pick"] else "  —"
        edge = _fmt_pct(float(r["edge"])) if r["edge"] is not None else "    —"
        eur = f"{float(r['stake_eur']):.2f}" if r["stake_eur"] is not None else "  —"
        nbk = str(r["n_books_at_pick"] or "-")
        result = r["result"] or "open"
        pnl = (f"{float(r['pnl_eur']):+6.2f}"
               if r["pnl_eur"] is not None else "    —")
        print(f"  {placed:<16} {bot:<22} {market:<12} {pick:<18} "
              f"{bookie:<8} {odds:>5} {edge:>6} {eur:>6} {nbk:>3} "
              f"{result:<6} {pnl:>7}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--days", type=int, default=7,
                    help="primary window in days (default: 7)")
    ap.add_argument("--long-days", type=int, default=30,
                    help="secondary comparison window in days (default: 30)")
    ap.add_argument("--recent", type=int, default=20,
                    help="recent picks to list (default: 20)")
    args = ap.parse_args()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print()
    print(_hr(f"CS2 BOT ACTIVITY REPORT  {now}"))
    print()

    short = _per_bot_activity(args.days)
    long  = _per_bot_activity(args.long_days)
    funnel = _supply_funnel(args.days)
    recent = _recent_picks(args.recent)

    _print_bot_table(short, args.days)
    _print_bot_table(long, args.long_days)
    _print_silent_check(short)
    _print_funnel(funnel, args.days)
    _print_recent(recent, args.recent)

    # Footer hints
    print(_hr("legend"))
    print("  ROI = pnl_eur / staked_eur on SETTLED bets only.")
    print("  avg #bk = mean n_books_at_pick — close to 1.0 ⇒ single-book picks;")
    print("            ≥2.0 ⇒ consensus picks. New diversification bots should")
    print("            see avg #bk near 1.0 post-CS2-MIN-BOOKS-RELAX.")
    print("  1bk     = count of picks fired on a single book (was 0 before relax).")
    print("  MW/A1M  = market split: match_winner vs atleast1map.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
