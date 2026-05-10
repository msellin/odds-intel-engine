"""
Stage 6a — Pre-match bot backtester.

Replays every active pre-match bot in `BOTS_CONFIG` against the historical
window. For each finished match: pulls the latest pre-kickoff ensemble
prediction + the best pre-kickoff odds per market, applies the bot's
edge/threshold/odds_range/min_prob/league_filter/tier_filter, and records
what each bot would have bet, whether it won, and the P&L at flat €10 stake.

**Scope honesty.** This does NOT re-run the full live pipeline:
  - No Pinnacle veto, no sharp_consensus gate, no calibration stack —
    those depend on real-time caches we can't reconstruct cleanly.
  - No Kelly stake sizing, no exposure cap, no league-bet rotation.
  - Flat €10 stake. P&L is a directional signal, not a faithful replay.
The point is "did this bot ever have edge in this league/era?" — not
"would these exact bets have placed at these exact stakes?".

Output: CSV at dev/active/backtest-pre-match-results.csv with one row per
(bot, match, candidate-bet). `would_bet=true` rows are the actual placements.

Usage:
    python3 scripts/backtest_pre_match_bots.py --from 2024-08-01 --to 2025-05-31
    python3 scripts/backtest_pre_match_bots.py --bot bot_lower_1x2
    python3 scripts/backtest_pre_match_bots.py --limit 200    # smoke run
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

from workers.api_clients.supabase_client import execute_query
from workers.jobs.daily_pipeline_v2 import BOTS_CONFIG, BOT_TIMING_COHORTS

console = Console()

DEFAULT_OUT = Path(__file__).parent.parent / "dev" / "active" / "backtest-pre-match-results.csv"

# Markets we attempt to replay. Keys map back to the bot config's `markets` flag.
# Each entry: (market_key_for_bot, selection_label, odds_field_in_match,
#              prob_field_in_pred, market_for_db, selection_for_db)
CANDIDATE_SPECS = [
    ("1x2", "Home",      "odds_home",     "home_prob",    "1x2",            "home"),
    ("1x2", "Draw",      "odds_draw",     "draw_prob",    "1x2",            "draw"),
    ("1x2", "Away",      "odds_away",     "away_prob",    "1x2",            "away"),
    ("ou",  "Over 2.5",  "odds_over_25",  "over_25_prob", "over_under_25",  "over"),
    ("ou",  "Under 2.5", "odds_under_25", "under_25_prob","over_under_25",  "under"),
    ("ou15","Over 1.5",  "odds_over_15",  "over_15_prob", "over_under_15",  "over"),
    ("ou15","Under 1.5", "odds_under_15", "under_15_prob","over_under_15",  "under"),
    ("ou35","Over 3.5",  "odds_over_35",  "over_35_prob", "over_under_35",  "over"),
    ("ou35","Under 3.5", "odds_under_35", "under_35_prob","over_under_35",  "under"),
    ("btts","Yes",       "odds_btts_yes", "btts_yes_prob","btts",           "yes"),
    ("btts","No",        "odds_btts_no",  "btts_no_prob", "btts",           "no"),
]


def _outcome(market: str, selection: str, sh: int, sa: int) -> bool:
    if market == "1x2":
        if selection == "Home": return sh > sa
        if selection == "Draw": return sh == sa
        if selection == "Away": return sh < sa
    if market == "over_under_25":
        return (sh + sa > 2.5) if selection == "over" else (sh + sa < 2.5)
    if market == "over_under_15":
        return (sh + sa > 1.5) if selection == "over" else (sh + sa < 1.5)
    if market == "over_under_35":
        return (sh + sa > 3.5) if selection == "over" else (sh + sa < 3.5)
    if market == "btts":
        btts = sh > 0 and sa > 0
        return btts if selection == "yes" else not btts
    return False


def _load_matches(date_from: str, date_to: str, limit: int | None) -> list[dict]:
    where = ["m.status = 'finished'", "m.score_home IS NOT NULL", "m.date >= %s", "m.date <= %s"]
    params: list = [f"{date_from}T00:00:00", f"{date_to}T23:59:59"]
    sql = (
        "SELECT m.id AS match_id, m.date, m.score_home, m.score_away, "
        "       m.season, m.league_id, m.home_team_id, m.away_team_id, "
        "       l.name AS league_name, l.country, l.tier "
        "FROM matches m "
        "JOIN leagues l ON l.id = m.league_id "
        "WHERE " + " AND ".join(where) + " ORDER BY m.date ASC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return execute_query(sql, params)


def _load_predictions(match_ids: list[str]) -> dict[str, dict]:
    """Returns {match_id: {prob fields}}. Picks the latest pre-kickoff ensemble pred."""
    if not match_ids:
        return {}
    placeholders = ",".join(["%s"] * len(match_ids))
    sql = (
        "SELECT DISTINCT ON (p.match_id, p.market) "
        "       p.match_id, p.market, p.model_probability "
        "FROM predictions p "
        "JOIN matches m ON m.id = p.match_id "
        f"WHERE p.match_id IN ({placeholders}) "
        "  AND p.source = 'ensemble' "
        "  AND p.created_at < m.date "
        "ORDER BY p.match_id, p.market, p.created_at DESC"
    )
    rows = execute_query(sql, tuple(match_ids))

    # Pivot: market='1x2_home' → home_prob, etc.
    out: dict[str, dict] = defaultdict(dict)
    for r in rows:
        mid = r["match_id"]
        m = r["market"]
        prob = float(r["model_probability"]) if r["model_probability"] is not None else None
        if prob is None:
            continue
        # Map per-selection prediction rows to the field names BOTS_CONFIG uses.
        # Predictions are stored as one row per (match, market) where market
        # already encodes the selection: '1x2_home', 'over25', 'btts_yes', etc.
        mapping = {
            "1x2_home": "home_prob",
            "1x2_draw": "draw_prob",
            "1x2_away": "away_prob",
            "over25":   "over_25_prob",
            "under25":  "under_25_prob",
            "over15":   "over_15_prob",
            "under15":  "under_15_prob",
            "over35":   "over_35_prob",
            "under35":  "under_35_prob",
            "btts_yes": "btts_yes_prob",
            "btts_no":  "btts_no_prob",
        }
        if m in mapping:
            out[mid][mapping[m]] = prob
    return dict(out)


def _load_pre_kickoff_odds(match_ids: list[str]) -> dict[str, dict]:
    """Best (max) pre-kickoff odds per (match, market, selection)."""
    if not match_ids:
        return {}
    placeholders = ",".join(["%s"] * len(match_ids))
    sql = (
        "SELECT os.match_id, os.market, os.selection, MAX(os.odds) AS odds "
        "FROM odds_snapshots os "
        "JOIN matches m ON m.id = os.match_id "
        f"WHERE os.match_id IN ({placeholders}) "
        "  AND os.is_live = false "
        "  AND os.timestamp < m.date "
        "GROUP BY os.match_id, os.market, os.selection"
    )
    rows = execute_query(sql, tuple(match_ids))
    out: dict[str, dict] = defaultdict(dict)
    for r in rows:
        mid = r["match_id"]
        m = r["market"].lower() if r["market"] else ""
        sel = (r["selection"] or "").lower()
        # Map (market, selection) → field name in match dict the BOTS_CONFIG loop reads
        if m == "1x2":
            if sel == "home": out[mid]["odds_home"] = float(r["odds"])
            elif sel == "draw": out[mid]["odds_draw"] = float(r["odds"])
            elif sel == "away": out[mid]["odds_away"] = float(r["odds"])
        elif m == "over_under_25":
            if sel == "over": out[mid]["odds_over_25"] = float(r["odds"])
            elif sel == "under": out[mid]["odds_under_25"] = float(r["odds"])
        elif m == "over_under_15":
            if sel == "over": out[mid]["odds_over_15"] = float(r["odds"])
            elif sel == "under": out[mid]["odds_under_15"] = float(r["odds"])
        elif m == "over_under_35":
            if sel == "over": out[mid]["odds_over_35"] = float(r["odds"])
            elif sel == "under": out[mid]["odds_under_35"] = float(r["odds"])
        elif m == "btts":
            if sel in ("yes", "y", "true"): out[mid]["odds_btts_yes"] = float(r["odds"])
            elif sel in ("no", "n", "false"): out[mid]["odds_btts_no"] = float(r["odds"])
    return dict(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default=None,
                    help="ISO date. Default: 1 year ago.")
    ap.add_argument("--to", dest="date_to", default=None,
                    help="ISO date. Default: yesterday.")
    ap.add_argument("--bot", dest="bot_filter", default=None,
                    help="Restrict to a single bot name (e.g. bot_lower_1x2).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap matches loaded — useful for smoke runs.")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="Output CSV path.")
    ap.add_argument("--stake", type=float, default=10.0)
    args = ap.parse_args()

    today = date.today()
    date_to = args.date_to or (today - timedelta(days=1)).isoformat()
    date_from = args.date_from or (today - timedelta(days=365)).isoformat()

    console.print(f"[cyan]Loading finished matches {date_from} → {date_to}…[/cyan]")
    matches = _load_matches(date_from, date_to, args.limit)
    console.print(f"  {len(matches):,} matches in scope")
    if not matches:
        return

    match_ids = [m["match_id"] for m in matches]

    # Predictions + odds in two bulk queries — much faster than per-match.
    # Chunk by 5000 IDs to stay under Postgres parameter limits.
    preds: dict[str, dict] = {}
    odds_lookup: dict[str, dict] = {}
    chunk = 4000
    for i in range(0, len(match_ids), chunk):
        ids = match_ids[i:i + chunk]
        preds.update(_load_predictions(ids))
        odds_lookup.update(_load_pre_kickoff_odds(ids))
    console.print(f"  Predictions for {len(preds):,} / {len(matches):,} matches")
    console.print(f"  Pre-kickoff odds for {len(odds_lookup):,} / {len(matches):,} matches")

    # Active bots only — and respect --bot filter
    bot_items = [(name, cfg) for name, cfg in BOTS_CONFIG.items()
                 if args.bot_filter is None or name == args.bot_filter]
    if not bot_items:
        console.print(f"[red]No bots matched filter {args.bot_filter}[/red]")
        return

    rows_out: list[dict] = []
    summary: dict[str, dict] = defaultdict(lambda: {"n_bets": 0, "wins": 0, "stake": 0.0, "pnl": 0.0})

    with Progress(TextColumn("[bold blue]backtest"), BarColumn(),
                  TextColumn("{task.completed}/{task.total} matches"),
                  TimeRemainingColumn(), console=console) as bar:
        task = bar.add_task("walk", total=len(matches))

        for m in matches:
            mid = m["match_id"]
            tier = m["tier"]
            country = m["country"]
            sh = int(m["score_home"])
            sa = int(m["score_away"])

            pred = preds.get(mid, {})
            match_odds = odds_lookup.get(mid, {})
            if not pred or not match_odds:
                bar.advance(task)
                continue

            for bot_name, cfg in bot_items:
                if cfg.get("tier_filter") and tier not in cfg["tier_filter"]:
                    continue
                if cfg.get("league_filter") and country not in cfg["league_filter"]:
                    continue

                thresholds = cfg["edge_thresholds"].get(tier, {})
                odds_min, odds_max = cfg["odds_range"]
                min_prob = cfg["min_prob"]

                # Build candidate list for this bot — matches the live loop logic
                cands = []
                for mkt_key, selection, odds_field, prob_field, db_market, db_sel in CANDIDATE_SPECS:
                    if mkt_key not in cfg.get("markets", []):
                        continue
                    odds = match_odds.get(odds_field, 0)
                    raw_prob = pred.get(prob_field)
                    if odds <= 0 or raw_prob is None:
                        continue

                    if mkt_key == "1x2":
                        threshold = thresholds.get("1x2_fav", 0.05) if odds < 2.0 else thresholds.get("1x2_long", 0.08)
                    elif mkt_key in ("ou", "ou15", "ou35"):
                        threshold = thresholds.get("ou", 0.05)
                    elif mkt_key == "btts":
                        threshold = thresholds.get("btts", 0.06)
                    else:
                        threshold = 0.05

                    ip = 1 / odds
                    edge = raw_prob - ip
                    if edge < threshold or odds < odds_min or odds > odds_max or raw_prob < min_prob:
                        continue
                    cands.append((mkt_key, selection, odds, raw_prob, ip, edge,
                                  db_market, db_sel))

                # Top edge wins (live pipeline does the same: sort, place top)
                cands.sort(key=lambda c: c[5], reverse=True)
                if not cands:
                    continue
                # Live pipeline places multiple top-edge bets — for backtest, take only top-1 per match per bot
                # (cleaner ROI calc; "best edge" is what matters for "did this bot ever have edge?")
                mkt_key, selection, odds, prob, ip, edge, db_market, db_sel = cands[0]
                won = _outcome(db_market, db_sel, sh, sa)
                pnl = round((odds - 1) * args.stake, 2) if won else -args.stake

                rows_out.append({
                    "bot": bot_name,
                    "match_id": mid,
                    "date": m["date"].isoformat() if hasattr(m["date"], "isoformat") else str(m["date"]),
                    "league": m["league_name"],
                    "country": country,
                    "tier": tier,
                    "season": m["season"],
                    "market": db_market,
                    "selection": db_sel,
                    "odds": round(odds, 3),
                    "model_prob": round(prob, 4),
                    "implied_prob": round(ip, 4),
                    "edge": round(edge, 4),
                    "stake": args.stake,
                    "won": won,
                    "pnl": pnl,
                    "score_home": sh,
                    "score_away": sa,
                })
                s = summary[bot_name]
                s["n_bets"] += 1
                s["wins"] += 1 if won else 0
                s["stake"] += args.stake
                s["pnl"] += pnl

            bar.advance(task)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if rows_out:
        fieldnames = list(rows_out[0].keys())
        with out_path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows_out)
        console.print(f"\n[green]✓ Wrote {len(rows_out):,} rows to {out_path}[/green]")
    else:
        console.print("[yellow]No backtest bets generated — nothing to write.[/yellow]")

    # Summary table
    if summary:
        t = Table(title=f"Backtest summary ({date_from} → {date_to})")
        t.add_column("Bot", style="cyan")
        t.add_column("Bets", justify="right")
        t.add_column("Wins", justify="right")
        t.add_column("Win %", justify="right")
        t.add_column("Stake", justify="right")
        t.add_column("PnL", justify="right")
        t.add_column("ROI %", justify="right")
        for bot, s in sorted(summary.items(), key=lambda kv: -kv[1]["pnl"]):
            n = s["n_bets"]
            wp = (s["wins"] / n * 100) if n else 0
            roi = (s["pnl"] / s["stake"] * 100) if s["stake"] else 0
            colour = "green" if roi > 0 else "red"
            t.add_row(bot, str(n), str(s["wins"]), f"{wp:.1f}",
                      f"{s['stake']:.0f}", f"[{colour}]{s['pnl']:+.2f}[/{colour}]",
                      f"[{colour}]{roi:+.1f}[/{colour}]")
        console.print(t)


if __name__ == "__main__":
    main()
