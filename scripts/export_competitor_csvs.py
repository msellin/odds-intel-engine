"""
Export the raw competitor scrape data to CSV so it's reusable later
(spreadsheets, re-audits, manual review). The JSONs at dev/active/*.json
are the canonical source — the CSVs are a human-friendly mirror.

Output: ledger/competitor_raw/{signalodds,deepbetting}.csv

Idempotent — overwrites both CSVs each run. JSON inputs are not modified.
Safe to re-run after a fresh scrape.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


REPO = Path(__file__).parent.parent
OUT_DIR = REPO / "ledger" / "competitor_raw"


def _norm(s):
    """Normalize a value for CSV — never None, never raw dict."""
    if s is None:
        return ""
    if isinstance(s, (dict, list)):
        return json.dumps(s, separators=(",", ":"))
    return str(s)


def export_signalodds() -> int:
    src = REPO / "dev" / "active" / "signalodds_soccer.json"
    if not src.exists():
        print(f"  signalodds: source missing at {src}")
        return 0
    rows = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print("  signalodds: source JSON is not a list — skipping")
        return 0
    if not rows:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "signalodds.csv"

    # Pick a stable schema — flatten the most-useful fields, drop nested
    # objects to keep the CSV grid-friendly. Anything dropped is still in
    # the canonical JSON.
    cols = [
        "page", "is_premium", "model", "sport", "league",
        "match_date", "home_team", "away_team",
        "market", "market_key", "outcome_name", "odds", "bookmaker",
        "confidence", "ev_pct",
        "result", "settled_at",
    ]
    n = 0
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([_norm(r.get(c)) for c in cols])
            n += 1
    print(f"  signalodds: wrote {n:,} rows → {out.relative_to(REPO)}")
    return n


def export_deepbetting() -> int:
    src = REPO / "dev" / "active" / "deepbetting_stats.json"
    if not src.exists():
        print(f"  deepbetting: source missing at {src}")
        return 0
    blob = json.loads(src.read_text(encoding="utf-8"))
    # DeepBetting envelope shape: { success, type, data: { football: [...] }, count }
    # Football rows live under `data.football`.
    data = blob.get("data") if isinstance(blob, dict) else None
    rows = data.get("football") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        print("  deepbetting: no usable football rows — skipping")
        return 0

    # Schema discovered from actual scraped payload (see sample in
    # comment): sport, division_label, date_norm, forecast_type,
    # forecast_status, odds, game_status, confidence
    cols = [
        "sport", "division_label", "date_norm",
        "forecast_type", "forecast_status",
        "odds", "game_status", "confidence",
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "deepbetting.csv"
    n = 0
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            if not isinstance(r, dict):
                continue
            w.writerow([_norm(r.get(c)) for c in cols])
            n += 1
    print(f"  deepbetting: wrote {n:,} rows → {out.relative_to(REPO)}")
    return n


def main() -> int:
    print("Exporting competitor CSVs from dev/active/*.json...")
    total = 0
    total += export_signalodds()
    total += export_deepbetting()
    print(f"Done. Total rows: {total:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
