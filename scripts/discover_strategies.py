"""
Stage 1: Strategy discovery — where is our model edge actually real?

Two modes:
  CSV mode (default): uses backtest CSVs in dev/active/. Fast, ~58K rows,
    limited features (market/odds/edge/tier/country/season).
  DB mode (--db): queries predictions + matches + match_feature_vectors from
    Supabase. Covers ALL finished matches with model predictions, not just
    bot-filtered candidates. Includes AF features: ELO, form, model
    disagreement, steam moves, injuries, news impact. ~100K+ rows.

Three outputs:
  1. Segment table: ROI by market / tier / odds range / edge band (no ML —
     just group-by + filter). Most actionable. Deduped to one row per unique
     (match, market, selection) opportunity.
  2. Feature importance: XGBoost regression trained to predict P&L per unit.
     Train on pre-2025, test on 2025+. Shows which features predict real edge.
  3. Bot config hints: top profitable segments rephrased as config parameters.

Usage:
    python3 scripts/discover_strategies.py
    python3 scripts/discover_strategies.py --min-bets 50
    python3 scripts/discover_strategies.py --db
    python3 scripts/discover_strategies.py --db --from 2024-01-01
    python3 scripts/discover_strategies.py --out dev/active/discovery-report.txt
"""

from __future__ import annotations

import argparse
import sys
import warnings
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent.parent))

console = Console()

ROOT = Path(__file__).parent.parent
CSV_3YEAR   = ROOT / "dev/active/backtest-3year.csv"
CSV_4WEEK   = ROOT / "dev/active/backtest-retired-4weeks.csv"
CSV_FD      = ROOT / "dev/active/backtest-football-data.csv"  # from backtest_football_data.py
DEFAULT_OUT = ROOT / "dev/active/strategy-discovery-report.txt"

TRAIN_CUTOFF  = "2025-01-01"
TOP_N_LEAGUES = 25
TOP_N_COUNTRIES = 15

# Markets in display order
MARKET_ORDER = ["1x2", "btts", "over_under_25", "over_under_15",
                "over_under_35", "double_chance", "draw_no_bet", "asian_handicap"]

# ── Data loading ──────────────────────────────────────────────────────────────

def load_csv_data(include_fd: bool = False) -> pd.DataFrame:
    console.print("\n[bold]Loading backtest CSVs…[/bold]")
    dfs = []
    for path in [CSV_3YEAR, CSV_4WEEK]:
        if path.exists():
            df = pd.read_csv(path, parse_dates=["date"])
            console.print(f"  {path.name}: {len(df):,} rows")
            dfs.append(df)
        else:
            console.print(f"  [yellow]Not found: {path.name}[/yellow]")
    if include_fd:
        if CSV_FD.exists():
            df_fd = pd.read_csv(CSV_FD, parse_dates=["date"])
            console.print(f"  {CSV_FD.name}: {len(df_fd):,} rows [cyan](football-data)[/cyan]")
            dfs.append(df_fd)
        else:
            console.print(f"  [yellow]football-data CSV not found — run backtest_football_data.py first[/yellow]")
    if not dfs:
        console.print("[red]No CSV files found. Run backtest_pre_match_bots.py first.[/red]")
        sys.exit(1)
    df = pd.concat(dfs, ignore_index=True)
    # Normalize all dates to UTC-aware (original CSVs have +00:00, fd CSV is naive)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    console.print(f"  Combined: {len(df):,} rows")
    return df


def load_db_data(date_from: str, date_to: str) -> pd.DataFrame:
    from workers.api_clients.db import execute_query

    console.print(f"\n[bold]Querying DB ({date_from} → {date_to})…[/bold]")

    # One row per (match, market, selection) with outcome + model prediction
    # + match-level features. Covers all predictions, not just bot picks.
    # Use simulated_bets (real placement odds + settled outcomes) joined with
    # match_feature_vectors (AF features: ELO, form, model disagreement, etc.).
    # This gives genuine bet data — not prediction artifacts with stale odds.
    # CLV is included as a secondary target alongside ROI.
    sql = """
        SELECT
            sb.match_id,
            sb.pick_time            AS date,
            sb.market,
            sb.selection,
            sb.odds_at_pick         AS odds,
            sb.model_probability    AS model_prob,
            sb.edge_percent         AS edge,
            sb.result,
            sb.pnl,
            sb.stake,
            sb.clv,
            sb.clv_pinnacle,
            b.name                  AS bot,
            l.name                  AS league,
            l.country,
            l.tier,
            m.season,
            m.score_home,
            m.score_away,
            fv.elo_home, fv.elo_away, fv.elo_diff,
            fv.form_ppg_home, fv.form_ppg_away,
            fv.form_momentum_home, fv.form_momentum_away,
            fv.model_disagreement,
            fv.opening_implied_home, fv.opening_implied_draw, fv.opening_implied_away,
            fv.odds_drift_home,
            fv.steam_move,
            fv.news_impact_score,
            fv.injury_severity_home, fv.injury_severity_away,
            fv.lineup_confirmed
        FROM simulated_bets sb
        JOIN bots b           ON b.id  = sb.bot_id
        JOIN matches m        ON m.id  = sb.match_id
        JOIN leagues l        ON l.id  = m.league_id
        LEFT JOIN match_feature_vectors fv ON fv.match_id = sb.match_id
        WHERE sb.result IN ('won', 'lost')
          AND sb.market != 'combo'
          AND sb.pick_time >= %s
          AND sb.pick_time <= %s
        ORDER BY sb.pick_time ASC
    """
    rows = execute_query(sql, (f"{date_from}T00:00:00", f"{date_to}T23:59:59"))
    if not rows:
        console.print("[red]No settled bets found in simulated_bets for this range.[/red]")
        sys.exit(1)

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    console.print(f"  Settled bets: {len(df):,}")

    # PostgreSQL numeric → float
    numeric_cols = [
        "odds", "model_prob", "edge", "pnl", "stake", "clv", "clv_pinnacle",
        "score_home", "score_away",
        "elo_home", "elo_away", "elo_diff",
        "form_ppg_home", "form_ppg_away",
        "form_momentum_home", "form_momentum_away",
        "model_disagreement", "odds_drift_home",
        "news_impact_score", "injury_severity_home", "injury_severity_away",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # market / selection already in correct convention from simulated_bets
    df["market_base"] = df["market"]
    df["implied_prob"] = 1.0 / df["odds"].clip(lower=1.01)
    df["won"] = df["result"] == "won"

    return df




# ── Cleaning & feature engineering ───────────────────────────────────────────

def clean_and_dedup(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Normalise types
    if df["won"].dtype == object:
        df["won"] = df["won"].astype(str).str.lower().map({"true": True, "false": False})
    df["won"]         = df["won"].fillna(False).astype(bool)
    df["odds"]        = pd.to_numeric(df["odds"], errors="coerce")
    df["model_prob"]  = pd.to_numeric(df["model_prob"], errors="coerce")
    df["implied_prob"]= pd.to_numeric(df["implied_prob"], errors="coerce")
    df["edge"]        = pd.to_numeric(df["edge"], errors="coerce")
    df["tier"]        = pd.to_numeric(df.get("tier", 0), errors="coerce").fillna(0).astype(int)

    df = df.dropna(subset=["odds", "model_prob", "edge"])
    df = df[df["odds"] >= 1.01]
    df = df[df["edge"] > 0]   # only positive-edge candidates

    # Normalize market names — three writers, three conventions
    _MARKET_NORM = {
        "1X2": "1x2", "1x2": "1x2",
        "O/U": "over_under_25", "o/u": "over_under_25",
        "ou25": "over_under_25",
        "ou15": "over_under_15",
        "ou35": "over_under_35",
        "BTTS": "btts",
    }
    df["market"] = df["market"].map(lambda m: _MARKET_NORM.get(m, m))
    df["market_base"] = df["market"]  # reset market_base to normalized name

    # Deduplicate: one row per unique (match, market, selection)
    df = df.sort_values("edge", ascending=False)
    if "match_id" in df.columns:
        df = df.drop_duplicates(subset=["match_id", "market", "selection"])

    console.print(f"  After clean/dedup: {len(df):,} unique bet opportunities")
    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["return_per_unit"] = df["pnl"] / df["stake"]
    df["year"]  = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["is_train"] = df["date"] < pd.Timestamp(TRAIN_CUTOFF, tz="UTC")

    # Market normalisation: strip trailing _home/_draw/etc for display
    df["market_base"] = (
        df.get("market_base", df["market"])
        .str.replace(r"_(home|draw|away|over|under|yes|no)$", "", regex=True)
    )

    # Selection simplification
    df["sel_simple"] = (
        df["selection"].astype(str).str.lower()
        .str.replace(r"over \d+\.\d+", "over", regex=True)
        .str.replace(r"under \d+\.\d+", "under", regex=True)
    )
    df["sel_simple"] = df["sel_simple"].where(
        df["sel_simple"].isin(["home", "away", "draw", "over", "under", "yes", "no"]),
        "other"
    )

    # Bucketed versions for segment grouping
    df["odds_bucket"] = pd.cut(
        df["odds"],
        bins=[0, 1.50, 2.00, 3.00, 5.00, 999],
        labels=["1.0–1.5", "1.5–2.0", "2.0–3.0", "3.0–5.0", "5.0+"],
    )
    df["edge_bucket"] = pd.cut(
        df["edge"],
        bins=[-999, 0.05, 0.08, 0.12, 0.20, 999],
        labels=["<5%", "5–8%", "8–12%", "12–20%", "20%+"],
    )
    df["tier_group"] = df["tier"].map(
        {0: "T0/unk", 1: "T1", 2: "T2", 3: "T3", 4: "T4"}
    ).fillna("T4+")

    # Derived numeric features for ML
    df["is_favourite"]    = (df["odds"] < 2.0).astype(int)
    df["is_longshot"]     = (df["odds"] >= 3.0).astype(int)
    df["relative_edge"]   = df["edge"] / df["implied_prob"].clip(lower=0.01)
    df["log_odds"]        = np.log(df["odds"].clip(lower=1.01))
    df["is_home"]         = (df["sel_simple"] == "home").astype(int)
    df["is_away"]         = (df["sel_simple"] == "away").astype(int)
    df["is_draw"]         = (df["sel_simple"] == "draw").astype(int)
    df["is_ou"]           = df["market_base"].str.contains("over_under|ou").astype(int)
    df["is_btts"]         = df["market_base"].str.contains("btts").astype(int)
    df["is_1x2"]          = (df["market_base"] == "1x2").astype(int)
    df["tier_num"]        = df["tier"].clip(0, 4)

    # Country encoding (top N → keep, rest → "other")
    if "country" in df.columns:
        top_c = df["country"].value_counts().nlargest(TOP_N_COUNTRIES).index
        df["country_enc"] = df["country"].where(df["country"].isin(top_c), "other")

    # League encoding
    if "league" in df.columns:
        top_l = df["league"].value_counts().nlargest(TOP_N_LEAGUES).index
        df["league_enc"] = df["league"].where(df["league"].isin(top_l), "other")

    return df


# ── Segment analysis ──────────────────────────────────────────────────────────

def roi_stats(g: pd.DataFrame) -> dict:
    n = len(g)
    won = g["won"].sum()
    total_pnl = g["pnl"].sum()
    total_stake = g["stake"].sum()
    roi = total_pnl / total_stake * 100 if total_stake > 0 else np.nan
    avg_edge = g["edge"].mean() * 100
    avg_odds = g["odds"].mean()
    return {
        "n": n, "won": won,
        "hit_rate": won / n * 100 if n else 0,
        "roi": roi, "avg_edge": avg_edge, "avg_odds": avg_odds,
    }


def segment_analysis(df: pd.DataFrame, min_bets: int, train_only: bool = True) -> None:
    train = df[df["is_train"]]
    if train_only and len(train) < 100:
        console.print(f"  [yellow]Train set too small ({len(train)} rows) — using all data for segments.[/yellow]")
        train_only = False
    subset = train if train_only else df

    # ── Level 1: by market ──────────────────────────────────────────────────
    console.print(f"\n[bold cyan]== ROI by Market (n≥{min_bets}) ==[/bold cyan]")
    t = Table(show_header=True, header_style="bold")
    for col in ["Market", "N", "Won", "Hit%", "ROI%", "AvgEdge%", "AvgOdds"]:
        t.add_column(col, justify="right" if col != "Market" else "left")

    rows_mkt = []
    for mkt, g in subset.groupby("market_base"):
        s = roi_stats(g)
        if s["n"] >= min_bets:
            rows_mkt.append((mkt, s))
    rows_mkt.sort(key=lambda x: x[1]["roi"], reverse=True)
    for mkt, s in rows_mkt:
        roi_str = f"[green]+{s['roi']:.1f}%[/green]" if s["roi"] > 0 else f"[red]{s['roi']:.1f}%[/red]"
        t.add_row(mkt, str(s["n"]), str(int(s["won"])),
                  f"{s['hit_rate']:.1f}", roi_str,
                  f"{s['avg_edge']:.1f}", f"{s['avg_odds']:.2f}")
    console.print(t)

    # ── Level 2: market × tier ──────────────────────────────────────────────
    console.print(f"\n[bold cyan]== ROI by Market × Tier (n≥{min_bets}) ==[/bold cyan]")
    t2 = Table(show_header=True, header_style="bold")
    for col in ["Market", "Tier", "N", "Won", "ROI%", "AvgEdge%", "AvgOdds"]:
        t2.add_column(col, justify="right" if col not in ("Market", "Tier") else "left")

    rows_mt = []
    for (mkt, tier), g in subset.groupby(["market_base", "tier_group"]):
        s = roi_stats(g)
        if s["n"] >= min_bets:
            rows_mt.append((mkt, tier, s))
    rows_mt.sort(key=lambda x: x[2]["roi"], reverse=True)
    for mkt, tier, s in rows_mt[:30]:
        roi_str = f"[green]+{s['roi']:.1f}%[/green]" if s["roi"] > 0 else f"[red]{s['roi']:.1f}%[/red]"
        t2.add_row(mkt, tier, str(s["n"]), str(int(s["won"])),
                   roi_str, f"{s['avg_edge']:.1f}", f"{s['avg_odds']:.2f}")
    console.print(t2)

    # ── Level 3: market × tier × odds_bucket ───────────────────────────────
    console.print(f"\n[bold cyan]== ROI by Market × Tier × Odds range (n≥{min_bets}, top 20) ==[/bold cyan]")
    t3 = Table(show_header=True, header_style="bold")
    for col in ["Market", "Tier", "Odds", "N", "ROI%", "AvgEdge%"]:
        t3.add_column(col, justify="right" if col not in ("Market", "Tier", "Odds") else "left")

    rows_mto = []
    for (mkt, tier, odds_b), g in subset.groupby(["market_base", "tier_group", "odds_bucket"], observed=True):
        s = roi_stats(g)
        if s["n"] >= min_bets:
            rows_mto.append((mkt, str(tier), str(odds_b), s))
    rows_mto.sort(key=lambda x: x[3]["roi"], reverse=True)
    for mkt, tier, odds_b, s in rows_mto[:20]:
        roi_str = f"[green]+{s['roi']:.1f}%[/green]" if s["roi"] > 0 else f"[red]{s['roi']:.1f}%[/red]"
        t3.add_row(mkt, tier, odds_b, str(s["n"]), roi_str, f"{s['avg_edge']:.1f}")
    console.print(t3)

    # ── Level 4: edge calibration ───────────────────────────────────────────
    console.print(f"\n[bold cyan]== Edge Calibration: does more edge → more ROI? ==[/bold cyan]")
    t4 = Table(show_header=True, header_style="bold")
    for col in ["Edge band", "N", "ROI%", "AvgEdge%", "AvgOdds", "Note"]:
        t4.add_column(col, justify="right" if col not in ("Edge band", "Note") else "left")

    for edge_b, g in subset.groupby("edge_bucket", observed=True):
        s = roi_stats(g)
        if s["n"] < 20:
            continue
        note = "[green]calibrated[/green]" if s["roi"] > 0 else "[red]overclaiming edge[/red]"
        roi_str = f"[green]+{s['roi']:.1f}%[/green]" if s["roi"] > 0 else f"[red]{s['roi']:.1f}%[/red]"
        t4.add_row(str(edge_b), str(s["n"]), roi_str, f"{s['avg_edge']:.1f}",
                   f"{s['avg_odds']:.2f}", note)
    console.print(t4)

    # ── CLV by market (DB mode only — tells us bet quality, not just luck) ──
    if "clv" in df.columns and df["clv"].notna().sum() > 50:
        console.print(f"\n[bold cyan]== CLV by Market (positive = beat closing line) ==[/bold cyan]")
        tc = Table(show_header=True, header_style="bold")
        for col in ["Market", "N w/CLV", "Avg CLV%", "ROI%", "Note"]:
            tc.add_column(col, justify="right" if col not in ("Market", "Note") else "left")
        for mkt, g in subset.groupby("market_base"):
            clv_g = g["clv"].dropna()
            if len(clv_g) < 20:
                continue
            avg_clv = clv_g.mean() * 100
            s = roi_stats(g)
            note = "[green]skill[/green]" if avg_clv > 0 else "[red]no edge[/red]"
            roi_str = f"[green]+{s['roi']:.1f}%[/green]" if s["roi"] > 0 else f"[red]{s['roi']:.1f}%[/red]"
            clv_str = f"[green]+{avg_clv:.2f}%[/green]" if avg_clv > 0 else f"[red]{avg_clv:.2f}%[/red]"
            tc.add_row(mkt, str(len(clv_g)), clv_str, roi_str, note)
        console.print(tc)

    # ── Out-of-sample check ─────────────────────────────────────────────────
    oos = df[~df["is_train"]]
    if len(oos) > 100:
        console.print(f"\n[bold cyan]== Out-of-sample (2025+, {len(oos):,} bets) by Market ==[/bold cyan]")
        t5 = Table(show_header=True, header_style="bold")
        for col in ["Market", "N", "ROI%", "Note"]:
            t5.add_column(col, justify="right" if col not in ("Market", "Note") else "left")
        for mkt, g in oos.groupby("market_base"):
            s = roi_stats(g)
            if s["n"] < 20:
                continue
            note = "[green]holds OOS[/green]" if s["roi"] > 0 else "[red]degrades OOS[/red]"
            roi_str = f"[green]+{s['roi']:.1f}%[/green]" if s["roi"] > 0 else f"[red]{s['roi']:.1f}%[/red]"
            t5.add_row(mkt, str(s["n"]), roi_str, note)
        console.print(t5)


# ── ML feature importance ─────────────────────────────────────────────────────

CSV_FEATURES = [
    "edge", "log_odds", "model_prob", "relative_edge",
    "is_home", "is_away", "is_draw", "is_ou", "is_btts", "is_1x2",
    "is_favourite", "is_longshot",
    "tier_num", "month", "year",
]

DB_EXTRA_FEATURES = [
    "elo_diff", "elo_home", "elo_away",
    "form_ppg_home", "form_ppg_away",
    "form_momentum_home", "form_momentum_away",
    "model_disagreement",
    "odds_drift_home",
    "news_impact_score",
    "injury_severity_home", "injury_severity_away",
    "clv",          # closing line value — best signal of bet quality
    "clv_pinnacle", # Pinnacle-specific CLV
]


def ml_analysis(df: pd.DataFrame, use_db_features: bool) -> None:
    from xgboost import XGBRegressor

    console.print("\n[bold]== XGBoost Feature Importance ==[/bold]")
    console.print(f"  Target: return per unit stake (regression)")
    console.print(f"  Train: pre-{TRAIN_CUTOFF} | Test: {TRAIN_CUTOFF}+")

    feature_cols = list(CSV_FEATURES)
    if use_db_features:
        db_cols = [c for c in DB_EXTRA_FEATURES if c in df.columns]
        feature_cols += db_cols
        console.print(f"  DB features added: {db_cols}")

    # Country/league as label-encoded ints
    for col, enc_col in [("country", "country_enc"), ("league", "league_enc")]:
        if enc_col in df.columns:
            cats = df[enc_col].astype("category")
            df[f"{enc_col}_int"] = cats.cat.codes
            feature_cols.append(f"{enc_col}_int")

    # Drop features not present
    feature_cols = [c for c in feature_cols if c in df.columns]

    train = df[df["is_train"]].copy()
    test  = df[~df["is_train"]].copy()

    X_train = train[feature_cols].apply(pd.to_numeric, errors="coerce").fillna(0)
    y_train = train["return_per_unit"]
    X_test  = test[feature_cols].apply(pd.to_numeric, errors="coerce").fillna(0)
    y_test  = test["return_per_unit"]

    console.print(f"  Train: {len(X_train):,} rows | Test: {len(X_test):,} rows")
    if len(X_train) < 100:
        console.print("[yellow]  Too few rows for reliable ML — skipping.[/yellow]")
        return

    model = XGBRegressor(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=20,   # require 20 samples per leaf → anti-overfit
        reg_lambda=2.0,
        random_state=42,
        verbosity=0,
    )
    model.fit(X_train, y_train,
              eval_set=[(X_test, y_test)],
              verbose=False)

    train_roi = y_train.mean() * 100
    test_roi  = y_test.mean()  * 100
    console.print(f"  Actual train ROI: {train_roi:+.1f}%  |  test ROI: {test_roi:+.1f}%")

    # Feature importance by gain
    importance = model.get_booster().get_score(importance_type="gain")
    ranked = sorted(importance.items(), key=lambda x: x[1], reverse=True)

    t = Table(show_header=True, header_style="bold")
    t.add_column("Rank", justify="right")
    t.add_column("Feature")
    t.add_column("Gain", justify="right")
    t.add_column("Interpretation")

    interpretations = {
        "edge":               "Our claimed edge — is it real?",
        "log_odds":           "Odds level",
        "model_prob":         "Raw model confidence",
        "relative_edge":      "Edge as % of implied prob (sharp if high)",
        "is_btts":            "BTTS market",
        "is_ou":              "O/U market",
        "is_1x2":             "1X2 market",
        "is_home":            "Home selection",
        "is_away":            "Away selection",
        "is_draw":            "Draw selection",
        "is_favourite":       "Odds < 2.0",
        "is_longshot":        "Odds ≥ 3.0",
        "tier_num":           "League tier (1=top, 4=low)",
        "month":              "Month of season (seasonality)",
        "year":               "Year (model drift over time)",
        "elo_diff":           "ELO gap (quality mismatch)",
        "form_ppg_home":      "Home team recent form",
        "form_ppg_away":      "Away team recent form",
        "model_disagreement": "Poisson vs XGBoost gap",
        "odds_drift_home":    "Market moved since open",
        "news_impact_score":  "Injury/lineup news impact",
        "injury_severity_home": "Home injury load",
        "injury_severity_away": "Away injury load",
    }

    for i, (feat, gain) in enumerate(ranked[:20], 1):
        interp = interpretations.get(feat, "—")
        bar = "█" * max(1, int(gain / (ranked[0][1] / 20)))
        t.add_row(str(i), feat, f"{gain:.1f}", interp)
    console.print(t)

    # Optional SHAP (requires shap package)
    try:
        import shap
        console.print("\n  [dim]Computing SHAP values…[/dim]")
        explainer = shap.TreeExplainer(model)
        shap_vals = explainer.shap_values(X_train.sample(min(2000, len(X_train)), random_state=42))
        mean_abs_shap = np.abs(shap_vals).mean(axis=0)
        shap_ranked = sorted(zip(feature_cols, mean_abs_shap), key=lambda x: x[1], reverse=True)

        console.print("\n[bold cyan]  SHAP feature importance (mean |SHAP|):[/bold cyan]")
        for feat, val in shap_ranked[:15]:
            bar = "█" * max(1, int(val / shap_ranked[0][1] * 20))
            console.print(f"    {feat:<35} {bar} {val:.4f}")
    except ImportError:
        console.print("  [dim](shap not installed — showing gain importance only)[/dim]")

    return model, feature_cols, ranked


# ── Bot config hints ──────────────────────────────────────────────────────────

def suggest_configs(df: pd.DataFrame, min_bets: int) -> None:
    console.print("\n[bold cyan]== Bot Config Hints (profitable segments, pre-2025 train set) ==[/bold cyan]")
    console.print("[dim]  These are starting points for new bots — validate on OOS data before going live.[/dim]\n")

    train = df[df["is_train"]]
    oos   = df[~df["is_train"]]
    oos_roi = {}
    for (mkt, tier), g in oos.groupby(["market_base", "tier_group"]):
        if len(g) >= 20:
            oos_roi[(mkt, tier)] = (g["pnl"].sum() / g["stake"].sum()) * 100

    suggestions = []
    for (mkt, tier, odds_b), g in train.groupby(
            ["market_base", "tier_group", "odds_bucket"], observed=True):
        s = roi_stats(g)
        if s["n"] < min_bets or s["roi"] <= 0:
            continue
        oos_v = oos_roi.get((mkt, tier), None)
        suggestions.append({
            "market": mkt, "tier": tier, "odds": str(odds_b),
            "n_train": s["n"], "roi_train": s["roi"],
            "roi_oos": oos_v,
            "avg_edge": s["avg_edge"],
        })

    suggestions.sort(key=lambda x: (x["roi_oos"] or -999), reverse=True)

    t = Table(show_header=True, header_style="bold")
    for col in ["Market", "Tier", "Odds range", "N (train)", "ROI train", "ROI OOS", "AvgEdge%"]:
        t.add_column(col, justify="right" if col not in ("Market", "Tier", "Odds range") else "left")

    shown = 0
    for s in suggestions:
        roi_t = f"[green]+{s['roi_train']:.1f}%[/green]"
        if s["roi_oos"] is None:
            roi_o = "[dim]too few[/dim]"
        elif s["roi_oos"] > 0:
            roi_o = f"[green]+{s['roi_oos']:.1f}%[/green]"
        else:
            roi_o = f"[red]{s['roi_oos']:.1f}%[/red]"
        t.add_row(s["market"], s["tier"], s["odds"],
                  str(s["n_train"]), roi_t, roi_o, f"{s['avg_edge']:.1f}")
        shown += 1
        if shown >= 25:
            break

    console.print(t)
    console.print(
        "\n[bold]Reading guide:[/bold] 'ROI train' = in-sample. "
        "'ROI OOS' = out-of-sample (2025+). "
        "Only trust rows where BOTH are positive.\n"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy discovery — Stage 1")
    parser.add_argument("--db", action="store_true",
                        help="Query Supabase for all predictions + match features (slower)")
    parser.add_argument("--from", dest="date_from", default="2023-01-01",
                        help="Start date for DB mode (default: 2023-01-01)")
    parser.add_argument("--to", dest="date_to", default="2026-12-31",
                        help="End date for DB mode (default: 2026-12-31)")
    parser.add_argument("--min-bets", type=int, default=30,
                        help="Minimum bets in a segment to report (default: 30)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Save text report to this path")
    parser.add_argument("--no-ml", action="store_true",
                        help="Skip ML analysis (segment analysis only)")
    parser.add_argument("--fd", action="store_true",
                        help="Include football-data.co.uk CSV (run backtest_football_data.py first)")
    args = parser.parse_args()

    # Optionally capture output to file
    buf = StringIO() if args.out else None
    out_console = Console(file=buf, width=120) if buf else console

    mode = "DB (all predictions + AF features)" if args.db else "CSV (backtest files)"
    if getattr(args, "fd", False) and not args.db:
        mode += " + football-data"
    console.rule("[bold]Strategy Discovery — Stage 1[/bold]")
    console.print(f"  Mode: {mode}")
    console.print(f"  Min bets per segment: {args.min_bets}")
    console.print(f"  Train/test split: pre-{TRAIN_CUTOFF} / {TRAIN_CUTOFF}+")

    if args.db:
        df_raw = load_db_data(args.date_from, args.date_to)
    else:
        df_raw = load_csv_data(include_fd=getattr(args, "fd", False))

    df = clean_and_dedup(df_raw)
    df = engineer_features(df)

    train_n = df["is_train"].sum()
    test_n  = (~df["is_train"]).sum()
    console.print(f"\n  Train rows: {train_n:,}  |  Test rows: {test_n:,}")
    console.print(f"  Date range: {df['date'].min().date()} → {df['date'].max().date()}")
    console.print(f"  Markets: {sorted(df['market_base'].unique())}")

    segment_analysis(df, min_bets=args.min_bets)

    if not args.no_ml:
        ml_analysis(df, use_db_features=args.db)

    suggest_configs(df, min_bets=args.min_bets)

    if args.out:
        # Re-render without Rich markup to plain text
        plain = Console(file=(args.out.open("w")), width=120, highlight=False, markup=False)
        # Re-run with plain console — just print the summary to file
        args.out.write_text(buf.getvalue())
        console.print(f"\n[green]Report saved → {args.out}[/green]")

    console.rule("[bold]Done[/bold]")


if __name__ == "__main__":
    main()
