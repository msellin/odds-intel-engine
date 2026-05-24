"""OU residual gap diagnostic (2026-05-24).

After WEEKLY-RETRAIN-OU-FEATURES restored the dropped market features,
v20260524_market still loses to v14 by +4.4% on the over_under XGBoost
head despite recovering on every other market. The leading hypothesis
is data-composition drift: TIER-C-EXPAND (2026-05-19) added ~5K low-tier
matches to the training pool that v14 (trained 2026-05-11) never saw.

This script tests the hypothesis the cheap way: train a v14-equivalent
bundle on TODAY's code (so all the recent feature plumbing is identical)
but with the data filtered to `match_date <= '2026-05-11'`. If the new
bundle matches v14's OU log-loss within noise, the gap is data, not code.
If the gap persists, it's something else (silent feature regression we
missed, calibration drift in upstream signals, etc.).

Bundle tag: v14_recreate_2026_05_11
Eval window: same 14-day holdout as MARKET-EVAL-BTTS-AH so we can compare
apples-to-apples against the v14 / v20260524_market numbers already
persisted in model_versions.cv_metrics.

Run: python3 scripts/diag_ou_data_drift.py
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
import pandas as pd

console = Console()

DATE_CUTOFF = "2026-05-11"
BUNDLE_TAG = "v14_recreate_2026_05_11"


def main():
    from workers.model.train import (
        train_all,
        load_training_data,
        FEATURE_COLS,
        PINNACLE_FEATURE_COLS,
        OU_MARKET_FEATURE_COLS,
    )

    console.print(f"\n[bold]Loading training data with cutoff match_date <= {DATE_CUTOFF}[/bold]")
    features_df, targets_df = load_training_data(
        include_pinnacle=True,
        include_ou_market=True,
    )
    n_before = len(features_df)
    # Filter by match_date — the column comes from MFV alongside the features.
    # targets_df is indexed identically to features_df so we filter both.
    date_mask = pd.to_datetime(targets_df["match_date"]) <= pd.to_datetime(DATE_CUTOFF)
    features_df = features_df[date_mask].reset_index(drop=True)
    targets_df = targets_df[date_mask].reset_index(drop=True)
    n_after = len(features_df)
    console.print(f"  Filtered {n_before:,} → {n_after:,} matches ({n_before - n_after:,} dropped)")

    console.print(f"\n[bold]Training {BUNDLE_TAG} with the v14 feature schema[/bold]")
    train_all(
        version=BUNDLE_TAG,
        features_df=features_df,
        targets_df=targets_df,
        include_pinnacle=True,
        include_ou_market=True,
    )
    console.print(f"\n[green]✓ {BUNDLE_TAG} trained — now run:[/green]")
    console.print(f"  python3 scripts/weekly_eval_and_compare.py {BUNDLE_TAG} v14 --days 14")


if __name__ == "__main__":
    main()
