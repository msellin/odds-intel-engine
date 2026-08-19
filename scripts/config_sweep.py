#!/usr/bin/env python3
"""
CONFIG-SWEEP-2026-08-19 — Parameter grid sweep to find profitable bot configs.

Walk-forward backtest of a theory-driven parameter grid over historical data
(2026-05-01 → today-7d). Reports configs that are consistently positive
across all three test windows.

Design: dev/active/config-sweep-2026-08-19-plan.md

Usage:
    python3 scripts/config_sweep.py                        # full sweep
    python3 scripts/config_sweep.py --markets 1x2_home     # single market
    python3 scripts/config_sweep.py --dry-run              # just load data
"""
from __future__ import annotations

import argparse
import csv
import itertools
import sys
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import execute_query
from workers.jobs.daily_pipeline_v2 import ACCESSIBLE_BOOKMAKERS


# --- Parameter grid (see plan doc for justification) ---------------------

MARKETS: list[str] = [
    "1x2_home", "1x2_draw", "1x2_away",
    "over_under_25_over", "over_under_25_under",
    "btts_yes", "btts_no",
]
EDGE_THRESHOLDS: list[float] = [0.03, 0.05, 0.07, 0.10]
ODDS_MIN: list[float] = [1.30, 1.50, 2.00]
ODDS_MAX: list[float] = [2.50, 3.50, 5.00]
MIN_PROB: list[float] = [0.25, 0.35, 0.45]
TIER_FILTERS: list[tuple[int, ...]] = [
    (1,), (1, 2), (1, 2, 3), (1, 2, 3, 4), (2, 3),
]
REQUIRE_PINNACLE: list[bool] = [True, False]

# --- Walk-forward windows ------------------------------------------------

WINDOWS: list[tuple[str, str, str]] = [
    ("W1", "2026-05-01", "2026-06-15"),
    ("W2", "2026-06-16", "2026-07-31"),
    ("W3", "2026-08-01", "2026-08-12"),
]

# --- Acceptance thresholds ----------------------------------------------

MIN_N_PER_WINDOW = 30
MIN_ROI_AGGREGATE = 0.05           # 5% overall
FANTASY_RATIO_CAP = 1.65            # drop rows where pick > 1.65× close


@dataclass
class Config:
    market: str
    edge_threshold: float
    odds_min: float
    odds_max: float
    min_prob: float
    tier_filter: tuple[int, ...]
    require_pinnacle: bool

    def key(self) -> str:
        tier_str = "-".join(str(t) for t in self.tier_filter)
        return (
            f"{self.market}|e{self.edge_threshold}|o{self.odds_min}-{self.odds_max}|"
            f"p{self.min_prob}|t{tier_str}|pin{int(self.require_pinnacle)}"
        )


@dataclass
class WindowResult:
    n: int
    won: int
    roi: float
    clv_mean: float
    total_pnl: float
    hit_rate: float


# --- Data loading --------------------------------------------------------

def load_backtest_frame(start: str, end: str) -> pd.DataFrame:
    """Load a flat DataFrame with one row per (match × market × selection)
    that has ensemble prob + best accessible odds. Includes actual result
    columns needed to compute won/lost, and closing_odds for CLV + fantasy
    filter.
    """
    print(f"[cyan]Loading matches {start} → {end}...", flush=True)

    # Matches with a finished status and a score
    matches_rows = execute_query(
        """
        SELECT m.id::text AS match_id, m.date, m.score_home, m.score_away,
               COALESCE(l.tier, 1) AS tier
          FROM matches m
          LEFT JOIN leagues l ON m.league_id = l.id
         WHERE m.date >= %s AND m.date < %s
           AND m.status = 'finished'
           AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
        """,
        (f"{start}T00:00:00Z", f"{end}T23:59:59Z"),
    )
    matches = pd.DataFrame(matches_rows or [])
    if matches.empty:
        return pd.DataFrame()
    print(f"  {len(matches)} finished matches", flush=True)
    matches["date"] = pd.to_datetime(matches["date"], utc=True)
    matches["score_home"] = matches["score_home"].astype(int)
    matches["score_away"] = matches["score_away"].astype(int)

    # Ensemble predictions — only the markets we care about
    pred_rows = execute_query(
        """
        SELECT p.match_id::text, p.market, p.model_probability
          FROM predictions p
          JOIN matches m ON p.match_id = m.id
         WHERE m.date >= %s AND m.date < %s
           AND m.status = 'finished'
           AND p.source = 'ensemble'
           AND p.market IN (
               '1x2_home','1x2_draw','1x2_away',
               'over_under_25_over','over_under_25_under',
               'btts_yes','btts_no'
           )
        """,
        (f"{start}T00:00:00Z", f"{end}T23:59:59Z"),
    )
    preds = pd.DataFrame(pred_rows or [])
    if preds.empty:
        return pd.DataFrame()
    preds["model_probability"] = preds["model_probability"].astype(float)
    print(f"  {len(preds)} ensemble predictions", flush=True)

    # Odds — accessible books only, non-closing. Group by (match, market,
    # selection) and take the max as the best odds a bettor could reach.
    # Also track whether Pinnacle quoted the market (for require_pinnacle
    # gate). "Selection" in odds_snapshots is 'home'/'draw'/'away'/'over'/
    # 'under'/'yes'/'no'; the market key includes the OU line.
    accessible_list = list(ACCESSIBLE_BOOKMAKERS)
    odds_rows = execute_query(
        """
        SELECT o.match_id::text, o.market, o.selection, o.odds, o.bookmaker
          FROM odds_snapshots o
          JOIN matches m ON o.match_id = m.id
         WHERE m.date >= %s AND m.date < %s
           AND m.status = 'finished'
           AND o.is_closing = false
           AND o.market IN ('1x2', 'over_under_25', 'btts')
           AND o.bookmaker = ANY(%s)
        """,
        (f"{start}T00:00:00Z", f"{end}T23:59:59Z", accessible_list),
    )
    odds = pd.DataFrame(odds_rows or [])
    if odds.empty:
        return pd.DataFrame()
    odds["odds"] = odds["odds"].astype(float)
    print(f"  {len(odds)} pre-match odds rows (accessible books only)", flush=True)

    # Best accessible odds per (match, market, selection)
    best_odds = (
        odds.groupby(["match_id", "market", "selection"])["odds"]
        .max()
        .reset_index()
        .rename(columns={"odds": "odds_at_pick"})
    )
    # Pinnacle presence per (match, market, selection)
    pin_flag = (
        odds[odds["bookmaker"] == "Pinnacle"]
        .groupby(["match_id", "market", "selection"])
        .size()
        .reset_index(name="pin_count")
    )
    best_odds = best_odds.merge(pin_flag, on=["match_id", "market", "selection"], how="left")
    best_odds["pinnacle_present"] = best_odds["pin_count"].notna()
    best_odds = best_odds.drop(columns=["pin_count"])

    # Convert (market, selection) into the composite market key used by
    # predictions: 1x2 + home → 1x2_home; over_under_25 + over → over_under_25_over.
    def _mkt_key(row):
        return f"{row['market']}_{row['selection']}"
    best_odds["mkt_key"] = best_odds.apply(_mkt_key, axis=1)

    # Closing odds — one per (match, market, selection). Take Pinnacle
    # close if available (sharpest), else max accessible close.
    close_rows = execute_query(
        """
        SELECT o.match_id::text, o.market, o.selection, o.odds, o.bookmaker
          FROM odds_snapshots o
          JOIN matches m ON o.match_id = m.id
         WHERE m.date >= %s AND m.date < %s
           AND m.status = 'finished'
           AND o.is_closing = true
           AND o.market IN ('1x2', 'over_under_25', 'btts')
           AND o.bookmaker = ANY(%s)
        """,
        (f"{start}T00:00:00Z", f"{end}T23:59:59Z", accessible_list),
    )
    closes = pd.DataFrame(close_rows or [])
    if not closes.empty:
        closes["odds"] = closes["odds"].astype(float)
        # Prefer Pinnacle close where present
        pin_close = closes[closes["bookmaker"] == "Pinnacle"].groupby(
            ["match_id", "market", "selection"]
        )["odds"].mean().reset_index().rename(columns={"odds": "close_pin"})
        acc_close = closes.groupby(
            ["match_id", "market", "selection"]
        )["odds"].median().reset_index().rename(columns={"odds": "close_med"})
        close_join = acc_close.merge(pin_close, on=["match_id", "market", "selection"], how="left")
        close_join["closing_odds"] = close_join["close_pin"].fillna(close_join["close_med"])
        close_join = close_join[["match_id", "market", "selection", "closing_odds"]]
        best_odds = best_odds.merge(close_join, on=["match_id", "market", "selection"], how="left")
    else:
        best_odds["closing_odds"] = np.nan

    # Join preds ↔ odds via (match_id, mkt_key <-> market)
    preds_r = preds.rename(columns={"market": "mkt_key"})
    df = best_odds.merge(preds_r, on=["match_id", "mkt_key"], how="inner")

    # Join with match info (date + score + tier)
    df = df.merge(matches, on="match_id", how="inner")

    # Compute derived columns
    df["edge"] = df["odds_at_pick"] * df["model_probability"] - 1.0
    df["ratio_close"] = df["odds_at_pick"] / df["closing_odds"]

    # Compute per-row won (0/1) from market + selection + score
    def _won(row):
        m = row["market"]
        sel = row["selection"]
        h, a = row["score_home"], row["score_away"]
        if m == "1x2":
            if sel == "home":
                return int(h > a)
            if sel == "draw":
                return int(h == a)
            if sel == "away":
                return int(a > h)
        if m == "over_under_25":
            total = h + a
            if sel == "over":
                return int(total > 2.5)
            if sel == "under":
                return int(total < 2.5)
        if m == "btts":
            if sel == "yes":
                return int(h > 0 and a > 0)
            if sel == "no":
                return int(h == 0 or a == 0)
        return None
    df["won"] = df.apply(_won, axis=1)
    df = df.dropna(subset=["won"])
    df["won"] = df["won"].astype(int)

    print(f"  {len(df)} evaluable (match × market × selection) rows", flush=True)
    return df


# --- Sweep --------------------------------------------------------------

def evaluate(df_window: pd.DataFrame, cfg: Config, stake: float = 10.0) -> WindowResult:
    """Filter df_window to rows matching cfg, aggregate ROI/CLV/n."""
    mask = (
        (df_window["mkt_key"] == cfg.market)
        & (df_window["tier"].isin(cfg.tier_filter))
        & (df_window["odds_at_pick"].between(cfg.odds_min, cfg.odds_max))
        & (df_window["model_probability"] >= cfg.min_prob)
        & (df_window["edge"] >= cfg.edge_threshold)
    )
    if cfg.require_pinnacle:
        mask = mask & df_window["pinnacle_present"]
    sub = df_window[mask]
    n = len(sub)
    if n == 0:
        return WindowResult(n=0, won=0, roi=np.nan, clv_mean=np.nan, total_pnl=0.0, hit_rate=np.nan)
    won = int(sub["won"].sum())
    # P&L: won → stake × (odds - 1); lost → -stake
    pnl = float((sub["won"] * (sub["odds_at_pick"] - 1) * stake - (1 - sub["won"]) * stake).sum())
    roi = pnl / (n * stake)
    close_valid = sub[sub["closing_odds"].notna() & (sub["closing_odds"] > 0)]
    clv_mean = float(np.mean(close_valid["odds_at_pick"] / close_valid["closing_odds"] - 1)) if len(close_valid) else np.nan
    hit_rate = won / n
    return WindowResult(n=n, won=won, roi=roi, clv_mean=clv_mean, total_pnl=pnl, hit_rate=hit_rate)


def enumerate_grid() -> list[Config]:
    out: list[Config] = []
    for m, e, omin, omax, mp, tf, rp in itertools.product(
        MARKETS, EDGE_THRESHOLDS, ODDS_MIN, ODDS_MAX, MIN_PROB, TIER_FILTERS, REQUIRE_PINNACLE
    ):
        if omin >= omax:
            continue
        out.append(Config(m, e, omin, omax, mp, tf, rp))
    return out


def run_sweep(dry_run: bool = False) -> None:
    end_date = (date.today() - timedelta(days=7)).isoformat()
    df = load_backtest_frame("2026-05-01", end_date)
    if df.empty:
        print("[red]no data — abort[/red]")
        return

    pre = len(df)
    df = df[~((df["ratio_close"] >= FANTASY_RATIO_CAP) & df["closing_odds"].notna())]
    print(f"[dim]dropped {pre - len(df)} fantasy-price rows (ratio >= {FANTASY_RATIO_CAP}×){pre}→{len(df)}[/dim]", flush=True)

    if dry_run:
        print(f"[green]dry-run: {len(df)} evaluable rows, {len(enumerate_grid())} configs[/green]")
        return

    configs = enumerate_grid()
    print(f"[cyan]running sweep: {len(configs)} configs × {len(WINDOWS)} windows = {len(configs)*len(WINDOWS)} evaluations[/cyan]", flush=True)

    # Pre-split df per window for speed
    df["date"] = pd.to_datetime(df["date"], utc=True)
    win_dfs: dict[str, pd.DataFrame] = {}
    for wname, ws, we in WINDOWS:
        ws_ts = pd.Timestamp(ws, tz="UTC")
        we_ts = pd.Timestamp(we, tz="UTC") + pd.Timedelta(days=1)
        win_dfs[wname] = df[(df["date"] >= ws_ts) & (df["date"] < we_ts)]
        print(f"  {wname}: {len(win_dfs[wname])} rows", flush=True)

    rows: list[dict] = []
    for i, cfg in enumerate(configs):
        row = {**asdict(cfg), "tier_filter": "-".join(str(t) for t in cfg.tier_filter)}
        agg_n = 0
        agg_pnl = 0.0
        agg_stake = 0.0
        agg_clv_num = 0.0
        agg_clv_den = 0
        pass_windows = 0
        for wname, _, _ in WINDOWS:
            r = evaluate(win_dfs[wname], cfg)
            row[f"{wname}_n"] = r.n
            row[f"{wname}_roi"] = round(r.roi, 4) if not np.isnan(r.roi) else None
            row[f"{wname}_clv"] = round(r.clv_mean, 4) if not np.isnan(r.clv_mean) else None
            row[f"{wname}_hit"] = round(r.hit_rate, 4) if not np.isnan(r.hit_rate) else None
            if r.n >= MIN_N_PER_WINDOW and r.roi >= 0:
                pass_windows += 1
            if r.n > 0:
                agg_n += r.n
                agg_pnl += r.total_pnl
                agg_stake += r.n * 10.0
                if not np.isnan(r.clv_mean):
                    agg_clv_num += r.clv_mean * r.n
                    agg_clv_den += r.n
        row["agg_n"] = agg_n
        row["agg_roi"] = round(agg_pnl / agg_stake, 4) if agg_stake > 0 else None
        row["agg_clv"] = round(agg_clv_num / agg_clv_den, 4) if agg_clv_den > 0 else None
        row["windows_positive"] = pass_windows
        rows.append(row)
        if (i + 1) % 500 == 0:
            print(f"  [{i+1}/{len(configs)}] configs evaluated...", flush=True)

    # Write full results
    out_csv = Path(__file__).parent.parent / "dev/active/config-sweep-2026-08-19-results.csv"
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"[green]wrote {len(rows)} rows to {out_csv}[/green]")

    # Acceptance filter
    df_res = pd.DataFrame(rows)
    winners = df_res[
        (df_res["windows_positive"] == 3)
        & (df_res["agg_roi"] >= MIN_ROI_AGGREGATE)
        & (df_res["agg_clv"] >= 0)
    ].sort_values("agg_clv", ascending=False)

    print("")
    print(f"[bold]{'='*80}")
    print(f"WINNERS (positive in all 3 windows AND agg ROI ≥ {MIN_ROI_AGGREGATE*100:.0f}% AND CLV ≥ 0)")
    print(f"{'='*80}[/bold]")
    if winners.empty:
        print("[yellow]No config passes all acceptance criteria — negative result.[/yellow]")
        print("[yellow]This means current bots are close to the frontier or 4mo isn't enough data.[/yellow]")
    else:
        print(f"[green]{len(winners)} config(s) passed. Top 20 by aggregate CLV:[/green]")
        cols = ["market", "edge_threshold", "odds_min", "odds_max", "min_prob", "tier_filter",
                "require_pinnacle", "agg_n", "agg_roi", "agg_clv",
                "W1_n", "W1_roi", "W2_n", "W2_roi", "W3_n", "W3_roi"]
        print(winners[cols].head(20).to_string(index=False))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Load data and count configs; do not run sweep")
    args = p.parse_args()
    run_sweep(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
