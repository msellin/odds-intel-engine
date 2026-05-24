"""Backfill MFV with B-ML3 v2 features (2026-05-24).

Computes the new market-microstructure columns added by migration 128 using
snapshots taken WHERE timestamp <= match.date - 6h. That cutoff is the key
guard against the closing-line leak that contaminated v1's `odds_drift_home`.

Features computed (per match, per selection where applicable):
  * odds_drift_home_at_t6h, steam_move_at_t6h
  * pinnacle_line_move_<home/draw/away>_at_t6h
  * sharp_consensus_<home/draw/away>_at_t6h
  * odds_volatility_<home/draw/away>_at_t6h

`pinnacle_ah_line_at_t6h` / `pinnacle_ah_line_move` deferred to v2.1 — main-line
extraction needs care (multiple handicap_line values per match per snapshot).

Idempotent: only re-writes rows where any of the new columns is NULL.

Run: python3 scripts/backfill_mfv_b_ml3_v2_features.py
     python3 scripts/backfill_mfv_b_ml3_v2_features.py --dry-run
     python3 scripts/backfill_mfv_b_ml3_v2_features.py --since 2026-05-06
"""
from __future__ import annotations
import sys, argparse
import statistics
from pathlib import Path
from collections import defaultdict
from datetime import timedelta
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console
from rich.progress import Progress

from workers.api_clients.db import execute_query, get_conn

console = Console()

# Books considered "sharp-aligned" for sharp_consensus. Pinnacle dominates; the
# others are EU-licensed and historically track Pinnacle more tightly than e.g.
# Bet365 or Marathonbet retail. Anchors close to true price.
SHARP_BOOKS = frozenset({"Pinnacle"})  # Stage 1: Pinnacle-only. Expand in v2.1 if signal is too thin.

# Books used for volatility (cross-book std). Mirrors ACCESSIBLE_BOOKMAKERS
# from daily_pipeline_v2.py — same set that drives the edge math today.
ACCESSIBLE_BOOKS = frozenset({
    "Bet365", "Unibet", "Betano", "Marathonbet", "10Bet", "888Sport", "Pinnacle",
})


def _compute_match_features(snaps: list, kickoff) -> dict:
    """Given all 1x2 snapshots for a match (timestamp ASC), compute the new
    feature dict for backfill. Cutoff = kickoff - 6h."""
    cutoff = kickoff - timedelta(hours=6)
    pre = [s for s in snaps if s["timestamp"] <= cutoff and not s.get("is_live", False)]
    if not pre:
        return {}  # Match has no pre-T6h snapshots — leave NULL

    # Group by (selection, bookmaker)
    by_sel_book: dict[tuple, list] = defaultdict(list)
    for s in pre:
        by_sel_book[(s["selection"].lower(), s["bookmaker"])].append(s)

    out = {}

    # ── odds_drift_home_at_t6h: avg drift across accessible books on HOME ──
    home_drifts = []
    for (sel, book), book_snaps in by_sel_book.items():
        if sel != "home" or book not in ACCESSIBLE_BOOKS:
            continue
        if len(book_snaps) < 2:
            continue
        try:
            opening = 1.0 / float(book_snaps[0]["odds"])
            latest = 1.0 / float(book_snaps[-1]["odds"])
            home_drifts.append(latest - opening)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    if home_drifts:
        avg_drift = sum(home_drifts) / len(home_drifts)
        out["odds_drift_home_at_t6h"] = round(avg_drift, 5)
        out["steam_move_at_t6h"] = abs(avg_drift) > 0.03

    # ── pinnacle_line_move_<sel>_at_t6h ──────────────────────────────────
    for sel in ("home", "draw", "away"):
        pin_snaps = by_sel_book.get((sel, "Pinnacle"), [])
        if len(pin_snaps) >= 2:
            try:
                opening = 1.0 / float(pin_snaps[0]["odds"])
                latest = 1.0 / float(pin_snaps[-1]["odds"])
                out[f"pinnacle_line_move_{sel}_at_t6h"] = round(latest - opening, 5)
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    # ── sharp_consensus_<sel>_at_t6h: avg implied across sharp books at the latest pre-cutoff snapshot ──
    # For each (sel, sharp book), take the LATEST snapshot ≤ cutoff, average across books.
    for sel in ("home", "draw", "away"):
        sharps = []
        for book in SHARP_BOOKS:
            book_snaps = by_sel_book.get((sel, book), [])
            if book_snaps:
                try:
                    sharps.append(1.0 / float(book_snaps[-1]["odds"]))
                except (TypeError, ValueError, ZeroDivisionError):
                    continue
        if sharps:
            out[f"sharp_consensus_{sel}_at_t6h"] = round(sum(sharps) / len(sharps), 5)

    # ── odds_volatility_<sel>_at_t6h: std across accessible books at the latest pre-cutoff snapshot ──
    for sel in ("home", "draw", "away"):
        impls = []
        for book in ACCESSIBLE_BOOKS:
            book_snaps = by_sel_book.get((sel, book), [])
            if book_snaps:
                try:
                    impls.append(1.0 / float(book_snaps[-1]["odds"]))
                except (TypeError, ValueError, ZeroDivisionError):
                    continue
        if len(impls) >= 2:
            out[f"odds_volatility_{sel}_at_t6h"] = round(statistics.pstdev(impls), 5)

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-05-06", help="Backfill MFV rows with match_date >= this")
    ap.add_argument("--dry-run", action="store_true", help="Compute but don't update DB")
    ap.add_argument("--chunk-size", type=int, default=500, help="Matches per batch")
    args = ap.parse_args()

    # Find MFV rows needing backfill
    console.print(f"[bold]Selecting MFV rows since {args.since} with NULL B-ML3 v2 columns...[/bold]")
    rows = execute_query("""
        SELECT mfv.match_id, m.date AS kickoff
        FROM match_feature_vectors mfv
        JOIN matches m ON m.id = mfv.match_id
        WHERE mfv.match_date >= %s
          AND (mfv.odds_drift_home_at_t6h IS NULL
               OR mfv.pinnacle_line_move_home_at_t6h IS NULL
               OR mfv.sharp_consensus_home_at_t6h IS NULL
               OR mfv.odds_volatility_home_at_t6h IS NULL)
        ORDER BY m.date ASC
    """, (args.since,))
    console.print(f"  {len(rows):,} MFV rows to backfill")
    if not rows:
        return

    # Process in chunks: batch-fetch snapshots + compute + bulk UPDATE
    total_updated = 0
    with Progress(console=console) as progress:
        task = progress.add_task("Backfilling", total=len(rows))
        for i in range(0, len(rows), args.chunk_size):
            chunk = rows[i:i + args.chunk_size]
            match_ids = [r["match_id"] for r in chunk]
            kickoffs = {r["match_id"]: r["kickoff"] for r in chunk}

            # Batch fetch snapshots
            snaps = execute_query("""
                SELECT match_id, selection, bookmaker, timestamp, odds, is_live
                FROM odds_snapshots
                WHERE match_id = ANY(%s::uuid[]) AND market = '1x2'
                ORDER BY match_id, selection, bookmaker, timestamp ASC
            """, (match_ids,))
            by_match: dict = defaultdict(list)
            for s in snaps:
                by_match[s["match_id"]].append(s)

            # Compute features + collect update payloads
            updates = []
            for mid in match_ids:
                feats = _compute_match_features(by_match.get(mid, []), kickoffs[mid])
                if feats:
                    updates.append((mid, feats))

            if updates and not args.dry_run:
                # Bulk UPDATE — one per row since columns may differ
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        for mid, feats in updates:
                            set_parts = []
                            values = []
                            for col, val in feats.items():
                                set_parts.append(f"{col} = %s")
                                values.append(val)
                            values.append(mid)
                            sql = f"UPDATE match_feature_vectors SET {', '.join(set_parts)} WHERE match_id = %s"
                            cur.execute(sql, values)
                    conn.commit()
                total_updated += len(updates)

            progress.update(task, advance=len(chunk))

    if args.dry_run:
        console.print(f"\n[yellow]--dry-run: would have updated {len(updates)} rows in last chunk[/yellow]")
    else:
        console.print(f"\n[green]✓ Updated {total_updated:,} MFV rows[/green]")


if __name__ == "__main__":
    main()
