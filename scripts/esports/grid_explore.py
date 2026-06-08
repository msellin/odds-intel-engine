#!/usr/bin/env python3
"""
GRID Open Access API explorer.
Run this to see CS2 upcoming series and team data.

CS2 titleId = 28 (confirmed via `titles` query).
NOTE: Open Access does NOT include player rosters — `players` array is always
empty. Rosters require the GRID Stats Feed (paid tier).

Usage:
    python3 scripts/esports/grid_explore.py --series
    python3 scripts/esports/grid_explore.py --results
    python3 scripts/esports/grid_explore.py --titles      # list all game titles

Docs: https://portal.grid.gg/documentation/
Open Access base URL: https://api-op.grid.gg/
Rate limit: 20 req/min
"""
import json, os, sys, argparse, time
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

CENTRAL = "https://api-op.grid.gg/central-data/graphql"
SERIES_STATE = "https://api-op.grid.gg/live-data-feed/series-state/graphql"
HEADERS = {"Content-Type": "application/json", "x-api-key": API_KEY}

CS2_TITLE_ID = "28"  # confirmed: Counter Strike 2


def gql(url, query, variables=None):
    r = requests.post(url, json={"query": query, "variables": variables or {}}, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def list_titles():
    """List all game titles available in GRID."""
    data = gql(CENTRAL, "{ titles { id name nameShortened } }")
    titles = (data.get("data") or {}).get("titles") or []
    print(f"=== GRID Titles ({len(titles)} games) ===")
    for t in titles:
        print(f"  id={t['id']:>4}  {t.get('nameShortened', '?'):15}  {t['name']}")


def fetch_cs2_series(upcoming: bool = True, days: int = 7):
    """Fetch upcoming or recent CS2 series."""
    now = datetime.now(timezone.utc)
    if upcoming:
        gte, lte = now.isoformat(), (now + timedelta(days=days)).isoformat()
        direction = "ASC"
    else:
        gte, lte = (now - timedelta(days=days)).isoformat(), now.isoformat()
        direction = "DESC"

    query = f"""{{
        allSeries(
            first: 50,
            filter: {{
                titleId: "{CS2_TITLE_ID}"
                startTimeScheduled: {{ gte: "{gte}", lte: "{lte}" }}
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
                        baseInfo {{ id name }}
                    }}
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
    parser.add_argument("--series", action="store_true", help="Fetch upcoming CS2 series")
    parser.add_argument("--results", action="store_true", help="Fetch recent CS2 results")
    parser.add_argument("--titles", action="store_true", help="List all GRID game titles")
    parser.add_argument("--days", type=int, default=7, help="Lookahead/lookback days (default 7)")
    args = parser.parse_args()

    if args.titles:
        list_titles()
        return

    if args.series:
        print(f"=== Upcoming CS2 series (titleId={CS2_TITLE_ID}, next {args.days}d) ===")
        data = fetch_cs2_series(upcoming=True, days=args.days)
        edges = (data.get("data") or {}).get("allSeries", {}).get("edges", [])
        total = (data.get("data") or {}).get("allSeries", {}).get("totalCount", 0)
        print(f"  {total} total upcoming series\n")

        for e in edges:
            node = e["node"]
            start = node.get("startTimeScheduled", "")[:16].replace("T", " ")
            fmt = (node.get("format") or {}).get("nameShortened", "?")
            tourn = (node.get("tournament") or {}).get("name", "?")
            teams = [t["baseInfo"] for t in (node.get("teams") or []) if t.get("baseInfo")]
            team_names = " vs ".join(t["name"] for t in teams)
            print(f"  {start}  {fmt}  |  {tourn}")
            print(f"    {team_names}  (series_id={node['id']})")
            print()

    if args.results:
        print(f"=== Recent CS2 results (titleId={CS2_TITLE_ID}, last {args.days}d) ===")
        data = fetch_cs2_series(upcoming=False, days=args.days)
        edges = (data.get("data") or {}).get("allSeries", {}).get("edges", [])
        total = (data.get("data") or {}).get("allSeries", {}).get("totalCount", 0)
        print(f"  {total} total recent series\n")

        for e in edges[:20]:
            node = e["node"]
            start = node.get("startTimeScheduled", "")[:16].replace("T", " ")
            fmt = (node.get("format") or {}).get("nameShortened", "?")
            tourn = (node.get("tournament") or {}).get("name", "?")
            teams = [t["baseInfo"] for t in (node.get("teams") or []) if t.get("baseInfo")]
            team_names = " vs ".join(t["name"] for t in teams)
            print(f"  {start}  {fmt}  |  {tourn}")
            print(f"    {team_names}  (series_id={node['id']})")

            try:
                time.sleep(3.2)  # rate limit: 20 req/min
                state = fetch_series_state(node["id"])
                ss = (state.get("data") or {}).get("seriesState")
                if ss and ss.get("finished"):
                    for t in (ss.get("teams") or []):
                        won = "✓ WON" if t.get("won") else "  lost"
                        print(f"    {won}  {t['name']}  score={t.get('score')}")
            except Exception:
                pass
            print()

    if not (args.series or args.results or args.titles):
        print("Run with --series, --results, or --titles to fetch CS2 data.")
        print("  NOTE: Player rosters are NOT available in Open Access tier.")
        print("Example: python3 scripts/esports/grid_explore.py --series")


if __name__ == "__main__":
    main()
