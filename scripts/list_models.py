"""
ML-BUNDLE-STORAGE — operator CLI for the model registry.

Lists every trained model bundle from the `model_versions` table, joined
with current Storage presence. Use this to decide which version to promote
or roll back to.

Usage:
    python3 scripts/list_models.py
    python3 scripts/list_models.py --in-storage  # only show bundles whose Storage prefix has files
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from workers.model.storage import bundle_exists_in_storage, list_versions

console = Console()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--in-storage", action="store_true",
                   help="Filter to versions whose binaries are present in Storage right now.")
    args = p.parse_args()

    rows = list_versions()
    if not rows:
        console.print("[yellow]No rows in model_versions. Run scripts/bootstrap_model_storage.py first.[/yellow]")
        return

    table = Table(title="Model Registry")
    table.add_column("version", style="cyan")
    table.add_column("trained_at", style="dim")
    table.add_column("rows", justify="right")
    table.add_column("storage", justify="center")
    table.add_column("promoted", style="green")
    table.add_column("demoted", style="red")
    table.add_column("notes", overflow="fold", max_width=60)

    for r in rows:
        in_storage = bundle_exists_in_storage(r["version"])
        if args.in_storage and not in_storage:
            continue
        table.add_row(
            r["version"],
            str(r["trained_at"])[:19] if r.get("trained_at") else "",
            str(r["n_training_rows"] or "—"),
            "✓" if in_storage else "—",
            str(r["promoted_at"])[:19] if r.get("promoted_at") else "",
            str(r["demoted_at"])[:19] if r.get("demoted_at") else "",
            r.get("notes") or "",
        )
    console.print(table)
    console.print(
        "\n[dim]Promote: UPDATE model_versions SET promoted_at = NOW() WHERE version = '...'\n"
        "Demote:  UPDATE model_versions SET demoted_at = NOW() WHERE version = '...'\n"
        "Switch:  set MODEL_VERSION env var on Railway → redeploy → bundle auto-downloads on first prediction.[/dim]"
    )


if __name__ == "__main__":
    main()
