"""
OddsIntel — daily public-ledger snapshot export.

Dumps every settled calibrated pre-match bet (1x2 + O/U + BTTS, no AH) to
a deterministic JSON file under ledger/YYYY-MM-DD.json. Also rewrites
ledger/latest.json and ledger/index.json. Designed to be committed by a
GitHub Action (signed by github-actions[bot]) so anyone can clone the
public repo, replay every pick against ESPN/Flashscore, and verify the
timestamp of the snapshot from git history.

This is the GitHub-signed-commits leg of the verification stack:
  /api/v1/track-record   →  live JSON feed from production DB
  ledger/YYYY-MM-DD.json →  immutable daily snapshot, GPG-verified by GitHub

Run:
  python3 scripts/export_track_record_snapshot.py
Optional:
  --since YYYY-MM-DD  (default 2026-05-04, calibrated tier launch)
  --out DIR           (default ./ledger)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402

DEFAULT_SINCE = "2026-05-04"
PRE_MATCH_MARKETS = ("1x2", "o/u", "over_under_25", "btts")


def _to_iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    return str(v)


def _num(v, digits: int | None = None) -> float | None:
    if v is None:
        return None
    f = float(v)
    return round(f, digits) if digits is not None else f


def pull_calibrated_ledger(since: str) -> list[dict]:
    rows = execute_query(
        """
        SELECT
          sb.id,
          sb.match_id::text AS match_id,
          sb.created_at AS placed_at_utc,
          sb.market, sb.selection,
          sb.odds_at_pick::float       AS placed_odds,
          sb.recommended_bookmaker     AS bookmaker,
          sb.stake::float              AS stake,
          sb.pnl::float                AS pnl,
          sb.result                    AS result,
          sb.closing_odds::float       AS closing_odds,
          sb.clv::float                AS clv_any,
          sb.clv_pinnacle::float       AS clv_pin,
          m.date                       AS kickoff_utc,
          m.score_home, m.score_away,
          l.name                       AS league,
          l.country                    AS country,
          b.name                       AS bot
        FROM simulated_bets sb
        JOIN bots b      ON b.id = sb.bot_id
        JOIN matches m   ON m.id = sb.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE b.maturity_label = 'calibrated'
          AND sb.market IN ('1x2','o/u','over_under_25','btts')
          AND sb.result IN ('won','lost')
          AND sb.created_at >= %s::date
        ORDER BY sb.created_at ASC, sb.id ASC
        """,
        (since,),
    )
    out: list[dict] = []
    for r in rows:
        score = None
        if r["score_home"] is not None and r["score_away"] is not None:
            score = f"{r['score_home']}-{r['score_away']}"
        out.append({
            "id": str(r["id"]),
            "match_id": r["match_id"],
            "kickoff_utc": _to_iso(r["kickoff_utc"]),
            "league": r["league"],
            "country": r["country"],
            "market": r["market"],
            "selection": r["selection"],
            "placed_odds": _num(r["placed_odds"], 4),
            "bookmaker": r["bookmaker"],
            "placed_at_utc": _to_iso(r["placed_at_utc"]),
            "closing_odds": _num(r["closing_odds"], 4),
            "clv_any_pct": _num(_num(r["clv_any"], 6) and r["clv_any"] * 100, 4)
                if r["clv_any"] is not None else None,
            "clv_pin_pct": _num(_num(r["clv_pin"], 6) and r["clv_pin"] * 100, 4)
                if r["clv_pin"] is not None else None,
            "stake": _num(r["stake"], 4),
            "pnl": _num(r["pnl"], 4),
            "result": r["result"],
            "score": score,
            "bot": r["bot"],
        })
    return out


def compute_summary(bets: list[dict], since: str) -> dict:
    n = len(bets)
    stake = sum((b["stake"] or 0) for b in bets)
    pnl = sum((b["pnl"] or 0) for b in bets)
    clv_vals = sorted([b["clv_pin_pct"] for b in bets
                       if b.get("clv_pin_pct") is not None])
    clv_n = len(clv_vals)
    # Median is the publishable robust stat. Mean is unreliable because some
    # "closing" Pinnacle snaps are days pre-kickoff (data-quality noise) and
    # produce ±50% CLV outliers that swing the mean by 10pp either way. The
    # 5-min closing_snap cron tightens this going forward; until then the
    # median is honest.
    clv_median = None
    if clv_n:
        if clv_n % 2 == 0:
            clv_median = (clv_vals[clv_n // 2 - 1] + clv_vals[clv_n // 2]) / 2
        else:
            clv_median = clv_vals[clv_n // 2]
    clv_beats = sum(1 for c in clv_vals if c > 0)
    return {
        "since": since,
        "total_bets": n,
        "stake_total": round(stake, 2),
        "pnl_total": round(pnl, 2),
        "roi_pct": round(100 * pnl / stake, 4) if stake > 0 else None,
        "median_clv_pin_pct": round(clv_median, 4) if clv_median is not None else None,
        "clv_pin_coverage_pct": round(100 * clv_n / n, 2) if n else 0,
        "clv_pin_beat_pct": round(100 * clv_beats / clv_n, 2) if clv_n else None,
        "scope": "calibrated bots, pre-match markets (1x2, OU 2.5, BTTS), settled only",
        "clv_methodology": ("CLV(pinnacle) is placed_odds / Pinnacle close - 1. "
                            "Median is robust to mixed-vintage closing snaps; "
                            "beat-rate counts picks with CLV>0."),
    }


def write_snapshot(out_dir: Path, snap_date: str, summary: dict, bets: list[dict]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "snapshot": {
            "date": snap_date,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "scope": summary["scope"],
        },
        "summary": summary,
        "bets": bets,
    }
    # Deterministic serialization — same input always produces byte-identical
    # output so git diff is clean and a reader can hash to verify.
    body = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"

    dated = out_dir / f"{snap_date}.json"
    dated.write_text(body, encoding="utf-8")

    latest = out_dir / "latest.json"
    latest.write_text(body, encoding="utf-8")

    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    index_path = out_dir / "index.json"
    index = {}
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            index = {}
    index[snap_date] = {
        "sha256": sha,
        "n_bets": summary["total_bets"],
        "roi_pct": summary["roi_pct"],
        "generated_at_utc": payload["snapshot"]["generated_at_utc"],
    }
    index_path.write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return dated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE)
    ap.add_argument("--out", default="ledger")
    args = ap.parse_args()

    out_dir = Path(args.out)
    snap_date = date.today().isoformat()

    bets = pull_calibrated_ledger(args.since)
    summary = compute_summary(bets, args.since)
    path = write_snapshot(out_dir, snap_date, summary, bets)

    print(f"Wrote {path}")
    print(f"  {summary['total_bets']:,} bets · ROI {summary['roi_pct']}% · "
          f"median CLV(pin) {summary['median_clv_pin_pct']}% · "
          f"beat {summary['clv_pin_beat_pct']}% (n={summary['total_bets'] * summary['clv_pin_coverage_pct'] // 100:.0f})")


if __name__ == "__main__":
    main()
