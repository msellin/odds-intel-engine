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


# Books treated as "soft" (retail) for the sharp_vs_soft comparison.
# Mirrors workers/api_clients/supabase_client.py:_SOFT_BMS.
SOFT_BOOKS = frozenset({
    "Bet365", "Unibet", "Betano", "Marathonbet", "10Bet", "888Sport",
})


def _compute_sharp_vs_soft(snaps_pre: list) -> dict:
    """Pinnacle implied minus soft-book-avg implied at the latest pre-cutoff snapshot,
    per selection. Matches the existing match_signals computation at
    supabase_client.py:4018 but with the T-6h cutoff applied.
    """
    by_sel_book: dict[tuple, list] = defaultdict(list)
    for s in snaps_pre:
        by_sel_book[(s["selection"].lower(), s["bookmaker"])].append(s)

    out = {}
    for sel in ("home", "draw", "away"):
        # Latest Pinnacle snapshot pre-cutoff
        pin = by_sel_book.get((sel, "Pinnacle"), [])
        if not pin:
            continue
        try:
            pin_impl = 1.0 / float(pin[-1]["odds"])
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        # Avg soft-book latest implied
        softs = []
        for book in SOFT_BOOKS:
            sn = by_sel_book.get((sel, book), [])
            if sn:
                try:
                    softs.append(1.0 / float(sn[-1]["odds"]))
                except (TypeError, ValueError, ZeroDivisionError):
                    continue
        if len(softs) >= 2:
            out[f"sharp_consensus_{sel}_at_t6h"] = round(pin_impl - (sum(softs) / len(softs)), 5)
    return out


def _fetch_pinnacle_ah_snaps_batch(match_ids: list) -> dict:
    """Batch-fetch Pinnacle AH HOME snapshots for matches.
    Returns {match_id: [row, ...]} where row has timestamp, odds, handicap_line.

    B-ML3-V2-G-MAIN-LINE (2026-05-25): the previous version filtered to
    handicap_line=0 only, which Pinnacle rarely offers (yielded ~3% coverage).
    Now fetches ALL handicap lines per (match, bookmaker, timestamp) so the
    main-line picker can pick the most-balanced line at each timestamp.
    """
    if not match_ids:
        return {}
    rows = execute_query("""
        SELECT match_id, timestamp, odds, handicap_line
        FROM odds_snapshots
        WHERE match_id = ANY(%s::uuid[])
          AND bookmaker = 'Pinnacle'
          AND market = 'asian_handicap'
          AND selection = 'home'
          AND is_live = false
          AND odds BETWEEN 1.2 AND 2.8
        ORDER BY match_id, timestamp ASC
    """, (match_ids,))
    by_match: dict = defaultdict(list)
    for r in rows:
        by_match[r["match_id"]].append(r)
    return by_match


def _compute_pinnacle_ah_line_move_from_snaps(snaps: list, kickoff) -> dict:
    """G — Pinnacle AH main-line drift, computed from pre-fetched batched
    snapshots. Replaces the broken handicap=0-only logic.

    Algorithm:
      1. Group snapshots by timestamp (each timestamp = a multi-line offering).
      2. At each timestamp, pick the "main line" = the handicap_line whose
         home_odds is closest to 1.95 (Pinnacle's balanced-line target).
      3. Track main-line implied prob over time.
      4. Drift = (T-6h main implied) − (opening main implied).
      5. Also store the chosen handicap_line at T-6h as a categorical feature.

    Expected coverage 25-40% (limited by Pinnacle 1X2/AH snapshot presence
    pre-T6h), vs ~3% for the old version. Expected signal lift on B-ML3 v2.2
    AUC: small but real (~1-2% if it works).
    """
    cutoff = kickoff - timedelta(hours=6)
    pre = [s for s in snaps if s["timestamp"] <= cutoff]
    if len(pre) < 2:
        return {}

    # Group by timestamp; pick main line per group
    from collections import defaultdict as _dd
    by_ts: dict = _dd(list)
    for s in pre:
        by_ts[s["timestamp"]].append(s)

    main_per_ts = []  # list of (timestamp, handicap_line, implied)
    for ts, rows in sorted(by_ts.items(), key=lambda kv: kv[0]):
        best = None
        best_dist = 1e9
        for r in rows:
            try:
                o = float(r["odds"])
                d = abs(o - 1.95)
                if d < best_dist:
                    best_dist = d
                    best = r
            except (TypeError, ValueError):
                continue
        if best is None:
            continue
        try:
            implied = 1.0 / float(best["odds"])
            hcp = float(best["handicap_line"]) if best["handicap_line"] is not None else 0.0
            main_per_ts.append((ts, hcp, implied))
        except (TypeError, ValueError, ZeroDivisionError):
            continue

    if len(main_per_ts) < 2:
        return {}

    opening_implied = main_per_ts[0][2]
    latest_hcp = main_per_ts[-1][1]
    latest_implied = main_per_ts[-1][2]
    return {
        "pinnacle_ah_line_at_t6h": round(latest_hcp, 4),  # the handicap value, not implied
        "pinnacle_ah_line_move": round(latest_implied - opening_implied, 5),
    }


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
               OR mfv.odds_volatility_home_at_t6h IS NULL
               OR mfv.pinnacle_ah_line_at_t6h IS NULL)
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

            # Batch fetch 1x2 snapshots
            snaps = execute_query("""
                SELECT match_id, selection, bookmaker, timestamp, odds, is_live
                FROM odds_snapshots
                WHERE match_id = ANY(%s::uuid[]) AND market = '1x2'
                ORDER BY match_id, selection, bookmaker, timestamp ASC
            """, (match_ids,))
            by_match: dict = defaultdict(list)
            for s in snaps:
                by_match[s["match_id"]].append(s)

            # Batch fetch Pinnacle AH snapshots for G (one query per chunk vs per-match)
            ah_snaps_by_match = _fetch_pinnacle_ah_snaps_batch(match_ids)

            # Compute features + collect update payloads
            updates = []
            for mid in match_ids:
                snaps = by_match.get(mid, [])
                kickoff = kickoffs[mid]
                feats = _compute_match_features(snaps, kickoff)
                # Overlay sharp-vs-soft (sharp_consensus = sharp_avg - soft_avg)
                pre = [s for s in snaps if s["timestamp"] <= kickoff - timedelta(hours=6)
                       and not s.get("is_live", False)]
                sharp_vs_soft = _compute_sharp_vs_soft(pre)
                feats.update(sharp_vs_soft)
                # G — Pinnacle AH line move (from batched fetch, not per-match SQL)
                ah_snaps = ah_snaps_by_match.get(mid, [])
                ah = _compute_pinnacle_ah_line_move_from_snaps(ah_snaps, kickoff)
                feats.update(ah)
                if feats:
                    updates.append((mid, feats))

            if updates and not args.dry_run:
                # COPY-into-temp + UPDATE FROM temp = single round-trip per chunk.
                # Previous per-row UPDATE pattern was 80-100ms each over the EU
                # pooler → 13+ min for 10K rows and prone to statement_timeout.
                # This pattern is ~50× faster.
                all_cols = [
                    "odds_drift_home_at_t6h", "steam_move_at_t6h",
                    "pinnacle_line_move_home_at_t6h", "pinnacle_line_move_draw_at_t6h",
                    "pinnacle_line_move_away_at_t6h",
                    "sharp_consensus_home_at_t6h", "sharp_consensus_draw_at_t6h",
                    "sharp_consensus_away_at_t6h",
                    "odds_volatility_home_at_t6h", "odds_volatility_draw_at_t6h",
                    "odds_volatility_away_at_t6h",
                    "pinnacle_ah_line_at_t6h", "pinnacle_ah_line_move",
                ]
                # Build rows as (match_id, val_col1, val_col2, ...) with NULL
                # for any column the compute didn't produce.
                rows_to_copy = []
                for mid, feats in updates:
                    row_vals = [str(mid)] + [feats.get(c) for c in all_cols]
                    rows_to_copy.append(row_vals)

                with get_conn() as conn:
                    with conn.cursor() as cur:
                        # Temp table — auto-dropped on COMMIT
                        col_defs = ", ".join([f"{c} FLOAT" if c != "steam_move_at_t6h" else f"{c} BOOLEAN" for c in all_cols])
                        cur.execute(f"""
                            CREATE TEMP TABLE _bf_v2_chunk (
                                match_id UUID PRIMARY KEY,
                                {col_defs}
                            ) ON COMMIT DROP
                        """)
                        # Use execute_values to insert into temp — much faster than per-row
                        import psycopg2.extras as _pgext
                        _pgext.execute_values(
                            cur,
                            f"INSERT INTO _bf_v2_chunk (match_id, {', '.join(all_cols)}) VALUES %s",
                            rows_to_copy,
                            page_size=500,
                        )
                        # Single UPDATE FROM. COALESCE so NULLs in temp don't wipe
                        # existing values — only computed-non-NULL overwrites.
                        set_clauses = ", ".join([
                            f"{c} = COALESCE(t.{c}, mfv.{c})" for c in all_cols
                        ])
                        cur.execute(f"""
                            UPDATE match_feature_vectors AS mfv
                            SET {set_clauses}
                            FROM _bf_v2_chunk AS t
                            WHERE mfv.match_id = t.match_id
                        """)
                    conn.commit()
                total_updated += len(updates)

            progress.update(task, advance=len(chunk))

    if args.dry_run:
        console.print(f"\n[yellow]--dry-run: would have updated {len(updates)} rows in last chunk[/yellow]")
    else:
        console.print(f"\n[green]✓ Updated {total_updated:,} MFV rows[/green]")


if __name__ == "__main__":
    main()
