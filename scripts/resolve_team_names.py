"""
OddsIntel — LLM Team Name Resolver (11.2)

Batch-resolves unmatched team names from the pipeline log using an LLM.
Results are cached permanently in workers/utils/team_names.py as new
entries in the KAMBI_TO_FOOTBALL_DATA mapping.

Usage:
  python scripts/resolve_team_names.py                    # Resolve + preview
  python scripts/resolve_team_names.py --apply            # Resolve + write to team_names.py
  python scripts/resolve_team_names.py --dry-run          # Preview only, no LLM calls

How it works:
  1. Parse unmatched_teams.log for unique unmatched names
  2. Load known team names from targets_v9.csv and targets_global.csv
  3. Send batches of 20 unmatched names + known teams to Gemini Flash
  4. LLM returns {"unmatched_name": "known_name" or null} for each
  5. Results are added to KAMBI_TO_FOOTBALL_DATA in team_names.py

Validation: Compare team count before/after. Run pipeline and check log shrinks.
"""

import sys
import os
import json
import re
import argparse
from pathlib import Path
from collections import Counter

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

console = Console()

ENGINE_DIR = Path(__file__).parent.parent
LOG_PATH = ENGINE_DIR / "data" / "logs" / "unmatched_teams.log"
TEAM_NAMES_PATH = ENGINE_DIR / "workers" / "utils" / "team_names.py"
CACHE_PATH = ENGINE_DIR / "data" / "processed" / "llm_team_name_cache.json"


def get_unmatched_teams() -> list[str]:
    """Parse unmatched_teams.log for unique team names, sorted by frequency."""
    if not LOG_PATH.exists():
        return []

    counts = Counter()
    for line in LOG_PATH.read_text().splitlines():
        match = re.search(r"UNMATCHED: '([^']+)'", line)
        if match:
            name = match.group(1)
            if name not in ("Mystery United", "Completely Unknown Club"):
                counts[name] += 1

    # Sort by frequency (most common first)
    return [name for name, _ in counts.most_common()]


def get_known_teams() -> set[str]:
    """Load all known team names from targets CSVs."""
    known = set()
    import pandas as pd

    for csv_name in ["targets_v9.csv", "targets_global.csv"]:
        path = ENGINE_DIR / "data" / "processed" / csv_name
        if path.exists():
            df = pd.read_csv(path, usecols=["home_team", "away_team"])
            known |= set(df["home_team"].unique()) | set(df["away_team"].unique())

    return known


def get_existing_mappings() -> dict[str, str]:
    """Load existing KAMBI_TO_FOOTBALL_DATA mappings."""
    from workers.utils.team_names import KAMBI_TO_FOOTBALL_DATA
    return dict(KAMBI_TO_FOOTBALL_DATA)


def load_cache() -> dict:
    """Load previously resolved names from cache."""
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            pass
    return {}


def save_cache(cache: dict):
    """Save resolved names to cache."""
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def resolve_batch_with_llm(unmatched: list[str], known_teams: set[str]) -> dict[str, str | None]:
    """
    Send a batch of unmatched names to Gemini Flash for resolution.
    Returns {unmatched_name: matched_name or null}.
    """
    from google import genai

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))

    # Send a sample of known teams (too many to send all)
    # Pick teams from diverse leagues to help the LLM
    # Filter out any NaN/None values from pandas
    known_clean = {t for t in known_teams if isinstance(t, str) and t.strip()}
    known_sample = sorted(known_clean)[:500]

    prompt = f"""You are a football team name matching expert. Match these unmatched team names to their equivalent in our known database.

UNMATCHED NAMES (from Kambi/Sofascore odds feeds):
{json.dumps(unmatched, ensure_ascii=False)}

KNOWN TEAM NAMES (from our database — match to these exactly):
{json.dumps(known_sample, ensure_ascii=False)}

For each unmatched name, find the EXACT matching name from the known list above. If no match exists in the known list, return null.

Common patterns:
- "FC", "FK", "SK", "CF" prefixes/suffixes may differ
- Accented characters may differ (ö→oe, ş→s, etc.)
- City names may be abbreviated or full
- "Internazionale" = "Inter", "Borussia Mönchengladbach" = "M'gladbach"

Respond with ONLY a JSON object mapping unmatched → known (or null):
{{{", ".join(f'"{name}": "matched_name_or_null"' for name in unmatched[:3])}...}}"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = response.text.strip()

        # Extract JSON
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            result = json.loads(json_match.group())
            # Validate: only keep matches that are actually in known_teams
            validated = {}
            for unmatched_name, matched_name in result.items():
                if matched_name and isinstance(matched_name, str) and matched_name in known_teams:
                    validated[unmatched_name] = matched_name
                else:
                    validated[unmatched_name] = None
            return validated
    except Exception as e:
        console.print(f"[red]LLM error: {e}[/red]")

    return {name: None for name in unmatched}


def apply_to_team_names(new_mappings: dict[str, str]):
    """
    Add resolved mappings to KAMBI_TO_FOOTBALL_DATA in team_names.py.
    Inserts new entries before the closing brace of the dict, after existing entries.
    """
    content = TEAM_NAMES_PATH.read_text()

    # Find the KAMBI_TO_FOOTBALL_DATA dict closing brace.
    # It's the "}" that appears before "# Reverse map"
    marker = "\n}\n\n# Reverse map"
    if marker not in content:
        console.print("[red]Could not find KAMBI_TO_FOOTBALL_DATA dict end marker[/red]")
        return

    # Build new entries block
    new_entries = "\n    # LLM-resolved mappings (auto-generated by resolve_team_names.py)\n"
    for kambi_name, fd_name in sorted(new_mappings.items()):
        new_entries += f'    "{kambi_name}": "{fd_name}",\n'

    # Insert before the closing brace
    updated = content.replace(marker, "\n" + new_entries + "}\n\n# Reverse map")
    TEAM_NAMES_PATH.write_text(updated)


def main():
    parser = argparse.ArgumentParser(description="Resolve unmatched team names with LLM")
    parser.add_argument("--apply", action="store_true", help="Write resolved names to team_names.py")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no LLM calls")
    args = parser.parse_args()

    console.print("[bold cyan]═══ OddsIntel Team Name Resolver ═══[/bold cyan]\n")

    # 1. Get unmatched names
    unmatched = get_unmatched_teams()
    console.print(f"Unmatched team names in log: [bold]{len(unmatched)}[/bold]")

    if not unmatched:
        console.print("[green]No unmatched teams![/green]")
        return

    # 2. Load known teams and existing mappings
    try:
        import pandas as pd
        known = get_known_teams()
    except ImportError:
        console.print("[red]pandas required. Install with: pip install pandas[/red]")
        return

    existing = get_existing_mappings()
    console.print(f"Known teams in CSVs: {len(known)}")
    console.print(f"Existing mappings: {len(existing)}")

    # 3. Filter out names already in mappings or known teams
    cache = load_cache()
    to_resolve = [n for n in unmatched if n not in existing and n not in known and n not in cache]
    already_cached = {n: cache[n] for n in unmatched if n in cache and cache[n] is not None}

    console.print(f"Already resolved (cached): {len(already_cached)}")
    console.print(f"Still need resolution: [bold]{len(to_resolve)}[/bold]\n")

    if args.dry_run:
        console.print("[yellow]Dry run — showing first 30 unresolved names:[/yellow]")
        for name in to_resolve[:30]:
            console.print(f"  {name}")
        return

    # 4. Resolve in batches of 20
    all_resolved = dict(already_cached)
    batch_size = 20

    for i in range(0, len(to_resolve), batch_size):
        batch = to_resolve[i:i + batch_size]
        console.print(f"[cyan]Resolving batch {i // batch_size + 1} ({len(batch)} names)...[/cyan]")

        results = resolve_batch_with_llm(batch, known)

        for name, match in results.items():
            cache[name] = match
            if match:
                all_resolved[name] = match
                console.print(f"  [green]✓ {name} → {match}[/green]")
            else:
                console.print(f"  [dim]✗ {name} → no match[/dim]")

    save_cache(cache)

    # 5. Summary
    console.print(f"\n[bold]Results:[/bold]")
    console.print(f"  Resolved: [green]{len(all_resolved)}[/green]")
    console.print(f"  Unresolvable: {len(to_resolve) - len([r for r in cache.values() if r])} (not in our historical data)")
    console.print(f"  Cache saved to: {CACHE_PATH}")

    if not all_resolved:
        console.print("[yellow]No new resolutions found.[/yellow]")
        return

    # Show resolved mappings
    t = Table(title=f"Resolved Mappings ({len(all_resolved)})")
    t.add_column("Odds Feed Name", style="cyan")
    t.add_column("Historical Name", style="green")
    for name, match in sorted(all_resolved.items()):
        t.add_row(name, match)
    console.print(t)

    # 6. Apply if requested
    if args.apply:
        apply_to_team_names(all_resolved)
        console.print(f"\n[bold green]✓ Added {len(all_resolved)} mappings to {TEAM_NAMES_PATH}[/bold green]")
        console.print("[dim]Run the daily pipeline again to verify reduced unmatched count.[/dim]")
    else:
        console.print(f"\n[yellow]Preview only. Run with --apply to write to team_names.py[/yellow]")


if __name__ == "__main__":
    main()
