"""Mark stale bundles as demoted so the registry shows the actual short list.

Today: 25 MAIN bundles in model_versions, only 5 (production + 4 candidates)
are deploy-relevant for 2026-06-08. The other 20 are legacy:
  - 12 Kaggle-era v9 (schema mismatch with current MFV)
  - 8 pre-v20260524 MFV bundles (superseded)

Marking demoted_at on them shrinks the operator's mental model from 25
to 5 actionable bundles. Files stay in Storage (rollback path preserved);
the registry just reflects which are LIVE candidates.

Run:
  python3 scripts/demote_stale_bundles.py             # dry run
  python3 scripts/demote_stale_bundles.py --apply     # mark demoted
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console

from workers.api_clients.db import execute_query, get_conn

console = Console()

# Bundles that should NOT be deploy candidates anymore.
# Reason per group:
STALE = {
    # Kaggle-era schema (v9_*) — incompatible with current MFV inference path
    "v9a_202425": "Kaggle-era schema, mismatched with MFV",
    "v9a_202324": "Kaggle-era schema, mismatched with MFV",
    "v9a_202223": "Kaggle-era schema, mismatched with MFV",
    "v9b_202425": "Kaggle-era schema, mismatched with MFV",
    "v9b_202324": "Kaggle-era schema, mismatched with MFV",
    "v9b_202223": "Kaggle-era schema, mismatched with MFV",
    "v9c_202425": "Kaggle-era schema, mismatched with MFV",
    "v9c_202324": "Kaggle-era schema, mismatched with MFV",
    "v9c_202223": "Kaggle-era schema, mismatched with MFV",
    "v9d_202425": "Kaggle-era schema, mismatched with MFV",
    "v9d_202324": "Kaggle-era schema, mismatched with MFV",
    "v9d_202223": "Kaggle-era schema, mismatched with MFV",
    # Pre-2026-05-24 MFV bundles — superseded
    "v10_pre_shadow":           "Superseded by v20260524_market",
    "v11_pinnacle":             "Superseded by v20260524_market",
    "v12_post0e":               "Superseded by v20260524_market",
    "v13_post0e_pin":           "Superseded by v20260524_market",
    "v14":                      "Superseded by v20260524_market",
    "v14_recreate_2026_05_11":  "Diagnostic-only OU drag audit bundle",
    "v20260517":                "Superseded by v20260524_market (had OU regression)",
    "v20260524":                "Replaced by v20260524_market (correct market flags)",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    targets = list(STALE.keys())
    rows = execute_query("""
        SELECT version, promoted_at, demoted_at
        FROM model_versions
        WHERE version = ANY(%s)
        ORDER BY version
    """, (targets,))
    to_demote = [r for r in rows if r["demoted_at"] is None]

    console.print(f"[bold]Stale bundles to demote: {len(to_demote)} of {len(rows)} matching[/bold]\n")
    for r in to_demote:
        reason = STALE.get(r["version"], "")
        promoted = " (was promoted)" if r["promoted_at"] else ""
        console.print(f"  {r['version']:35s} → demote ({reason}){promoted}")

    if not args.apply:
        console.print("\n[yellow]Dry run — pass --apply to write demoted_at + notes[/yellow]")
        return

    if not to_demote:
        console.print("[green]No work — already all demoted[/green]")
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            for r in to_demote:
                reason = STALE[r["version"]]
                cur.execute("""
                    UPDATE model_versions
                    SET demoted_at = NOW(),
                        notes = COALESCE(notes, '') ||
                                CASE WHEN COALESCE(notes,'') = '' THEN '' ELSE ' | ' END ||
                                'Demoted 2026-05-25: ' || %s
                    WHERE version = %s
                """, (reason, r["version"]))
        conn.commit()
    console.print(f"\n[green]✓ Marked {len(to_demote)} bundles as demoted[/green]")
    console.print("\n[dim]Files stay in Supabase Storage — rollback path preserved. demoted_at just hides them from candidate lists.[/dim]")


if __name__ == "__main__":
    main()
