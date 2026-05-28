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

DC/DNB probs are derived inline from 1x2 ensemble predictions (same as live
pipeline). AH probs use Poisson re-computation via `compute_prediction()` +
the same `targets_poisson_history.csv` / `targets_global.csv` / `targets_extended.csv`
the live pipeline uses — `exp_home`/`exp_away` are not stored in the DB so must be
reconstructed here.

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
import math
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from workers.api_clients.supabase_client import execute_query
from workers.jobs.daily_pipeline_v2 import (
    BOTS_CONFIG, BOT_TIMING_COHORTS, PROCESSED_DIR,
    _ah_model_prob, _load_dc_rho_cache, compute_prediction,
)

console = Console()

DEFAULT_OUT = Path(__file__).parent.parent / "dev" / "active" / "backtest-pre-match-results.csv"

# Markets we attempt to replay. Keys map back to the bot config's `markets` flag.
# Each entry: (market_key_for_bot, selection_label, odds_field_in_match,
#              prob_field_in_pred, market_for_db, selection_for_db)
# DC / DNB / AH are handled inline below (derived probs, not flat specs).
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


def _outcome(market: str, selection: str, sh: int, sa: int) -> bool | None:
    """
    Returns True (win), False (loss), or None (void/push — stake returned).

    BACKTEST-ZERO-WIN-INVESTIGATE (2026-05-18): selection comes in lowercase
    for 1x2 (the CANDIDATE_SPECS tuples' 6th element is "home"/"draw"/"away").
    """
    if market == "1x2":
        if selection == "home": return sh > sa
        if selection == "draw": return sh == sa
        if selection == "away": return sh < sa
    if market == "over_under_25":
        return (sh + sa > 2.5) if selection == "over" else (sh + sa < 2.5)
    if market == "over_under_15":
        return (sh + sa > 1.5) if selection == "over" else (sh + sa < 1.5)
    if market == "over_under_35":
        return (sh + sa > 3.5) if selection == "over" else (sh + sa < 3.5)
    if market == "btts":
        btts = sh > 0 and sa > 0
        return btts if selection == "yes" else not btts
    if market == "double_chance":
        if selection == "1x": return sh >= sa   # home win or draw
        if selection == "x2": return sa >= sh   # draw or away win
        if selection == "12": return sh != sa   # either team wins (no draw)
    if market == "draw_no_bet":
        if sh == sa: return None                # draw → void, stake returned
        if selection == "home": return sh > sa
        if selection == "away": return sh < sa
    if market == "asian_handicap":
        # selection format: "Home -1.5" or "Away +0.5"
        parts = selection.split()
        if len(parts) != 2:
            return False
        side = parts[0].lower()
        try:
            line = float(parts[1])
        except ValueError:
            return False
        # positive margin = home team is ahead
        margin = (sh - sa) if side == "home" else (sa - sh)
        adjusted = margin + line  # positive = win for our selection
        if adjusted > 0: return True
        if adjusted == 0: return None  # push → void
        return False
    return False


def _load_matches(date_from: str, date_to: str, limit: int | None) -> list[dict]:
    where = ["m.status = 'finished'", "m.score_home IS NOT NULL", "m.date >= %s", "m.date <= %s"]
    params: list = [f"{date_from}T00:00:00", f"{date_to}T23:59:59"]
    sql = (
        "SELECT m.id AS match_id, m.date, m.score_home, m.score_away, "
        "       m.season, m.league_id, m.home_team_id, m.away_team_id, "
        "       l.name AS league_name, l.country, l.tier, "
        "       ht.name AS home_team, at.name AS away_team "
        "FROM matches m "
        "JOIN leagues l ON l.id = m.league_id "
        "JOIN teams ht ON ht.id = m.home_team_id "
        "JOIN teams at ON at.id = m.away_team_id "
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

    out: dict[str, dict] = defaultdict(dict)
    for r in rows:
        mid = r["match_id"]
        m = r["market"]
        prob = float(r["model_probability"]) if r["model_probability"] is not None else None
        if prob is None:
            continue
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
        elif m == "double_chance":
            if sel == "1x": out[mid]["odds_dc_1x"] = float(r["odds"])
            elif sel == "x2": out[mid]["odds_dc_x2"] = float(r["odds"])
            elif sel == "12": out[mid]["odds_dc_12"] = float(r["odds"])
    return dict(out)


def _load_ah_odds(match_ids: list[str]) -> dict[str, list[dict]]:
    """Best pre-kickoff AH odds per (match, selection), skipping quarter lines."""
    if not match_ids:
        return {}
    placeholders = ",".join(["%s"] * len(match_ids))
    sql = (
        "SELECT os.match_id, os.selection, MAX(os.odds) AS odds "
        "FROM odds_snapshots os "
        "JOIN matches m ON m.id = os.match_id "
        f"WHERE os.match_id IN ({placeholders}) "
        "  AND os.market = 'asian_handicap' "
        "  AND os.is_live = false "
        "  AND os.timestamp < m.date "
        "GROUP BY os.match_id, os.selection"
    )
    rows = execute_query(sql, tuple(match_ids))
    out: dict[str, list] = defaultdict(list)
    for r in rows:
        sel_raw = (r["selection"] or "").strip()  # e.g. "Home -1.25"
        parts = sel_raw.split()
        if len(parts) != 2:
            continue
        side = parts[0].lower()
        if side not in ("home", "away"):
            continue
        try:
            line = float(parts[1])
        except ValueError:
            continue
        # AH-NO-QUARTER: skip ±.25 / ±.75 lines (Coolbet doesn't offer them)
        if abs(line % 0.5) == 0.25:
            continue
        out[r["match_id"]].append({
            "selection": side,
            "handicap_line": line,
            "odds": float(r["odds"]),
            "label": sel_raw,
        })
    return dict(out)


def _build_poisson_lookup(matches: list[dict]) -> dict[str, dict]:
    """
    Re-compute Poisson exp_home/exp_away for every match using the same
    targets_poisson_history.csv + targets_global.csv the live pipeline uses.
    Returns {match_id: {"exp_home": float, "exp_away": float}}.
    Mirrors pipeline startup: loads targets_global.csv + targets_extended.csv into hist_targets_global.
    """
    targets_path = PROCESSED_DIR / "targets_poisson_history.csv"
    if not targets_path.exists():
        targets_path = PROCESSED_DIR / "targets_fast.csv"
    hist_targets = pd.read_csv(targets_path)

    hist_targets_global = None
    global_path = PROCESSED_DIR / "targets_global.csv"
    if global_path.exists():
        hist_targets_global = pd.read_csv(global_path)

    extended_path = PROCESSED_DIR / "targets_extended.csv"
    if extended_path.exists():
        extended_df = pd.read_csv(extended_path)
        if hist_targets_global is not None:
            hist_targets_global = pd.concat([hist_targets_global, extended_df], ignore_index=True)
        else:
            hist_targets_global = extended_df

    v9_teams = set(hist_targets["home_team"].unique()) | set(hist_targets["away_team"].unique())
    global_teams: set | None = None
    if hist_targets_global is not None:
        global_teams = (
            set(hist_targets_global["home_team"].unique()) |
            set(hist_targets_global["away_team"].unique())
        )
    _team_sets = (v9_teams, global_teams)

    out: dict[str, dict] = {}
    for m in matches:
        p = compute_prediction(m, hist_targets, hist_targets_global, _team_sets=_team_sets)
        if p and p.get("exp_home") and p.get("exp_away"):
            out[m["match_id"]] = {"exp_home": p["exp_home"], "exp_away": p["exp_away"]}
    return out


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

    # Determine which market types are needed across active bots
    active_bots = [(name, cfg) for name, cfg in BOTS_CONFIG.items()
                   if args.bot_filter is None or name == args.bot_filter]
    needs_ah = any("ah" in cfg.get("markets", []) for _, cfg in active_bots)

    # Bulk-load predictions + odds in parallel chunks.
    # Chunk size 8000: larger than the old 4000 — fewer round-trips, Supabase handles it fine.
    # Predictions and odds are independent so we fire both in a thread pool per chunk.
    preds: dict[str, dict] = {}
    odds_lookup: dict[str, dict] = {}
    ah_odds_lookup: dict[str, list] = {}
    chunk = 8000
    chunks = [match_ids[i:i + chunk] for i in range(0, len(match_ids), chunk)]
    n_tasks = len(chunks) * (3 if needs_ah else 2)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]loading data"),
        BarColumn(bar_width=36),
        MofNCompleteColumn(),
        TextColumn("queries"),
        TimeElapsedColumn(),
        console=console,
    ) as load_bar:
        load_task = load_bar.add_task("load", total=n_tasks)

        def _fetch_preds(ids):
            result = _load_predictions(ids)
            load_bar.advance(load_task)
            return result

        def _fetch_odds(ids):
            result = _load_pre_kickoff_odds(ids)
            load_bar.advance(load_task)
            return result

        def _fetch_ah(ids):
            result = _load_ah_odds(ids)
            load_bar.advance(load_task)
            return result

        with ThreadPoolExecutor(max_workers=6) as pool:
            fut_preds = {pool.submit(_fetch_preds, ids): ids for ids in chunks}
            fut_odds  = {pool.submit(_fetch_odds,  ids): ids for ids in chunks}
            fut_ah    = ({pool.submit(_fetch_ah, ids): ids for ids in chunks}
                         if needs_ah else {})

            for fut in as_completed({**fut_preds, **fut_odds, **fut_ah}):
                result = fut.result()
                if fut in fut_preds:
                    preds.update(result)
                elif fut in fut_odds:
                    odds_lookup.update(result)
                else:
                    ah_odds_lookup.update(result)

    console.print(f"  Predictions for [green]{len(preds):,}[/green] / {len(matches):,} matches")
    console.print(f"  Pre-kickoff odds for [green]{len(odds_lookup):,}[/green] / {len(matches):,} matches")
    if needs_ah:
        console.print(f"  AH odds for {len(ah_odds_lookup):,} matches")

    # For AH: re-compute Poisson exp_home/exp_away from hist_targets CSVs.
    # PERF-AH-SKIP-EMPTY (2026-05-19): only build the Poisson lookup for matches
    # that ACTUALLY have AH odds. Without this we paid ~15min of fuzzy-team-match
    # + DataFrame-scan across 28k matches even when ah_odds_lookup was empty
    # (football-data ingest doesn't ship AH lines → all-zero coverage).
    poisson_lookup: dict[str, dict] = {}
    if needs_ah and ah_odds_lookup:
        matches_with_ah = [m for m in matches if m["match_id"] in ah_odds_lookup]
        console.print(
            f"[cyan]Pre-computing Poisson goals for {len(matches_with_ah):,} matches "
            f"with AH odds (uses targets CSVs)…[/cyan]"
        )
        poisson_lookup = _build_poisson_lookup(matches_with_ah)
        console.print(f"  Poisson computed for {len(poisson_lookup):,} / {len(matches_with_ah):,} matches")
    elif needs_ah:
        console.print(
            "[yellow]AH bots active but no AH odds in scope — "
            "skipping the Poisson pre-compute pass entirely.[/yellow]"
        )

    # DC rho cache (needed for AH _ah_model_prob)
    dc_rho_cache: dict = _load_dc_rho_cache() if needs_ah else {}

    if not active_bots:
        console.print(f"[red]No bots matched filter {args.bot_filter}[/red]")
        return

    rows_out: list[dict] = []
    summary: dict[str, dict] = defaultdict(lambda: {"n_bets": 0, "wins": 0, "voids": 0, "stake": 0.0, "pnl": 0.0})
    _bets_found = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]backtest"),
        BarColumn(bar_width=36),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
        TextColumn("•  [yellow]{task.fields[cur_date]}[/yellow]  [green]{task.fields[bets_found]} bets[/green]"),
        console=console,
        refresh_per_second=8,
    ) as bar:
        task = bar.add_task("walk", total=len(matches),
                            cur_date=str(matches[0]["date"])[:10] if matches else "",
                            bets_found=0)

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
                bar.update(task, cur_date=str(m["date"])[:10])
                continue

            for bot_name, cfg in active_bots:
                if cfg.get("tier_filter") and tier not in cfg["tier_filter"]:
                    continue
                if cfg.get("league_filter") and country not in cfg["league_filter"]:
                    continue

                thresholds = cfg["edge_thresholds"].get(tier, {})
                odds_min, odds_max = cfg["odds_range"]
                min_prob = cfg["min_prob"]

                # Build candidate list — start with flat CANDIDATE_SPECS markets
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

                # Double Chance — probs derived from 1x2 (same as live pipeline)
                if "dc" in cfg.get("markets", []):
                    hp = pred.get("home_prob")
                    dp = pred.get("draw_prob")
                    ap = pred.get("away_prob")
                    if hp is not None and dp is not None and ap is not None:
                        dc_threshold = thresholds.get("dc", 0.04)
                        for dc_label, dc_db_sel, dc_prob, odds_field in [
                            ("1X", "1x", hp + dp, "odds_dc_1x"),
                            ("X2", "x2", dp + ap, "odds_dc_x2"),
                            ("12", "12", hp + ap, "odds_dc_12"),
                        ]:
                            odds = match_odds.get(odds_field, 0)
                            if odds <= 0:
                                continue
                            ip = 1 / odds
                            edge = dc_prob - ip
                            if edge >= dc_threshold and odds_min <= odds <= odds_max and dc_prob >= min_prob:
                                cands.append(("dc", dc_label, odds, dc_prob, ip, edge, "double_chance", dc_db_sel))

                # Draw No Bet — probs and odds derived from 1x2 (same as live pipeline)
                if "dnb" in cfg.get("markets", []):
                    h_odds = match_odds.get("odds_home", 0)
                    a_odds = match_odds.get("odds_away", 0)
                    hp = pred.get("home_prob", 0) or 0
                    ap = pred.get("away_prob", 0) or 0
                    denom = hp + ap
                    if h_odds > 0 and a_odds > 0 and denom > 0:
                        dnb_threshold = thresholds.get("dnb", 0.05)
                        dnb_h_prob = hp / denom
                        dnb_a_prob = ap / denom
                        dnb_h_odds = (a_odds + h_odds) / a_odds
                        dnb_a_odds = (a_odds + h_odds) / h_odds
                        for dnb_sel, dnb_prob, dnb_odds in [
                            ("home", dnb_h_prob, dnb_h_odds),
                            ("away", dnb_a_prob, dnb_a_odds),
                        ]:
                            if dnb_odds <= 0:
                                continue
                            ip = 1 / dnb_odds
                            edge = dnb_prob - ip
                            if edge >= dnb_threshold and odds_min <= dnb_odds <= odds_max and dnb_prob >= min_prob:
                                cands.append(("dnb", dnb_sel.capitalize(), dnb_odds, dnb_prob, ip, edge, "draw_no_bet", dnb_sel))

                # Asian Handicap — Poisson _ah_model_prob (same as live pipeline)
                if "ah" in cfg.get("markets", []):
                    poisson = poisson_lookup.get(mid)
                    if poisson:
                        _exp_h = poisson["exp_home"]
                        _exp_a = poisson["exp_away"]
                        _tier_rho = dc_rho_cache.get(tier)
                        ah_threshold = thresholds.get("ah", 0.05)
                        for ah_line in ah_odds_lookup.get(mid, []):
                            _sel = ah_line["selection"]
                            _hl = ah_line["handicap_line"]
                            _odds = ah_line["odds"]
                            _ah_prob = _ah_model_prob(_exp_h, _exp_a, _sel, _hl, rho=_tier_rho)
                            _sel_label = f"{_sel.capitalize()} {_hl:+.4g}"
                            ip = 1 / _odds
                            edge = _ah_prob - ip
                            if edge >= ah_threshold and odds_min <= _odds <= odds_max and _ah_prob >= min_prob:
                                cands.append(("ah", _sel_label, _odds, _ah_prob, ip, edge, "asian_handicap", _sel_label))

                # Top edge wins (live pipeline does the same: sort, place top)
                cands.sort(key=lambda c: c[5], reverse=True)
                if not cands:
                    continue
                # Live pipeline places multiple top-edge bets — for backtest, take only top-1 per match per bot
                # (cleaner ROI calc; "best edge" is what matters for "did this bot ever have edge?")
                mkt_key, selection, odds, prob, ip, edge, db_market, db_sel = cands[0]
                won = _outcome(db_market, db_sel, sh, sa)
                if won is None:
                    pnl = 0.0  # void — stake returned
                else:
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
                if won is None:
                    s["voids"] += 1
                elif won:
                    s["wins"] += 1
                s["stake"] += args.stake
                s["pnl"] += pnl

            _bets_found += 1 if rows_out and rows_out[-1].get("match_id") == mid else 0
            bar.update(task, advance=1,
                       cur_date=str(m["date"])[:10],
                       bets_found=len(rows_out))

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
        t.add_column("Voids", justify="right")
        t.add_column("Win %", justify="right")
        t.add_column("PnL", justify="right")
        t.add_column("ROI %", justify="right")
        for bot, s in sorted(summary.items(), key=lambda kv: -kv[1]["pnl"]):
            n = s["n_bets"]
            settled = n - s["voids"]
            wp = (s["wins"] / settled * 100) if settled else 0
            roi = (s["pnl"] / s["stake"] * 100) if s["stake"] else 0
            colour = "green" if roi > 0 else "red"
            t.add_row(bot, str(n), str(s["wins"]), str(s["voids"]), f"{wp:.1f}",
                      f"[{colour}]{s['pnl']:+.2f}[/{colour}]",
                      f"[{colour}]{roi:+.1f}[/{colour}]")
        console.print(t)


if __name__ == "__main__":
    main()
