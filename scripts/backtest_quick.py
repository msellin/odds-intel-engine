"""
OddsIntel — Quick Backtest
Runs on just the last 2 seasons to get results fast.
The full backtest (all 20 seasons) can run overnight.
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.model.features import build_feature_matrix, FEATURE_COLS

console = Console()

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"


def main():
    console.print("[bold]OddsIntel Quick Backtest[/bold]\n")

    data_path = PROCESSED_DIR / "all_matches.csv"
    if not data_path.exists():
        console.print("[red]No data found. Run import_historical.py first.[/red]")
        sys.exit(1)

    console.print("[yellow]Loading match data...[/yellow]")
    df = pd.read_csv(data_path, parse_dates=["Date"], low_memory=False)

    # Only use last 5 seasons for speed (still enough training data)
    recent_seasons = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
    df = df[df["season"].isin(recent_seasons)].copy()
    console.print(f"Filtered to {len(df):,} matches ({', '.join(recent_seasons)})")

    # Only use top leagues for speed
    top_leagues = ["E0", "E1", "SP1", "D1", "D2", "I1", "I2", "F1", "N1"]
    df = df[df["league_code"].isin(top_leagues)].copy()
    console.print(f"Filtered to {len(df):,} matches (top leagues + second divisions)")

    console.print("\n[yellow]Building feature matrix...[/yellow]")
    features_df, targets_df = build_feature_matrix(df)

    # Save for reuse
    features_df.to_csv(PROCESSED_DIR / "features_quick.csv", index=False)
    targets_df.to_csv(PROCESSED_DIR / "targets_quick.csv", index=False)
    console.print(f"[green]Feature matrix: {len(features_df):,} matches[/green]")

    # Import and run backtest
    from scripts.backtest import run_backtest

    # Test on 2024-25 season
    if "2024-25" in targets_df["season"].values:
        run_backtest(features_df, targets_df, test_season="2024-25", min_edge=0.03)

        # Also try with higher edge threshold
        console.print("\n\n[bold yellow]═══ Re-running with 5% minimum edge ═══[/bold yellow]")
        run_backtest(features_df, targets_df, test_season="2024-25", min_edge=0.05)

        # Also try on 2023-24 for comparison
        console.print("\n\n[bold yellow]═══ Cross-validation on 2023-24 ═══[/bold yellow]")
        run_backtest(features_df, targets_df, test_season="2023-24", min_edge=0.03)
    elif "2023-24" in targets_df["season"].values:
        run_backtest(features_df, targets_df, test_season="2023-24", min_edge=0.03)


if __name__ == "__main__":
    main()
