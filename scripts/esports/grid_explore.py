#!/usr/bin/env python3
"""
GRID Open Access API explorer.
Run this to discover CS2 titleId, available series, and team/roster data.

Usage:
    python3 scripts/esports/grid_explore.py
    python3 scripts/esports/grid_explore.py --series      # upcoming CS2 series
    python3 scripts/esports/grid_explore.py --teams       # CS2 teams + rosters
    python3 scripts/esports/grid_explore.py --results     # recent CS2 results

Docs: https://portal.grid.gg/documentation/
Open Access base URL: https://api-op.grid.gg/
"""
import json, os, sys, argparse
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    import subprocess; subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "--break-system-packages", "-q"])
    import requests

from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv("GRID_API_KEY", "")
if not API_KEY:
    print("GRID_API_KEY not set in .env"); sys.exit(1)

# Open Access uses api-op.grid.gg (NOT api.grid.gg)
CENTRAL = "https://api-op.grid.gg/central-data/graphql"
SERIES_STATE = "https://api-op.grid.gg/live-data-feed/series-state/graphql"
HEADERS = {"Content-Type": "application/json", "x-api-key": API_KEY}

def gql(url, query, variables=None):
    r = requests.post(url, json={"query": query, "variables": variables or {}}, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def find_cs2_title_id():
    """Discover CS2 titleId by trying all known and unknown IDs."""
    print("=== Finding CS2 titleId ===")
    # Known: Valorant=6, LoL=3, R6:Siege=25. CS2 not documented — try range.
    found = {}
    for tid in list(range(1, 31)) + [40, 50, 100]:
        try:
            data = gql(CENTRAL, f"""{{
                allSeries(
                    first: 1,
                    filter: {{ titleId: {{ eq: {tid} }}, types: ESPORTS }}
                    orderBy: StartTimeScheduled
                    orderDirection: DESC
                ) {{
                    totalCount
                    edges {{
                        node {{
                            id
                            startTimeScheduled
                            tournament {{ name }}
                            teams {{ name }}
                        }}
                    }}
                }}
            }}""")
            total = data.get("data", {}).get("allSeries", {}).get("totalCount", 0)
            edges = data.get("data", {}).get("allSeries", {}).get("edges", [])
            if total and total > 0:
                tourn = ""
                teams = ""
                if edges:
                    node = edges[0]["node"]
                    tourn = (node.get("tournament") or {}).get("name", "")
                    teams = " vs ".join(t["name"] for t in (node.get("teams") or []))
                found[tid] = total
                print(f"  titleId={tid:3d}: {total:5d} series  |  {tourn}  |  {teams}")
        except Exception as e:
            pass  # skip errors silently for unknown IDs

    if not found:
        print("  No titleIds returned results — check API key or endpoint")
    return found


def fetch_cs2_series(title_id: int, upcoming: bool = True, days: int = 7):
    """Fetch upcoming or recent CS2 series."""
    now = datetime.now(timezone.utc)
    if upcoming:
        time_filter = f'startTimeScheduled: {{ gte: "{now.isoformat()}", lte: "{(now + timedelta(days=days)).isoformat()}" }}'
        direction = "ASC"
    else:
        time_filter = f'startTimeScheduled: {{ gte: "{(now - timedelta(days=days)).isoformat()}", lte: "{now.isoformat()}" }}'
        direction = "DESC"

    query = f"""{{
        allSeries(
            first: 50,
            filter: {{
                titleId: {{ eq: {title_id} }}
                types: ESPORTS
                {time_filter}
            }}
            orderBy: StartTimeScheduled
            orderDirection: {direction}
        ) {{
            totalCount
            edges {{
                node {{
                    id
                    startTimeScheduled
                    format {{ nameShortened }}
                    tournament {{ id name }}
                    teams {{
                        id
                        name
                        players {{
                            id
                            nickname
                        }}
                    }}
                    valid
                }}
            }}
        }}
    }}"""
    return gql(CENTRAL, query)


def fetch_series_state(series_id: str):
    """Get live or completed series state (scores, teams)."""
    query = f"""{{
        seriesState(id: "{series_id}") {{
            id
            started
            finished
            teams {{
                id
                name
                won
                score
            }}
            games {{
                sequenceNumber
                started
                finished
                teams {{
                    id
                    score
                    won
                }}
            }}
        }}
    }}"""
    return gql(SERIES_STATE, query)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title-id", type=int, default=None, help="GRID titleId for CS2 (auto-discover if not set)")
    parser.add_argument("--series", action="store_true", help="Fetch upcoming CS2 series")
    parser.add_argument("--results", action="store_true", help="Fetch recent CS2 results")
    parser.add_argument("--teams", action="store_true", help="Show team rosters from series")
    parser.add_argument("--days", type=int, default=7, help="Lookahead/lookback days (default 7)")
    args = parser.parse_args()

    # Step 1: find CS2 titleId
    title_id = args.title_id
    if title_id is None:
        found = find_cs2_title_id()
        if not found:
            sys.exit(1)
        # Heuristic: CS2 is likely not LoL(3), Valorant(6), R6(25). Pick the most populated unknown.
        cs2_candidates = {k: v for k, v in found.items() if k not in (3, 6, 25)}
        if cs2_candidates:
            title_id = max(cs2_candidates, key=cs2_candidates.get)
            print(f"\n  → Using titleId={title_id} as CS2 (highest series count among unknowns)")
        else:
            title_id = list(found.keys())[0]
        print()

    if args.series or args.teams:
        print(f"=== Upcoming CS2 series (titleId={title_id}, next {args.days}d) ===")
        data = fetch_cs2_series(title_id, upcoming=True, days=args.days)
        edges = data.get("data", {}).get("allSeries", {}).get("edges", [])
        total = data.get("data", {}).get("allSeries", {}).get("totalCount", 0)
        print(f"  {total} total upcoming series\n")

        for e in edges:
            node = e["node"]
            start = node.get("startTimeScheduled", "")[:16].replace("T", " ")
            fmt = (node.get("format") or {}).get("nameShortened", "?")
            tourn = (node.get("tournament") or {}).get("name", "?")
            teams = node.get("teams") or []
            team_names = " vs ".join(t["name"] for t in teams)
            print(f"  {start}  {fmt}  |  {tourn}")
            print(f"    {team_names}  (id={node['id']})")

            if args.teams and teams:
                for t in teams:
                    players = t.get("players") or []
                    player_str = ", ".join(p.get("nickname", "?") for p in players)
                    print(f"    [{t['name']}] roster: {player_str or 'no roster data'}")
            print()

    if args.results:
        print(f"=== Recent CS2 results (titleId={title_id}, last {args.days}d) ===")
        data = fetch_cs2_series(title_id, upcoming=False, days=args.days)
        edges = data.get("data", {}).get("allSeries", {}).get("edges", [])
        total = data.get("data", {}).get("allSeries", {}).get("totalCount", 0)
        print(f"  {total} total recent series\n")

        for e in edges[:20]:
            node = e["node"]
            start = node.get("startTimeScheduled", "")[:16].replace("T", " ")
            fmt = (node.get("format") or {}).get("nameShortened", "?")
            tourn = (node.get("tournament") or {}).get("name", "?")
            teams = node.get("teams") or []
            team_names = " vs ".join(t["name"] for t in teams)
            valid = node.get("valid", True)
            print(f"  {start}  {fmt}  |  {tourn}")
            print(f"    {team_names}  valid={valid}  (id={node['id']})")

            # Try to get result from series state
            try:
                state = fetch_series_state(node["id"])
                ss = state.get("data", {}).get("seriesState")
                if ss and ss.get("finished"):
                    for t in (ss.get("teams") or []):
                        won = "✓ WON" if t.get("won") else "  lost"
                        print(f"    {won}  {t['name']}  score={t.get('score')}")
            except Exception:
                pass
            print()

    if not (args.series or args.results or args.teams):
        print("Run with --series, --results, or --teams to fetch CS2 data.")
        print("Example: python3 scripts/esports/grid_explore.py --series --teams")


if __name__ == "__main__":
    main()
