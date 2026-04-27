"""
OddsIntel — Bookmaker Sharpness Analysis (P5.1)
Analyzes the European Soccer Database (25K matches, 10 bookmakers) to determine
which bookmakers are sharpest (best predict outcomes via closing odds).

Produces:
  1. bookmaker_sharpness_rankings.csv — ranked by closing line accuracy
  2. Analysis of sharp money signal — when Pinnacle diverges from soft books
  3. Feature engineering: sharp_money_signal for model integration

Usage:
  python scripts/bookmaker_sharpness.py
"""

import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from rich.console import Console
from rich.table import Table
from scipy.stats import spearmanr

console = Console()
DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "raw" / "database.sqlite"
PROCESSED_DIR = DATA_DIR / "processed"

# Bookmaker codes → full names
BOOKMAKERS = {
    "B365": "Bet365",
    "BW": "bwin",
    "IW": "Interwetten",
    "LB": "Ladbrokes",
    "PS": "Pinnacle",
    "WH": "William Hill",
    "SJ": "Stan James",
    "VC": "VC Bet",
    "GB": "Gamebookers",
    "BS": "Blue Square",
}


def load_data() -> pd.DataFrame:
    """Load matches with all bookmaker odds from SQLite."""
    conn = sqlite3.connect(str(DB_PATH))

    query = """
    SELECT
        m.id, m.date, m.season,
        m.home_team_goal, m.away_team_goal,
        l.name as league,
        ht.team_long_name as home_team,
        at.team_long_name as away_team,
        m.B365H, m.B365D, m.B365A,
        m.BWH, m.BWD, m.BWA,
        m.IWH, m.IWD, m.IWA,
        m.LBH, m.LBD, m.LBA,
        m.PSH, m.PSD, m.PSA,
        m.WHH, m.WHD, m.WHA,
        m.SJH, m.SJD, m.SJA,
        m.VCH, m.VCD, m.VCA,
        m.GBH, m.GBD, m.GBA,
        m.BSH, m.BSD, m.BSA
    FROM Match m
    JOIN League l ON m.league_id = l.id
    JOIN Team ht ON m.home_team_api_id = ht.team_api_id
    JOIN Team at ON m.away_team_api_id = at.team_api_id
    WHERE m.home_team_goal IS NOT NULL
      AND m.away_team_goal IS NOT NULL
    ORDER BY m.date
    """

    df = pd.read_sql_query(query, conn)
    conn.close()

    # Compute result
    df["result"] = np.where(df["home_team_goal"] > df["away_team_goal"], "H",
                   np.where(df["home_team_goal"] < df["away_team_goal"], "A", "D"))
    df["total_goals"] = df["home_team_goal"] + df["away_team_goal"]
    df["date"] = pd.to_datetime(df["date"])

    console.print(f"[green]Loaded {len(df):,} matches, {df['league'].nunique()} leagues, "
                  f"{df['season'].nunique()} seasons[/green]")
    return df


def compute_implied_probs(df: pd.DataFrame, bk: str) -> pd.DataFrame:
    """Compute implied probabilities for a bookmaker, normalized to remove overround."""
    h_col, d_col, a_col = f"{bk}H", f"{bk}D", f"{bk}A"

    result = pd.DataFrame(index=df.index)
    result[f"{bk}_raw_h"] = 1 / df[h_col]
    result[f"{bk}_raw_d"] = 1 / df[d_col]
    result[f"{bk}_raw_a"] = 1 / df[a_col]

    # Overround
    overround = result[f"{bk}_raw_h"] + result[f"{bk}_raw_d"] + result[f"{bk}_raw_a"]

    # Normalize
    result[f"{bk}_prob_h"] = result[f"{bk}_raw_h"] / overround
    result[f"{bk}_prob_d"] = result[f"{bk}_raw_d"] / overround
    result[f"{bk}_prob_a"] = result[f"{bk}_raw_a"] / overround
    result[f"{bk}_overround"] = overround

    return result


def analyse_sharpness(df: pd.DataFrame):
    """
    Measure bookmaker sharpness via multiple metrics:
    1. Log-loss (calibration) — how well do closing odds predict outcomes?
    2. Overround — lower = sharper (less margin = more confident)
    3. Rank correlation — do their probabilities rank outcomes correctly?
    """
    console.print("\n[bold cyan]═══ Bookmaker Sharpness Analysis ═══[/bold cyan]")
    console.print(f"Dataset: {len(df):,} matches, 2008-2016, 11 European leagues\n")

    results = []

    for bk, name in BOOKMAKERS.items():
        h_col, d_col, a_col = f"{bk}H", f"{bk}D", f"{bk}A"

        # Filter to matches where this bookmaker has odds
        mask = df[h_col].notna() & df[d_col].notna() & df[a_col].notna()
        subset = df[mask].copy()

        if len(subset) < 100:
            continue

        probs = compute_implied_probs(subset, bk)

        # 1. Log-loss (lower = better calibrated)
        # For each match, the "true" outcome probability should be 1.0
        eps = 1e-10
        log_losses = []
        for idx in subset.index:
            r = subset.loc[idx, "result"]
            if r == "H":
                p = probs.loc[idx, f"{bk}_prob_h"]
            elif r == "D":
                p = probs.loc[idx, f"{bk}_prob_d"]
            else:
                p = probs.loc[idx, f"{bk}_prob_a"]
            log_losses.append(-np.log(max(p, eps)))

        avg_logloss = np.mean(log_losses)

        # 2. Average overround
        avg_overround = probs[f"{bk}_overround"].mean()

        # 3. Calibration: bin predicted probabilities and compare to actual frequencies
        # For home win as example
        pred_h = probs[f"{bk}_prob_h"].values
        actual_h = (subset["result"] == "H").astype(float).values

        # Brier score (lower = better)
        pred_d = probs[f"{bk}_prob_d"].values
        pred_a = probs[f"{bk}_prob_a"].values
        actual_d = (subset["result"] == "D").astype(float).values
        actual_a = (subset["result"] == "A").astype(float).values

        brier = np.mean(
            (pred_h - actual_h)**2 +
            (pred_d - actual_d)**2 +
            (pred_a - actual_a)**2
        )

        # 4. ROI if you always bet the favorite at this bookmaker
        fav_mask = (probs[f"{bk}_prob_h"] > probs[f"{bk}_prob_d"]) & (probs[f"{bk}_prob_h"] > probs[f"{bk}_prob_a"])
        fav_odds = subset.loc[fav_mask, h_col]
        fav_won = (subset.loc[fav_mask, "result"] == "H")
        fav_pnl = np.where(fav_won, fav_odds - 1, -1)
        fav_roi = fav_pnl.sum() / len(fav_pnl) if len(fav_pnl) > 0 else 0

        # 5. Coverage
        coverage = len(subset) / len(df)

        results.append({
            "code": bk,
            "name": name,
            "matches": len(subset),
            "coverage": coverage,
            "log_loss": avg_logloss,
            "brier_score": brier,
            "avg_overround": avg_overround,
            "fav_roi": fav_roi,
        })

    results_df = pd.DataFrame(results)

    # Rank by log-loss (lower = sharper)
    results_df = results_df.sort_values("log_loss")
    results_df["sharpness_rank"] = range(1, len(results_df) + 1)

    # Display
    t = Table(title="Bookmaker Sharpness Rankings (lower log-loss = sharper)")
    t.add_column("Rank", justify="right")
    t.add_column("Bookmaker")
    t.add_column("Code")
    t.add_column("Matches", justify="right")
    t.add_column("Coverage", justify="right")
    t.add_column("Log-Loss", justify="right")
    t.add_column("Brier", justify="right")
    t.add_column("Overround", justify="right")
    t.add_column("Fav ROI", justify="right")

    for _, row in results_df.iterrows():
        sharp_color = "green" if row["sharpness_rank"] <= 3 else "yellow" if row["sharpness_rank"] <= 6 else "red"
        t.add_row(
            f"[{sharp_color}]{row['sharpness_rank']}[/{sharp_color}]",
            f"[{sharp_color}]{row['name']}[/{sharp_color}]",
            row["code"],
            f"{row['matches']:,}",
            f"{row['coverage']:.0%}",
            f"{row['log_loss']:.4f}",
            f"{row['brier_score']:.4f}",
            f"{row['avg_overround']:.3f}",
            f"{row['fav_roi']:+.1%}",
        )

    console.print(t)

    # Save rankings
    out = PROCESSED_DIR / "bookmaker_sharpness_rankings.csv"
    results_df.to_csv(out, index=False)
    console.print(f"\n[dim]Rankings saved to {out}[/dim]")

    return results_df


def analyse_sharp_money_signal(df: pd.DataFrame, sharp_bk: str = "PS"):
    """
    Build and evaluate the sharp money signal:
    When Pinnacle's implied prob diverges from soft bookmaker average by >X%,
    does betting with Pinnacle produce positive ROI?
    """
    console.print(f"\n[bold cyan]═══ Sharp Money Signal Analysis (sharp={sharp_bk}) ═══[/bold cyan]")

    # Define soft bookmakers (everyone except Pinnacle)
    soft_bks = [bk for bk in BOOKMAKERS if bk != sharp_bk]

    # Compute Pinnacle implied probs
    h_col = f"{sharp_bk}H"
    mask = df[h_col].notna()

    # Also need at least 3 soft books with odds
    for bk in soft_bks:
        mask = mask & df[f"{bk}H"].notna()
    # Relax: need at least 3 soft books
    soft_count = sum(df[f"{bk}H"].notna() for bk in soft_bks)
    mask = df[h_col].notna() & (soft_count >= 3)

    subset = df[mask].copy()
    console.print(f"Matches with Pinnacle + 3+ soft books: {len(subset):,}")

    if len(subset) < 100:
        console.print("[red]Insufficient data for sharp money analysis[/red]")
        return

    # Compute sharp (Pinnacle) implied probs
    sharp_probs = compute_implied_probs(subset, sharp_bk)

    # Compute average soft bookmaker implied probs
    soft_prob_h_list = []
    soft_prob_d_list = []
    soft_prob_a_list = []

    for bk in soft_bks:
        bk_mask = subset[f"{bk}H"].notna()
        probs = compute_implied_probs(subset[bk_mask], bk)

        soft_prob_h_list.append(probs[f"{bk}_prob_h"].reindex(subset.index))
        soft_prob_d_list.append(probs[f"{bk}_prob_d"].reindex(subset.index))
        soft_prob_a_list.append(probs[f"{bk}_prob_a"].reindex(subset.index))

    soft_avg_h = pd.concat(soft_prob_h_list, axis=1).mean(axis=1)
    soft_avg_d = pd.concat(soft_prob_d_list, axis=1).mean(axis=1)
    soft_avg_a = pd.concat(soft_prob_a_list, axis=1).mean(axis=1)

    # Divergence: sharp - soft (positive = Pinnacle thinks more likely)
    subset["div_h"] = sharp_probs[f"{sharp_bk}_prob_h"] - soft_avg_h
    subset["div_d"] = sharp_probs[f"{sharp_bk}_prob_d"] - soft_avg_d
    subset["div_a"] = sharp_probs[f"{sharp_bk}_prob_a"] - soft_avg_a

    # Test: when Pinnacle thinks home is MORE likely than soft books
    # (div_h > threshold), does backing home at soft odds produce +ROI?
    console.print("\n[bold]Signal test: bet WITH Pinnacle divergence[/bold]")

    t = Table(title="Sharp Money Signal ROI by Divergence Threshold")
    t.add_column("Selection")
    t.add_column("Threshold")
    t.add_column("Bets", justify="right")
    t.add_column("Hit%", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("P&L", justify="right")
    t.add_column("Avg Odds", justify="right")

    signal_results = []

    for selection, div_col, result_val, odds_col in [
        ("Home", "div_h", "H", "B365H"),
        ("Draw", "div_d", "D", "B365D"),
        ("Away", "div_a", "A", "B365A"),
    ]:
        for threshold in [0.01, 0.02, 0.03, 0.05, 0.07, 0.10]:
            sig_mask = (subset[div_col] > threshold) & subset[odds_col].notna()
            sig_bets = subset[sig_mask]

            if len(sig_bets) < 20:
                continue

            won = (sig_bets["result"] == result_val)
            odds = sig_bets[odds_col]
            pnl = np.where(won, odds - 1, -1)
            roi = pnl.sum() / len(pnl)
            hit_rate = won.mean()

            c = "green" if roi > 0 else "red"
            t.add_row(
                selection,
                f"{threshold:.0%}",
                f"{len(sig_bets)}",
                f"{hit_rate:.1%}",
                f"[{c}]{roi:+.1%}[/{c}]",
                f"[{c}]{pnl.sum():+.0f}[/{c}]",
                f"{odds.mean():.2f}",
            )

            signal_results.append({
                "selection": selection,
                "threshold": threshold,
                "n_bets": len(sig_bets),
                "hit_rate": hit_rate,
                "roi": roi,
                "total_pnl": pnl.sum(),
                "avg_odds": odds.mean(),
            })

    console.print(t)

    # Also test the inverse: betting AGAINST Pinnacle (when soft books disagree)
    console.print("\n[bold]Counter-signal: bet AGAINST Pinnacle divergence[/bold]")

    ct = Table(title="Counter-Signal: When Pinnacle Shortens but Result Doesn't Follow")
    ct.add_column("Selection")
    ct.add_column("Threshold")
    ct.add_column("Bets", justify="right")
    ct.add_column("Hit%", justify="right")
    ct.add_column("ROI", justify="right")

    for selection, div_col, result_val, odds_col in [
        ("Home", "div_h", "H", "B365H"),
        ("Away", "div_a", "A", "B365A"),
    ]:
        for threshold in [-0.03, -0.05, -0.07]:
            sig_mask = (subset[div_col] < threshold) & subset[odds_col].notna()
            sig_bets = subset[sig_mask]

            if len(sig_bets) < 20:
                continue

            won = (sig_bets["result"] == result_val)
            odds = sig_bets[odds_col]
            pnl = np.where(won, odds - 1, -1)
            roi = pnl.sum() / len(pnl)

            c = "green" if roi > 0 else "red"
            ct.add_row(selection, f"{threshold:+.0%}", f"{len(sig_bets)}",
                       f"{won.mean():.1%}", f"[{c}]{roi:+.1%}[/{c}]")

    console.print(ct)

    # Save signal data
    if signal_results:
        sig_df = pd.DataFrame(signal_results)
        sig_out = PROCESSED_DIR / "sharp_money_signal_analysis.csv"
        sig_df.to_csv(sig_out, index=False)
        console.print(f"\n[dim]Signal analysis saved to {sig_out}[/dim]")

    # Summary
    best_signals = [s for s in signal_results if s["roi"] > 0.05 and s["n_bets"] >= 50]
    if best_signals:
        console.print("\n[bold green]Actionable signals found:[/bold green]")
        for s in sorted(best_signals, key=lambda x: -x["roi"]):
            console.print(f"  {s['selection']} divergence >{s['threshold']:.0%}: "
                          f"ROI {s['roi']:+.1%}, {s['n_bets']} bets, hit {s['hit_rate']:.1%}")
    else:
        console.print("\n[yellow]No strong signals above 5% ROI with 50+ bets.[/yellow]")


def analyse_overround_by_league(df: pd.DataFrame):
    """Show which leagues have the tightest margins (sharpest markets)."""
    console.print("\n[bold cyan]═══ Overround by League ═══[/bold cyan]")

    t = Table(title="Average Overround by League (Pinnacle vs Bet365)")
    t.add_column("League")
    t.add_column("Matches", justify="right")
    t.add_column("PS Overround", justify="right")
    t.add_column("B365 Overround", justify="right")
    t.add_column("Margin Gap", justify="right")

    for league in sorted(df["league"].unique()):
        lg = df[df["league"] == league]

        ps_mask = lg["PSH"].notna()
        b365_mask = lg["B365H"].notna()

        if ps_mask.sum() < 50 or b365_mask.sum() < 50:
            continue

        ps_probs = compute_implied_probs(lg[ps_mask], "PS")
        b365_probs = compute_implied_probs(lg[b365_mask], "B365")

        ps_or = ps_probs["PS_overround"].mean()
        b365_or = b365_probs["B365_overround"].mean()
        gap = b365_or - ps_or

        t.add_row(
            league,
            f"{len(lg):,}",
            f"{ps_or:.3f}",
            f"{b365_or:.3f}",
            f"{gap:+.3f}",
        )

    console.print(t)


def main():
    console.print("[bold]OddsIntel — Bookmaker Sharpness Analysis (P5.1)[/bold]\n")

    df = load_data()

    # 1. Sharpness rankings
    rankings = analyse_sharpness(df)

    # 2. Sharp money signal
    # Use the sharpest bookmaker from rankings
    sharpest = rankings.iloc[0]["code"]
    console.print(f"\n[bold]Sharpest bookmaker: {rankings.iloc[0]['name']} ({sharpest})[/bold]")
    analyse_sharp_money_signal(df, sharp_bk=sharpest)

    # 3. Overround by league
    analyse_overround_by_league(df)

    console.print("\n[bold green]Done.[/bold green] Check data/processed/ for output files.")


if __name__ == "__main__":
    main()
