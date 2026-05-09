"""
AF bulk-endpoint probe — AUDIT-AF-ENDPOINTS task.

For /standings, /sidelined, /transfers, /coachs we currently call once per
team / player / league. AF's /injuries endpoint quietly accepts ?ids=A-B-C
(47× speedup measured — INJURIES-BY-DATE). This probes whether the same
trick works for the other four.

For each endpoint we:
  1. Pick a small representative sample from the production DB
  2. Fire one bulk call with hyphen-joined params
  3. Fire N per-id calls
  4. Diff the records returned at row level

Reads .env directly. Costs ~20 AF calls total. Run once.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.api_football import _get  # noqa: E402
from workers.api_clients.db import execute_query  # noqa: E402


def _hr(label: str):
    print()
    print("=" * 70)
    print(f"  {label}")
    print("=" * 70)


# ─── /standings ─────────────────────────────────────────────────────────────

def probe_standings():
    _hr("/standings  — bulk via ?league=A-B-C")
    league_ids = [39, 140, 78, 135, 61]  # EPL, La Liga, Bundesliga, Serie A, Ligue 1
    season = 2024  # safer than 2025 for end-of-season data

    # Per-id baseline: 5 sequential calls
    t0 = time.time()
    per_id = {}
    for lid in league_ids:
        try:
            data = _get("standings", {"league": lid, "season": season})
            per_id[lid] = data.get("response", [])
        except Exception as e:
            per_id[lid] = f"ERR: {e}"
    per_id_secs = time.time() - t0
    per_id_records = sum(len(v) if isinstance(v, list) else 0 for v in per_id.values())
    print(f"  Per-id baseline: 5 calls in {per_id_secs:.2f}s, {per_id_records} response[] entries")

    # Bulk attempt 1: dash-joined league IDs
    print()
    print("  → Bulk attempt: ?league=39-140-78-135-61&season=2024")
    t0 = time.time()
    try:
        data = _get("standings", {"league": "-".join(map(str, league_ids)), "season": season})
        bulk_secs = time.time() - t0
        resp = data.get("response", [])
        errs = data.get("errors") or {}
        print(f"    {bulk_secs:.2f}s  errors={errs}  response[] entries: {len(resp)}")
        if resp:
            leagues_returned = sorted({r.get("league", {}).get("id") for r in resp})
            print(f"    leagues in response: {leagues_returned}")
            # If we got 5, this is a clean bulk win
            if leagues_returned == sorted(league_ids):
                print("    ✓ all 5 leagues present in single response — BULK WORKS")
            elif len(leagues_returned) == 1:
                print("    ✗ only 1 league returned — bulk param ignored")
            else:
                print(f"    ⚠ partial: {len(leagues_returned)} of 5 leagues")
    except Exception as e:
        print(f"    EXC: {e}")

    # Bulk attempt 2: comma-joined
    print()
    print("  → Bulk attempt: ?league=39,140,78,135,61&season=2024")
    try:
        data = _get("standings", {"league": ",".join(map(str, league_ids)), "season": season})
        resp = data.get("response", [])
        errs = data.get("errors") or {}
        print(f"    errors={errs}  response[] entries: {len(resp)}")
        if resp:
            leagues_returned = sorted({r.get("league", {}).get("id") for r in resp})
            print(f"    leagues in response: {leagues_returned}")
    except Exception as e:
        print(f"    EXC: {e}")


# ─── /transfers ─────────────────────────────────────────────────────────────

def probe_transfers():
    _hr("/transfers  — bulk via ?team=A-B")
    # Get 3 teams that have AF IDs and recent transfer activity
    rows = execute_query("""
        SELECT DISTINCT tt.team_api_id AS team_id
        FROM team_transfers tt
        WHERE tt.team_api_id IS NOT NULL
        ORDER BY tt.team_api_id
        LIMIT 3
    """)
    team_ids = [r["team_id"] for r in rows]
    print(f"  Sample teams: {team_ids}")

    # Per-id baseline
    t0 = time.time()
    per_id = {}
    for tid in team_ids:
        try:
            data = _get("transfers", {"team": tid})
            per_id[tid] = data.get("response", [])
        except Exception as e:
            per_id[tid] = f"ERR: {e}"
    per_id_secs = time.time() - t0
    per_id_records = sum(len(v) if isinstance(v, list) else 0 for v in per_id.values())
    print(f"  Per-id baseline: {len(team_ids)} calls in {per_id_secs:.2f}s, {per_id_records} response[] entries")

    # Bulk attempt: dash-joined
    print()
    print(f"  → Bulk attempt: ?team={'-'.join(map(str, team_ids))}")
    t0 = time.time()
    try:
        data = _get("transfers", {"team": "-".join(map(str, team_ids))})
        bulk_secs = time.time() - t0
        resp = data.get("response", [])
        errs = data.get("errors") or {}
        print(f"    {bulk_secs:.2f}s  errors={errs}  response[] entries: {len(resp)}")
        if resp:
            teams_in_response = set()
            for entry in resp:
                pl = entry.get("player") or {}
                pl_team = pl.get("team", {}) if isinstance(pl, dict) else {}
                teams_in_response.add(pl_team.get("id"))
                # transfers within entry can mention multiple team ids
            print(f"    distinct (player.team.id) values in response: {sorted(t for t in teams_in_response if t is not None)}")
            sample = resp[0]
            print(f"    sample entry keys: {list(sample.keys())}")
    except Exception as e:
        print(f"    EXC: {e}")


# ─── /coachs ────────────────────────────────────────────────────────────────

def probe_coaches():
    _hr("/coachs  — bulk via ?team=A-B")
    rows = execute_query("""
        SELECT DISTINCT team_af_id AS team_id
        FROM team_coaches
        WHERE team_af_id IS NOT NULL
        ORDER BY team_af_id
        LIMIT 3
    """)
    team_ids = [r["team_id"] for r in rows]
    print(f"  Sample teams: {team_ids}")

    # Per-id baseline
    t0 = time.time()
    per_id = {}
    for tid in team_ids:
        try:
            data = _get("coachs", {"team": tid})
            per_id[tid] = data.get("response", [])
        except Exception as e:
            per_id[tid] = f"ERR: {e}"
    per_id_secs = time.time() - t0
    per_id_records = sum(len(v) if isinstance(v, list) else 0 for v in per_id.values())
    print(f"  Per-id baseline: {len(team_ids)} calls in {per_id_secs:.2f}s, {per_id_records} entries")

    # Bulk attempt
    print()
    print(f"  → Bulk attempt: ?team={'-'.join(map(str, team_ids))}")
    t0 = time.time()
    try:
        data = _get("coachs", {"team": "-".join(map(str, team_ids))})
        bulk_secs = time.time() - t0
        resp = data.get("response", [])
        errs = data.get("errors") or {}
        print(f"    {bulk_secs:.2f}s  errors={errs}  response[] entries: {len(resp)}")
        if resp:
            tids = set()
            for c in resp:
                tm = c.get("team", {}) if isinstance(c, dict) else {}
                tids.add(tm.get("id"))
            print(f"    distinct team.id in response: {sorted(t for t in tids if t is not None)}")
    except Exception as e:
        print(f"    EXC: {e}")


# ─── /sidelined ─────────────────────────────────────────────────────────────

def probe_sidelined():
    _hr("/sidelined  — bulk via ?players=A-B-C")
    rows = execute_query("""
        SELECT player_id, player_name AS name FROM player_sidelined
        WHERE player_id IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 3
    """) if _table_exists("player_sidelined") else []

    if not rows:
        # Fall back to a hand-picked AF player ID likely to have records
        # Mohamed Salah = 306, Erling Haaland = 1100
        player_ids = [306, 1100, 521]
        print(f"  Fallback player IDs: {player_ids}")
    else:
        player_ids = [r["player_id"] for r in rows]
        print(f"  Sample players: {[(r['player_id'], r.get('name')) for r in rows]}")

    # Per-id baseline
    t0 = time.time()
    per_id = {}
    for pid in player_ids:
        try:
            data = _get("sidelined", {"player": pid})
            per_id[pid] = data.get("response", [])
        except Exception as e:
            per_id[pid] = f"ERR: {e}"
    per_id_secs = time.time() - t0
    per_id_records = sum(len(v) if isinstance(v, list) else 0 for v in per_id.values())
    print(f"  Per-id baseline: {len(player_ids)} calls in {per_id_secs:.2f}s, {per_id_records} entries")

    # Bulk attempt 1: ?players=A-B-C (per AF docs, this is the documented form)
    print()
    print(f"  → Bulk attempt: ?players={'-'.join(map(str, player_ids))}")
    t0 = time.time()
    bulk_resp = None
    try:
        data = _get("sidelined", {"players": "-".join(map(str, player_ids))})
        bulk_secs = time.time() - t0
        bulk_resp = data.get("response", [])
        errs = data.get("errors") or {}
        print(f"    {bulk_secs:.2f}s  errors={errs}  response[] entries: {len(bulk_resp)}")
        if bulk_resp:
            sample = bulk_resp[0]
            print(f"    sample full entry: {sample}")
            print(f"    sample keys: {list(sample.keys())}")
            ids_seen = set()
            for entry in bulk_resp:
                # AF returns {"id": player_id, "sidelined": [...]}
                if "id" in entry:
                    ids_seen.add(entry["id"])
            print(f"    distinct top-level 'id' in response: {sorted(ids_seen)}")
            # Compare to per-id totals
            per_count = {}
            for pid, val in per_id.items():
                if isinstance(val, list):
                    per_count[pid] = len(val)
            print(f"    per-id sidelined counts: {per_count}")
            for entry in bulk_resp:
                if "sidelined" in entry:
                    print(f"      bulk pid={entry.get('id')} sidelined_count={len(entry['sidelined'])}")
    except Exception as e:
        print(f"    EXC: {e}")

    # Bulk attempt 2 (fallback): ?player=A-B-C (singular form, like /injuries quirk)
    print()
    print(f"  → Bulk attempt fallback: ?player={'-'.join(map(str, player_ids))}")
    try:
        data = _get("sidelined", {"player": "-".join(map(str, player_ids))})
        resp = data.get("response", [])
        errs = data.get("errors") or {}
        print(f"    errors={errs}  response[] entries: {len(resp)}")
    except Exception as e:
        print(f"    EXC: {e}")


def _table_exists(name: str) -> bool:
    rows = execute_query(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s LIMIT 1",
        [name],
    )
    return bool(rows)


def main():
    # Probes that already returned NEGATIVE on first run — kept for re-verify
    # but commented out by default to save quota.
    # probe_standings()  → 'The League field must contain an integer.'
    # probe_transfers()  → 'The Team field must contain an integer.'
    # probe_coaches()    → 'The Team field must contain an integer.'
    probe_sidelined()


if __name__ == "__main__":
    main()
