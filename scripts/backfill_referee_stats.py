"""
OddsIntel — Backfill referee_stats table from historical match data.

Reads all finished matches with a referee name and match_stats card data,
then computes per-referee aggregate stats (cards/game, home win%, O/U 2.5%).

Run once after migration 011. Safe to re-run (upserts on referee_name).

Usage:
    python scripts/backfill_referee_stats.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.supabase_client import build_referee_stats

if __name__ == "__main__":
    print("Building referee stats from historical match data...")
    count = build_referee_stats()
    print(f"Done — {count} referees upserted into referee_stats table.")
