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
    """Pull the public ledger cohort at EXECUTABLE prices.

    ── LEDGER-EXEC-PRICE-BASIS-2026-09-06 ──────────────────────────────────
    This was the FIFTH copy of the executable-price rule and the one nobody
    found on 2026-09-05, when the same rule was fixed four times in one day
    (admin index, admin detail, /api/v1/track-record, /performance).

    It published `placed_odds = sb.odds_at_pick` and `pnl = sb.pnl`, both
    derived from the STALE-BEST-ODDS high-water mark (gotcha 30 — the odds
    tables are append-only, so a MAX is not a price anyone could have taken).

    Measured on this script's own cohort (n=553, calibrated, settled,
    since 2026-05-04):

        published here (flat EUR 10 on odds_at_pick)   +18.54%
        its stored Kelly pnl/stake                     +17.72%
        executable (odds_at_pick_live)                 +14.23%

    So the artefact the docstring calls "the GitHub-signed-commits leg of the
    verification stack" — the one a skeptic clones to check us — overstated
    ROI by 4.31 percentage points. That is the worst possible place for this
    bug, because signing a number does not make it true; it just makes it
    durable.

    Two other cohort defects, both of which made this ledger disagree with
    every other public surface:
      * no `inplay_%` exclusion — in-play bots are not part of the published
        pre-match record anywhere else;
      * no `retired_at IS NULL` — retired bots leak in, which is exactly the
        selection bias RETIRED-BOT-LEAK-FIX exists to prevent.

    The raw stored values are still emitted alongside, under explicit names,
    so the ledger stays auditable rather than merely restated: a reader can
    reproduce both numbers and see which basis produced which.
    """
    rows = execute_query(
        """
        SELECT
          sb.id,
          sb.match_id::text AS match_id,
          sb.created_at AS placed_at_utc,
          sb.market, sb.selection,
          -- Executable price: the live re-quote when we have one, else the
          -- pick-time price. Mirrors execOdds() in odds-intel-web/src/lib/
          -- engine-data.ts exactly; see LEDGER-EXEC-PRICE-BASIS above.
          CASE WHEN sb.odds_at_pick_live IS NOT NULL AND sb.odds_at_pick_live > 1
               THEN sb.odds_at_pick_live::float
               ELSE sb.odds_at_pick::float
          END                          AS placed_odds,
          sb.odds_at_pick::float       AS odds_at_pick_raw,
          sb.odds_at_pick_live::float  AS odds_at_pick_live,
          sb.recommended_bookmaker     AS bookmaker,
          sb.stake::float              AS stake,
          -- P&L repriced at the executable odds, mirroring execPnl(). Combos
          -- keep their stored pnl: odds_at_pick_live describes ONE selection,
          -- so repricing a multi-leg bet off a single leg would be worse than
          -- the bug being fixed.
          CASE
            WHEN sb.combo_legs IS NOT NULL THEN sb.pnl::float
            WHEN sb.stake IS NULL OR sb.stake <= 0 THEN sb.pnl::float
            WHEN sb.result = 'won' THEN
              (CASE WHEN sb.odds_at_pick_live IS NOT NULL AND sb.odds_at_pick_live > 1
                    THEN sb.odds_at_pick_live::float
                    ELSE sb.odds_at_pick::float END - 1) * sb.stake::float
            WHEN sb.result = 'lost' THEN -sb.stake::float
            ELSE sb.pnl::float
          END                          AS pnl,
          sb.pnl::float                AS pnl_stored,
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
          AND b.retired_at IS NULL
          AND b.name NOT LIKE 'inplay\\_%%'
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
            # LEDGER-EXEC-PRICE-BASIS: both inputs are published so the
            # executable price is reproducible from the ledger itself rather
            # than taken on trust. `placed_odds` == odds_at_pick_live when
            # present and > 1, else odds_at_pick.
            "price_basis": ("live_requote"
                            if (r["odds_at_pick_live"] or 0) > 1
                            else "pick_time"),
            "odds_at_pick": _num(r["odds_at_pick_raw"], 4),
            "odds_at_pick_live": _num(r["odds_at_pick_live"], 4),
            "bookmaker": r["bookmaker"],
            "placed_at_utc": _to_iso(r["placed_at_utc"]),
            "closing_odds": _num(r["closing_odds"], 4),
            "clv_any_pct": _num(_num(r["clv_any"], 6) and r["clv_any"] * 100, 4)
                if r["clv_any"] is not None else None,
            "clv_pin_pct": _num(_num(r["clv_pin"], 6) and r["clv_pin"] * 100, 4)
                if r["clv_pin"] is not None else None,
            "stake": _num(r["stake"], 4),
            "pnl": _num(r["pnl"], 4),
            "pnl_stored": _num(r["pnl_stored"], 4),
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
