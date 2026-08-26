"""Promote a trained model version to production.

What "promote" means in this codebase:
  - The runtime checks `MODEL_VERSION` env var (per-market: also `MODEL_VERSION_1X2`,
    `MODEL_VERSION_OU`, `MODEL_VERSION_BTTS` once Phase C-light ships).
  - model_versions.promoted_at = NOW() — audit trail
  - The previous promoted version's demoted_at = NOW() (if known)

This script handles the DB side and prints the the VPS env-var command for
the human to run (since changing the VPS env requires the dashboard or CLI
auth, not our DB connection).

Usage:
    # promote a global model
    python3 scripts/promote_model.py v20260517

    # promote per-market (requires Phase C-light)
    python3 scripts/promote_model.py v20260517 --market 1x2
    python3 scripts/promote_model.py v14 --market ou

    # dry-run (no DB writes, just prints what would change)
    python3 scripts/promote_model.py v20260517 --dry-run
"""
from __future__ import annotations
import os, sys, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import psycopg2
from rich.console import Console

console = Console()


def _market_env_var(market: str | None) -> str:
    if market is None:
        return "MODEL_VERSION"
    return f"MODEL_VERSION_{market.upper()}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("version", help="Version to promote (e.g. v20260517)")
    p.add_argument("--market", choices=["1x2", "ou", "btts"], default=None,
                   help="Per-market promotion (requires Phase C-light per-market env routing). "
                        "Omit for full global promotion.")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would change without writing to DB")
    args = p.parse_args()

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()

    # Verify the target version exists
    cur.execute("SELECT version, trained_at, promoted_at FROM model_versions WHERE version = %s",
                (args.version,))
    target = cur.fetchone()
    if not target:
        console.print(f"[red]Version '{args.version}' not found in model_versions.[/red]")
        sys.exit(1)
    console.print(f"[bold]Target:[/bold] {target[0]} (trained {target[1]})  "
                  f"{'already promoted ' + str(target[2]) if target[2] else 'not yet promoted'}")

    # Find current production (most recent non-demoted promotion)
    cur.execute("""
        SELECT version, promoted_at FROM model_versions
        WHERE promoted_at IS NOT NULL AND demoted_at IS NULL
          AND version <> %s
        ORDER BY promoted_at DESC LIMIT 1
    """, (args.version,))
    current = cur.fetchone()
    if current:
        console.print(f"[bold]Current production (per DB):[/bold] {current[0]} (promoted {current[1]})")
    else:
        console.print(f"[bold]Current production (per DB):[/bold] [dim]none recorded[/dim] "
                      f"— set MODEL_VERSION env on the VPS is the actual source of truth.")

    env_var = _market_env_var(args.market)
    scope = f"market={args.market.upper()}" if args.market else "global"
    console.print(f"[bold]Scope:[/bold] {scope} → the VPS env [cyan]{env_var}={args.version}[/cyan]")

    if args.dry_run:
        console.print("\n[yellow]DRY RUN — no DB writes performed.[/yellow]")
        console.print(f"\nTo actually promote:")
        console.print(f"  1. python3 scripts/promote_model.py {args.version}"
                      + (f" --market {args.market}" if args.market else ""))
        console.print(f"  2. On the VPS: set env var {env_var}={args.version} and redeploy")
        return

    # Demote the current production (if any) — only on global promotion.
    # Per-market promotion doesn't necessarily demote the global version.
    if args.market is None and current:
        cur.execute("UPDATE model_versions SET demoted_at = NOW() WHERE version = %s", (current[0],))
        console.print(f"  Demoted {current[0]} (demoted_at = NOW())")

    # Promote the new one
    cur.execute("""
        UPDATE model_versions
        SET promoted_at = COALESCE(promoted_at, NOW()),
            demoted_at = NULL,
            notes = COALESCE(notes, '') ||
                    E'\\n[PROMOTED ' || NOW()::timestamp(0)::text || %s || ']'
        WHERE version = %s
    """, (f" — {scope}", args.version))
    conn.commit()
    console.print(f"  Promoted {args.version} (promoted_at = NOW())")

    console.print("\n[bold green]✓ DB updated.[/bold green]")
    console.print(f"\n[bold]Next step (manual):[/bold] flip the VPS env var")
    console.print(f"  [cyan]{env_var}={args.version}[/cyan]")
    console.print(f"\nAfter scheduler restarts, the bots will use this version on the next pipeline cohort.")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
