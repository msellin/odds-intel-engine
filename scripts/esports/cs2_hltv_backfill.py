#!/usr/bin/env python3
"""
hltv_v1 calibration backfill.

We can't recover historical HLTV rankings (the ranking page doesn't expose
weekly snapshots). But the rankings change slowly (top-30 is stable
month-over-month), so applying TODAY's ranking to historical matches gives
a directionally-correct calibration sample. Use it to fit a Platt scaler.

For each historical match in cs2_results that has BOTH teams in today's
cs2_hltv_rankings snapshot:
  - Compute hltv_v1 raw prediction with current ranking
  - Pair with actual outcome
  - Write to cs2_predictions(model_version='hltv_v1_backfill')

Then fit Platt against the result and store in cs2_model_coefficients.

Usage:
    python3 scripts/esports/cs2_hltv_backfill.py            # dry run, show distribution
    python3 scripts/esports/cs2_hltv_backfill.py --record   # write predictions + Platt
"""
import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.esports.cs2_hltv_predict import HLTV_K, MODEL_VERSION
from scripts.esports.cs2_calibrate import _log_loss, _accuracy, _ece, _fit_platt
from workers.api_clients.db import execute_query, execute_write

BACKFILL_MODEL_VERSION = "hltv_v1_backfill"


def _normalize(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# Reuse the scanner's alias map for team-name resolution.
_ALIASES = {
    "vitality": "vitality", "teamvitality": "vitality",
    "navi": "natusvincere", "natusvincere": "natusvincere",
    "vp": "virtuspro", "themongolz": "themongolz", "mongolz": "themongolz",
    "spirit": "spirit", "teamspirit": "spirit",
    "liquid": "teamliquid", "teamliquid": "teamliquid",
}


def _load_hltv() -> dict[str, tuple[int, int]]:
    rows = execute_query("""
        SELECT DISTINCT ON (team_name) team_name, hltv_rank, hltv_points
        FROM cs2_hltv_rankings ORDER BY team_name, snapshot_date DESC
    """, ())
    out = {_normalize(r["team_name"]): (r["hltv_rank"], r["hltv_points"]) for r in rows if r.get("team_name")}
    return out


def _lookup(name: str, hltv: dict) -> tuple[int, int] | tuple[None, None]:
    k = _normalize(name)
    if k in hltv: return hltv[k]
    alias = _ALIASES.get(k)
    if alias and alias in hltv: return hltv[alias]
    return None, None


def _raw_hltv_prob(pts1: int, pts2: int) -> float:
    log_diff = math.log(pts1 + 1) - math.log(pts2 + 1)
    return 1.0 / (1.0 + math.exp(-HLTV_K * log_diff))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--record", action="store_true",
                   help="Write to cs2_predictions + UPSERT Platt in cs2_model_coefficients")
    p.add_argument("--limit", type=int, help="Cap matches for testing")
    args = p.parse_args()

    print(f"\n=== HLTV-only backfill calibration  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    hltv = _load_hltv()
    print(f"  HLTV rankings loaded: {len(hltv)} teams")
    if not hltv:
        print("  [!] no HLTV rankings — run cs2_hltv_rankings.py --record first")
        sys.exit(1)

    # All settled matches (both backfill and live results)
    results = execute_query("""
        SELECT r.bo3gg_id, r.team1, r.team2, r.kickoff_time, r.best_of, r.winner
        FROM cs2_results r
        WHERE r.winner IN ('team1', 'team2')
    """, ())
    print(f"  settled matches available: {len(results):,}")

    pairs: list[tuple[float, int]] = []
    written = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for i, r in enumerate(results):
        if args.limit and i >= args.limit:
            break
        h1, pts1 = _lookup(r["team1"], hltv)
        h2, pts2 = _lookup(r["team2"], hltv)
        if pts1 is None or pts2 is None:
            continue
        raw = _raw_hltv_prob(pts1, pts2)
        actual = 1 if r["winner"] == "team1" else 0
        pairs.append((raw, actual))

        if args.record:
            execute_write("""
                INSERT INTO cs2_predictions
                    (bo3gg_id, scan_time, kickoff_time, league, best_of,
                     team1, team2, win_prob1, win_prob2, fair_odds1, fair_odds2,
                     hltv_rank1, hltv_rank2, hltv_points1, hltv_points2,
                     model_version)
                VALUES (%s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (bo3gg_id, scan_time, model_version) DO NOTHING
            """, (
                r["bo3gg_id"], r["kickoff_time"] or now_iso, r["kickoff_time"], r["best_of"],
                r["team1"], r["team2"],
                round(raw, 4), round(1 - raw, 4),
                round(1 / raw, 3) if raw > 0 else 999.99,
                round(1 / (1 - raw), 3) if raw < 1 else 999.99,
                h1, h2, pts1, pts2, BACKFILL_MODEL_VERSION,
            ))
            written += 1

    n = len(pairs)
    print(f"\n  sample size : {n:,}  (had HLTV data for both teams)")
    if n == 0:
        sys.exit(1)
    acc = _accuracy(pairs)
    ll = _log_loss(pairs)
    ece = _ece(pairs)
    print(f"  raw HLTV-only metrics:  acc={acc*100:.1f}%  log_loss={ll:.4f}  ECE={ece*100:.2f}%")

    a, b = _fit_platt(pairs)
    print(f"  Platt fit: a={a:.4f}  b={b:.4f}")

    if args.record:
        execute_write("""
            INSERT INTO cs2_model_coefficients
                (model_version, a, b, n, log_loss, accuracy, ece, seeded_from)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'hltv_v1_backfill')
            ON CONFLICT (model_version) DO UPDATE SET
                a=EXCLUDED.a, b=EXCLUDED.b, n=EXCLUDED.n,
                log_loss=EXCLUDED.log_loss, accuracy=EXCLUDED.accuracy, ece=EXCLUDED.ece,
                updated_at=NOW()
        """, ("hltv_v1", a, b, n, ll, acc, ece))
        execute_write("""
            INSERT INTO cs2_model_coefficients
                (model_version, a, b, n, log_loss, accuracy, ece)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (model_version) DO UPDATE SET
                a=EXCLUDED.a, b=EXCLUDED.b, n=EXCLUDED.n,
                log_loss=EXCLUDED.log_loss, accuracy=EXCLUDED.accuracy, ece=EXCLUDED.ece,
                updated_at=NOW()
        """, ("hltv_v1_backfill", a, b, n, ll, acc, ece))
        print(f"\n  → wrote {written:,} predictions")
        print(f"  → upserted Platt coefficients for hltv_v1 (live) and hltv_v1_backfill")
    else:
        print("\n  (dry run — add --record to persist)")


if __name__ == "__main__":
    main()
