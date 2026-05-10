"""
ML-BUNDLE-STORAGE — one-shot bootstrap.

Walks `data/models/soccer/`, uploads every bundle to Supabase Storage, and
inserts a `model_versions` registry row for each. Idempotent — re-running
overwrites existing Storage files and updates registry rows.

Usage:
    python3 scripts/bootstrap_model_storage.py
    python3 scripts/bootstrap_model_storage.py --only v12_post0e
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

from workers.model.storage import upload_bundle, register_version

console = Console()

MODELS_ROOT = Path(__file__).parent.parent / "data" / "models" / "soccer"

# Hand-crafted notes for each known bundle. New bundles trained via
# train.py should populate these from CV metrics + arg metadata.
BUNDLE_NOTES = {
    "v9a_202425": "Production baseline. Kaggle 2022-25 schema (home_elo, h_*, a_*). Trained Apr 2026.",
    "v9b_202425": "Kaggle baseline variant.",
    "v9c_202425": "Kaggle baseline variant.",
    "v9d_202425": "Kaggle baseline variant.",
    "v10_pre_shadow": "First MFV-schema bundle. 2026-05-10. Stage-2a per-league imputation + missing indicators.",
    "v11_pinnacle": "v10 + Pinnacle 1X2 implied features. Indicator columns dominate at 5% Pinnacle coverage.",
    "v12_post0e": "Post-Stage-0e MFV refresh. No Pinnacle. Best 1X2 log_loss in offline_eval (2026-05-10 final report).",
    "v13_post0e_pin": "Post-0e + Pinnacle. Wins on over_25 + btts but loses 1X2 vs v12.",
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only", default=None, help="Bootstrap a single version subdir")
    args = p.parse_args()

    if not MODELS_ROOT.exists():
        console.print(f"[red]Models root not found: {MODELS_ROOT}[/red]")
        sys.exit(1)

    versions = []
    if args.only:
        if (MODELS_ROOT / args.only).exists():
            versions.append(args.only)
        else:
            console.print(f"[red]No such bundle: {MODELS_ROOT / args.only}[/red]")
            sys.exit(1)
    else:
        for child in sorted(MODELS_ROOT.iterdir()):
            if child.is_dir() and (child / "feature_cols.pkl").exists():
                versions.append(child.name)

    console.print(f"[cyan]Bootstrapping {len(versions)} bundle(s) → Supabase Storage[/cyan]")

    failed = []
    for v in versions:
        local_dir = MODELS_ROOT / v
        console.print(f"\n[bold]{v}[/bold]")
        try:
            n = upload_bundle(v, local_dir)
            # Register with whatever metadata we can derive cheaply.
            import joblib
            try:
                feature_cols = joblib.load(local_dir / "feature_cols.pkl")
            except Exception:
                feature_cols = None
            register_version(
                v,
                feature_cols=feature_cols if isinstance(feature_cols, list) else None,
                notes=BUNDLE_NOTES.get(v),
            )
            console.print(f"  [green]✓[/green] {v}: uploaded {n} files + registered")
        except Exception as e:
            failed.append((v, str(e)))
            console.print(f"  [red]✗[/red] {v}: {e}")

    if failed:
        console.print(f"\n[red]Failed: {len(failed)}[/red]")
        for v, e in failed:
            console.print(f"  {v}: {e}")
        sys.exit(1)

    console.print(f"\n[bold green]✓ Bootstrap complete — {len(versions)} bundles in Storage.[/bold green]")
    console.print("Run [cyan]python3 scripts/list_models.py[/cyan] to inspect the registry.")


if __name__ == "__main__":
    main()
