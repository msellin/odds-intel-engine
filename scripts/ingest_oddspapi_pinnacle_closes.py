"""Ingest Pinnacle closing odds extracted via OddsPapi historical-odds backfill
into our `odds_snapshots` table so `real_bets.clv` and other downstream
computations pick them up.

Reads:  /tmp/op_phase3_extracted.json (produced by /tmp/oddspapi_phase3.py)
Writes: rows into odds_snapshots with bookmaker='Pinnacle', is_closing=true.

For each (match_id, market, selection) we have a Pinnacle close for, insert ONE
snapshot per outcome at the close timestamp. Markets covered: 1x2, OU 0.75-3.5
half-lines, AH -1.5 to +1.5 home-side. (Other markets are decoded by clv_report.py
but typically not needed for CLV — see the OU_LINE_TO_MID and AH_LINE_TO_MID tables.)

Dry-run by default. Pass --execute to actually write.
"""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/Users/margussellin/www/odds-intel-engine")
from workers.api_clients.db import execute_query, get_conn

EXTRACTED = Path("/tmp/op_phase3_extracted.json")

# Same decoder as clv_report.py — kept in sync manually
OUTCOME_ID_TO_1X2 = {"101": "home", "102": "draw", "103": "away"}
OU_MID_TO_LINE = {
    10490: 0.75, 10492: 1.0, 10494: 1.25, 10258: 1.5, 10496: 1.75,
    10168: 2.0, 10170: 2.25, 1010: 2.5, 10172: 2.75, 10174: 3.0, 10176: 3.25, 1012: 3.5,
}
AH_MID_TO_LINE_HOME = {
    1060: -1.5, 1062: -1.25, 1064: -1.0, 1066: -0.75, 1068: -0.5, 1070: -0.25,
    1072: 0.0, 1074: 0.25, 1076: 0.5, 1078: 0.75, 1080: 1.0, 1082: 1.25, 1084: 1.5,
}

def collect_rows():
    """Yield (match_id, bookmaker, market, selection, odds, timestamp,
              is_closing, minutes_to_kickoff, handicap_line) tuples."""
    extracted = json.loads(EXTRACTED.read_text())
    seen = set()  # de-dup by (match_id, market, selection, handicap_line)
    for rec in extracted:
        match_id = rec["match_id"]
        ko_dt = datetime.fromisoformat(rec["our_kickoff"])
        for o in rec["outcomes"]:
            mid = str(o["marketId"]); oid = str(o["outcomeId"])
            close = o.get("close")
            if not close: continue
            ts_iso, price, _active = close
            if not price or price <= 1.0: continue
            close_dt = datetime.fromisoformat(ts_iso.replace("Z","+00:00"))
            mins_to_ko = int((ko_dt - close_dt).total_seconds() / 60)

            market = selection = None
            handicap_line = None

            # 1x2
            if mid == "101" and oid in OUTCOME_ID_TO_1X2:
                market = "1x2"; selection = OUTCOME_ID_TO_1X2[oid]
            # OU — convert numeric IDs.
            # Existing odds_snapshots convention uses HALF-LINES ONLY (over_under_15/25/35/45);
            # quarter-lines (over_under_08 etc.) are non-standard and pollute the schema.
            # Skip non-half-line OU markets entirely.
            else:
                try: mid_int = int(mid)
                except: mid_int = None
                if mid_int in OU_MID_TO_LINE:
                    line = OU_MID_TO_LINE[mid_int]
                    if line not in (0.5, 1.5, 2.5, 3.5, 4.5):
                        continue
                    cents = int(round(line * 10))
                    market = f"over_under_{cents:02d}"
                    # within market: lower outcomeId = over
                    try:
                        oid_int = int(oid)
                        # Find the other outcome in same market for this fixture
                        sibling = next((str(int(oo["outcomeId"])) for oo in rec["outcomes"]
                                        if str(oo["marketId"]) == mid and str(oo["outcomeId"]) != oid), None)
                        selection = "over" if (sibling is None or oid_int < int(sibling)) else "under"
                    except Exception:
                        selection = None
                elif mid_int in AH_MID_TO_LINE_HOME:
                    line_home = AH_MID_TO_LINE_HOME[mid_int]
                    market = "asian_handicap"
                    handicap_line = line_home
                    try:
                        oid_int = int(oid)
                        sibling = next((str(int(oo["outcomeId"])) for oo in rec["outcomes"]
                                        if str(oo["marketId"]) == mid and str(oo["outcomeId"]) != oid), None)
                        selection = "home" if (sibling is None or oid_int < int(sibling)) else "away"
                    except Exception:
                        selection = None
            if not (market and selection): continue
            key = (match_id, market, selection, handicap_line)
            if key in seen: continue
            seen.add(key)
            yield (match_id, "Pinnacle", market, selection, float(price), close_dt.isoformat(),
                   True, mins_to_ko, handicap_line)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="actually INSERT (default: dry-run)")
    args = ap.parse_args()

    rows = list(collect_rows())
    print(f"prepared {len(rows)} Pinnacle closing-snapshot rows")
    from collections import Counter
    market_breakdown = Counter(r[2] for r in rows)
    print(f"  market breakdown: {dict(market_breakdown)}")
    if rows:
        print(f"\n  sample row:")
        sample = rows[0]
        print(f"    match_id={sample[0]}")
        print(f"    market={sample[2]} selection={sample[3]} odds={sample[4]}")
        print(f"    timestamp={sample[5]} mins_to_ko={sample[7]}  handicap_line={sample[8]}")

    # Check how many would be duplicates of existing odds_snapshots
    match_ids = list({r[0] for r in rows})
    existing = execute_query("""
      SELECT match_id, market, selection, handicap_line FROM odds_snapshots
      WHERE bookmaker='Pinnacle'
        AND is_closing = true
        AND match_id = ANY(%s::uuid[])
    """, (match_ids,))
    existing_keys = {(str(r["match_id"]), r["market"], r["selection"], r.get("handicap_line")) for r in existing}
    will_insert = [r for r in rows if (r[0], r[2], r[3], r[8]) not in existing_keys]
    skip = len(rows) - len(will_insert)
    print(f"\n  already have is_closing=true Pinnacle: {len(existing_keys)} rows for these matches")
    print(f"  would skip {skip} (already there), insert {len(will_insert)} new")

    if not args.execute:
        print(f"\nDRY-RUN. Re-run with --execute to insert.")
        return

    if not will_insert:
        print(f"\nnothing to insert."); return

    print(f"\nINSERTING {len(will_insert)} rows...")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO odds_snapshots
                   (match_id, bookmaker, market, selection, odds, timestamp,
                    is_closing, minutes_to_kickoff, handicap_line)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                will_insert,
            )
            conn.commit()
    print(f"✓ inserted {len(will_insert)} rows")

if __name__ == "__main__":
    main()
