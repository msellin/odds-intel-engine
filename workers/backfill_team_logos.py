"""
Backfill missing team logos from API-Football.

Usage:
    python -m workers.backfill_team_logos [--dry-run] [--limit N]

For each team in the DB with logo_url IS NULL, searches API-Football by team name
and updates the DB if a match is found.

Cost: 1 AF API call per team. With ~150ms rate limiting, ~50 teams/minute.
"""

import sys
import argparse
import unicodedata
import re
from dotenv import load_dotenv

load_dotenv()

from workers.api_clients.api_football import _get
from workers.api_clients.supabase_client import execute_query, execute_write


def _normalize(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = nfkd.encode("ASCII", "ignore").decode("ASCII")
    return re.sub(r"[^a-z0-9]", "", ascii_only.lower())


def _af_safe_name(name: str) -> str:
    """Strip accents + non-alphanumeric chars except spaces (AF search requirement)."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = nfkd.encode("ASCII", "ignore").decode("ASCII")
    # Keep only letters, digits, spaces; collapse multiple spaces
    cleaned = re.sub(r"[^a-zA-Z0-9 ]", " ", ascii_only)
    return re.sub(r"\s+", " ", cleaned).strip()


def _best_logo_match(results: list, norm_target: str) -> str | None:
    """Pick the best logo from AF search results."""
    if not results:
        return None
    # Exact normalized match first
    for r in results:
        team = r.get("team", {})
        if _normalize(team.get("name", "")) == norm_target:
            return team.get("logo") or None
    # Substring containment
    for r in results:
        team = r.get("team", {})
        norm_candidate = _normalize(team.get("name", ""))
        if norm_target in norm_candidate or norm_candidate in norm_target:
            return team.get("logo") or None
    # Only one result — take it
    if len(results) == 1:
        return results[0].get("team", {}).get("logo") or None
    return None


def search_team_logo(team_name: str) -> str | None:
    """Search AF /teams?search={name} and return the best-matching logo URL.
    Falls back to a word-stripped query if the full name returns nothing
    (e.g. '1. FC Union Berlin' → 'Union Berlin').
    """
    norm_target = _normalize(team_name)
    safe_name = _af_safe_name(team_name)
    if not safe_name:
        return None

    def _search(query: str) -> list:
        try:
            data = _get("teams", params={"search": query})
            return data.get("response", [])
        except Exception as e:
            print(f"  AF error for '{query}': {e}")
            return []

    # Primary search
    results = _search(safe_name)
    logo = _best_logo_match(results, norm_target)
    if logo:
        return logo

    # Fallback: progressively drop leading tokens to find the significant name
    # e.g. "1. FC Union Berlin" → "FC Union Berlin" → "Union Berlin"
    # Also handles "FK", "SC", "AC", "CD" etc. style prefixes
    SHORT_PREFIXES = {"fc", "sc", "ac", "cd", "fk", "sk", "bk", "rfc", "afc", "if", "if", "bfc"}
    words = safe_name.split()
    tried: set[str] = {safe_name}
    while words:
        # Drop first token if it's a number or a well-known short prefix
        if words[0].isdigit() or words[0].lower() in SHORT_PREFIXES:
            words = words[1:]
        else:
            break
    # Try without leading junk
    if len(words) >= 2:
        fallback = " ".join(words)
        if fallback not in tried:
            tried.add(fallback)
            results = _search(fallback)
            logo = _best_logo_match(results, norm_target)
            if logo:
                return logo
    # Last resort: try just the last 2 words (e.g. "1. FC Union Berlin" → "Union Berlin")
    orig_words = safe_name.split()
    if len(orig_words) >= 3:
        last2 = " ".join(orig_words[-2:])
        if last2 not in tried:
            tried.add(last2)
            results = _search(last2)
            logo = _best_logo_match(results, norm_target)
            if logo:
                return logo

    return None


def main():
    parser = argparse.ArgumentParser(description="Backfill missing team logos from API-Football")
    parser.add_argument("--dry-run", action="store_true", help="Print updates without writing to DB")
    parser.add_argument("--limit", type=int, default=500, help="Max teams to process (default 500)")
    args = parser.parse_args()

    # Only teams that appear in matches (no point filling orphan teams)
    teams = execute_query(
        """
        SELECT DISTINCT t.id, t.name
        FROM teams t
        JOIN (
            SELECT home_team_id AS team_id FROM matches
            UNION
            SELECT away_team_id AS team_id FROM matches
        ) m ON m.team_id = t.id
        WHERE t.logo_url IS NULL
        ORDER BY t.name
        LIMIT %s
        """,
        (args.limit,),
    )

    total = len(teams)
    print(f"Found {total} teams without logos (active in matches)\n")

    updated = 0
    not_found = 0

    for i, row in enumerate(teams, 1):
        team_id = row["id"]
        team_name = row["name"]
        print(f"[{i}/{total}] {team_name} ... ", end="", flush=True)

        logo_url = search_team_logo(team_name)

        if logo_url:
            print(f"found: {logo_url}")
            if not args.dry_run:
                execute_write(
                    "UPDATE teams SET logo_url = %s WHERE id = %s",
                    (logo_url, team_id),
                )
            updated += 1
        else:
            print("not found")
            not_found += 1

    print(f"\nDone. Updated: {updated}, Not found: {not_found}")
    if args.dry_run:
        print("(dry-run — no changes written)")


if __name__ == "__main__":
    main()
