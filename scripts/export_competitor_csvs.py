"""
Export the raw competitor scrape data to CSV so it's reusable later
(spreadsheets, re-audits, manual review). The JSONs at dev/active/*.json
are the canonical source — the CSVs are a human-friendly mirror.

Output: ledger/competitor_raw/{signalodds,deepbetting,forebet,tipstrr,
        betaminic}.csv

Idempotent — overwrites every CSV each run. JSON inputs are not modified.
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


def export_forebet() -> int:
    src = REPO / "dev" / "active" / "forebet_raw.json"
    if not src.exists():
        print(f"  forebet: source missing at {src}")
        return 0
    rows = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        print("  forebet: no rows — skipping")
        return 0
    cols = [
        "match_date", "requested_date", "market",
        "home_team", "away_team", "match_name", "league_short",
        "pick", "settled", "correct",
        "score_home", "score_away",
        "odds_home", "odds_draw", "odds_away",
        "odds_over", "odds_under", "pick_odds",
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "forebet.csv"
    n = 0
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            if not isinstance(r, dict):
                continue
            w.writerow([_norm(r.get(c)) for c in cols])
            n += 1
    print(f"  forebet: wrote {n:,} rows → {out.relative_to(REPO)}")
    return n


def export_tipstrr() -> int:
    src = REPO / "dev" / "active" / "tipstrr_raw.json"
    if not src.exists():
        print(f"  tipstrr: source missing at {src}")
        return 0
    tipsters = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(tipsters, list) or not tipsters:
        print("  tipstrr: no tipsters — skipping")
        return 0
    # Flatten to one row per (tipster × month). Per-bet detail is paywalled
    # on Tipstrr — we only have monthly aggregates.
    cols = [
        "slug", "name", "active", "football_only",
        "month", "tips", "win", "lose", "void",
        "averageOdds", "staked", "profit",
        "levelStakeProfit", "levelStakeROI", "winPercentage",
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "tipstrr.csv"
    n = 0
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for t in tipsters:
            if not isinstance(t, dict):
                continue
            base = {
                "slug": t.get("slug"),
                "name": t.get("name"),
                "active": t.get("active"),
                "football_only": t.get("football_only"),
            }
            for m in (t.get("monthly") or []):
                if not isinstance(m, dict):
                    continue
                row = dict(base)
                row["month"] = (m.get("date") or "")[:7]
                for k in ("tips", "win", "lose", "void", "averageOdds",
                          "staked", "profit", "levelStakeProfit",
                          "levelStakeROI", "winPercentage"):
                    row[k] = m.get(k)
                w.writerow([_norm(row.get(c)) for c in cols])
                n += 1
    print(f"  tipstrr: wrote {n:,} rows → {out.relative_to(REPO)}")
    return n


def export_betaminic() -> int:
    src = REPO / "dev" / "active" / "betaminic_raw.json"
    if not src.exists():
        print(f"  betaminic: source missing at {src}")
        return 0
    blob = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(blob, dict):
        print("  betaminic: source JSON is not a dict — skipping")
        return 0
    strategies = blob.get("strategies") or []
    if not strategies:
        # Still write a one-row CSV with the stub status so downstream
        # readers see the audit was attempted and why it has no data.
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / "betaminic.csv"
        with out.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["status", "reason", "snapshot_at_utc"])
            w.writerow([
                _norm(blob.get("status")),
                _norm(blob.get("reason")),
                _norm(blob.get("snapshot_at_utc")),
            ])
        print(f"  betaminic: auth-gated stub written → {out.relative_to(REPO)}")
        return 0
    # Future: real strategy export — same shape as the other audits
    cols = ["strategy_id", "name", "date", "league", "market",
            "pick", "odds", "stake", "result", "profit"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "betaminic.csv"
    n = 0
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for s in strategies:
            if not isinstance(s, dict):
                continue
            base = {"strategy_id": s.get("strategy_id"), "name": s.get("name")}
            for b in (s.get("settled_bets") or []):
                if not isinstance(b, dict):
                    continue
                row = dict(base)
                for k in cols[2:]:
                    row[k] = b.get(k)
                w.writerow([_norm(row.get(c)) for c in cols])
                n += 1
    print(f"  betaminic: wrote {n:,} rows → {out.relative_to(REPO)}")
    return n


def main() -> int:
    print("Exporting competitor CSVs from dev/active/*.json...")
    total = 0
    total += export_signalodds()
    total += export_deepbetting()
    total += export_forebet()
    total += export_tipstrr()
    total += export_betaminic()
    print(f"Done. Total rows: {total:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
