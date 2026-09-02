"""
Forebet vs OddsIntel — apples-to-apples ROI audit.

Reads dev/active/forebet_raw.json (produced by scripts/scrape_forebet.py) and
computes ROI on the settled rows whose pick_odds is present, restricted to the
markets our production model trades (1X2 highest-prob pick, OU 2.5 pick).

Settlement source: Forebet's own predict_y / predict_no class on each row —
they show a green checkmark (yes) for hits and a red cross (no) for misses
right next to the predicted outcome. We trust that flag rather than re-deriving
"did pick == outcome" because Forebet handles edge cases (draw push on AH,
void on abandoned matches) inside the flag.

Stake methodology: 10 EUR flat per bet (same as audit_vs_signalodds.py and
audit_vs_deepbetting.py).

Coverage notes (see scrape_forebet.py for details): Forebet's historical date
URLs only resolve for ~38 days back, so the matched window is shorter than the
default May 4 → today. The scraper drops rows whose match_date doesn't match
the requested date — only same-day rows count.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402

INPUT_PATH = ROOT / "dev" / "active" / "forebet_raw.json"
LEDGER_DIR = ROOT / "ledger"
OUT_PATH = LEDGER_DIR / "comparison_forebet.json"
PICKS_CSV_PATH = LEDGER_DIR / "picks_forebet.csv"

STAKE = 10.0
MIN_SAMPLE = 50
DEFAULT_START = "2026-05-04"   # calibrated tier launch


def load_forebet() -> list[dict]:
    if not INPUT_PATH.exists():
        print(f"FATAL: {INPUT_PATH} not found. Run scripts/scrape_forebet.py first.",
              file=sys.stderr)
        sys.exit(2)
    rows = json.loads(INPUT_PATH.read_text())
    print(f"Loaded {len(rows)} Forebet rows from {INPUT_PATH.name}")
    return rows


def filter_rows(rows: list[dict], start: str, end: str) -> tuple[list[dict], dict]:
    drops: Counter = Counter()
    kept: list[dict] = []
    for r in rows:
        d = r.get("match_date")
        if not d:
            drops["no_date"] += 1
            continue
        if not (start <= d < end):
            drops["out_of_window"] += 1
            continue
        if not r.get("settled"):
            drops["not_settled"] += 1
            continue
        if r.get("market") not in ("1x2", "over_under_25"):
            drops[f"market_{r.get('market')}"] += 1
            continue
        if not r.get("pick"):
            drops["no_pick"] += 1
            continue
        po = r.get("pick_odds")
        try:
            po = float(po) if po is not None else None
        except (TypeError, ValueError):
            po = None
        if po is None or po < 1.01:
            # Forebet sometimes shows "-" for niche leagues; can't audit
            # without odds.
            drops["no_pick_odds"] += 1
            continue
        kept.append(r)
    return kept, dict(drops)


def stats(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    pnl = 0.0
    won = 0
    odds_sum = 0.0
    for r in rows:
        o = float(r["pick_odds"])
        if r.get("correct"):
            pnl += (o - 1.0) * STAKE
            won += 1
        else:
            pnl -= STAKE
        odds_sum += o
    stake = STAKE * len(rows)
    return {
        "n": len(rows),
        "stake_total": round(stake, 2),
        "pnl_total": round(pnl, 2),
        "roi_pct": round(100.0 * pnl / stake, 2),
        "hit_rate_pct": round(100.0 * won / len(rows), 2),
        "avg_odds": round(odds_sum / len(rows), 3),
    }


def breakdown(rows: list[dict]) -> dict:
    out: dict = {}
    by_market = defaultdict(list)
    by_pick = defaultdict(list)
    for r in rows:
        by_market[r["market"]].append(r)
        by_pick[f'{r["market"]}::{r["pick"]}'].append(r)
    out["by_market"] = {k: stats(v) for k, v in sorted(by_market.items())}
    out["by_pick"] = {k: stats(v) for k, v in sorted(by_pick.items())}
    return out


# STALE-ODDS-HISTORY-RESTATE-2026-09-02: our side of the comparison now comes
# from scripts/_our_stats.py, which prices at odds that were LIVE at pick time
# rather than summing `pnl` (settled from the inflated `odds_at_pick`). Six
# copies of this query existed; five would have kept publishing the old number.
from scripts._our_stats import our_stats  # noqa: E402,F401


def _print_section(title: str, s: dict) -> None:
    print(f"\n[{title}]")
    if not s or s.get("n", 0) == 0:
        print("  (no data)")
        return
    print(f"  n={s['n']:>5}  stake={s.get('stake_total', 0):>9.2f}  "
          f"pnl={s.get('pnl_total', 0):>+9.2f}  "
          f"ROI={s.get('roi_pct', 0):>+6.2f}%  hit={s.get('hit_rate_pct', 0):>5.2f}%  "
          f"avg_odds={s.get('avg_odds', 0)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    start = args.start
    end = args.end or (date.today() + timedelta(days=1)).isoformat()
    print(f"Window: {start} → {end}")

    raw = load_forebet()
    kept, drops = filter_rows(raw, start, end)
    print(f"After scope filter: {len(kept)} kept, drops={drops}")
    fb = stats(kept)
    bd = breakdown(kept) if kept else {}

    print("\nPulling our production stats from DB ...")
    ours = our_stats(start, end)

    print()
    print("=" * 80)
    print(f"Forebet vs OddsIntel · {start} → {end} · stake 10 EUR flat · 1X2 + OU only")
    print("=" * 80)
    _print_section("Forebet (top-prob 1X2 + OU 2.5)", fb)
    _print_section("OddsIntel (calibrated+beta+active)", ours)

    if bd:
        print("\nForebet — by market:")
        for k, v in bd["by_market"].items():
            _print_section(f"  market: {k}", v)

    enough = fb.get("n", 0) >= MIN_SAMPLE and ours.get("n", 0) >= MIN_SAMPLE
    status = "ok" if enough else "insufficient-data-pending"
    if not enough:
        print(f"\nNOTE: below MIN_SAMPLE={MIN_SAMPLE} on one side — "
              "publishing as insufficient-data-pending.")

    # Build the per-pick rows here rather than after the payload: the
    # repricing below consumes them, and the published JSON must carry the
    # repriced figures.
    from scripts._picks_csv import compute_pnl, write_picks_csv  # noqa: E402
    csv_rows = []
    for r in kept:
        odds_f = float(r["pick_odds"])
        result = "won" if r.get("correct") else "lost"
        csv_rows.append({
            "source": "forebet",
            "kickoff_date": r.get("match_date") or "",
            "league": r.get("league_short") or "",
            "home_team": r.get("home_team") or "",
            "away_team": r.get("away_team") or "",
            "market": r.get("market") or "",
            "pick": r.get("pick") or "",
            "odds": f"{odds_f:.3f}",
            "result": result,
            "pnl_per_unit": compute_pnl(odds_f, result),
            "ref_url": f"https://www.forebet.com/en/football-predictions/predictions-1x2/{r.get('match_date','')}",
        })

    # FOREBET-REPRICE-2026-09-02 — settle Forebet's own picks at prices that
    # existed. Their published ROI is computed from the odds they SAY they
    # took, and FOREBET-ODDS-CROSS-SOURCE showed those are frequently
    # unobtainable (9.5% of picks claim >1.5x the best price anywhere, against
    # 0.7% for a source that names its book, concentrated on winners at
    # p=2.4e-06). Republishing their number with a caveat still presents
    # fiction as a measurement, so we recompute it.
    print("\nRepricing Forebet's picks at closing market odds ...")
    from workers.api_clients.db import get_conn  # noqa: E402
    from scripts._competitor_reprice import reprice  # noqa: E402
    with get_conn() as _conn, _conn.cursor() as _cur:
        rep = reprice(_cur, csv_rows, start, end)
    print(f"  repriced {rep['n_repriced']}/{rep['n_total']} picks "
          f"({rep['coverage_pct']}%), median {rep['median_books_at_close']} books at close")
    for k in ("at_claimed_odds", "at_best_close", "at_median_close", "at_bet365_close"):
        print(f"    {k:18} n={rep[k]['n']:5}  ROI {rep[k]['roi_pct']:+7.2f}%")

    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "source": "Forebet",
        "source_url": (
            "https://www.forebet.com/en/football-predictions/predictions-1x2/<date>  "
            "(and /under-over-25-goals/<date>)"
        ),
        "snapshot_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {"start": start, "end": end},
        "status": status,
        "min_sample_each_side": MIN_SAMPLE,
        "scope_notes": (
            "1X2 highest-prob pick + OU 2.5 pick only, soccer, settled rows "
            "(Forebet's own predict_y/predict_no flag), 10 EUR flat stake. "
            "Coverage limited to ~last 38 days because Forebet date URLs "
            "older than that silently fall back to today. "
            "FOREBET-REPRICE-2026-09-02: their_stats is NOT Forebet's published "
            "ROI. Forebet's quoted odds are frequently unobtainable — see "
            "scripts/verify_forebet_odds_cross_source.py, which shows 9.5% of "
            "their picks claiming >1.5x the best price available anywhere "
            "against 0.7% for a third-party control, concentrated on winners "
            "(p=2.4e-06). their_stats therefore re-settles their own picks at "
            "the BEST closing price across our books — the most favourable "
            "realistic assumption for them — over the subset we can "
            "fixture-match. Their published figure is kept verbatim as "
            "their_stats_claimed_odds, and at_median_close / at_bet365_close "
            "in their_reprice show what an ordinary single account would get."
        ),
        "reproducible_via": "scripts/scrape_forebet.py + scripts/audit_vs_forebet.py",
        # their_stats is what the landing renders. It is the REPRICED record —
        # their picks at the best closing price available across our books,
        # which is the most favourable realistic assumption for them. Their
        # own claimed-odds figure is kept, clearly labelled, as
        # their_stats_claimed_odds.
        "their_stats": {
            **rep["at_best_close"],
            "priced_at": "best closing price across our books",
            "n_total_picks": rep["n_total"],
            "coverage_pct": rep["coverage_pct"],
        },
        "their_stats_claimed_odds": {**fb, "priced_at": "Forebet's own published odds"},
        "their_reprice": rep,
        "their_breakdown": bd,
        "their_drop_reasons": drops,
        "our_stats_same_window": ours,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    blob = json.dumps({k: v for k, v in out.items() if k != "snapshot_at_utc"},
                      sort_keys=True).encode()
    print(f"\nFingerprint: {hashlib.sha256(blob).hexdigest()[:16]}")
    print(f"Wrote: {OUT_PATH}")

    n_csv = write_picks_csv(PICKS_CSV_PATH, csv_rows)
    print(f"Wrote {n_csv} rows to {PICKS_CSV_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
